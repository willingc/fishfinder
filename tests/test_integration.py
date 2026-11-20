"""End-to-end integration tests against the live PostgreSQL demo database and FastAPI API."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.nl2sql.generator import GeneratedSQL, SQLGenerator
from app.nl2sql.pipeline import NL2SQLPipeline


def _make_client() -> TestClient:
    settings = Settings()
    app = create_app(settings)
    return TestClient(app)


def test_health_and_read_only_connection() -> None:
    with _make_client() as client:
        res = client.get("/api/health")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ok"
        assert body["database"]["connected"] is True
        assert body["database"]["user"] == "nl2sql_ro"
        assert body["database"]["transaction_read_only"] is True
        assert body["catalog_tables"] >= 18


def test_schema_introspection_profiles_complex_types() -> None:
    with _make_client() as client:
        res = client.get("/api/schema")
        assert res.status_code == 200
        schema = res.json()

        assert "account_status" in schema["enums"]
        assert set(schema["enums"]["account_status"]) == {"active", "trial", "suspended", "churned"}

        customers = schema["tables"]["customers"]
        cols = {c["name"]: c for c in customers["columns"]}

        # JSONB recursive key path inference
        assert "tier" in cols["profile_meta"]["jsonb_schema"]
        assert "onboarding.score" in cols["profile_meta"]["jsonb_schema"]
        # Array element profiling
        assert cols["tags"]["array_element_type"] == "text"
        assert "vip" in cols["tags"]["sample_values"]
        # Low-cardinality text profiling
        assert "outbound" in cols["acquisition_channel"]["distinct_values"]
        # Enum values attached
        assert "active" in cols["status"]["enum_values"]

        # Refresh endpoint works
        refresh = client.post("/api/schema/refresh")
        assert refresh.status_code == 200
        assert refresh.json()["table_count"] >= 20  # 18 tables + 2 views


def test_nl_query_endpoint_and_bridge_table_expansion() -> None:
    with _make_client() as client:
        res = client.post(
            "/api/query",
            json={"prompt": "Which products tagged as SOC 2 Ready generated the most net order revenue?"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["error"] is None
        assert data["row_count"] >= 1
        assert "product_tags" in data["trace"]["linked_tables"]
        assert "order_items" in data["trace"]["linked_tables"]
        assert data["trace"]["explain_total_cost"] is not None


def test_execute_endpoint_blocks_destructive_sql_and_runs_valid_sql() -> None:
    with _make_client() as client:
        blocked = client.post("/api/execute", json={"sql": "DELETE FROM customers"})
        assert blocked.status_code == 200
        assert blocked.json()["error"] is not None
        assert "Safety gate blocked" in blocked.json()["error"]

        allowed = client.post(
            "/api/execute",
            json={
                "sql": "SELECT organization_name, total_mrr_usd FROM v_active_customer_mrr ORDER BY total_mrr_usd DESC",
                "max_rows": 3,
            },
        )
        assert allowed.status_code == 200
        body = allowed.json()
        assert body["error"] is None
        assert body["row_count"] == 3
        assert body["rows"][0]["organization_name"] == "Nordic Cloud Logistics"


def test_auto_repair_loop_recovers_from_postgres_error() -> None:
    """Verify Layer 3 EXPLAIN/execution auto-repair feeds Postgres error back and succeeds."""

    class _FlakyGenerator(SQLGenerator):
        def __init__(self) -> None:
            super().__init__(gemini_api_key="")
            self.repair_called_with_error: str = ""

        def generate(self, **kwargs) -> GeneratedSQL:
            # Deliberately reference a nonexistent column to trigger PostgreSQL EXPLAIN failure
            return GeneratedSQL(
                sql="SELECT nonexistent_col FROM regions",
                explanation="Initial broken attempt",
            )

        def repair(self, *, prompt, failed_sql, error_message, catalog, selected_tables) -> GeneratedSQL:
            self.repair_called_with_error = error_message
            return GeneratedSQL(
                sql="SELECT code, name FROM regions ORDER BY code",
                explanation="Repaired column names using catalog.",
            )

    with _make_client() as client:
        ctx = client.app.state.ctx
        flaky = _FlakyGenerator()
        pipeline = NL2SQLPipeline(
            settings=ctx.settings,
            db=ctx.db,
            linker=ctx.linker,
            generator=flaky,
        )
        result = pipeline.run_query(prompt="List all regions", catalog=ctx.get_catalog())

        assert result.error is None
        assert result.row_count == 4
        assert result.trace["repair_attempts"] == 1
        assert "nonexistent_col" in flaky.repair_called_with_error
