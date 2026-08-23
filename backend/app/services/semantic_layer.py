from collections.abc import Iterable

from backend.app.schemas.catalog import (
    SchemaCatalog,
)
from backend.app.schemas.semantic import (
    SemanticBusinessRule,
    SemanticColumnReference,
    SemanticDimension,
    SemanticFilter,
    SemanticLayer,
    SemanticMetric,
    SemanticRelationship,
    SemanticValue,
)
from backend.app.services.schema_catalog import (
    build_schema_catalog,
)

RETAIL_SCHEMA = "retail"


class SemanticLayerConstructionError(ValueError):
    """Raised when semantic definitions violate the safe catalog."""


def _reference(
    table_name: str,
    column_name: str,
) -> SemanticColumnReference:
    return SemanticColumnReference(
        schema_name=RETAIL_SCHEMA,
        table_name=table_name,
        column_name=column_name,
    )


ORDER_STATUS_VALUES = (
    SemanticValue(
        value="cancelled",
        label="Cancelled",
        synonyms=(
            "canceled",
            "cancelado",
            "cancelados",
        ),
    ),
    SemanticValue(
        value="delivered",
        label="Delivered",
        synonyms=(
            "completed",
            "entregue",
            "entregues",
            "concluído",
            "concluidos",
        ),
    ),
    SemanticValue(
        value="paid",
        label="Paid",
        synonyms=(
            "pago",
            "pagos",
        ),
    ),
    SemanticValue(
        value="pending",
        label="Pending",
        synonyms=(
            "pendente",
            "pendentes",
        ),
    ),
    SemanticValue(
        value="shipped",
        label="Shipped",
        synonyms=(
            "enviado",
            "enviados",
        ),
    ),
)

SALES_CHANNEL_VALUES = (
    SemanticValue(
        value="marketplace",
        label="Marketplace",
        synonyms=(
            "market place",
            "plataforma parceira",
        ),
    ),
    SemanticValue(
        value="mobile",
        label="Mobile",
        synonyms=(
            "app",
            "aplicativo",
            "celular",
        ),
    ),
    SemanticValue(
        value="store",
        label="Store",
        synonyms=(
            "physical store",
            "loja",
            "loja física",
        ),
    ),
    SemanticValue(
        value="web",
        label="Web",
        synonyms=(
            "website",
            "site",
            "online",
        ),
    ),
)

PAYMENT_STATUS_VALUES = (
    SemanticValue(
        value="approved",
        label="Approved",
        synonyms=(
            "aprovado",
            "aprovados",
            "successful",
        ),
    ),
    SemanticValue(
        value="failed",
        label="Failed",
        synonyms=(
            "falhou",
            "falha",
            "recusado",
        ),
    ),
    SemanticValue(
        value="pending",
        label="Pending",
        synonyms=(
            "pendente",
            "pendentes",
        ),
    ),
    SemanticValue(
        value="refunded",
        label="Refunded",
        synonyms=(
            "reembolsado",
            "estornado",
        ),
    ),
)

PAYMENT_METHOD_VALUES = (
    SemanticValue(
        value="bank_slip",
        label="Bank slip",
        synonyms=(
            "boleto",
            "boleto bancário",
        ),
    ),
    SemanticValue(
        value="credit_card",
        label="Credit card",
        synonyms=(
            "cartão de crédito",
            "cartao de credito",
        ),
    ),
    SemanticValue(
        value="debit_card",
        label="Debit card",
        synonyms=(
            "cartão de débito",
            "cartao de debito",
        ),
    ),
    SemanticValue(
        value="pix",
        label="Pix",
        synonyms=(
            "instant payment",
            "pagamento instantâneo",
        ),
    ),
)

CUSTOMER_SEGMENT_VALUES = (
    SemanticValue(
        value="consumer",
        label="Consumer",
        synonyms=(
            "consumidor",
            "pessoa física",
            "varejo",
        ),
    ),
    SemanticValue(
        value="enterprise",
        label="Enterprise",
        synonyms=(
            "corporate",
            "empresa",
            "grande empresa",
        ),
    ),
    SemanticValue(
        value="small_business",
        label="Small business",
        synonyms=(
            "small company",
            "pequena empresa",
            "pequenos negócios",
        ),
    ),
)

BOOLEAN_ACTIVE_VALUES = (
    SemanticValue(
        value=True,
        label="Active",
        synonyms=(
            "ativo",
            "ativos",
        ),
    ),
    SemanticValue(
        value=False,
        label="Inactive",
        synonyms=(
            "inativo",
            "inativos",
        ),
    ),
)

