"""Layer 2 static SQL safety gate using sqlglot AST inspection (PostgreSQL dialect)."""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


# Expressions forbidden anywhere in the AST (including inside CTEs or subqueries)
FORBIDDEN_EXPRESSIONS: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Merge,
    exp.Command,
    exp.Copy,
    exp.Grant,
    exp.Revoke,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Set,
    exp.Lock,
    exp.Into,
)

# Check optional expression classes that vary slightly across sqlglot versions
for _optional_cls in ("TruncateTable", "Truncate", "Execute", "Call", "Analyze", "Refresh"):
    _cls = getattr(exp, _optional_cls, None)
    if _cls is not None:
        FORBIDDEN_EXPRESSIONS = (*FORBIDDEN_EXPRESSIONS, _cls)


DENYLISTED_FUNCTIONS: frozenset[str] = frozenset(
    {
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "pg_switch_wal",
        "pg_create_restore_point",
        "dblink",
        "dblink_exec",
        "dblink_connect",
        "dblink_connect_u",
        "dblink_send_query",
        "lo_import",
        "lo_export",
        "lo_unlink",
        "lo_create",
        "lo_open",
        "lo_put",
        "query_to_xml",
        "query_to_xml_and_xmlschema",
        "set_config",
    }
)


@dataclass
class ValidationResult:
    is_valid: bool
    normalized_sql: str = ""
    applied_limit: int | None = None
    limit_modified: bool = False
    errors: list[str] = field(default_factory=list)


def _extract_function_name(node: exp.Expression) -> str:
    """Return lowercase bare function name for Func or Anonymous nodes."""
    if isinstance(node, exp.Anonymous):
        return (node.name or "").lower()
    if isinstance(node, exp.Func):
        # sql_name() returns canonical SQL function name
        return (node.sql_name() or "").lower()
    return ""


def _extract_limit_value(limit_node: exp.Limit | None) -> int | None:
    """Extract integer literal from a sqlglot Limit node, or None if non-integer/absent."""
    if limit_node is None or limit_node.expression is None:
        return None
    expr = limit_node.expression
    if isinstance(expr, exp.Literal) and expr.is_int:
        try:
            return int(expr.this)
        except ValueError:
            return None
    return None


def validate_and_normalize_sql(
    raw_sql: str,
    *,
    default_limit: int = 200,
    max_limit: int = 1000,
) -> ValidationResult:
    """
    Validate a PostgreSQL query with sqlglot AST analysis and enforce safe LIMIT bounds.

    Enforces:
      - Exactly one SQL statement (no stacked queries)
      - Root must be SELECT or UNION/INTERSECT/EXCEPT (including WITH ... SELECT)
      - No DML/DDL/locking clauses anywhere in AST (including data-modifying CTEs)
      - No denylisted hazardous PostgreSQL functions
      - Automatic LIMIT injection/capping
    """
    cleaned = (raw_sql or "").strip()
    if not cleaned:
        return ValidationResult(is_valid=False, errors=["SQL query is empty."])

    try:
        statements = [s for s in sqlglot.parse(cleaned, read="postgres") if s is not None]
    except ParseError as exc:
        return ValidationResult(is_valid=False, errors=[f"SQL parse error: {exc}"])

    if len(statements) == 0:
        return ValidationResult(is_valid=False, errors=["No SQL statement found."])

    if len(statements) > 1:
        return ValidationResult(
            is_valid=False,
            errors=[f"Multiple SQL statements are not allowed (found {len(statements)})."],
        )

    stmt = statements[0]
    errors: list[str] = []

    # 1. Root statement must be a read query (SELECT or set operation like UNION)
    if not isinstance(stmt, (exp.Select, exp.Union)):
        errors.append(
            f"Only read-only SELECT / WITH ... SELECT queries are allowed (got {stmt.__class__.__name__})."
        )

    # 2. Deep AST inspection for any forbidden DML/DDL/transaction/locking nodes
    for node in stmt.walk():
        if isinstance(node, FORBIDDEN_EXPRESSIONS):
            errors.append(
                f"Forbidden SQL operation detected in query AST: {node.__class__.__name__}."
            )
            break

    # Block SELECT ... FOR UPDATE / FOR SHARE locking clauses
    if isinstance(stmt, exp.Select) and stmt.args.get("locks"):
        errors.append("Row-level locking clauses (FOR UPDATE / FOR SHARE) are not allowed.")

    # 3. Check for denylisted hazardous PostgreSQL functions (bare or schema-qualified)
    for node in stmt.walk():
        if isinstance(node, (exp.Func, exp.Anonymous)):
            fn_name = _extract_function_name(node)
            if fn_name in DENYLISTED_FUNCTIONS:
                errors.append(f"Hazardous PostgreSQL function '{fn_name}' is blocked.")
        elif isinstance(node, exp.Dot):
            # Handles schema-qualified calls like pg_catalog.pg_read_file(...)
            rhs = node.expression
            if isinstance(rhs, (exp.Func, exp.Anonymous)):
                fn_name = _extract_function_name(rhs)
                if fn_name in DENYLISTED_FUNCTIONS:
                    errors.append(f"Hazardous PostgreSQL function '{fn_name}' is blocked.")

    if errors:
        # Deduplicate while preserving order
        deduped = list(dict.fromkeys(errors))
        return ValidationResult(is_valid=False, errors=deduped)

    # 4. Enforce LIMIT bounds (inject if missing, cap if > max_limit, preserve smaller valid limit)
    effective_default = min(max(1, default_limit), max_limit)
    limit_node = stmt.args.get("limit")
    existing_limit = _extract_limit_value(limit_node)
    limit_modified = False

    if existing_limit is None:
        stmt = stmt.limit(effective_default)
        applied_limit = effective_default
        limit_modified = True
    elif existing_limit > max_limit or existing_limit <= 0:
        stmt = stmt.limit(max_limit)
        applied_limit = max_limit
        limit_modified = True
    else:
        applied_limit = existing_limit

    normalized_sql = stmt.sql(dialect="postgres", pretty=True)
    return ValidationResult(
        is_valid=True,
        normalized_sql=normalized_sql,
        applied_limit=applied_limit,
        limit_modified=limit_modified,
        errors=[],
    )
