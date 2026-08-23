import pytest
from pydantic import ValidationError
from sqlalchemy import MetaData

from backend.app.db.grant_provisioning import (
    CUSTOMER_ALLOWED_COLUMNS,
    CUSTOMER_RESTRICTED_COLUMNS,
)
from backend.app.services.schema_catalog import (
    CatalogConstructionError,
    build_schema_catalog,
)


def test_catalog_contains_expected_tables_and_columns() -> None:
    catalog = build_schema_catalog()

    assert catalog.catalog_version == "1"
    assert catalog.schema_name == "retail"
    assert tuple(table.name for table in catalog.tables) == (
        "categories",
        "customers",
        "order_items",
        "orders",
        "payments",
        "products",
        "regions",
        "sales_targets",
    )
    assert sum(len(table.columns) for table in catalog.tables) == 52


def test_catalog_excludes_restricted_customer_columns() -> None:
    catalog = build_schema_catalog()
    customer_table = next(table for table in catalog.tables if table.name == "customers")

    assert tuple(column.name for column in customer_table.columns) == CUSTOMER_ALLOWED_COLUMNS

    serialized_catalog = catalog.model_dump_json()

    for restricted_column in CUSTOMER_RESTRICTED_COLUMNS:
        assert restricted_column not in (serialized_catalog)


def test_catalog_documents_every_exposed_object() -> None:
    catalog = build_schema_catalog()

    assert all(table.description for table in catalog.tables)
    assert all(
        column.description and column.data_type
        for table in catalog.tables
        for column in table.columns
    )


def test_catalog_contains_primary_and_foreign_keys() -> None:
    catalog = build_schema_catalog()

    primary_key_count = sum(
        column.primary_key for table in catalog.tables for column in table.columns
    )
    references = [
        reference
        for table in catalog.tables
        for column in table.columns
        for reference in column.references
    ]

    assert primary_key_count == 8
    assert len(references) == 8
    assert {
        (
            reference.schema_name,
            reference.table_name,
            reference.column_name,
        )
        for reference in references
    } >= {
        ("retail", "customers", "id"),
        ("retail", "orders", "id"),
        ("retail", "products", "id"),
        ("retail", "regions", "id"),
    }


def test_catalog_models_are_immutable() -> None:
    catalog = build_schema_catalog()

    with pytest.raises(
        ValidationError,
        match="frozen",
    ):
        catalog.schema_name = "other"


def test_catalog_generation_is_deterministic() -> None:
    first = build_schema_catalog()
    second = build_schema_catalog()

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()


def test_incomplete_metadata_is_rejected() -> None:
    with pytest.raises(
        CatalogConstructionError,
        match="Catalog table set mismatch",
    ):
        build_schema_catalog(source_metadata=MetaData())
