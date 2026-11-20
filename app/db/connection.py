"""PostgreSQL connection pooling and read-only query execution helpers."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import time
from typing import Any, Iterator

import psycopg
from psycopg_pool import ConnectionPool

from app.db.serializer import serialize_rows


@dataclass
class QueryExecutionResult:
    """Raw and serialized output from a read-only PostgreSQL query."""

    columns: list[str]
    column_types: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    execution_ms: float
    explain_plan: dict[str, Any] | None = None


class DatabaseManager:
    """Manages a psycopg3 connection pool and enforces read-only execution."""

    def __init__(self, database_url: str, statement_timeout_ms: int = 10_000) -> None:
        self.database_url = database_url
        self.statement_timeout_ms = statement_timeout_ms
        self._pool: ConnectionPool | None = None
        self._oid_type_cache: dict[int, str] = {}

    def open(self) -> None:
        if self._pool is None:
            self._pool = ConnectionPool(
                conninfo=self.database_url,
                min_size=1,
                max_size=8,
                open=True,
                kwargs={"autocommit": False},
            )
            self._load_oid_types()

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    @contextmanager
    def read_only_cursor(self, statement_timeout_ms: int | None = None) -> Iterator[psycopg.Cursor]:
        """Yield a cursor inside an explicit READ ONLY transaction with statement_timeout."""
        if self._pool is None:
            self.open()
        assert self._pool is not None
        timeout = int(statement_timeout_ms or self.statement_timeout_ms)

        with self._pool.connection() as conn:
            try:
                with conn.cursor() as cur:
                    # Enforce Layer 1 read-only transaction and statement timeout
                    cur.execute("SET TRANSACTION READ ONLY")
                    cur.execute(f"SET LOCAL statement_timeout = '{timeout}'")
                    yield cur
            finally:
                # Always roll back read-only transactions so connections return clean
                conn.rollback()

    def _load_oid_types(self) -> None:
        """Cache PostgreSQL type OID -> human-readable type name mappings."""
        try:
            with self.read_only_cursor() as cur:
                cur.execute("SELECT oid, typname FROM pg_catalog.pg_type")
                self._oid_type_cache = {int(oid): str(name) for oid, name in cur.fetchall()}
        except Exception:
            self._oid_type_cache = {}

    def type_name_for_oid(self, oid: int | None) -> str:
        if oid is None:
            return "unknown"
        return self._oid_type_cache.get(oid, str(oid))

    def check_health(self) -> dict[str, Any]:
        """Verify database connectivity and read-only session settings."""
        with self.read_only_cursor(statement_timeout_ms=3000) as cur:
            cur.execute("SELECT current_database(), current_user, version(), current_setting('transaction_read_only')")
            db_name, db_user, pg_version, tx_ro = cur.fetchone()
            return {
                "connected": True,
                "database": db_name,
                "user": db_user,
                "version": pg_version.split(",")[0],
                "transaction_read_only": tx_ro == "on",
            }

    def explain_query(self, sql: str, statement_timeout_ms: int | None = None) -> dict[str, Any]:
        """Run EXPLAIN (FORMAT JSON) inside a read-only transaction as a dry-run check."""
        with self.read_only_cursor(statement_timeout_ms=statement_timeout_ms) as cur:
            cur.execute(f"EXPLAIN (FORMAT JSON) {sql}")
            row = cur.fetchone()
            if row and isinstance(row[0], list) and row[0]:
                return row[0][0]
            return {}

    def execute_query(
        self,
        sql: str,
        *,
        run_explain: bool = True,
        statement_timeout_ms: int | None = None,
    ) -> QueryExecutionResult:
        """Dry-run with EXPLAIN (optional) and execute a validated SELECT query safely."""
        explain_plan: dict[str, Any] | None = None
        if run_explain:
            explain_plan = self.explain_query(sql, statement_timeout_ms=statement_timeout_ms)

        start = time.perf_counter()
        with self.read_only_cursor(statement_timeout_ms=statement_timeout_ms) as cur:
            cur.execute(sql)
            description = cur.description or []
            columns = [col.name for col in description]
            column_types = [self.type_name_for_oid(col.type_code) for col in description]
            raw_rows = cur.fetchall()
        elapsed_ms = round((time.perf_counter() - start) * 1000.0, 2)

        serialized = serialize_rows(columns, raw_rows)
        return QueryExecutionResult(
            columns=columns,
            column_types=column_types,
            rows=serialized,
            row_count=len(serialized),
            execution_ms=elapsed_ms,
            explain_plan=explain_plan,
        )
