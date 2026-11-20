"""End-to-end NL2SQL pipeline orchestrator with EXPLAIN dry-run, auto-repair, and execution trace."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

from app.config import Settings
from app.db.connection import DatabaseManager
from app.nl2sql.catalog import SchemaCatalog
from app.nl2sql.generator import GeneratedSQL, SQLGenerator
from app.nl2sql.linker import SchemaLinker
from app.nl2sql.validator import validate_and_normalize_sql


@dataclass
class PipelineResponse:
    sql: str = ""
    explanation: str = ""
    assumptions: list[str] = field(default_factory=list)
    clarifying_question: str | None = None
    columns: list[str] = field(default_factory=list)
    column_types: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    execution_ms: float = 0.0
    error: str | None = None
    trace: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sql": self.sql,
            "explanation": self.explanation,
            "assumptions": self.assumptions,
            "clarifying_question": self.clarifying_question,
            "columns": self.columns,
            "column_types": self.column_types,
            "rows": self.rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "execution_ms": self.execution_ms,
            "error": self.error,
            "trace": self.trace,
        }


class NL2SQLPipeline:
    """Coordinates Linking -> FK Expansion -> Generation -> AST Safety Gate -> EXPLAIN/Execute -> Auto-Repair."""

    def __init__(
        self,
        *,
        settings: Settings,
        db: DatabaseManager,
        linker: SchemaLinker,
        generator: SQLGenerator,
    ) -> None:
        self.settings = settings
        self.db = db
        self.linker = linker
        self.generator = generator

    def run_query(
        self,
        *,
        prompt: str,
        catalog: SchemaCatalog,
        history: list[dict[str, str]] | None = None,
        max_rows: int | None = None,
    ) -> PipelineResponse:
        """Execute the full 6-stage NL2SQL pipeline with full observability trace."""
        pipeline_start = time.perf_counter()
        requested_limit = min(
            max(1, max_rows or self.settings.default_row_limit),
            self.settings.max_row_limit,
        )

        steps: list[dict[str, Any]] = []

        # Stage 1 + 2: Schema Linking & FK Graph Expansion
        t0 = time.perf_counter()
        link_result = self.linker.link(catalog, prompt, history)
        steps.append(
            {
                "stage": "schema_linking",
                "method": link_result.method,
                "seed_tables": link_result.seed_tables,
                "expanded_tables": link_result.expanded_tables,
                "added_bridge_tables": link_result.added_bridge_tables,
                "join_paths": link_result.join_paths,
                "reasoning": link_result.reasoning,
                "duration_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            }
        )

        # Stage 3: SQL Generation
        t1 = time.perf_counter()
        gen_result: GeneratedSQL = self.generator.generate(
            prompt=prompt,
            catalog=catalog,
            selected_tables=link_result.expanded_tables,
            history=history,
        )
        steps.append(
            {
                "stage": "sql_generation",
                "mode": self.generator.mode,
                "has_clarifying_question": bool(gen_result.clarifying_question),
                "duration_ms": round((time.perf_counter() - t1) * 1000.0, 2),
            }
        )

        # If model asks for clarification instead of guessing, return early
        if gen_result.clarifying_question and not gen_result.sql.strip():
            return PipelineResponse(
                clarifying_question=gen_result.clarifying_question,
                explanation=gen_result.explanation,
                assumptions=gen_result.assumptions,
                trace={
                    "mode": self.generator.mode,
                    "linked_tables": link_result.expanded_tables,
                    "bridge_tables": link_result.added_bridge_tables,
                    "repair_attempts": 0,
                    "total_ms": round((time.perf_counter() - pipeline_start) * 1000.0, 2),
                    "steps": steps,
                },
            )

        # Stage 4 & 5: Static AST Safety Gate + EXPLAIN Dry-Run + Read-Only Execution (with up to N retries)
        current_sql = gen_result.sql
        current_explanation = gen_result.explanation
        current_assumptions = list(gen_result.assumptions)
        repair_attempts = 0
        last_error: str | None = None

        for attempt in range(self.settings.max_repair_attempts + 1):
            # Layer 2: sqlglot AST validation & LIMIT enforcement
            val = validate_and_normalize_sql(
                current_sql,
                default_limit=requested_limit,
                max_limit=self.settings.max_row_limit,
            )
            if not val.is_valid:
                last_error = "; ".join(val.errors)
                steps.append(
                    {
                        "stage": "safety_gate",
                        "attempt": attempt + 1,
                        "status": "blocked",
                        "errors": val.errors,
                    }
                )
                # Do NOT auto-repair deliberate destructive operations — reject immediately
                if any(
                    kw in last_error
                    for kw in ("Forbidden SQL operation", "Hazardous PostgreSQL function", "Multiple SQL statements", "Only read-only")
                ):
                    return PipelineResponse(
                        sql=current_sql,
                        explanation=current_explanation,
                        assumptions=current_assumptions,
                        error=f"Safety gate blocked query: {last_error}",
                        trace={
                            "mode": self.generator.mode,
                            "linked_tables": link_result.expanded_tables,
                            "bridge_tables": link_result.added_bridge_tables,
                            "repair_attempts": repair_attempts,
                            "total_ms": round((time.perf_counter() - pipeline_start) * 1000.0, 2),
                            "steps": steps,
                        },
                    )
            else:
                steps.append(
                    {
                        "stage": "safety_gate",
                        "attempt": attempt + 1,
                        "status": "passed",
                        "applied_limit": val.applied_limit,
                        "limit_modified": val.limit_modified,
                    }
                )
                # Layer 3: EXPLAIN dry-run + Read-Only PostgreSQL execution
                try:
                    exec_result = self.db.execute_query(
                        val.normalized_sql,
                        run_explain=True,
                        statement_timeout_ms=self.settings.statement_timeout_ms,
                    )
                    plan_cost = None
                    if exec_result.explain_plan and "Plan" in exec_result.explain_plan:
                        plan_cost = exec_result.explain_plan["Plan"].get("Total Cost")

                    steps.append(
                        {
                            "stage": "postgres_execution",
                            "attempt": attempt + 1,
                            "status": "succeeded",
                            "execution_ms": exec_result.execution_ms,
                            "explain_total_cost": plan_cost,
                            "row_count": exec_result.row_count,
                        }
                    )

                    truncated = bool(
                        val.applied_limit is not None and exec_result.row_count >= val.applied_limit
                    )
                    return PipelineResponse(
                        sql=val.normalized_sql,
                        explanation=current_explanation,
                        assumptions=current_assumptions,
                        columns=exec_result.columns,
                        column_types=exec_result.column_types,
                        rows=exec_result.rows,
                        row_count=exec_result.row_count,
                        truncated=truncated,
                        execution_ms=exec_result.execution_ms,
                        trace={
                            "mode": self.generator.mode,
                            "linked_tables": link_result.expanded_tables,
                            "bridge_tables": link_result.added_bridge_tables,
                            "join_paths": link_result.join_paths,
                            "applied_limit": val.applied_limit,
                            "explain_total_cost": plan_cost,
                            "repair_attempts": repair_attempts,
                            "total_ms": round((time.perf_counter() - pipeline_start) * 1000.0, 2),
                            "steps": steps,
                        },
                    )
                except Exception as exc:
                    last_error = str(exc).strip()
                    steps.append(
                        {
                            "stage": "postgres_execution",
                            "attempt": attempt + 1,
                            "status": "error",
                            "error": last_error,
                        }
                    )

            # Trigger self-correction loop if attempts remain
            if attempt < self.settings.max_repair_attempts:
                repair_attempts += 1
                repaired = self.generator.repair(
                    prompt=prompt,
                    failed_sql=current_sql,
                    error_message=last_error or "Unknown SQL error",
                    catalog=catalog,
                    selected_tables=link_result.expanded_tables,
                )
                current_sql = repaired.sql or current_sql
                if repaired.explanation:
                    current_explanation = repaired.explanation
                steps.append(
                    {
                        "stage": "auto_repair",
                        "attempt": repair_attempts,
                        "status": "regenerated",
                    }
                )

        return PipelineResponse(
            sql=current_sql,
            explanation=current_explanation,
            assumptions=current_assumptions,
            error=f"Query failed after {repair_attempts} repair attempts: {last_error}",
            trace={
                "mode": self.generator.mode,
                "linked_tables": link_result.expanded_tables,
                "bridge_tables": link_result.added_bridge_tables,
                "repair_attempts": repair_attempts,
                "total_ms": round((time.perf_counter() - pipeline_start) * 1000.0, 2),
                "steps": steps,
            },
        )

    def execute_raw_sql(self, *, sql: str, max_rows: int | None = None) -> PipelineResponse:
        """Validate and execute user-edited SQL through the exact same safety gate and read-only executor."""
        t0 = time.perf_counter()
        requested_limit = min(
            max(1, max_rows or self.settings.default_row_limit),
            self.settings.max_row_limit,
        )
        val = validate_and_normalize_sql(
            sql,
            default_limit=requested_limit,
            max_limit=self.settings.max_row_limit,
        )
        if not val.is_valid:
            return PipelineResponse(
                sql=sql,
                error=f"Safety gate blocked query: {'; '.join(val.errors)}",
                trace={
                    "mode": "direct_sql",
                    "safety_gate": "blocked",
                    "errors": val.errors,
                    "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                },
            )

        try:
            exec_result = self.db.execute_query(
                val.normalized_sql,
                run_explain=True,
                statement_timeout_ms=self.settings.statement_timeout_ms,
            )
            plan_cost = None
            if exec_result.explain_plan and "Plan" in exec_result.explain_plan:
                plan_cost = exec_result.explain_plan["Plan"].get("Total Cost")

            truncated = bool(
                val.applied_limit is not None and exec_result.row_count >= val.applied_limit
            )
            return PipelineResponse(
                sql=val.normalized_sql,
                explanation="Executed user-edited SQL through AST safety gate and read-only PostgreSQL transaction.",
                columns=exec_result.columns,
                column_types=exec_result.column_types,
                rows=exec_result.rows,
                row_count=exec_result.row_count,
                truncated=truncated,
                execution_ms=exec_result.execution_ms,
                trace={
                    "mode": "direct_sql",
                    "safety_gate": "passed",
                    "applied_limit": val.applied_limit,
                    "limit_modified": val.limit_modified,
                    "explain_total_cost": plan_cost,
                    "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                },
            )
        except Exception as exc:
            return PipelineResponse(
                sql=val.normalized_sql,
                error=f"PostgreSQL execution error: {exc}",
                trace={
                    "mode": "direct_sql",
                    "safety_gate": "passed",
                    "execution_error": str(exc),
                    "total_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                },
            )
