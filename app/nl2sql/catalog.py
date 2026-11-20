"""Enriched schema catalog, foreign-key graph, and business glossary loader."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ColumnInfo(BaseModel):
    name: str
    data_type: str
    udt_name: str = ""
    nullable: bool = True
    is_primary_key: bool = False
    default: str | None = None
    comment: str | None = None
    enum_values: list[str] = Field(default_factory=list)
    jsonb_schema: dict[str, str] = Field(default_factory=dict)
    array_element_type: str | None = None
    sample_values: list[str] = Field(default_factory=list)
    distinct_values: list[str] = Field(default_factory=list)


class ForeignKeyInfo(BaseModel):
    constraint_name: str
    source_table: str
    source_column: str
    target_table: str
    target_column: str


class TableInfo(BaseModel):
    name: str
    table_type: str = "table"  # "table" or "view"
    comment: str | None = None
    row_estimate: int = 0
    columns: list[ColumnInfo] = Field(default_factory=list)
    primary_keys: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = Field(default_factory=list)
    check_constraints: list[str] = Field(default_factory=list)

    @property
    def is_junction_table(self) -> bool:
        """Heuristic: a bridge/junction table has >=2 FKs and most columns are FK/PK/metadata."""
        if self.table_type != "table" or len(self.foreign_keys) < 2:
            return False
        fk_cols = {fk.source_column for fk in self.foreign_keys}
        return len(self.columns) <= len(fk_cols) + 4


class GlossaryTerm(BaseModel):
    name: str
    definition: str
    sql_expression: str = ""
    tables: list[str] = Field(default_factory=list)


class FewShotExample(BaseModel):
    question: str
    sql: str
    tables: list[str] = Field(default_factory=list)


class Glossary(BaseModel):
    terms: list[GlossaryTerm] = Field(default_factory=list)
    column_overrides: dict[str, str] = Field(default_factory=dict)
    few_shot_examples: list[FewShotExample] = Field(default_factory=list)


class SchemaCatalog(BaseModel):
    """Complete enriched catalog for a PostgreSQL schema."""

    schema_name: str = "public"
    database_name: str = ""
    database_timezone: str = "UTC"
    refreshed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    enums: dict[str, list[str]] = Field(default_factory=dict)
    tables: dict[str, TableInfo] = Field(default_factory=dict)
    foreign_keys: list[ForeignKeyInfo] = Field(default_factory=list)
    glossary: Glossary = Field(default_factory=Glossary)

    def build_fk_graph(self) -> dict[str, dict[str, list[ForeignKeyInfo]]]:
        """Build an undirected adjacency map: table -> neighbor_table -> [FK edges]."""
        graph: dict[str, dict[str, list[ForeignKeyInfo]]] = defaultdict(lambda: defaultdict(list))
        for table_name in self.tables:
            graph[table_name]  # ensure every table is present as a node
        for fk in self.foreign_keys:
            if fk.source_table in self.tables and fk.target_table in self.tables:
                graph[fk.source_table][fk.target_table].append(fk)
                graph[fk.target_table][fk.source_table].append(fk)
        return graph

    def compact_index(self) -> str:
        """Stage 1 compact index (~1 line per table) for fast Gemini Flash table linking."""
        lines: list[str] = []
        for name in sorted(self.tables):
            tbl = self.tables[name]
            kind = "[VIEW]" if tbl.table_type == "view" else "[TABLE]"
            key_cols = ", ".join(c.name for c in tbl.columns[:10])
            if len(tbl.columns) > 10:
                key_cols += f", ... (+{len(tbl.columns) - 10})"
            fk_targets = sorted({fk.target_table for fk in tbl.foreign_keys})
            fk_str = f" | FKs->[{', '.join(fk_targets)}]" if fk_targets else ""
            desc = tbl.comment or "No description"
            lines.append(f"- {name} {kind}: {desc} | cols: {key_cols}{fk_str}")
        return "\n".join(lines)

    def render_focused_context(self, selected_tables: list[str]) -> str:
        """Stage 2 focused schema DDL + enriched metadata for only the linked tables."""
        selected_set = {t for t in selected_tables if t in self.tables}
        blocks: list[str] = [
            f"Database Schema: {self.schema_name} | Timezone: {self.database_timezone}",
        ]

        for name in sorted(selected_set):
            tbl = self.tables[name]
            header = f"\n### {tbl.table_type.upper()} {tbl.name}"
            if tbl.row_estimate > 0:
                header += f" (~{tbl.row_estimate} rows)"
            blocks.append(header)
            if tbl.comment:
                blocks.append(f"-- {tbl.comment}")

            for col in tbl.columns:
                parts = [f"  - {col.name} {col.data_type}"]
                if col.is_primary_key:
                    parts.append("PK")
                if not col.nullable:
                    parts.append("NOT NULL")
                if col.enum_values:
                    vals = ", ".join(f"'{v}'" for v in col.enum_values)
                    parts.append(f"ENUM({vals})")
                if col.distinct_values:
                    vals = ", ".join(f"'{v}'" for v in col.distinct_values[:25])
                    parts.append(f"VALUES({vals})")
                if col.array_element_type:
                    sample_str = ", ".join(col.sample_values[:8])
                    parts.append(f"ARRAY<{col.array_element_type}> samples=[{sample_str}]")
                if col.jsonb_schema:
                    jpaths = "; ".join(f"{k}: {v}" for k, v in list(col.jsonb_schema.items())[:18])
                    parts.append(f"JSONB_PATHS({jpaths})")

                override_key = f"{tbl.name}.{col.name}"
                note = self.glossary.column_overrides.get(override_key) or col.comment
                if note:
                    parts.append(f"-- {note}")
                blocks.append(" ".join(parts))

            # Foreign keys involving selected tables
            relevant_fks = [
                fk for fk in tbl.foreign_keys if fk.target_table in selected_set
            ]
            if relevant_fks:
                blocks.append("  Foreign Keys:")
                for fk in relevant_fks:
                    blocks.append(
                        f"    * {fk.source_table}.{fk.source_column} -> {fk.target_table}.{fk.target_column}"
                    )

            if tbl.check_constraints:
                blocks.append("  Checks: " + "; ".join(tbl.check_constraints[:6]))

        # Relevant business glossary terms
        matching_terms = [
            term
            for term in self.glossary.terms
            if not term.tables or any(t in selected_set for t in term.tables)
        ]
        if matching_terms:
            blocks.append("\n### Business Glossary")
            for term in matching_terms:
                expr = f" | SQL: `{term.sql_expression}`" if term.sql_expression else ""
                blocks.append(f"- **{term.name}**: {term.definition}{expr}")

        # Relevant few-shot examples
        matching_examples = [
            ex
            for ex in self.glossary.few_shot_examples
            if any(t in selected_set for t in ex.tables)
        ]
        if matching_examples:
            blocks.append("\n### Verified Few-Shot Examples")
            for ex in matching_examples[:3]:
                blocks.append(f"Q: {ex.question}\n```sql\n{ex.sql.strip()}\n```")

        return "\n".join(blocks)

    def to_api_dict(self) -> dict[str, Any]:
        """Serialize catalog for GET /api/schema."""
        return self.model_dump()


def load_glossary(path: Path) -> Glossary:
    """Load glossary.yaml if present, returning an empty Glossary otherwise."""
    if not path.exists():
        return Glossary()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Glossary.model_validate(raw)