SEMANTIC_DIMENSIONS = (
    SemanticDimension(
        name="order_date",
        label="Order date",
        description="Calendar date when an order was placed.",
        source=_reference(
            "orders",
            "placed_at",
        ),
        kind="temporal",
        synonyms=(
            "purchase date",
            "data do pedido",
            "data da compra",
        ),
        time_granularities=(
            "day",
            "month",
            "quarter",
            "year",
        ),
    ),
    SemanticDimension(
        name="order_status",
        label="Order status",
        description="Current lifecycle status of an order.",
        source=_reference(
            "orders",
            "status",
        ),
        kind="categorical",
        synonyms=(
            "purchase status",
            "status do pedido",
        ),
        values=ORDER_STATUS_VALUES,
    ),
    SemanticDimension(
        name="sales_channel",
        label="Sales channel",
        description="Channel through which an order was placed.",
        source=_reference(
            "orders",
            "channel",
        ),
        kind="categorical",
        synonyms=(
            "order channel",
            "canal",
            "canal de venda",
        ),
        values=SALES_CHANNEL_VALUES,
    ),
    SemanticDimension(
        name="payment_date",
        label="Payment date",
        description="Date when an approved payment was settled.",
        source=_reference(
            "payments",
            "paid_at",
        ),
        kind="temporal",
        synonyms=(
            "settlement date",
            "data do pagamento",
        ),
        time_granularities=(
            "day",
            "month",
            "quarter",
            "year",
        ),
    ),
    SemanticDimension(
        name="payment_status",
        label="Payment status",
        description="Processing status of a payment.",
        source=_reference(
            "payments",
            "status",
        ),
        kind="categorical",
        synonyms=(
            "settlement status",
            "status do pagamento",
        ),
        values=PAYMENT_STATUS_VALUES,
    ),
    SemanticDimension(
        name="payment_method",
        label="Payment method",
        description="Method used to pay for an order.",
        source=_reference(
            "payments",
            "method",
        ),
        kind="categorical",
        synonyms=(
            "payment type",
            "forma de pagamento",
            "meio de pagamento",
        ),
        values=PAYMENT_METHOD_VALUES,
    ),
    SemanticDimension(
        name="customer_segment",
        label="Customer segment",
        description="Commercial segment assigned to a customer.",
        source=_reference(
            "customers",
            "segment",
        ),
        kind="categorical",
        synonyms=(
            "client segment",
            "segmento do cliente",
        ),
        values=CUSTOMER_SEGMENT_VALUES,
    ),
    SemanticDimension(
        name="customer_active",
        label="Active customer",
        description="Whether the customer account is active.",
        source=_reference(
            "customers",
            "active",
        ),
        kind="categorical",
        synonyms=(
            "customer activity",
            "cliente ativo",
        ),
        values=BOOLEAN_ACTIVE_VALUES,
    ),
    SemanticDimension(
        name="region",
        label="Region",
        description="Brazilian sales region attributed to the record.",
        source=_reference(
            "regions",
            "name",
        ),
        kind="categorical",
        synonyms=(
            "sales region",
            "região",
            "região de vendas",
        ),
    ),
    SemanticDimension(
        name="region_code",
        label="Region code",
        description="Stable business code for a sales region.",
        source=_reference(
            "regions",
            "code",
        ),
        kind="identifier",
        synonyms=(
            "regional code",
            "código da região",
        ),
    ),
    SemanticDimension(
        name="category",
        label="Product category",
        description="Commercial category assigned to a product.",
        source=_reference(
            "categories",
            "name",
        ),
        kind="categorical",
        synonyms=(
            "category",
            "categoria",
            "categoria do produto",
        ),
    ),
    SemanticDimension(
        name="product",
        label="Product",
        description="Product display name.",
        source=_reference(
            "products",
            "name",
        ),
        kind="categorical",
        synonyms=(
            "item",
            "produto",
        ),
    ),
    SemanticDimension(
        name="product_sku",
        label="Product SKU",
        description="Stable stock keeping unit of a product.",
        source=_reference(
            "products",
            "sku",
        ),
        kind="identifier",
        synonyms=(
            "sku",
            "stock code",
            "código do produto",
        ),
    ),
    SemanticDimension(
        name="target_month",
        label="Target month",
        description="Calendar month associated with a sales target.",
        source=_reference(
            "sales_targets",
            "target_month",
        ),
        kind="temporal",
        synonyms=(
            "goal month",
            "mês da meta",
        ),
        time_granularities=(
            "month",
            "quarter",
            "year",
        ),
    ),
)

