from datetime import date
from decimal import Decimal

from backend.app.db.grant_provisioning import (
    CUSTOMER_RESTRICTED_COLUMNS,
)
from backend.app.db.seed_generation import (
    CATEGORIES,
    GeneratedDataset,
    SeedConfig,
    generate_dataset,
    order_date_weight,
)

SMALL_CONFIG = SeedConfig(
    customer_count=60,
    product_count=36,
    order_count=200,
)


def as_records(
    dataset: GeneratedDataset,
    table_name: str,
) -> list[dict[str, object]]:
    table = dataset.tables[table_name]

    return [dict(zip(table.columns, row, strict=True)) for row in table.rows]


def test_generation_is_deterministic() -> None:
    first = generate_dataset(SMALL_CONFIG)
    second = generate_dataset(SMALL_CONFIG)

    assert first.row_counts == second.row_counts
    assert first.tables == second.tables


def test_expected_row_counts_and_item_volume() -> None:
    dataset = generate_dataset(SMALL_CONFIG)
    counts = dataset.row_counts

    assert counts["regions"] == 5
    assert counts["categories"] == len(CATEGORIES)
    assert counts["customers"] == 60
    assert counts["products"] == 36
    assert counts["orders"] == 200
    assert counts["payments"] == 200
    assert 500 <= counts["order_items"] <= 700
    assert counts["sales_targets"] == 280


def test_generated_foreign_keys_are_consistent() -> None:
    dataset = generate_dataset(SMALL_CONFIG)

    region_ids = {record["id"] for record in as_records(dataset, "regions")}
    customer_ids = {record["id"] for record in as_records(dataset, "customers")}
    product_ids = {record["id"] for record in as_records(dataset, "products")}
    order_ids = {record["id"] for record in as_records(dataset, "orders")}

    assert {record["region_id"] for record in as_records(dataset, "customers")} <= region_ids

    assert {record["customer_id"] for record in as_records(dataset, "orders")} <= customer_ids

    assert {record["product_id"] for record in as_records(dataset, "order_items")} <= product_ids

    assert {record["order_id"] for record in as_records(dataset, "order_items")} <= order_ids

    assert {record["order_id"] for record in as_records(dataset, "payments")} <= order_ids


def test_financial_values_respect_database_constraints() -> None:
    dataset = generate_dataset(SMALL_CONFIG)

    for record in as_records(dataset, "products"):
        assert record["unit_cost"] >= Decimal("0")
        assert record["list_price"] > Decimal("0")

    for record in as_records(dataset, "order_items"):
        assert record["quantity"] > 0
        assert record["unit_price"] > Decimal("0")
        assert record["unit_cost"] >= Decimal("0")
        assert record["discount_amount"] >= Decimal("0")
        assert record["discount_amount"] <= (record["quantity"] * record["unit_price"])

    for record in as_records(dataset, "payments"):
        assert record["amount"] > Decimal("0")


def test_sensitive_customer_fields_are_synthetic() -> None:
    dataset = generate_dataset(SMALL_CONFIG)
    customers = as_records(dataset, "customers")

    assert set(CUSTOMER_RESTRICTED_COLUMNS) == {
        "email",
        "document_number",
    }
    assert all(str(customer["email"]).endswith("@example.test") for customer in customers)
    assert all(len(str(customer["document_number"])) == 11 for customer in customers)


def test_controlled_temporal_anomalies_are_encoded() -> None:
    normal_april = order_date_weight(date(2025, 4, 1))
    anomalous_may = order_date_weight(date(2025, 5, 1))
    october = order_date_weight(date(2024, 10, 1))
    black_friday_month = order_date_weight(date(2024, 11, 1))

    assert anomalous_may < normal_april
    assert black_friday_month > october


def test_category_trends_include_growth_and_decline() -> None:
    trends = {name: annual_trend for name, annual_trend, _ in CATEGORIES}

    assert trends["Electronics"] > 1
    assert trends["Grocery"] > 1
    assert trends["Books"] < 1
    assert trends["Garden"] < 1
