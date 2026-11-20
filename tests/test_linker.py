"""Unit tests for Stage 1 schema linking, Stage 2 FK graph bridge expansion, and focused context pruning."""

from app.nl2sql.catalog import ColumnInfo, ForeignKeyInfo, SchemaCatalog, TableInfo
from app.nl2sql.linker import expand_fk_graph, heuristic_link_tables


def _build_sample_catalog() -> SchemaCatalog:
    fks = [
        ForeignKeyInfo(
            constraint_name="fk_oi_order",
            source_table="order_items",
            source_column="order_id",
            target_table="orders",
            target_column="order_id",
        ),
        ForeignKeyInfo(
            constraint_name="fk_oi_product",
            source_table="order_items",
            source_column="product_id",
            target_table="products",
            target_column="product_id",
        ),
        ForeignKeyInfo(
            constraint_name="fk_pt_product",
            source_table="product_tags",
            source_column="product_id",
            target_table="products",
            target_column="product_id",
        ),
        ForeignKeyInfo(
            constraint_name="fk_pt_tag",
            source_table="product_tags",
            source_column="tag_id",
            target_table="tags",
            target_column="tag_id",
        ),
        ForeignKeyInfo(
            constraint_name="fk_orders_customer",
            source_table="orders",
            source_column="customer_id",
            target_table="customers",
            target_column="customer_id",
        ),
    ]

    tables = {
        "customers": TableInfo(
            name="customers",
            comment="Customer accounts with JSONB profile metadata.",
            columns=[
                ColumnInfo(name="customer_id", data_type="uuid", is_primary_key=True),
                ColumnInfo(name="email", data_type="text"),
                ColumnInfo(name="profile_meta", data_type="jsonb", jsonb_schema={"tier": "text"}),
            ],
        ),
        "orders": TableInfo(
            name="orders",
            comment="Purchase orders placed by customers.",
            columns=[
                ColumnInfo(name="order_id", data_type="uuid", is_primary_key=True),
                ColumnInfo(name="customer_id", data_type="uuid"),
                ColumnInfo(name="total_usd", data_type="numeric(12,2)"),
            ],
            foreign_keys=[fks[4]],
        ),
        "order_items": TableInfo(
            name="order_items",
            comment="Junction table linking orders and products.",
            columns=[
                ColumnInfo(name="order_item_id", data_type="integer", is_primary_key=True),
                ColumnInfo(name="order_id", data_type="uuid"),
                ColumnInfo(name="product_id", data_type="uuid"),
                ColumnInfo(name="quantity", data_type="integer"),
            ],
            foreign_keys=[fks[0], fks[1]],
        ),
        "products": TableInfo(
            name="products",
            comment="Sellable products.",
            columns=[
                ColumnInfo(name="product_id", data_type="uuid", is_primary_key=True),
                ColumnInfo(name="sku", data_type="text"),
                ColumnInfo(name="name", data_type="text"),
            ],
        ),
        "product_tags": TableInfo(
            name="product_tags",
            comment="Junction table mapping products to tags.",
            columns=[
                ColumnInfo(name="product_id", data_type="uuid", is_primary_key=True),
                ColumnInfo(name="tag_id", data_type="integer", is_primary_key=True),
            ],
            foreign_keys=[fks[2], fks[3]],
        ),
        "tags": TableInfo(
            name="tags",
            comment="Normalized taxonomy tags.",
            columns=[
                ColumnInfo(name="tag_id", data_type="integer", is_primary_key=True),
                ColumnInfo(name="slug", data_type="text"),
            ],
        ),
        "unrelated_audit": TableInfo(
            name="unrelated_audit",
            comment="Security audit table.",
            columns=[ColumnInfo(name="log_id", data_type="bigint", is_primary_key=True)],
        ),
    }

    return SchemaCatalog(tables=tables, foreign_keys=fks)


def test_fk_graph_expansion_discovers_bridge_tables() -> None:
    catalog = _build_sample_catalog()
    expanded, added_bridges, join_paths = expand_fk_graph(catalog, ["orders", "tags"])

    assert "order_items" in added_bridges
    assert "products" in added_bridges
    assert "product_tags" in added_bridges
    assert set(expanded) == {"orders", "tags", "order_items", "products", "product_tags"}
    assert any("order_items" in p and "product_tags" in p for p in join_paths)


def test_focused_context_prunes_unrelated_tables() -> None:
    catalog = _build_sample_catalog()
    focused = catalog.render_focused_context(["customers", "orders"])

    assert "### TABLE customers" in focused
    assert "### TABLE orders" in focused
    assert "JSONB_PATHS(tier: text)" in focused
    assert "unrelated_audit" not in focused


def test_heuristic_linker_matches_relevant_tables() -> None:
    catalog = _build_sample_catalog()
    seeds, _ = heuristic_link_tables(catalog, "Show top products by orders and tags")

    assert "products" in seeds
    assert "orders" in seeds
    assert "tags" in seeds