SEMANTIC_METRICS = (
    SemanticMetric(
        name="order_count",
        label="Order count",
        description="Distinct number of orders.",
        aggregation="count_distinct",
        source=_reference(
            "orders",
            "id",
        ),
        unit="count",
        synonyms=(
            "orders",
            "pedidos",
            "quantidade de pedidos",
        ),
    ),
    SemanticMetric(
        name="customer_count",
        label="Customer count",
        description="Distinct number of customers.",
        aggregation="count_distinct",
        source=_reference(
            "customers",
            "id",
        ),
        unit="count",
        synonyms=(
            "customers",
            "clientes",
            "quantidade de clientes",
        ),
    ),
    SemanticMetric(
        name="active_customer_count",
        label="Active customer count",
        description="Distinct number of active customers.",
        aggregation="count_distinct",
        source=_reference(
            "customers",
            "id",
        ),
        unit="count",
        synonyms=(
            "active customers",
            "clientes ativos",
        ),
        filters=(
            SemanticFilter(
                source=_reference(
                    "customers",
                    "active",
                ),
                operator="equals",
                value=True,
            ),
        ),
    ),
    SemanticMetric(
        name="product_count",
        label="Product count",
        description="Distinct number of products.",
        aggregation="count_distinct",
        source=_reference(
            "products",
            "id",
        ),
        unit="count",
        synonyms=(
            "products",
            "produtos",
            "quantidade de produtos",
        ),
    ),
    SemanticMetric(
        name="units_sold",
        label="Units sold",
        description="Total quantity of product units in order items.",
        aggregation="sum",
        source=_reference(
            "order_items",
            "quantity",
        ),
        unit="units",
        synonyms=(
            "sales volume",
            "unidades vendidas",
            "volume vendido",
        ),
    ),
    SemanticMetric(
        name="approved_revenue",
        label="Approved revenue",
        description="Total BRL amount of approved payments.",
        aggregation="sum",
        source=_reference(
            "payments",
            "amount",
        ),
        unit="brl",
        synonyms=(
            "revenue",
            "sales revenue",
            "faturamento",
            "receita",
        ),
        filters=(
            SemanticFilter(
                source=_reference(
                    "payments",
                    "status",
                ),
                operator="equals",
                value="approved",
            ),
        ),
    ),
    SemanticMetric(
        name="average_approved_order_value",
        label="Average approved order value",
        description="Average BRL amount of approved payments.",
        aggregation="average",
        source=_reference(
            "payments",
            "amount",
        ),
        unit="brl",
        synonyms=(
            "average ticket",
            "average order value",
            "ticket médio",
        ),
        filters=(
            SemanticFilter(
                source=_reference(
                    "payments",
                    "status",
                ),
                operator="equals",
                value="approved",
            ),
        ),
    ),
    SemanticMetric(
        name="revenue_target",
        label="Revenue target",
        description="Total monthly revenue target in BRL.",
        aggregation="sum",
        source=_reference(
            "sales_targets",
            "revenue_target",
        ),
        unit="brl",
        synonyms=(
            "sales target",
            "meta de receita",
            "meta de faturamento",
        ),
    ),
    SemanticMetric(
        name="orders_target",
        label="Orders target",
        description="Total monthly target number of orders.",
        aggregation="sum",
        source=_reference(
            "sales_targets",
            "orders_target",
        ),
        unit="count",
        synonyms=(
            "order goal",
            "meta de pedidos",
        ),
    ),
)

SEMANTIC_BUSINESS_RULES = (
    SemanticBusinessRule(
        name="approved_revenue_only",
        description=("Revenue includes only payments whose status is approved."),
        related_metrics=(
            "approved_revenue",
            "average_approved_order_value",
        ),
    ),
    SemanticBusinessRule(
        name="distinct_order_count",
        description=("Order count uses distinct orders.id to avoid multiplication by joins."),
        related_metrics=("order_count",),
    ),
    SemanticBusinessRule(
        name="sales_region_at_order_time",
        description=(
            "Sales analysis uses orders.region_id; customers.region_id is the home region."
        ),
        related_metrics=(
            "order_count",
            "approved_revenue",
        ),
    ),
    SemanticBusinessRule(
        name="monthly_regional_targets",
        description=(
            "Sales targets have one monthly record per region and must be compared at that grain."
        ),
        related_metrics=(
            "revenue_target",
            "orders_target",
        ),
    ),
    SemanticBusinessRule(
        name="brl_currency",
        description=(
            "All monetary metrics in the retail schema are denominated in Brazilian reais."
        ),
        related_metrics=(
            "approved_revenue",
            "average_approved_order_value",
            "revenue_target",
        ),
    ),
)


