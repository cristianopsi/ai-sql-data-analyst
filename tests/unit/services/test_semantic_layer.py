import pytest
from pydantic import ValidationError

from backend.app.schemas.catalog import (
    SchemaCatalog,
)
from backend.app.schemas.semantic import (
    SemanticLayer,
)
from backend.app.services.schema_catalog import (
    build_schema_catalog,
)
from backend.app.services.semantic_layer import (
    SemanticLayerConstructionError,
    build_semantic_layer,
)


@pytest.fixture(scope="module")
def semantic_layer() -> SemanticLayer:
    return build_semantic_layer()


def test_semantic_layer_has_expected_contract(
    semantic_layer: SemanticLayer,
) -> None:
    assert semantic_layer.semantic_version == "1"
    assert semantic_layer.catalog_version == "1"
    assert semantic_layer.schema_name == "retail"
    assert len(semantic_layer.dimensions) == 14
    assert len(semantic_layer.metrics) == 9
    assert len(semantic_layer.relationships) == 8
    assert len(semantic_layer.business_rules) == 5


def test_every_semantic_reference_exists_in_catalog(
    semantic_layer: SemanticLayer,
) -> None:
    catalog = build_schema_catalog()

    catalog_references = {
        (
            table.schema_name,
            table.name,
            column.name,
        )
        for table in catalog.tables
        for column in table.columns
    }

    semantic_references = {
        (
            reference.schema_name,
            reference.table_name,
            reference.column_name,
        )
        for reference in (
            *(dimension.source for dimension in semantic_layer.dimensions),
            *(metric.source for metric in semantic_layer.metrics),
            *(
                metric_filter.source
                for metric in semantic_layer.metrics
                for metric_filter in metric.filters
            ),
            *(relationship.from_column for relationship in semantic_layer.relationships),
            *(relationship.to_column for relationship in semantic_layer.relationships),
        )
    }

    assert semantic_references <= catalog_references


def test_restricted_customer_columns_are_never_referenced(
    semantic_layer: SemanticLayer,
) -> None:
    serialized = semantic_layer.model_dump_json()

    assert "document_number" not in serialized
    assert "email" not in serialized


def test_semantic_vocabularies_match_dataset_contract(
    semantic_layer: SemanticLayer,
) -> None:
    dimensions = {dimension.name: dimension for dimension in semantic_layer.dimensions}

    assert {value.value for value in dimensions["order_status"].values} == {
        "cancelled",
        "delivered",
        "paid",
        "pending",
        "shipped",
    }

    assert {value.value for value in dimensions["sales_channel"].values} == {
        "marketplace",
        "mobile",
        "store",
        "web",
    }

    assert {value.value for value in dimensions["payment_method"].values} == {
        "bank_slip",
        "credit_card",
        "debit_card",
        "pix",
    }


def test_metrics_encode_required_business_rules(
    semantic_layer: SemanticLayer,
) -> None:
    metrics = {metric.name: metric for metric in semantic_layer.metrics}

    approved_revenue = metrics["approved_revenue"]

    assert approved_revenue.aggregation == "sum"
    assert approved_revenue.unit == "brl"
    assert approved_revenue.source.table_name == ("payments")
    assert approved_revenue.source.column_name == ("amount")
    assert len(approved_revenue.filters) == 1
    assert approved_revenue.filters[0].source.column_name == "status"
    assert approved_revenue.filters[0].value == "approved"

    assert metrics["order_count"].aggregation == "count_distinct"

    assert metrics["units_sold"].aggregation == "sum"


def test_relationships_are_derived_from_catalog(
    semantic_layer: SemanticLayer,
) -> None:
    assert {
        (
            relationship.from_column.table_name,
            relationship.from_column.column_name,
            relationship.to_column.table_name,
            relationship.to_column.column_name,
        )
        for relationship in semantic_layer.relationships
    } == {
        (
            "customers",
            "region_id",
            "regions",
            "id",
        ),
        (
            "order_items",
            "order_id",
            "orders",
            "id",
        ),
        (
            "order_items",
            "product_id",
            "products",
            "id",
        ),
        (
            "orders",
            "customer_id",
            "customers",
            "id",
        ),
        (
            "orders",
            "region_id",
            "regions",
            "id",
        ),
        (
            "payments",
            "order_id",
            "orders",
            "id",
        ),
        (
            "products",
            "category_id",
            "categories",
            "id",
        ),
        (
            "sales_targets",
            "region_id",
            "regions",
            "id",
        ),
    }


def test_semantic_layer_is_immutable_and_deterministic(
    semantic_layer: SemanticLayer,
) -> None:
    rebuilt_layer = build_semantic_layer()

    assert rebuilt_layer == semantic_layer
    assert rebuilt_layer.model_dump_json() == semantic_layer.model_dump_json()

    with pytest.raises(
        ValidationError,
        match="frozen",
    ):
        semantic_layer.schema_name = "changed"


def test_missing_catalog_column_is_rejected() -> None:
    catalog = build_schema_catalog()

    orders = next(table for table in catalog.tables if table.name == "orders")

    changed_orders = orders.model_copy(
        update={"columns": tuple(column for column in orders.columns if column.name != "status")}
    )

    changed_catalog = SchemaCatalog(
        schema_name=catalog.schema_name,
        tables=tuple(
            (changed_orders if table.name == "orders" else table) for table in catalog.tables
        ),
    )

    with pytest.raises(
        SemanticLayerConstructionError,
        match=("missing catalog column: order_status -> retail.orders.status"),
    ):
        build_semantic_layer(changed_catalog)
