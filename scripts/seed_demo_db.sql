-- =============================================================================
-- PG-NL2SQL Demo Database Schema & Seed Data
-- Demonstrates a multi-domain PostgreSQL database with 18 tables, 2 views,
-- bridge/junction tables for multi-hop FK graph expansion, and diverse
-- PostgreSQL types: ENUM, JSONB, text[]/int[], TIMESTAMPTZ, DATE, INTERVAL,
-- NUMERIC, UUID, INET, DATERANGE, BYTEA, and low-cardinality text columns.
-- =============================================================================

BEGIN;

-- Drop existing objects if re-seeding
DROP VIEW IF EXISTS v_monthly_revenue_summary CASCADE;
DROP VIEW IF EXISTS v_active_customer_mrr CASCADE;

DROP TABLE IF EXISTS audit_logs CASCADE;
DROP TABLE IF EXISTS support_tickets CASCADE;
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS invoices CASCADE;
DROP TABLE IF EXISTS order_items CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS inventory_lots CASCADE;
DROP TABLE IF EXISTS warehouses CASCADE;
DROP TABLE IF EXISTS product_tags CASCADE;
DROP TABLE IF EXISTS tags CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS subscription_addons CASCADE;
DROP TABLE IF EXISTS addons CASCADE;
DROP TABLE IF EXISTS subscriptions CASCADE;
DROP TABLE IF EXISTS plans CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS organizations CASCADE;
DROP TABLE IF EXISTS regions CASCADE;

DROP TYPE IF EXISTS payment_method CASCADE;
DROP TYPE IF EXISTS ticket_status CASCADE;
DROP TYPE IF EXISTS ticket_priority CASCADE;
DROP TYPE IF EXISTS order_status CASCADE;
DROP TYPE IF EXISTS account_status CASCADE;

-- =============================================================================
-- 1. Custom PostgreSQL ENUM Types
-- =============================================================================

CREATE TYPE account_status AS ENUM ('active', 'trial', 'suspended', 'churned');
CREATE TYPE order_status AS ENUM ('pending', 'confirmed', 'shipped', 'delivered', 'cancelled', 'refunded');
CREATE TYPE ticket_priority AS ENUM ('low', 'medium', 'high', 'urgent');
CREATE TYPE ticket_status AS ENUM ('open', 'in_progress', 'waiting_customer', 'resolved', 'closed');
CREATE TYPE payment_method AS ENUM ('card', 'ach', 'wire', 'invoice');

-- =============================================================================
-- 2. Tables (CRM, Billing, Catalog, Inventory, Commerce, Support, Audit)
-- =============================================================================

CREATE TABLE regions (
    region_id SERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE CHECK (code IN ('NA', 'EU', 'APAC', 'LATAM')),
    name TEXT NOT NULL,
    default_currency TEXT NOT NULL CHECK (default_currency IN ('USD', 'EUR', 'GBP', 'JPY')),
    primary_timezone TEXT NOT NULL,
    tax_rate NUMERIC(5, 4) NOT NULL DEFAULT 0.0000
);
COMMENT ON TABLE regions IS 'Geographic sales regions with default currency, timezone, and baseline tax rate.';
COMMENT ON COLUMN regions.code IS 'Short region code: NA, EU, APAC, or LATAM.';

CREATE TABLE organizations (
    org_id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    region_id INT NOT NULL REFERENCES regions(region_id),
    status account_status NOT NULL DEFAULT 'active',
    industry TEXT NOT NULL,
    employee_count INT CHECK (employee_count > 0),
    annual_contract_value NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    founded_date DATE,
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);
COMMENT ON TABLE organizations IS 'B2B customer organizations with account status, ACV, and JSONB workspace settings.';
COMMENT ON COLUMN organizations.settings IS 'JSONB configuration including tier, sso_enabled, compliance flags, and billing contacts.';

CREATE TABLE customers (
    customer_id UUID PRIMARY KEY,
    org_id UUID NOT NULL REFERENCES organizations(org_id),
    region_id INT NOT NULL REFERENCES regions(region_id),
    email TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    status account_status NOT NULL DEFAULT 'active',
    acquisition_channel TEXT NOT NULL CHECK (acquisition_channel IN ('organic', 'partner', 'outbound', 'referral', 'event')),
    tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    profile_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_login_ip INET,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);
COMMENT ON TABLE customers IS 'Individual customer contacts belonging to organizations, with array tags, JSONB profile metadata, and login IP.';
COMMENT ON COLUMN customers.profile_meta IS 'Nested JSONB containing tier, role, preferences (newsletter, locale), and onboarding score.';
COMMENT ON COLUMN customers.tags IS 'PostgreSQL text[] array of customer segment labels (e.g., vip, beta_tester, champion).';

CREATE TABLE plans (
    plan_id SERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    monthly_price_usd NUMERIC(10, 2) NOT NULL CHECK (monthly_price_usd >= 0),
    trial_duration INTERVAL NOT NULL DEFAULT INTERVAL '14 days',
    included_features TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    quotas JSONB NOT NULL DEFAULT '{}'::jsonb
);
COMMENT ON TABLE plans IS 'SaaS subscription plans with monthly pricing, trial interval, feature arrays, and JSONB quotas.';

