import pytest

from backend.app.db.grant_provisioning import (
    CUSTOMER_ALLOWED_COLUMNS,
    CUSTOMER_RESTRICTED_COLUMNS,
    RETAIL_TABLES_WITH_FULL_SELECT,
    RetailGrantPolicy,
)
from backend.app.db.models import Customer
from backend.app.db.role_provisioning import (
    RoleConfigurationError,
)


def test_policy_covers_all_retail_tables() -> None:
    policy = RetailGrantPolicy()

    tables = set(policy.full_select_tables) | {policy.column_restricted_table}

    assert tables == {
        "categories",
        "customers",
        "order_items",
        "orders",
        "payments",
        "products",
        "regions",
        "sales_targets",
    }


def test_customer_policy_covers_every_model_column() -> None:
    model_columns = set(Customer.__table__.columns.keys())
    policy_columns = set(CUSTOMER_ALLOWED_COLUMNS) | set(CUSTOMER_RESTRICTED_COLUMNS)

    assert policy_columns == model_columns


def test_sensitive_customer_columns_are_not_allowed() -> None:
    assert set(CUSTOMER_RESTRICTED_COLUMNS) == {
        "email",
        "document_number",
    }
    assert not (set(CUSTOMER_ALLOWED_COLUMNS) & set(CUSTOMER_RESTRICTED_COLUMNS))


def test_customers_do_not_receive_table_level_select() -> None:
    assert "customers" not in RETAIL_TABLES_WITH_FULL_SELECT


def test_policy_rejects_overlapping_customer_columns() -> None:
    with pytest.raises(
        RoleConfigurationError,
        match="columns overlap",
    ):
        RetailGrantPolicy(
            allowed_customer_columns=("id", "email"),
            restricted_customer_columns=("email",),
        )


def test_policy_rejects_full_select_on_customers() -> None:
    with pytest.raises(
        RoleConfigurationError,
        match="cannot receive table-level SELECT",
    ):
        RetailGrantPolicy(
            full_select_tables=(
                *RETAIL_TABLES_WITH_FULL_SELECT,
                "customers",
            )
        )


def test_policy_rejects_unsafe_identifier() -> None:
    with pytest.raises(
        RoleConfigurationError,
        match="Invalid PostgreSQL identifiers",
    ):
        RetailGrantPolicy(schema_name="retail; DROP SCHEMA retail")
