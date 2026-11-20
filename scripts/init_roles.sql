-- =============================================================================
-- Dedicated Read-Only Role for PG-NL2SQL (Layer 1 Database-Level Isolation)
-- =============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nl2sql_ro') THEN
        CREATE ROLE nl2sql_ro LOGIN PASSWORD 'nl2sql_ro_demo_pass';
    ELSE
        ALTER ROLE nl2sql_ro WITH LOGIN PASSWORD 'nl2sql_ro_demo_pass';
    END IF;
END
$$;

REVOKE ALL ON DATABASE nl2sql_demo FROM nl2sql_ro;
GRANT CONNECT ON DATABASE nl2sql_demo TO nl2sql_ro;

REVOKE ALL ON SCHEMA public FROM nl2sql_ro;
GRANT USAGE ON SCHEMA public TO nl2sql_ro;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM nl2sql_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO nl2sql_ro;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO nl2sql_ro;

-- Prevent read-only role from creating temporary tables or exceeding safe defaults
ALTER ROLE nl2sql_ro SET default_transaction_read_only = on;
ALTER ROLE nl2sql_ro SET statement_timeout = '10s';
