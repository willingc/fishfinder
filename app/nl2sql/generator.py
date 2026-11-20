"""Stage 2 SQL Generator & Self-Correction Loop (Gemini Pro + local demo fallback)."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from pydantic import BaseModel, Field

from app.nl2sql.catalog import SchemaCatalog


class GeneratedSQL(BaseModel):
    """Structured SQL generation payload."""

    sql: str = Field(
        default="",
        description="Single valid read-only PostgreSQL SELECT or WITH ... SELECT statement.",
    )
    explanation: str = Field(
        default="",
        description="Plain-English explanation of what the query calculates.",
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Explicit assumptions made (e.g., active customers filter, timezone, currency).",
    )
    clarifying_question: str | None = Field(
        default=None,
        description="If the user request is genuinely ambiguous, ask a concise clarifying question and leave sql empty.",
    )


SYSTEM_PROMPT = """You are an expert PostgreSQL SQL engineer generating safe, accurate, read-only analytical queries.

Rules:
1. Generate ONLY a single PostgreSQL SELECT or WITH ... SELECT query. Never generate INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE, COPY, or multi-statement SQL.
2. Use ONLY tables, views, columns, and enum values present in the provided Focused Schema Context.
3. Complex PostgreSQL data types:
   - ENUM columns: match exact enum labels listed in ENUM(...).
   - JSONB columns: use ->> for text extraction, -> for nested objects, @> for containment, jsonb_array_elements_text() for JSON arrays, and cast numeric/int values explicitly (e.g. (profile_meta->'onboarding'->>'score')::INT).
   - Arrays (text[], int[]): use 'val' = ANY(col), col @> ARRAY['val'], or unnest(col).
   - DATERANGE: use col @> CURRENT_DATE or lower(col) / upper(col).
   - TIMESTAMPTZ / DATE / INTERVAL: prefer index-friendly range predicates (col >= NOW() - INTERVAL '90 days').
   - NUMERIC: round/cast cleanly to NUMERIC(12, 2) for monetary totals.