def _catalog_references(
    catalog: SchemaCatalog,
) -> set[tuple[str, str, str]]:
    return {
        (
            table.schema_name,
            table.name,
            column.name,
        )
        for table in catalog.tables
        for column in table.columns
    }


def _validate_unique_names(
    names: Iterable[str],
    object_type: str,
) -> None:
    normalized_names = tuple(names)

    if len(normalized_names) != len(set(normalized_names)):
        raise SemanticLayerConstructionError(f"Duplicate semantic {object_type} names")


def _validate_reference(
    reference: SemanticColumnReference,
    available_references: set[tuple[str, str, str]],
    object_name: str,
) -> None:
    key = (
        reference.schema_name,
        reference.table_name,
        reference.column_name,
    )

    if key not in available_references:
        qualified_name = ".".join(key)

        raise SemanticLayerConstructionError(
            f"Semantic object references missing catalog column: {object_name} -> {qualified_name}"
        )


def _build_relationships(
    catalog: SchemaCatalog,
) -> tuple[SemanticRelationship, ...]:
    relationships: list[SemanticRelationship] = []

    for table in catalog.tables:
        for column in table.columns:
            for target in column.references:
                relationships.append(
                    SemanticRelationship(
                        name=(
                            f"{table.name}_"
                            f"{column.name}_to_"
                            f"{target.table_name}_"
                            f"{target.column_name}"
                        ),
                        from_column=SemanticColumnReference(
                            schema_name=table.schema_name,
                            table_name=table.name,
                            column_name=column.name,
                        ),
                        to_column=SemanticColumnReference(
                            schema_name=target.schema_name,
                            table_name=target.table_name,
                            column_name=target.column_name,
                        ),
                        cardinality="many_to_one",
                    )
                )

    return tuple(
        sorted(
            relationships,
            key=lambda relationship: relationship.name,
        )
    )


def build_semantic_layer(
    catalog: SchemaCatalog | None = None,
) -> SemanticLayer:
    """Build and validate deterministic business semantics."""
    active_catalog = catalog if catalog is not None else build_schema_catalog()

    if active_catalog.schema_name != RETAIL_SCHEMA:
        raise SemanticLayerConstructionError("Semantic layer requires the retail schema")

    relationships = _build_relationships(active_catalog)
    available_references = _catalog_references(active_catalog)

    _validate_unique_names(
        (dimension.name for dimension in SEMANTIC_DIMENSIONS),
        "dimension",
    )
    _validate_unique_names(
        (metric.name for metric in SEMANTIC_METRICS),
        "metric",
    )
    _validate_unique_names(
        (relationship.name for relationship in relationships),
        "relationship",
    )
    _validate_unique_names(
        (rule.name for rule in SEMANTIC_BUSINESS_RULES),
        "business-rule",
    )

    for dimension in SEMANTIC_DIMENSIONS:
        _validate_reference(
            dimension.source,
            available_references,
            dimension.name,
        )

    for metric in SEMANTIC_METRICS:
        _validate_reference(
            metric.source,
            available_references,
            metric.name,
        )

        for metric_filter in metric.filters:
            _validate_reference(
                metric_filter.source,
                available_references,
                metric.name,
            )

    for relationship in relationships:
        _validate_reference(
            relationship.from_column,
            available_references,
            relationship.name,
        )
        _validate_reference(
            relationship.to_column,
            available_references,
            relationship.name,
        )

    metric_names = {metric.name for metric in SEMANTIC_METRICS}

    for rule in SEMANTIC_BUSINESS_RULES:
        unknown_metrics = sorted(set(rule.related_metrics) - metric_names)

        if unknown_metrics:
            raise SemanticLayerConstructionError(
                f"Business rule references unknown metrics: {rule.name} -> {unknown_metrics}"
            )

    return SemanticLayer(
        catalog_version=(active_catalog.catalog_version),
        schema_name=active_catalog.schema_name,
        dimensions=SEMANTIC_DIMENSIONS,
        metrics=SEMANTIC_METRICS,
        relationships=relationships,
        business_rules=(SEMANTIC_BUSINESS_RULES),
    )
