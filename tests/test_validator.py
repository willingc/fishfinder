"""Unit tests for the sqlglot PostgreSQL AST safety gate and LIMIT enforcer."""

import pytest

from app.nl2sql.validator import validate_and_normalize_sql


class TestSafetyBlocking:
    @pytest.mark.parametrize(
        "bad_sql",
        [
            "",
            "   ",
            "DROP TABLE customers",
            "DELETE FROM orders WHERE order_id = '60000000-0000-4000-8000-000000000001'",
            "INSERT INTO regions(code, name, default_currency, primary_timezone) VALUES ('X', 'X', 'USD', 'UTC')",
            "UPDATE customers SET status = 'churned'",
            "TRUNCATE TABLE audit_logs",
            "ALTER TABLE customers ADD COLUMN hacked TEXT",
            "CREATE TABLE evil (id INT)",
            "SELECT * FROM customers; DROP TABLE customers;",
            "SELECT 1; SELECT 2;",
            "WITH deleted AS (DELETE FROM customers RETURNING *) SELECT * FROM deleted",
            "WITH updated AS (UPDATE orders SET status = 'cancelled' RETURNING *) SELECT * FROM updated",
            "SELECT * INTO temp_copy FROM customers",
            "SELECT * FROM customers FOR UPDATE",
        ],
    )
    def test_blocks_destructive_and_non_readonly_queries(self, bad_sql: str) -> None:
        result = validate_and_normalize_sql(bad_sql)
        assert not result.is_valid, f"Expected query to be blocked: {bad_sql}"
        assert result.errors

    @pytest.mark.parametrize(
        "hazardous_sql",
        [
            "SELECT pg_read_file('/etc/passwd')",
            "SELECT pg_catalog.pg_read_file('/etc/passwd')",
            "SELECT pg_ls_dir('.')",
            "SELECT pg_sleep(5)",
            "SELECT dblink('dbname=postgres', 'SELECT 1')",
            "SELECT lo_import('/etc/passwd')",
            "SELECT set_config('log_statement', 'none', false)",
        ],
    )
    def test_blocks_hazardous_postgres_functions(self, hazardous_sql: str) -> None:
        result = validate_and_normalize_sql(hazardous_sql)
        assert not result.is_valid, f"Expected hazardous function to be blocked: {hazardous_sql}"
        assert any("blocked" in err.lower() for err in result.errors)


class TestLimitEnforcement:
    def test_injects_default_limit_when_omitted(self) -> None:
        result = validate_and_normalize_sql("SELECT full_name FROM customers", default_limit=150, max_limit=1000)
        assert result.is_valid
        assert result.applied_limit == 150
        assert result.limit_modified is True
        assert "LIMIT 150" in result.normalized_sql

    def test_preserves_existing_smaller_limit(self) -> None:
        result = validate_and_normalize_sql("SELECT full_name FROM customers LIMIT 25", default_limit=200, max_limit=1000)
        assert result.is_valid
        assert result.applied_limit == 25
        assert result.limit_modified is False
        assert "LIMIT 25" in result.normalized_sql

    def test_caps_excessive_limit_at_max_limit(self) -> None:
        result = validate_and_normalize_sql("SELECT full_name FROM customers LIMIT 50000", default_limit=200, max_limit=1000)
        assert result.is_valid
        assert result.applied_limit == 1000
        assert result.limit_modified is True
        assert "LIMIT 1000" in result.normalized_sql

    def test_allows_cte_select_with_jsonb_and_arrays(self) -> None:
        sql = """
        WITH enterprise_cust AS (
            SELECT customer_id, full_name, profile_meta->>'tier' AS tier
            FROM customers
            WHERE 'vip' = ANY(tags)
        )
        SELECT * FROM enterprise_cust ORDER BY full_name
        """
        result = validate_and_normalize_sql(sql, default_limit=200)
        assert result.is_valid
        assert result.applied_limit == 200
