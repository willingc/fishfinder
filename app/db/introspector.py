"""PostgreSQL pg_catalog introspection and safe data profiling."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg import sql as psql

from app.db.connection import DatabaseManager
from app.nl2sql.catalog import ColumnInfo, ForeignKeyInfo, SchemaCatalog, TableInfo, load_glossary


def infer_jsonb_paths(
    samples: list[Any],
    *,
    max_depth: int = 3,
    max_paths: int = 24,
) -> dict[str, str]:
    """Recursively infer JSONB key paths and value types from sampled JSON documents."""
    path_types: dict[str, set[str]] = defaultdict(set)

    def _walk(obj: Any, prefix: str, depth: int) -> None:
        if len(path_types) >= max_paths or depth > max_depth:
            return
        if isinstance(obj, dict):
            for key, val in obj.items():
                key_path = f"{prefix}.{key}" if prefix else str(key)
                if isinstance(val, dict):
                    path_types[key_path].add("object")
                    _walk(val, key_path, depth + 1)
                elif isinstance(val, list):
                    elem_types = {_scalar_type_name(item) for item in val[:10]}
                    elem_label = "|".join(sorted(elem_types)) if elem_types else "any"
                    path_types[f"{key_path}[]"].add(elem_label)
                    for item in val[:3]:
                        if isinstance(item, dict):
                            _walk(item, f"{key_path}[]", depth + 1)
                else:
                    path_types[key_path].add(_scalar_type_name(val))
        elif isinstance(obj, list):
            for item in obj[:5]:
                _walk(item, f"{prefix}[]", depth + 1)

    for doc in samples:
        if isinstance(doc, (dict, list)):
            _walk(doc, "", 0)

    return {path: "|".join(sorted(types)) for path, types in sorted(path_types.items())[:max_paths]}


def _scalar_type_name(val: Any) -> str:
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "bool"
    if isinstance(val, int):
        return "int"
    if isinstance(val, float):
        return "number"
    if isinstance(val, str):
        return "text"
    if isinstance(val, dict):
        return "object"
    if isinstance(val, list):
        return "array"
    return type(val).__name__


class SchemaIntrospector:
    """Introspects PostgreSQL pg_catalog metadata and profiles complex data types safely."""

    def __init__(
        self,
        db: DatabaseManager,
        *,
        schema_name: str = "public",
        sample_limit: int = 500,
        low_cardinality_limit: int = 25,
        glossary_path: Path | None = None,
    ) -> None:
        self.db = db
        self.schema_name = schema_name
        self.sample_limit = sample_limit
        self.low_cardinality_limit = low_cardinality_limit
        self.glossary_path = glossary_path

    def introspect(self) -> SchemaCatalog:
        """Run full metadata introspection and bounded data profiling in read-only mode."""
        with self.db.read_only_cursor(statement_timeout_ms=15_000) as cur:
            cur.execute("SELECT current_database(), current_setting('TimeZone')")
            database_name, db_timezone = cur.fetchone()

            enums = self._fetch_enums(cur)
            tables = self._fetch_tables_and_views(cur)
            pk_map = self._fetch_primary_keys(cur)
            fk_list = self._fetch_foreign_keys(cur)
            check_map = self._fetch_check_constraints(cur)
            self._fetch_columns(cur, tables, pk_map, enums)

            for tbl_name, tbl in tables.items():
                tbl.primary_keys = pk_map.get(tbl_name, [])
                tbl.check_constraints = check_map.get(tbl_name, [])

            for fk in fk_list:
                if fk.source_table in tables:
                    tables[fk.source_table].foreign_keys.append(fk)

            # Safe data profiling for JSONB, arrays, and low-cardinality text columns
            for tbl in tables.values():
                if tbl.table_type != "table":
                    continue
                self._profile_table_columns(cur, tbl)

        glossary = load_glossary(self.glossary_path) if self.glossary_path else load_glossary(Path("__none__"))
        # Apply glossary column overrides directly onto catalog metadata comments if helpful
        return SchemaCatalog(
            schema_name=self.schema_name,
            database_name=database_name,
            database_timezone=db_timezone,
            refreshed_at=datetime.now(timezone.utc).isoformat(),
            enums=enums,
            tables=tables,
            foreign_keys=fk_list,
            glossary=glossary,
        )

    def _fetch_enums(self, cur) -> dict[str, list[str]]:
        cur.execute(
            """
            SELECT t.typname, e.enumlabel
            FROM pg_catalog.pg_type t
            JOIN pg_catalog.pg_enum e ON e.enumtypid = t.oid
            JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
            WHERE n.nspname = %s
            ORDER BY t.typname, e.enumsortorder
            """,
            (self.schema_name,),
        )
        enums: dict[str, list[str]] = defaultdict(list)
        for typname, label in cur.fetchall():
            enums[typname].append(label)
        return dict(enums)

    def _fetch_tables_and_views(self, cur) -> dict[str, TableInfo]:
        cur.execute(
            """
            SELECT
                c.relname,
                CASE c.relkind
                    WHEN 'v' THEN 'view'
                    WHEN 'm' THEN 'view'
                    ELSE 'table'
                END AS table_type,
                pg_catalog.obj_description(c.oid, 'pg_class') AS comment,
                GREATEST(c.reltuples::BIGINT, 0) AS row_estimate
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND c.relkind IN ('r', 'p', 'v', 'm')
            ORDER BY c.relname
            """,
            (self.schema_name,),
        )
        tables: dict[str, TableInfo] = {}
        for relname, table_type, comment, row_est in cur.fetchall():
            tables[relname] = TableInfo(
                name=relname,
                table_type=table_type,
                comment=comment,
                row_estimate=int(row_est or 0),
            )
        return tables

    def _fetch_primary_keys(self, cur) -> dict[str, list[str]]:
        cur.execute(
            """
            SELECT
                c.relname,
                a.attname
            FROM pg_catalog.pg_constraint con
            JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
            JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
            WHERE n.nspname = %s
              AND con.contype = 'p'
            ORDER BY c.relname, k.ord
            """,
            (self.schema_name,),
        )
        pk_map: dict[str, list[str]] = defaultdict(list)
        for tbl_name, col_name in cur.fetchall():
            pk_map[tbl_name].append(col_name)
        return dict(pk_map)

    def _fetch_foreign_keys(self, cur) -> list[ForeignKeyInfo]:
        cur.execute(
            """
            SELECT
                con.conname,
                src.relname AS source_table,
                sa.attname AS source_column,
                tgt.relname AS target_table,
                ta.attname AS target_column
            FROM pg_catalog.pg_constraint con
            JOIN pg_catalog.pg_class src ON src.oid = con.conrelid
            JOIN pg_catalog.pg_namespace sn ON sn.oid = src.relnamespace
            JOIN pg_catalog.pg_class tgt ON tgt.oid = con.confrelid
            JOIN pg_catalog.pg_namespace tn ON tn.oid = tgt.relnamespace
            JOIN LATERAL unnest(con.conkey, con.confkey) AS cols(s_attnum, t_attnum) ON TRUE
            JOIN pg_catalog.pg_attribute sa ON sa.attrelid = src.oid AND sa.attnum = cols.s_attnum
            JOIN pg_catalog.pg_attribute ta ON ta.attrelid = tgt.oid AND ta.attnum = cols.t_attnum
            WHERE sn.nspname = %s
              AND tn.nspname = %s
              AND con.contype = 'f'
            ORDER BY src.relname, con.conname, sa.attname
            """,
            (self.schema_name, self.schema_name),
        )
        return [
            ForeignKeyInfo(
                constraint_name=conname,
                source_table=src_tbl,
                source_column=src_col,
                target_table=tgt_tbl,
                target_column=tgt_col,
            )
            for conname, src_tbl, src_col, tgt_tbl, tgt_col in cur.fetchall()
        ]

    def _fetch_check_constraints(self, cur) -> dict[str, list[str]]:
        cur.execute(
            """
            SELECT
                c.relname,
                pg_catalog.pg_get_constraintdef(con.oid) AS definition
            FROM pg_catalog.pg_constraint con
            JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND con.contype = 'c'
            ORDER BY c.relname, con.conname
            """,
            (self.schema_name,),
        )
        checks: dict[str, list[str]] = defaultdict(list)
        for tbl_name, defn in cur.fetchall():
            checks[tbl_name].append(defn)
        return dict(checks)

    def _fetch_columns(
        self,
        cur,
        tables: dict[str, TableInfo],
        pk_map: dict[str, list[str]],
        enums: dict[str, list[str]],
    ) -> None:
        cur.execute(
            """
            SELECT
                c.relname AS table_name,
                a.attname AS column_name,
                pg_catalog.format_type(a.atttypid, a.atttypmod) AS formatted_type,
                t.typname AS udt_name,
                t.typelem AS elem_oid,
                et.typname AS elem_type_name,
                NOT a.attnotnull AS is_nullable,
                pg_catalog.pg_get_expr(ad.adbin, ad.adrelid) AS column_default,
                pg_catalog.col_description(c.oid, a.attnum) AS comment
            FROM pg_catalog.pg_attribute a
            JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_catalog.pg_type t ON t.oid = a.atttypid
            LEFT JOIN pg_catalog.pg_type et ON et.oid = t.typelem AND t.typelem <> 0
            LEFT JOIN pg_catalog.pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
            WHERE n.nspname = %s
              AND c.relkind IN ('r', 'p', 'v', 'm')
              AND a.attnum > 0
              AND NOT a.attisdropped
            ORDER BY c.relname, a.attnum
            """,
            (self.schema_name,),
        )
        for (
            tbl_name,
            col_name,
            formatted_type,
            udt_name,
            elem_oid,
            elem_type_name,
            is_nullable,
            col_default,
            comment,
        ) in cur.fetchall():
            if tbl_name not in tables:
                continue
            is_array = formatted_type.endswith("[]") or (bool(elem_oid) and udt_name.startswith("_"))
            enum_vals = enums.get(udt_name, []) or enums.get(formatted_type, [])
            tables[tbl_name].columns.append(
                ColumnInfo(
                    name=col_name,
                    data_type=formatted_type,
                    udt_name=udt_name,
                    nullable=bool(is_nullable),
                    is_primary_key=col_name in pk_map.get(tbl_name, []),
                    default=col_default,
                    comment=comment,
                    enum_values=enum_vals,
                    array_element_type=elem_type_name if is_array else None,
                )
            )

    def _profile_table_columns(self, cur, tbl: TableInfo) -> None:
        """Profile JSONB key paths, array sample elements, and low-cardinality text columns."""
        schema_ident = psql.Identifier(self.schema_name)
        table_ident = psql.Identifier(tbl.name)

        # Update exact count for small demo tables if reltuples hasn't been analyzed yet
        if tbl.row_estimate == 0:
            cur.execute(
                psql.SQL("SELECT COUNT(*) FROM (SELECT 1 FROM {}.{} LIMIT %s) s").format(
                    schema_ident, table_ident
                ),
                (self.sample_limit,),
            )
            tbl.row_estimate = int(cur.fetchone()[0])

        for col in tbl.columns:
            col_ident = psql.Identifier(col.name)

            # 1. JSONB / JSON recursive key path inference
            if col.udt_name in ("jsonb", "json"):
                cur.execute(
                    psql.SQL(
                        "SELECT {} FROM {}.{} WHERE {} IS NOT NULL LIMIT %s"
                    ).format(col_ident, schema_ident, table_ident, col_ident),
                    (self.sample_limit,),
                )
                samples = [row[0] for row in cur.fetchall()]
                if samples:
                    col.jsonb_schema = infer_jsonb_paths(samples)
                continue

            # 2. PostgreSQL Arrays: collect sample distinct elements
            if col.array_element_type:
                cur.execute(
                    psql.SQL(
                        """
                        SELECT DISTINCT elem::TEXT
                        FROM (
                            SELECT unnest({}) AS elem
                            FROM (SELECT {} FROM {}.{} WHERE {} IS NOT NULL LIMIT %s) sub
                        ) elems
                        WHERE elem IS NOT NULL
                        ORDER BY 1
                        LIMIT 20
                        """
                    ).format(col_ident, col_ident, schema_ident, table_ident, col_ident),
                    (self.sample_limit,),
                )
                col.sample_values = [row[0] for row in cur.fetchall()]
                continue

            # 3. Low-cardinality text/varchar columns (skip emails, subjects, names, numbers)
            if col.udt_name in ("text", "varchar", "bpchar") and not col.is_primary_key:
                if any(
                    skip in col.name.lower()
                    for skip in ("email", "subject", "full_name", "number", "sku", "checksum")
                ):
                    continue
                cur.execute(
                    psql.SQL(
                        """
                        SELECT DISTINCT {}::TEXT
                        FROM (SELECT {} FROM {}.{} WHERE {} IS NOT NULL LIMIT %s) sub
                        ORDER BY 1
                        LIMIT %s
                        """
                    ).format(col_ident, col_ident, schema_ident, table_ident, col_ident),
                    (self.sample_limit, self.low_cardinality_limit + 1),
                )
                vals = [row[0] for row in cur.fetchall()]
                if 0 < len(vals) <= self.low_cardinality_limit:
                    # Only treat as low-cardinality if values aren't mostly unique 1-to-1 strings
                    if tbl.row_estimate == 0 or len(vals) < max(tbl.row_estimate, 2) or len(vals) <= 6:
                        col.distinct_values = vals