4. Apply Business Glossary definitions whenever the user uses defined business terms (e.g. "active customer", "net order revenue", "overdue invoice").
5. Include meaningful column aliases and sensible ORDER BY clauses.
6. If the request is genuinely ambiguous and cannot be safely answered without guessing, set clarifying_question and leave sql empty.
"""


class SQLGenerator:
    """Generates and repairs PostgreSQL SQL via Gemini Pro or built-in demo heuristics."""

    def __init__(self, *, gemini_api_key: str = "", model_name: str = "gemini-2.5-pro") -> None:
        self.gemini_api_key = gemini_api_key.strip()
        self.model_name = model_name
        self._client: Any | None = None

    @property
    def mode(self) -> str:
        return f"gemini:{self.model_name}" if self.gemini_api_key else "demo_heuristic"

    def _get_client(self) -> Any:
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.gemini_api_key)
        return self._client

    def generate(
        self,
        *,
        prompt: str,
        catalog: SchemaCatalog,
        selected_tables: list[str],
        history: list[dict[str, str]] | None = None,
    ) -> GeneratedSQL:
        """Generate structured SQL from a natural-language question and focused schema context."""
        if self.gemini_api_key:
            return self._generate_with_gemini(
                prompt=prompt,
                catalog=catalog,
                selected_tables=selected_tables,
                history=history,
            )
        return self._generate_demo_sql(prompt=prompt, catalog=catalog, history=history)

    def repair(
        self,
        *,
        prompt: str,
        failed_sql: str,
        error_message: str,
        catalog: SchemaCatalog,
        selected_tables: list[str],
    ) -> GeneratedSQL:
        """Ask Gemini (or fallback heuristic) to fix a query that failed validation or PostgreSQL EXPLAIN/execution."""
        if self.gemini_api_key:
            return self._repair_with_gemini(
                prompt=prompt,
                failed_sql=failed_sql,
                error_message=error_message,
                catalog=catalog,
                selected_tables=selected_tables,
            )
        # In demo mode, strip trailing semicolons or invalid casts as a basic repair attempt
        cleaned = failed_sql.strip().rstrip(";")
        return GeneratedSQL(
            sql=cleaned,
            explanation="Cleaned syntax after validation/execution feedback (demo mode).",
            assumptions=["Running in local Demo Mode without GEMINI_API_KEY."],
        )

    def _generate_with_gemini(
        self,
        *,
        prompt: str,
        catalog: SchemaCatalog,
        selected_tables: list[str],
        history: list[dict[str, str]] | None = None,
    ) -> GeneratedSQL:
        from google.genai import types

        client = self._get_client()
        focused_context = catalog.render_focused_context(selected_tables)
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        history_text = ""
        if history:
            lines = [f"{m.get('role', 'user').upper()}: {m.get('content', '')}" for m in history[-6:]]
            history_text = "Conversation History:\n" + "\n".join(lines) + "\n\n"

        user_prompt = (
            f"Current UTC Timestamp: {now_utc}\n"
            f"Database Timezone: {catalog.database_timezone}\n\n"
            f"{history_text}"
            f"User Request: {prompt}\n\n"
            f"Focused Schema Context:\n{focused_context}"
        )

        response = client.models.generate_content(
            model=self.model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=GeneratedSQL,
                temperature=0.1,
            ),
        )
        return GeneratedSQL.model_validate_json(response.text)

    def _repair_with_gemini(
        self,
        *,
        prompt: str,
        failed_sql: str,
        error_message: str,
        catalog: SchemaCatalog,
        selected_tables: list[str],
    ) -> GeneratedSQL:
        from google.genai import types

        client = self._get_client()
        focused_context = catalog.render_focused_context(selected_tables)
        repair_prompt = (
            f"Original User Request: {prompt}\n\n"
            f"Previous SQL Attempt:\n```sql\n{failed_sql}\n```\n\n"
            f"PostgreSQL / Validator Error:\n{error_message}\n\n"
            f"Please fix the SQL query so it executes cleanly against PostgreSQL using only the schema below.\n\n"
            f"Focused Schema Context:\n{focused_context}"
        )

        response = client.models.generate_content(
            model=self.model_name,
            contents=repair_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=GeneratedSQL,
                temperature=0.0,
            ),
        )
        return GeneratedSQL.model_validate_json(response.text)

    # -------------------------------------------------------------------------
    # Local Demo / Heuristic SQL Generator (runs against live PostgreSQL with
    # zero API key required, honoring follow-ups like region/limit/tier filters)
    # -------------------------------------------------------------------------
    def _generate_demo_sql(
        self,
        *,
        prompt: str,
        catalog: SchemaCatalog,
        history: list[dict[str, str]] | None = None,
    ) -> GeneratedSQL:
        raw_lower = prompt.lower().strip()
        if raw_lower in ("show data", "show me data", "data", "report", "metrics", "stats"):
            return GeneratedSQL(
                sql="",
                explanation="",
                assumptions=[],
                clarifying_question=(
                    "Your request is broad — which domain would you like to explore? "
                    "For example: active enterprise customers, monthly revenue by region, "
                    "top products by net revenue, active subscription MRR, overdue invoices, "
                    "or open urgent support tickets."
                ),
            )

        # Combine recent user context for follow-up refinements ("now filter to EU", "top 3")
        combined_lower = raw_lower
        if history and any(
            phrase in raw_lower
            for phrase in ("filter", "only", "now ", "just ", "limit", "top ", "break", "by region", "eu", "na", "apac", "latam")
        ):
            prev_user = " ".join(
                m.get("content", "") for m in history[-2:] if m.get("role") == "user"
            ).lower()
            combined_lower = f"{prev_user} {raw_lower}".strip()

        region_match = re.search(r"\b(na|eu|apac|latam)\b", combined_lower)
        region_code = region_match.group(1).upper() if region_match else None

        limit_match = re.search(r"\b(?:top|limit)\s+(\d{1,3})\b", combined_lower)
        limit_val = int(limit_match.group(1)) if limit_match else 200

        assumptions = ["Generated via local Demo Mode (set GEMINI_API_KEY in .env for Gemini 2.5 Pro)."]
        if region_code:
            assumptions.append(f"Filtered to region code '{region_code}'.")

        # 1. Overdue / unpaid invoices & payments
        if any(k in combined_lower for k in ("invoice", "overdue", "unpaid", "payment", "checksum")):
            where_clauses = ["1=1"]
            if "overdue" in combined_lower or "unpaid" in combined_lower:
                where_clauses.append("i.amount_paid < i.amount_due")
                assumptions.append("Included invoices with outstanding balance (amount_paid < amount_due).")
            if region_code:
                where_clauses.append(f"r.code = '{region_code}'")
            sql = f"""
            SELECT
                i.invoice_number,
                o.name AS organization_name,
                r.code AS region_code,
                i.currency,
                i.amount_due,
                i.amount_paid,
                (i.amount_due - i.amount_paid)::NUMERIC(12, 2) AS balance_due,
                i.issued_date,
                i.due_date,
                i.pdf_checksum,
                COALESCE(ARRAY_AGG(DISTINCT p.method::TEXT) FILTER (WHERE p.payment_id IS NOT NULL), ARRAY[]::TEXT[]) AS payment_methods
            FROM invoices i
            JOIN organizations o ON o.org_id = i.org_id
            JOIN regions r ON r.region_id = o.region_id
            LEFT JOIN payments p ON p.invoice_id = i.invoice_id
            WHERE {' AND '.join(where_clauses)}
            GROUP BY i.invoice_id, i.invoice_number, o.name, r.code, i.currency, i.amount_due, i.amount_paid, i.issued_date, i.due_date, i.pdf_checksum
            ORDER BY balance_due DESC, i.due_date ASC
            LIMIT {limit_val}
            """
            return GeneratedSQL(
                sql=sql.strip(),
                explanation="Lists invoices with organization, region, balance due, BYTEA PDF checksum, and payment methods used.",
                assumptions=assumptions,
            )

        # 2. Support tickets, SLA, intervals, array labels
        if any(k in combined_lower for k in ("ticket", "support", "sla", "urgent", "incident")):
            where_clauses = ["1=1"]
            if "open" in combined_lower or "unresolved" in combined_lower:
                where_clauses.append("st.status IN ('open', 'in_progress', 'waiting_customer')")
                assumptions.append("Filtered to active/unresolved ticket statuses.")
            if "urgent" in combined_lower or "high" in combined_lower:
                where_clauses.append("st.priority IN ('high', 'urgent')")
            if region_code:
                where_clauses.append(f"r.code = '{region_code}'")
            sql = f"""
            SELECT
                st.ticket_number,
                st.priority,
                st.status,
                st.subject,
                st.labels,
                st.sla_target,
                st.resolution_time,
                c.full_name AS customer_name,
                o.name AS organization_name,
                r.code AS region_code,
                st.created_at
            FROM support_tickets st
            JOIN customers c ON c.customer_id = st.customer_id
            JOIN organizations o ON o.org_id = c.org_id
            JOIN regions r ON r.region_id = c.region_id
            WHERE {' AND '.join(where_clauses)}
            ORDER BY st.priority DESC, st.created_at ASC
            LIMIT {limit_val}
            """
            return GeneratedSQL(
                sql=sql.strip(),
                explanation="Retrieves support tickets with enum priority/status, array labels, PostgreSQL INTERVAL SLA/resolution times, and customer details.",
                assumptions=assumptions,
            )

        # 3. Subscriptions, MRR, add-ons, billing_period daterange
        if any(k in combined_lower for k in ("mrr", "subscription", "addon", "add-on", "plan", "billing period", "daterange")):
            where_clauses = ["o.deleted_at IS NULL"]
            if "active" in combined_lower or "mrr" in combined_lower:
                where_clauses.append("o.status = 'active'")
                where_clauses.append("s.status = 'active'")
                assumptions.append("Applied glossary definition for active organization and active subscriptions.")
            if region_code:
                where_clauses.append(f"r.code = '{region_code}'")
            sql = f"""
            SELECT
                o.name AS organization_name,
                r.code AS region_code,
                p.name AS plan_name,
                s.seats,
                s.mrr_usd AS base_mrr_usd,
                COALESCE(SUM(a.monthly_price_usd * sa.quantity), 0)::NUMERIC(12, 2) AS addons_mrr_usd,
                (s.mrr_usd + COALESCE(SUM(a.monthly_price_usd * sa.quantity), 0))::NUMERIC(12, 2) AS total_mrr_usd,
                s.billing_period,
                COALESCE(ARRAY_AGG(a.name ORDER BY a.name) FILTER (WHERE a.addon_id IS NOT NULL), ARRAY[]::TEXT[]) AS active_addons
            FROM organizations o
            JOIN regions r ON r.region_id = o.region_id
            JOIN subscriptions s ON s.org_id = o.org_id
            JOIN plans p ON p.plan_id = s.plan_id
            LEFT JOIN subscription_addons sa ON sa.subscription_id = s.subscription_id
            LEFT JOIN addons a ON a.addon_id = sa.addon_id
            WHERE {' AND '.join(where_clauses)}
            GROUP BY o.org_id, o.name, r.code, p.name, s.seats, s.mrr_usd, s.billing_period
            ORDER BY total_mrr_usd DESC
            LIMIT {limit_val}
            """
            return GeneratedSQL(
                sql=sql.strip(),
                explanation="Calculates base MRR, add-on MRR via the subscription_addons bridge table, total MRR, and DATERANGE billing periods.",
                assumptions=assumptions,
            )

        # 4. Products, tags (multi-hop via product_tags & order_items), specs JSONB, firmware_hash
        if any(k in combined_lower for k in ("product", "sku", "hardware", "license", "soc2", "soc 2", "on-prem", "tag", "firmware")):
            where_clauses = ["ord.status NOT IN ('cancelled', 'refunded')"]
            assumptions.append("Applied glossary definition for net order revenue (excluded cancelled and refunded orders).")
            if "soc2" in combined_lower or "soc 2" in combined_lower:
                where_clauses.append("t.slug = 'soc2-ready'")
            elif "hipaa" in combined_lower:
                where_clauses.append("t.slug = 'hipaa-ready'")
            elif "on-prem" in combined_lower:
                where_clauses.append("t.slug = 'on-prem'")
            if region_code:
                where_clauses.append(f"r.code = '{region_code}'")
            sql = f"""
            SELECT
                p.sku,
                p.name AS product_name,
                p.category,
                p.unit_price_usd,
                p.compatible_plan_ids,
                p.specs,
                p.firmware_hash,
                COALESCE(ARRAY_AGG(DISTINCT t.label ORDER BY t.label) FILTER (WHERE t.tag_id IS NOT NULL), ARRAY[]::TEXT[]) AS product_tags,
                COALESCE(SUM(oi.quantity), 0) AS units_sold,
                COALESCE(SUM(oi.quantity * oi.unit_price_usd * (1 - oi.discount_pct / 100.0)), 0)::NUMERIC(12, 2) AS net_revenue_usd
            FROM products p
            LEFT JOIN product_tags pt ON pt.product_id = p.product_id
            LEFT JOIN tags t ON t.tag_id = pt.tag_id
            LEFT JOIN order_items oi ON oi.product_id = p.product_id
            LEFT JOIN orders ord ON ord.order_id = oi.order_id
            LEFT JOIN customers c ON c.customer_id = ord.customer_id
            LEFT JOIN regions r ON r.region_id = c.region_id
            WHERE {' AND '.join(where_clauses)}
            GROUP BY p.product_id, p.sku, p.name, p.category, p.unit_price_usd, p.compatible_plan_ids, p.specs, p.firmware_hash
            ORDER BY net_revenue_usd DESC
            LIMIT {limit_val}
            """
            return GeneratedSQL(
                sql=sql.strip(),
                explanation="Joins products across product_tags and order_items bridge tables to compute units sold and net revenue alongside JSONB specs, int[] plans, and BYTEA firmware hashes.",
                assumptions=assumptions,
            )

        # 5. Inventory & Warehouses
        if any(k in combined_lower for k in ("warehouse", "inventory", "stock", "lot")):
            where_clauses = ["1=1"]
            if region_code:
                where_clauses.append(f"r.code = '{region_code}'")
            sql = f"""
            SELECT
                w.code AS warehouse_code,
                w.name AS warehouse_name,
                r.code AS region_code,
                p.sku,
                p.name AS product_name,
                il.qty_on_hand,
                il.unit_cost_usd,
                (il.qty_on_hand * il.unit_cost_usd)::NUMERIC(12, 2) AS stock_value_usd,
                il.valid_window,
                w.capabilities
            FROM inventory_lots il
            JOIN warehouses w ON w.warehouse_id = il.warehouse_id
            JOIN regions r ON r.region_id = w.region_id
            JOIN products p ON p.product_id = il.product_id
            WHERE {' AND '.join(where_clauses)}
            ORDER BY stock_value_usd DESC
            LIMIT {limit_val}
            """
            return GeneratedSQL(
                sql=sql.strip(),
                explanation="Summarizes inventory lots across warehouses and products with DATERANGE validity windows and JSONB warehouse capabilities.",
                assumptions=assumptions,
            )

        # 6. Audit logs & security / IP
        if any(k in combined_lower for k in ("audit", "login", "ip", "security", "activity")):
            where_clauses = ["1=1"]
            if region_code:
                where_clauses.append(f"r.code = '{region_code}'")
            sql = f"""
            SELECT
                al.log_id,
                al.action,
                al.client_ip,
                c.full_name AS customer_name,
                o.name AS organization_name,
                r.code AS region_code,
                al.payload,
                al.occurred_at
            FROM audit_logs al
            LEFT JOIN customers c ON c.customer_id = al.customer_id
            LEFT JOIN organizations o ON o.org_id = c.org_id
            LEFT JOIN regions r ON r.region_id = c.region_id
            WHERE {' AND '.join(where_clauses)}
            ORDER BY al.occurred_at DESC
            LIMIT {limit_val}
            """
            return GeneratedSQL(
                sql=sql.strip(),
                explanation="Returns recent security and activity audit logs with INET client IP addresses and JSONB event payloads.",
                assumptions=assumptions,
            )

        # 7. Monthly revenue / orders breakdown by region
        if any(k in combined_lower for k in ("revenue", "order", "sales", "monthly", "region")):
            where_clauses = ["ord.status NOT IN ('cancelled', 'refunded')"]
            assumptions.append("Excluded cancelled and refunded orders per the net order revenue glossary term.")
            if region_code:
                where_clauses.append(f"r.code = '{region_code}'")
            if "month" in combined_lower or "breakdown" in combined_lower or "by region" in combined_lower:
                sql = f"""
                SELECT
                    DATE_TRUNC('month', ord.ordered_at)::DATE AS revenue_month,
                    r.code AS region_code,
                    r.name AS region_name,
                    ord.currency,
                    COUNT(*) AS order_count,
                    SUM(ord.subtotal_usd)::NUMERIC(12, 2) AS subtotal_usd,
                    SUM(ord.total_usd)::NUMERIC(12, 2) AS total_revenue_usd
                FROM orders ord
                JOIN customers c ON c.customer_id = ord.customer_id
                JOIN regions r ON r.region_id = c.region_id
                WHERE {' AND '.join(where_clauses)}
                GROUP BY 1, 2, 3, 4
                ORDER BY revenue_month DESC, total_revenue_usd DESC
                LIMIT {limit_val}
                """
                return GeneratedSQL(
                    sql=sql.strip(),
                    explanation="Aggregates monthly net order count and revenue by geographic region and currency.",
                    assumptions=assumptions,
                )

            sql = f"""
            SELECT
                ord.order_number,
                ord.status,
                c.full_name AS customer_name,
                o.name AS organization_name,
                r.code AS region_code,
                ord.currency,
                ord.total_usd,
                ord.shipping_address,
                ord.ordered_at
            FROM orders ord
            JOIN customers c ON c.customer_id = ord.customer_id
            JOIN organizations o ON o.org_id = c.org_id
            JOIN regions r ON r.region_id = c.region_id
            WHERE {' AND '.join(where_clauses)}
            ORDER BY ord.ordered_at DESC
            LIMIT {limit_val}
            """
            return GeneratedSQL(
                sql=sql.strip(),
                explanation="Lists net customer orders with organization, region, monetary totals, and JSONB shipping addresses.",
                assumptions=assumptions,
            )

        # 8. Default: Active customers with JSONB profile_meta, tags[], INET IP, org & region
        where_clauses = ["c.deleted_at IS NULL"]
        if "active" in combined_lower or ("churned" not in combined_lower and "suspended" not in combined_lower and "all" not in combined_lower):
            where_clauses.append("c.status = 'active'")
            assumptions.append("Applied glossary definition for active customer (status = 'active' AND deleted_at IS NULL).")
        if "enterprise" in combined_lower:
            where_clauses.append("c.profile_meta->>'tier' = 'enterprise'")
            assumptions.append("Filtered via JSONB path customers.profile_meta->>'tier' = 'enterprise'.")
        if "vip" in combined_lower:
            where_clauses.append("'vip' = ANY(c.tags)")
        if "beta" in combined_lower:
            where_clauses.append("'beta_tester' = ANY(c.tags)")
        if region_code:
            where_clauses.append(f"r.code = '{region_code}'")

        sql = f"""
        SELECT
            c.full_name,
            c.email,
            c.status,
            o.name AS organization_name,
            r.code AS region_code,
            c.profile_meta->>'tier' AS tier,
            (c.profile_meta->'onboarding'->>'score')::INT AS onboarding_score,
            c.tags,
            c.last_login_ip,
            c.profile_meta
        FROM customers c
        JOIN organizations o ON o.org_id = c.org_id
        JOIN regions r ON r.region_id = c.region_id
        WHERE {' AND '.join(where_clauses)}
        ORDER BY onboarding_score DESC NULLS LAST, c.full_name ASC
        LIMIT {limit_val}
        """
        return GeneratedSQL(
            sql=sql.strip(),
            explanation="Queries customer contacts with organization, region, JSONB tier & onboarding score, text[] tags, and INET last login IP.",
            assumptions=assumptions,
        )
