from sqlalchemy import CheckConstraint, UniqueConstraint

from backend.app.db.base import metadata
from backend.app.db.models import (
    Category,
    Customer,
    Order,
    OrderItem,
    Payment,
    Product,
    Region,
    SalesTarget,
)

EXPECTED_TABLES = {
    "retail.categories",
    "retail.customers",
    "retail.order_items",
    "retail.orders",
    "retail.payments",
    "retail.products",
    "retail.regions",
    "retail.sales_targets",
}


def test_expected_retail_tables_are_declared() -> None:
    assert set(metadata.tables) == EXPECTED_TABLES


def test_all_models_use_retail_schema() -> None:
    model_tables = [
        Category.__table__,
        Customer.__table__,
        Order.__table__,
        OrderItem.__table__,
        Payment.__table__,
        Product.__table__,
        Region.__table__,
        SalesTarget.__table__,
    ]

    assert all(table.schema == "retail" for table in model_tables)


def test_foreign_keys_reference_expected_tables() -> None:
    references = {
        foreign_key.target_fullname
        for table in metadata.tables.values()
        for foreign_key in table.foreign_keys
    }

    assert references == {
        "retail.categories.id",
        "retail.customers.id",
        "retail.orders.id",
        "retail.products.id",
        "retail.regions.id",
    }


def test_customer_contains_explicitly_sensitive_columns() -> None:
    columns = set(Customer.__table__.columns.keys())

    assert {"email", "document_number"} <= columns


def test_query_performance_indexes_are_declared() -> None:
    indexes = {index.name for table in metadata.tables.values() for index in table.indexes}

    assert {
        "ix_customers_region_id",
        "ix_orders_customer_id",
        "ix_orders_region_id",
        "ix_orders_placed_at",
        "ix_order_items_order_id",
        "ix_order_items_product_id",
        "ix_payments_order_id",
        "ix_products_category_id",
        "ix_sales_targets_target_month",
    } <= indexes


def test_financial_and_quantity_checks_are_declared() -> None:
    check_names = {
        constraint.name
        for table in metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert {
        "ck_products_list_price_positive",
        "ck_order_items_quantity_positive",
        "ck_order_items_discount_not_above_gross",
        "ck_payments_amount_positive",
        "ck_sales_targets_revenue_target_positive",
    } <= check_names


def test_business_keys_have_unique_constraints() -> None:
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for table in metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert {
        ("code",),
        ("email",),
        ("sku",),
        ("order_number",),
        ("transaction_reference",),
        ("region_id", "target_month"),
    } <= unique_columns


def test_all_tables_have_documentation_comments() -> None:
    assert all(table.comment for table in metadata.tables.values())