CREATE TABLE subscriptions (
    subscription_id UUID PRIMARY KEY,
    org_id UUID NOT NULL REFERENCES organizations(org_id),
    plan_id INT NOT NULL REFERENCES plans(plan_id),
    status account_status NOT NULL DEFAULT 'active',
    seats INT NOT NULL CHECK (seats > 0),
    mrr_usd NUMERIC(12, 2) NOT NULL CHECK (mrr_usd >= 0),
    billing_period DATERANGE NOT NULL,
    auto_renew BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE subscriptions IS 'Organization subscriptions linking organizations to plans with DATERANGE billing windows and MRR.';
COMMENT ON COLUMN subscriptions.billing_period IS 'PostgreSQL daterange representing the active contract period [start, end).';

CREATE TABLE addons (
    addon_id SERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    monthly_price_usd NUMERIC(10, 2) NOT NULL CHECK (monthly_price_usd >= 0),
    compatible_tiers TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[]
);
COMMENT ON TABLE addons IS 'Optional paid add-on modules attachable to subscriptions.';

CREATE TABLE subscription_addons (
    subscription_id UUID NOT NULL REFERENCES subscriptions(subscription_id),
    addon_id INT NOT NULL REFERENCES addons(addon_id),
    quantity INT NOT NULL DEFAULT 1 CHECK (quantity > 0),
    activated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (subscription_id, addon_id)
);
COMMENT ON TABLE subscription_addons IS 'Junction table mapping active add-ons onto organization subscriptions.';

CREATE TABLE products (
    product_id UUID PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('hardware', 'license', 'service', 'accessory')),
    unit_price_usd NUMERIC(10, 2) NOT NULL CHECK (unit_price_usd >= 0),
    compatible_plan_ids INT[] NOT NULL DEFAULT ARRAY[]::INT[],
    specs JSONB NOT NULL DEFAULT '{}'::jsonb,
    firmware_hash BYTEA,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE products IS 'Sellable products (hardware, licenses, services, accessories) with JSONB specs, int[] compatible plans, and bytea firmware checksum.';

CREATE TABLE tags (
    tag_id SERIAL PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    tag_group TEXT NOT NULL CHECK (tag_group IN ('compliance', 'use_case', 'deployment', 'promo'))
);
COMMENT ON TABLE tags IS 'Normalized taxonomy tags for products.';

CREATE TABLE product_tags (
    product_id UUID NOT NULL REFERENCES products(product_id),
    tag_id INT NOT NULL REFERENCES tags(tag_id),
    PRIMARY KEY (product_id, tag_id)
);
COMMENT ON TABLE product_tags IS 'Junction table associating products with normalized taxonomy tags.';

CREATE TABLE warehouses (
    warehouse_id SERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    region_id INT NOT NULL REFERENCES regions(region_id),
    capabilities JSONB NOT NULL DEFAULT '{}'::jsonb
);
COMMENT ON TABLE warehouses IS 'Fulfillment warehouses per region with JSONB operational capabilities.';

CREATE TABLE inventory_lots (
    lot_id UUID PRIMARY KEY,
    warehouse_id INT NOT NULL REFERENCES warehouses(warehouse_id),
    product_id UUID NOT NULL REFERENCES products(product_id),
    qty_on_hand INT NOT NULL CHECK (qty_on_hand >= 0),
    unit_cost_usd NUMERIC(10, 2) NOT NULL CHECK (unit_cost_usd >= 0),
    valid_window DATERANGE NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE inventory_lots IS 'Stock lots per warehouse and product with DATERANGE shelf/warranty windows.';

CREATE TABLE orders (
    order_id UUID PRIMARY KEY,
    order_number TEXT NOT NULL UNIQUE,
    customer_id UUID NOT NULL REFERENCES customers(customer_id),
    status order_status NOT NULL DEFAULT 'pending',
    currency TEXT NOT NULL DEFAULT 'USD' CHECK (currency IN ('USD', 'EUR', 'GBP')),
    subtotal_usd NUMERIC(12, 2) NOT NULL CHECK (subtotal_usd >= 0),
    tax_usd NUMERIC(12, 2) NOT NULL DEFAULT 0.00 CHECK (tax_usd >= 0),
    total_usd NUMERIC(12, 2) NOT NULL CHECK (total_usd >= 0),
    shipping_address JSONB NOT NULL DEFAULT '{}'::jsonb,
    ordered_at TIMESTAMPTZ NOT NULL,
    shipped_at TIMESTAMPTZ
);
COMMENT ON TABLE orders IS 'Customer purchase orders with enum status, NUMERIC monetary totals, and JSONB shipping address.';

CREATE TABLE order_items (
    order_item_id SERIAL PRIMARY KEY,
    order_id UUID NOT NULL REFERENCES orders(order_id),
    product_id UUID NOT NULL REFERENCES products(product_id),
    quantity INT NOT NULL CHECK (quantity > 0),
    unit_price_usd NUMERIC(10, 2) NOT NULL CHECK (unit_price_usd >= 0),
    discount_pct NUMERIC(5, 2) NOT NULL DEFAULT 0.00 CHECK (discount_pct >= 0 AND discount_pct <= 100)
);
COMMENT ON TABLE order_items IS 'Line items linking orders to products with quantity, unit price, and discount percentage.';

CREATE TABLE invoices (
    invoice_id UUID PRIMARY KEY,
    invoice_number TEXT NOT NULL UNIQUE,
    org_id UUID NOT NULL REFERENCES organizations(org_id),
    order_id UUID REFERENCES orders(order_id),
    currency TEXT NOT NULL DEFAULT 'USD' CHECK (currency IN ('USD', 'EUR', 'GBP')),
    amount_due NUMERIC(12, 2) NOT NULL CHECK (amount_due >= 0),
    amount_paid NUMERIC(12, 2) NOT NULL DEFAULT 0.00 CHECK (amount_paid >= 0),
    issued_date DATE NOT NULL,
    due_date DATE NOT NULL,
    pdf_checksum BYTEA
);
COMMENT ON TABLE invoices IS 'Billing invoices issued to organizations (optionally tied to an order) with BYTEA PDF checksum.';

CREATE TABLE payments (
    payment_id UUID PRIMARY KEY,
    invoice_id UUID NOT NULL REFERENCES invoices(invoice_id),
    method payment_method NOT NULL,
    amount NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    paid_at TIMESTAMPTZ NOT NULL,
    processor_meta JSONB NOT NULL DEFAULT '{}'::jsonb
);
COMMENT ON TABLE payments IS 'Payments settled against invoices with payment_method enum and JSONB processor metadata.';

CREATE TABLE support_tickets (
    ticket_id UUID PRIMARY KEY,
    ticket_number TEXT NOT NULL UNIQUE,
    customer_id UUID NOT NULL REFERENCES customers(customer_id),
    order_id UUID REFERENCES orders(order_id),
    priority ticket_priority NOT NULL,
    status ticket_status NOT NULL,
    subject TEXT NOT NULL,
    labels TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    sla_target INTERVAL NOT NULL,
    resolution_time INTERVAL,
    created_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ
);
COMMENT ON TABLE support_tickets IS 'Customer support tickets with priority/status enums, text[] labels, and PostgreSQL INTERVAL SLA/resolution times.';

CREATE TABLE audit_logs (
    log_id BIGSERIAL PRIMARY KEY,
    customer_id UUID REFERENCES customers(customer_id),
    action TEXT NOT NULL CHECK (action IN ('login', 'export_csv', 'api_key_rotate', 'plan_upgrade', 'permission_change', 'order_place')),
    client_ip INET NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE audit_logs IS 'Security and activity audit trail recording client_ip (INET) and JSONB event payloads.';

-- Useful indexes
CREATE INDEX idx_customers_org ON customers(org_id);
CREATE INDEX idx_customers_profile_meta ON customers USING GIN(profile_meta);
CREATE INDEX idx_customers_tags ON customers USING GIN(tags);
CREATE INDEX idx_orders_customer_ordered ON orders(customer_id, ordered_at DESC);
CREATE INDEX idx_order_items_order ON order_items(order_id);
CREATE INDEX idx_order_items_product ON order_items(product_id);
CREATE INDEX idx_subscriptions_org ON subscriptions(org_id);
CREATE INDEX idx_support_tickets_customer ON support_tickets(customer_id);
CREATE INDEX idx_audit_logs_occurred ON audit_logs(occurred_at DESC);

-- =============================================================================
-- 3. Views
-- =============================================================================

CREATE VIEW v_active_customer_mrr AS
SELECT
    o.org_id,
    o.name AS organization_name,
    r.code AS region_code,
    p.name AS plan_name,
    s.seats,
    s.mrr_usd,
    COALESCE(SUM(a.monthly_price_usd * sa.quantity), 0)::NUMERIC(12, 2) AS addons_mrr_usd,
    (s.mrr_usd + COALESCE(SUM(a.monthly_price_usd * sa.quantity), 0))::NUMERIC(12, 2) AS total_mrr_usd
FROM organizations o
JOIN regions r ON r.region_id = o.region_id
JOIN subscriptions s ON s.org_id = o.org_id AND s.status = 'active'
JOIN plans p ON p.plan_id = s.plan_id
LEFT JOIN subscription_addons sa ON sa.subscription_id = s.subscription_id
LEFT JOIN addons a ON a.addon_id = sa.addon_id
WHERE o.status = 'active' AND o.deleted_at IS NULL
GROUP BY o.org_id, o.name, r.code, p.name, s.seats, s.mrr_usd;
COMMENT ON VIEW v_active_customer_mrr IS 'Active organizations with their current base plan MRR, add-on MRR, and combined total MRR.';

CREATE VIEW v_monthly_revenue_summary AS
SELECT
    DATE_TRUNC('month', ord.ordered_at)::DATE AS revenue_month,
    r.code AS region_code,
    ord.currency,
    COUNT(*) AS order_count,
    SUM(ord.subtotal_usd)::NUMERIC(12, 2) AS gross_subtotal_usd,
    SUM(ord.total_usd)::NUMERIC(12, 2) AS total_revenue_usd
FROM orders ord
JOIN customers c ON c.customer_id = ord.customer_id
JOIN regions r ON r.region_id = c.region_id
WHERE ord.status NOT IN ('cancelled', 'refunded')
GROUP BY 1, 2, 3;
COMMENT ON VIEW v_monthly_revenue_summary IS 'Monthly order count and net revenue grouped by region and currency (excluding cancelled and refunded orders).';

-- =============================================================================
-- 4. Realistic Seed Data
-- =============================================================================

INSERT INTO regions (region_id, code, name, default_currency, primary_timezone, tax_rate) VALUES
    (1, 'NA', 'North America', 'USD', 'America/Los_Angeles', 0.0825),
    (2, 'EU', 'Europe', 'EUR', 'Europe/Berlin', 0.1900),
    (3, 'APAC', 'Asia-Pacific', 'JPY', 'Asia/Tokyo', 0.1000),
    (4, 'LATAM', 'Latin America', 'USD', 'America/Sao_Paulo', 0.1200);
SELECT setval('regions_region_id_seq', 4);

INSERT INTO organizations (org_id, name, region_id, status, industry, employee_count, annual_contract_value, founded_date, settings, created_at, deleted_at) VALUES
    ('10000000-0000-4000-8000-000000000001', 'Apex FinTech Systems', 1, 'active', 'fintech', 420, 144000.00, '2017-04-12',
     '{"tier": "enterprise", "sso_enabled": true, "compliance": ["SOC2", "PCI-DSS"], "billing_contact": {"email": "ap@apexfintech.io", "net_terms": 30}}'::jsonb,
     '2024-03-10 14:20:00+00', NULL),
    ('10000000-0000-4000-8000-000000000002', 'Nordic Cloud Logistics', 2, 'active', 'logistics', 850, 198000.00, '2014-09-01',
     '{"tier": "enterprise", "sso_enabled": true, "compliance": ["ISO27001", "GDPR"], "billing_contact": {"email": "finance@nordiccloud.eu", "net_terms": 45}}'::jsonb,
     '2024-05-18 09:00:00+00', NULL),
    ('10000000-0000-4000-8000-000000000003', 'Sakura Robotics Co', 3, 'active', 'manufacturing', 210, 72000.00, '2019-11-20',
     '{"tier": "growth", "sso_enabled": true, "compliance": ["ISO27001"], "billing_contact": {"email": "billing@sakurarobotics.jp", "net_terms": 30}}'::jsonb,
     '2025-01-12 03:45:00+00', NULL),
    ('10000000-0000-4000-8000-000000000004', 'Solstice Health AI', 1, 'active', 'healthcare', 135, 96000.00, '2020-06-15',
     '{"tier": "enterprise", "sso_enabled": true, "compliance": ["HIPAA", "SOC2"], "billing_contact": {"email": "payables@solsticehealth.ai", "net_terms": 30}}'::jsonb,
     '2025-04-02 17:10:00+00', NULL),
    ('10000000-0000-4000-8000-000000000005', 'Andes Commerce Group', 4, 'trial', 'retail', 65, 18000.00, '2022-02-08',
     '{"tier": "starter", "sso_enabled": false, "compliance": [], "billing_contact": {"email": "ops@andescommerce.co", "net_terms": 15}}'::jsonb,
     '2026-10-15 16:30:00+00', NULL),
    ('10000000-0000-4000-8000-000000000006', 'Helios Energy Analytics', 2, 'active', 'energy', 310, 120000.00, '2016-01-25',
     '{"tier": "growth", "sso_enabled": true, "compliance": ["GDPR"], "billing_contact": {"email": "invoices@heliosenergy.de", "net_terms": 30}}'::jsonb,
     '2024-08-22 11:05:00+00', NULL),
    ('10000000-0000-4000-8000-000000000007', 'Vanguard Retail Labs', 1, 'churned', 'retail', 95, 0.00, '2018-07-04',
     '{"tier": "starter", "sso_enabled": false, "compliance": [], "billing_contact": {"email": "ap@vanguardretail.com", "net_terms": 30}}'::jsonb,
     '2024-02-11 20:00:00+00', '2026-06-01 00:00:00+00'),
    ('10000000-0000-4000-8000-000000000008', 'Pacific Media Telemetry', 3, 'suspended', 'media', 180, 48000.00, '2019-03-19',
     '{"tier": "growth", "sso_enabled": false, "compliance": ["SOC2"], "billing_contact": {"email": "accounts@pacificmedia.sg", "net_terms": 30}}'::jsonb,
     '2025-07-14 07:15:00+00', NULL);

INSERT INTO customers (customer_id, org_id, region_id, email, full_name, status, acquisition_channel, tags, profile_meta, last_login_ip, created_at, deleted_at) VALUES
    ('20000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', 1, 'maya.chen@apexfintech.io', 'Maya Chen', 'active', 'outbound',
     ARRAY['vip', 'champion', 'beta_tester'],
     '{"tier": "enterprise", "role": "VP Data Engineering", "preferences": {"newsletter": true, "locale": "en-US"}, "onboarding": {"completed": true, "score": 96}}'::jsonb,
     '198.51.100.14', '2024-03-10 15:00:00+00', NULL),
    ('20000000-0000-4000-8000-000000000002', '10000000-0000-4000-8000-000000000001', 1, 'david.ross@apexfintech.io', 'David Ross', 'active', 'outbound',
     ARRAY['technical_buyer'],
     '{"tier": "enterprise", "role": "Staff Analytics Engineer", "preferences": {"newsletter": false, "locale": "en-US"}, "onboarding": {"completed": true, "score": 88}}'::jsonb,
     '198.51.100.22', '2024-04-02 18:10:00+00', NULL),
    ('20000000-0000-4000-8000-000000000003', '10000000-0000-4000-8000-000000000002', 2, 'astrid.lindqvist@nordiccloud.eu', 'Astrid Lindqvist', 'active', 'partner',
     ARRAY['vip', 'executive_sponsor'],
     '{"tier": "enterprise", "role": "Chief Data Officer", "preferences": {"newsletter": true, "locale": "en-GB"}, "onboarding": {"completed": true, "score": 94}}'::jsonb,
     '203.0.113.45', '2024-05-18 10:15:00+00', NULL),
    ('20000000-0000-4000-8000-000000000004', '10000000-0000-4000-8000-000000000003', 3, 'kenji.takahashi@sakurarobotics.jp', 'Kenji Takahashi', 'active', 'event',
     ARRAY['beta_tester', 'hardware_lead'],
     '{"tier": "growth", "role": "Head of Robotics Platform", "preferences": {"newsletter": true, "locale": "ja-JP"}, "onboarding": {"completed": true, "score": 91}}'::jsonb,
     '192.0.2.88', '2025-01-12 04:20:00+00', NULL),
    ('20000000-0000-4000-8000-000000000005', '10000000-0000-4000-8000-000000000004', 1, 'elena.rodriguez@solsticehealth.ai', 'Elena Rodriguez', 'active', 'referral',
     ARRAY['vip', 'hipaa_admin'],
     '{"tier": "enterprise", "role": "Director of Clinical Data", "preferences": {"newsletter": true, "locale": "en-US"}, "onboarding": {"completed": true, "score": 98}}'::jsonb,
     '198.51.100.77', '2025-04-02 18:00:00+00', NULL),
    ('20000000-0000-4000-8000-000000000006', '10000000-0000-4000-8000-000000000005', 4, 'mateo.silva@andescommerce.co', 'Mateo Silva', 'trial', 'organic',
     ARRAY['self_serve'],
     '{"tier": "starter", "role": "Data Lead", "preferences": {"newsletter": true, "locale": "es-CO"}, "onboarding": {"completed": false, "score": 54}}'::jsonb,
     '203.0.113.120', '2026-10-15 17:00:00+00', NULL),
    ('20000000-0000-4000-8000-000000000007', '10000000-0000-4000-8000-000000000006', 2, 'lukas.weber@heliosenergy.de', 'Lukas Weber', 'active', 'organic',
     ARRAY['champion', 'api_power_user'],
     '{"tier": "growth", "role": "Principal Data Architect", "preferences": {"newsletter": false, "locale": "de-DE"}, "onboarding": {"completed": true, "score": 89}}'::jsonb,
     '203.0.113.61', '2024-08-22 12:30:00+00', NULL),
    ('20000000-0000-4000-8000-000000000008', '10000000-0000-4000-8000-000000000007', 1, 'claire.bennett@vanguardretail.com', 'Claire Bennett', 'churned', 'organic',
     ARRAY['former_customer'],
     '{"tier": "starter", "role": "BI Manager", "preferences": {"newsletter": false, "locale": "en-US"}, "onboarding": {"completed": true, "score": 70}}'::jsonb,
     '198.51.100.201', '2024-02-11 21:00:00+00', '2026-06-01 00:00:00+00'),
    ('20000000-0000-4000-8000-000000000009', '10000000-0000-4000-8000-000000000008', 3, 'wei.tan@pacificmedia.sg', 'Wei Tan', 'suspended', 'partner',
     ARRAY['billing_hold'],
     '{"tier": "growth", "role": "Platform Lead", "preferences": {"newsletter": true, "locale": "en-SG"}, "onboarding": {"completed": true, "score": 82}}'::jsonb,
     '192.0.2.155', '2025-07-14 08:00:00+00', NULL),
    ('20000000-0000-4000-8000-000000000010', '10000000-0000-4000-8000-000000000002', 2, 'sofia.berg@nordiccloud.eu', 'Sofia Berg', 'active', 'partner',
     ARRAY['beta_tester', 'sql_analyst'],
     '{"tier": "enterprise", "role": "Senior BI Engineer", "preferences": {"newsletter": true, "locale": "sv-SE"}, "onboarding": {"completed": true, "score": 93}}'::jsonb,
     '203.0.113.49', '2025-02-19 11:40:00+00', NULL);

INSERT INTO plans (plan_id, code, name, monthly_price_usd, trial_duration, included_features, quotas) VALUES
    (1, 'starter', 'Starter', 1500.00, INTERVAL '14 days', ARRAY['sql_studio', 'csv_export', 'email_support'], '{"max_seats": 10, "max_queries_day": 500, "retention_days": 30}'::jsonb),
    (2, 'growth', 'Growth', 6000.00, INTERVAL '21 days', ARRAY['sql_studio', 'csv_export', 'sso', 'api_access', 'slack_support'], '{"max_seats": 50, "max_queries_day": 5000, "retention_days": 180}'::jsonb),
    (3, 'enterprise', 'Enterprise', 12000.00, INTERVAL '30 days', ARRAY['sql_studio', 'csv_export', 'sso', 'api_access', 'dedicated_vpc', 'audit_logs', '24x7_sla'], '{"max_seats": 500, "max_queries_day": 50000, "retention_days": 730}'::jsonb);
SELECT setval('plans_plan_id_seq', 3);

INSERT INTO subscriptions (subscription_id, org_id, plan_id, status, seats, mrr_usd, billing_period, auto_renew, created_at) VALUES
    ('30000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', 3, 'active', 120, 12000.00, '[2026-01-01,2027-01-01)', TRUE, '2024-03-10 14:25:00+00'),
    ('30000000-0000-4000-8000-000000000002', '10000000-0000-4000-8000-000000000002', 3, 'active', 240, 16500.00, '[2026-05-01,2027-05-01)', TRUE, '2024-05-18 09:10:00+00'),
    ('30000000-0000-4000-8000-000000000003', '10000000-0000-4000-8000-000000000003', 2, 'active', 45, 6000.00, '[2026-01-15,2027-01-15)', TRUE, '2025-01-12 03:55:00+00'),
    ('30000000-0000-4000-8000-000000000004', '10000000-0000-4000-8000-000000000004', 3, 'active', 80, 8000.00, '[2026-04-01,2027-04-01)', TRUE, '2025-04-02 17:15:00+00'),
    ('30000000-0000-4000-8000-000000000005', '10000000-0000-4000-8000-000000000005', 1, 'trial', 8, 1500.00, '[2026-10-15,2026-11-15)', FALSE, '2026-10-15 16:35:00+00'),
    ('30000000-0000-4000-8000-000000000006', '10000000-0000-4000-8000-000000000006', 2, 'active', 60, 10000.00, '[2026-08-01,2027-08-01)', TRUE, '2024-08-22 11:10:00+00'),
    ('30000000-0000-4000-8000-000000000007', '10000000-0000-4000-8000-000000000007', 1, 'churned', 10, 1500.00, '[2025-06-01,2026-06-01)', FALSE, '2024-02-11 20:05:00+00'),
    ('30000000-0000-4000-8000-000000000008', '10000000-0000-4000-8000-000000000008', 2, 'suspended', 30, 4000.00, '[2026-07-01,2027-07-01)', FALSE, '2025-07-14 07:20:00+00');

INSERT INTO addons (addon_id, code, name, monthly_price_usd, compatible_tiers) VALUES
    (1, 'hipaa_baa', 'HIPAA BAA & Encryption Pack', 1500.00, ARRAY['enterprise']),
    (2, 'eu_residency', 'EU Data Residency Pinning', 1200.00, ARRAY['growth', 'enterprise']),
    (3, 'ai_copilot_pro', 'Gemini Pro Unlimited Seats', 900.00, ARRAY['starter', 'growth', 'enterprise']),
    (4, 'dedicated_tam', 'Dedicated Technical Account Manager', 2500.00, ARRAY['enterprise']);
SELECT setval('addons_addon_id_seq', 4);

INSERT INTO subscription_addons (subscription_id, addon_id, quantity, activated_at) VALUES
    ('30000000-0000-4000-8000-000000000001', 3, 2, '2026-02-01 00:00:00+00'),
    ('30000000-0000-4000-8000-000000000001', 4, 1, '2026-01-01 00:00:00+00'),
    ('30000000-0000-4000-8000-000000000002', 2, 1, '2026-05-01 00:00:00+00'),
    ('30000000-0000-4000-8000-000000000002', 3, 3, '2026-05-15 00:00:00+00'),
    ('30000000-0000-4000-8000-000000000004', 1, 1, '2026-04-01 00:00:00+00'),
    ('30000000-0000-4000-8000-000000000006', 2, 1, '2026-08-01 00:00:00+00');

INSERT INTO products (product_id, sku, name, category, unit_price_usd, compatible_plan_ids, specs, firmware_hash, is_active) VALUES
    ('40000000-0000-4000-8000-000000000001', 'HW-EDGE-100', 'Edge Telemetry Gateway X1', 'hardware', 1299.00, ARRAY[2, 3],
     '{"weight_kg": 1.45, "power_watts": 18, "ports": ["10GbE", "USB-C", "RS485"], "certifications": ["CE", "FCC", "UL"]}'::jsonb,
     E'\\x9f86d081884c7d659a2feaa0c55ad015', TRUE),
    ('40000000-0000-4000-8000-000000000002', 'HW-SENS-200', 'Industrial Sensor Hub Pro', 'hardware', 549.50, ARRAY[1, 2, 3],
     '{"weight_kg": 0.42, "power_watts": 5, "ports": ["PoE", "CAN"], "certifications": ["CE", "IP67"]}'::jsonb,
     E'\\x4b227777d4dd1fc61c6f884f48641d02', TRUE),
    ('40000000-0000-4000-8000-000000000003', 'LIC-NL2SQL-ENT', 'PG-NL2SQL On-Prem Enterprise License', 'license', 8500.00, ARRAY[3],
     '{"deployment": "kubernetes", "max_clusters": 10, "certifications": ["SOC2", "HIPAA"]}'::jsonb,
     NULL, TRUE),
    ('40000000-0000-4000-8000-000000000004', 'SVC-SCHEMA-OPT', 'Schema Enrichment & Semantic Workshop', 'service', 3200.00, ARRAY[1, 2, 3],
     '{"delivery_days": 5, "remote_available": true, "deliverables": ["glossary.yaml", "eval_suite"]}'::jsonb,
     NULL, TRUE),
    ('40000000-0000-4000-8000-000000000005', 'ACC-RACK-KIT', '1U Dual-Rail Rack Mount Kit', 'accessory', 149.00, ARRAY[2, 3],
     '{"weight_kg": 2.1, "material": "anodized_steel", "certifications": ["RoHS"]}'::jsonb,
     NULL, TRUE),
    ('40000000-0000-4000-8000-000000000006', 'LIC-AUDIT-PACK', 'Real-Time Compliance Audit Streamer', 'license', 2400.00, ARRAY[2, 3],
     '{"deployment": "sidecar", "formats": ["CEF", "JSON", "Parquet"], "certifications": ["SOC2", "ISO27001"]}'::jsonb,
     E'\\xe3b0c44298fc1c149afbf4c8996fb924', TRUE);

INSERT INTO tags (tag_id, slug, label, tag_group) VALUES
    (1, 'soc2-ready', 'SOC 2 Ready', 'compliance'),
    (2, 'hipaa-ready', 'HIPAA Ready', 'compliance'),
    (3, 'edge-iot', 'Edge & IoT', 'use_case'),
    (4, 'on-prem', 'On-Premises', 'deployment'),
    (5, 'q4-bundle', 'Q4 Promo Bundle', 'promo');
SELECT setval('tags_tag_id_seq', 5);

INSERT INTO product_tags (product_id, tag_id) VALUES
    ('40000000-0000-4000-8000-000000000001', 3),
    ('40000000-0000-4000-8000-000000000001', 5),
    ('40000000-0000-4000-8000-000000000002', 3),
    ('40000000-0000-4000-8000-000000000003', 1),
    ('40000000-0000-4000-8000-000000000003', 2),
    ('40000000-0000-4000-8000-000000000003', 4),
    ('40000000-0000-4000-8000-000000000004', 5),
    ('40000000-0000-4000-8000-000000000006', 1),
    ('40000000-0000-4000-8000-000000000006', 4);

INSERT INTO warehouses (warehouse_id, code, name, region_id, capabilities) VALUES
    (1, 'US-WEST-1', 'Reno Fulfillment Center', 1, '{"cold_chain": false, "same_day_dispatch": true, "carriers": ["UPS", "FedEx"]}'::jsonb),
    (2, 'EU-CENTRAL-1', 'Frankfurt Logistics Hub', 2, '{"cold_chain": false, "same_day_dispatch": true, "carriers": ["DHL", "DPD"]}'::jsonb),
    (3, 'APAC-EAST-1', 'Tokyo Bay Depot', 3, '{"cold_chain": false, "same_day_dispatch": false, "carriers": ["Yamato", "DHL"]}'::jsonb);
SELECT setval('warehouses_warehouse_id_seq', 3);

INSERT INTO inventory_lots (lot_id, warehouse_id, product_id, qty_on_hand, unit_cost_usd, valid_window) VALUES
    ('50000000-0000-4000-8000-000000000001', 1, '40000000-0000-4000-8000-000000000001', 42, 620.00, '[2026-01-01,2028-01-01)'),
    ('50000000-0000-4000-8000-000000000002', 2, '40000000-0000-4000-8000-000000000001', 18, 635.00, '[2026-03-01,2028-03-01)'),
    ('50000000-0000-4000-8000-000000000003', 3, '40000000-0000-4000-8000-000000000002', 95, 240.00, '[2026-02-15,2028-02-15)'),
    ('50000000-0000-4000-8000-000000000004', 1, '40000000-0000-4000-8000-000000000005', 150, 52.00, '[2025-11-01,2029-11-01)');

INSERT INTO orders (order_id, order_number, customer_id, status, currency, subtotal_usd, tax_usd, total_usd, shipping_address, ordered_at, shipped_at) VALUES
    ('60000000-0000-4000-8000-000000000001', 'ORD-2026-1001', '20000000-0000-4000-8000-000000000001', 'delivered', 'USD', 11098.00, 915.59, 12013.59,
     '{"city": "San Francisco", "state": "CA", "country": "US", "postal_code": "94105"}'::jsonb,
     '2026-08-14 16:20:00+00', '2026-08-16 09:00:00+00'),
    ('60000000-0000-4000-8000-000000000002', 'ORD-2026-1002', '20000000-0000-4000-8000-000000000003', 'delivered', 'EUR', 14196.00, 2697.24, 16893.24,
     '{"city": "Stockholm", "state": "AB", "country": "SE", "postal_code": "11122"}'::jsonb,
     '2026-09-03 10:05:00+00', '2026-09-05 14:30:00+00'),
    ('60000000-0000-4000-8000-000000000003', 'ORD-2026-1003', '20000000-0000-4000-8000-000000000004', 'shipped', 'USD', 5346.50, 534.65, 5881.15,
     '{"city": "Tokyo", "state": "13", "country": "JP", "postal_code": "100-0001"}'::jsonb,
     '2026-10-22 05:40:00+00', '2026-10-24 08:15:00+00'),
    ('60000000-0000-4000-8000-000000000004', 'ORD-2026-1004', '20000000-0000-4000-8000-000000000005', 'delivered', 'USD', 11700.00, 965.25, 12665.25,
     '{"city": "Boston", "state": "MA", "country": "US", "postal_code": "02110"}'::jsonb,
     '2026-09-19 19:12:00+00', '2026-09-20 11:00:00+00'),
    ('60000000-0000-4000-8000-000000000005', 'ORD-2026-1005', '20000000-0000-4000-8000-000000000007', 'confirmed', 'EUR', 6297.00, 1196.43, 7493.43,
     '{"city": "Munich", "state": "BY", "country": "DE", "postal_code": "80331"}'::jsonb,
     '2026-11-04 13:50:00+00', NULL),
    ('60000000-0000-4000-8000-000000000006', 'ORD-2026-1006', '20000000-0000-4000-8000-000000000006', 'pending', 'USD', 3200.00, 384.00, 3584.00,
     '{"city": "Bogota", "state": "DC", "country": "CO", "postal_code": "110111"}'::jsonb,
     '2026-11-12 20:15:00+00', NULL),
    ('60000000-0000-4000-8000-000000000007', 'ORD-2026-1007', '20000000-0000-4000-8000-000000000008', 'refunded', 'USD', 1099.00, 90.67, 1189.67,
     '{"city": "Austin", "state": "TX", "country": "US", "postal_code": "78701"}'::jsonb,
     '2026-05-02 15:00:00+00', '2026-05-04 10:00:00+00'),
    ('60000000-0000-4000-8000-000000000008', 'ORD-2026-1008', '20000000-0000-4000-8000-000000000010', 'delivered', 'EUR', 10900.00, 2071.00, 12971.00,
     '{"city": "Stockholm", "state": "AB", "country": "SE", "postal_code": "11122"}'::jsonb,
     '2026-10-08 09:30:00+00', '2026-10-09 12:00:00+00'),
    ('60000000-0000-4000-8000-000000000009', 'ORD-2026-1009', '20000000-0000-4000-8000-000000000002', 'delivered', 'USD', 5196.00, 428.67, 5624.67,
     '{"city": "New York", "state": "NY", "country": "US", "postal_code": "10004"}'::jsonb,
     '2026-11-01 14:10:00+00', '2026-11-03 16:45:00+00'),
    ('60000000-0000-4000-8000-000000000010', 'ORD-2026-1010', '20000000-0000-4000-8000-000000000009', 'cancelled', 'USD', 2400.00, 240.00, 2640.00,
     '{"city": "Singapore", "state": "SG", "country": "SG", "postal_code": "048619"}'::jsonb,
     '2026-09-28 06:00:00+00', NULL);

INSERT INTO order_items (order_id, product_id, quantity, unit_price_usd, discount_pct) VALUES
    ('60000000-0000-4000-8000-000000000001', '40000000-0000-4000-8000-000000000003', 1, 8500.00, 0.00),
    ('60000000-0000-4000-8000-000000000001', '40000000-0000-4000-8000-000000000001', 2, 1299.00, 0.00),
    ('60000000-0000-4000-8000-000000000002', '40000000-0000-4000-8000-000000000003', 1, 8500.00, 0.00),
    ('60000000-0000-4000-8000-000000000002', '40000000-0000-4000-8000-000000000001', 4, 1299.00, 0.00),
    ('60000000-0000-4000-8000-000000000002', '40000000-0000-4000-8000-000000000005', 4, 149.00, 16.11),
    ('60000000-0000-4000-8000-000000000003', '40000000-0000-4000-8000-000000000001', 2, 1299.00, 0.00),
    ('60000000-0000-4000-8000-000000000003', '40000000-0000-4000-8000-000000000002', 5, 549.50, 0.00),
    ('60000000-0000-4000-8000-000000000004', '40000000-0000-4000-8000-000000000003', 1, 8500.00, 0.00),
    ('60000000-0000-4000-8000-000000000004', '40000000-0000-4000-8000-000000000004', 1, 3200.00, 0.00),
    ('60000000-0000-4000-8000-000000000005', '40000000-0000-4000-8000-000000000001', 3, 1299.00, 0.00),
    ('60000000-0000-4000-8000-000000000005', '40000000-0000-4000-8000-000000000006', 1, 2400.00, 0.00),
    ('60000000-0000-4000-8000-000000000006', '40000000-0000-4000-8000-000000000004', 1, 3200.00, 0.00),
    ('60000000-0000-4000-8000-000000000007', '40000000-0000-4000-8000-000000000002', 2, 549.50, 0.00),
    ('60000000-0000-4000-8000-000000000008', '40000000-0000-4000-8000-000000000003', 1, 8500.00, 0.00),
    ('60000000-0000-4000-8000-000000000008', '40000000-0000-4000-8000-000000000006', 1, 2400.00, 0.00),
    ('60000000-0000-4000-8000-000000000009', '40000000-0000-4000-8000-000000000001', 4, 1299.00, 0.00),
    ('60000000-0000-4000-8000-000000000010', '40000000-0000-4000-8000-000000000006', 1, 2400.00, 0.00);

INSERT INTO invoices (invoice_id, invoice_number, org_id, order_id, currency, amount_due, amount_paid, issued_date, due_date, pdf_checksum) VALUES
    ('70000000-0000-4000-8000-000000000001', 'INV-2026-5001', '10000000-0000-4000-8000-000000000001', '60000000-0000-4000-8000-000000000001', 'USD', 12013.59, 12013.59, '2026-08-14', '2026-09-13', E'\\xdeadbeef01020304'),
    ('70000000-0000-4000-8000-000000000002', 'INV-2026-5002', '10000000-0000-4000-8000-000000000002', '60000000-0000-4000-8000-000000000002', 'EUR', 16893.24, 16893.24, '2026-09-03', '2026-10-18', E'\\xdeadbeef05060708'),
    ('70000000-0000-4000-8000-000000000003', 'INV-2026-5003', '10000000-0000-4000-8000-000000000003', '60000000-0000-4000-8000-000000000003', 'USD', 5881.15, 5881.15, '2026-10-22', '2026-11-21', E'\\xdeadbeef090a0b0c'),
    ('70000000-0000-4000-8000-000000000004', 'INV-2026-5004', '10000000-0000-4000-8000-000000000004', '60000000-0000-4000-8000-000000000004', 'USD', 12665.25, 12665.25, '2026-09-19', '2026-10-19', E'\\xdeadbeef0d0e0f10'),
    ('70000000-0000-4000-8000-000000000005', 'INV-2026-5005', '10000000-0000-4000-8000-000000000006', '60000000-0000-4000-8000-000000000005', 'EUR', 7493.43, 0.00, '2026-11-04', '2026-12-04', E'\\xdeadbeef11121314'),
    ('70000000-0000-4000-8000-000000000006', 'INV-2026-5006', '10000000-0000-4000-8000-000000000002', '60000000-0000-4000-8000-000000000008', 'EUR', 12971.00, 6500.00, '2026-10-08', '2026-11-22', E'\\xdeadbeef15161718'),
    ('70000000-0000-4000-8000-000000000007', 'INV-2026-5007', '10000000-0000-4000-8000-000000000008', NULL, 'USD', 12000.00, 0.00, '2026-08-01', '2026-09-01', E'\\xdeadbeef191a1b1c');

INSERT INTO payments (payment_id, invoice_id, method, amount, paid_at, processor_meta) VALUES
    ('80000000-0000-4000-8000-000000000001', '70000000-0000-4000-8000-000000000001', 'ach', 12013.59, '2026-08-29 14:00:00+00', '{"provider": "stripe", "bank": "SVB", "fee_usd": 5.00}'::jsonb),
    ('80000000-0000-4000-8000-000000000002', '70000000-0000-4000-8000-000000000002', 'wire', 16893.24, '2026-10-02 09:30:00+00', '{"provider": "sepa", "reference": "SEPA-99281", "fee_usd": 12.00}'::jsonb),
    ('80000000-0000-4000-8000-000000000003', '70000000-0000-4000-8000-000000000003', 'card', 5881.15, '2026-10-23 03:12:00+00', '{"provider": "stripe", "brand": "visa", "last4": "4242", "fee_usd": 170.55}'::jsonb),
    ('80000000-0000-4000-8000-000000000004', '70000000-0000-4000-8000-000000000004', 'ach', 12665.25, '2026-10-04 16:45:00+00', '{"provider": "stripe", "bank": "FirstRepublic", "fee_usd": 5.00}'::jsonb),
    ('80000000-0000-4000-8000-000000000005', '70000000-0000-4000-8000-000000000006', 'wire', 6500.00, '2026-11-01 11:20:00+00', '{"provider": "sepa", "reference": "SEPA-99812", "partial": true}'::jsonb);

INSERT INTO support_tickets (ticket_id, ticket_number, customer_id, order_id, priority, status, subject, labels, sla_target, resolution_time, created_at, resolved_at) VALUES
    ('90000000-0000-4000-8000-000000000001', 'SUP-4001', '20000000-0000-4000-8000-000000000001', '60000000-0000-4000-8000-000000000001', 'high', 'resolved',
     'JSONB query timeout on 50M row telemetry table', ARRAY['performance', 'postgres', 'jsonb'],
     INTERVAL '4 hours', INTERVAL '2 hours 45 minutes', '2026-09-10 15:00:00+00', '2026-09-10 17:45:00+00'),
    ('90000000-0000-4000-8000-000000000002', 'SUP-4002', '20000000-0000-4000-8000-000000000003', '60000000-0000-4000-8000-000000000002', 'urgent', 'open',
     'EU data residency pinning verification for Frankfurt cluster', ARRAY['security', 'gdpr', 'enterprise'],
     INTERVAL '2 hours', NULL, '2026-11-18 14:20:00+00', NULL),
    ('90000000-0000-4000-8000-000000000003', 'SUP-4003', '20000000-0000-4000-8000-000000000004', '60000000-0000-4000-8000-000000000003', 'medium', 'in_progress',
     'Edge Telemetry Gateway X1 firmware checksum mismatch', ARRAY['hardware', 'firmware'],
     INTERVAL '12 hours', NULL, '2026-11-17 06:30:00+00', NULL),
    ('90000000-0000-4000-8000-000000000004', 'SUP-4004', '20000000-0000-4000-8000-000000000005', NULL, 'high', 'waiting_customer',
     'HIPAA audit log export automation setup', ARRAY['security', 'hipaa', 'api'],
     INTERVAL '4 hours', NULL, '2026-11-15 19:00:00+00', NULL),
    ('90000000-0000-4000-8000-000000000005', 'SUP-4005', '20000000-0000-4000-8000-000000000007', '60000000-0000-4000-8000-000000000005', 'low', 'closed',
     'Requesting VAT invoice copy for Munich office', ARRAY['billing', 'eu'],
     INTERVAL '24 hours', INTERVAL '6 hours 10 minutes', '2026-11-05 09:00:00+00', '2026-11-05 15:10:00+00'),
    ('90000000-0000-4000-8000-000000000006', 'SUP-4006', '20000000-0000-4000-8000-000000000009', NULL, 'urgent', 'open',
     'Account suspended due to overdue invoice INV-2026-5007', ARRAY['billing', 'escalation'],
     INTERVAL '2 hours', NULL, '2026-11-16 04:10:00+00', NULL);

INSERT INTO audit_logs (customer_id, action, client_ip, payload, occurred_at) VALUES
    ('20000000-0000-4000-8000-000000000001', 'login', '198.51.100.14', '{"mfa": true, "method": "okta_sso"}'::jsonb, '2026-11-18 16:00:00+00'),
    ('20000000-0000-4000-8000-000000000001', 'export_csv', '198.51.100.14', '{"query_id": "q-991", "rows": 142}'::jsonb, '2026-11-18 16:12:00+00'),
    ('20000000-0000-4000-8000-000000000003', 'api_key_rotate', '203.0.113.45', '{"key_prefix": "pk_live_8a", "rotated_by": "astrid.lindqvist@nordiccloud.eu"}'::jsonb, '2026-11-17 09:45:00+00'),
    ('20000000-0000-4000-8000-000000000005', 'permission_change', '198.51.100.77', '{"target_role": "nl2sql_ro", "granted": ["SELECT"]}'::jsonb, '2026-11-16 20:30:00+00'),
    ('20000000-0000-4000-8000-000000000007', 'order_place', '203.0.113.61', '{"order_number": "ORD-2026-1005", "total_usd": 7493.43}'::jsonb, '2026-11-04 13:50:00+00'),
    ('20000000-0000-4000-8000-000000000010', 'login', '203.0.113.49', '{"mfa": true, "method": "okta_sso"}'::jsonb, '2026-11-19 08:15:00+00');

COMMIT;
