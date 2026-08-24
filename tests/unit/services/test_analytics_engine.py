from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.app.schemas.analytics import (
    AnalyticsMetricSummary,
    AnalyticsRanking,
    AnalyticsRankingItem,
    AnalyticsSeries,
    AnalyticsSeriesPoint,
    DeterministicAnalyticsResult,
)
from backend.app.schemas.query_execution import (
    QueryExecutionResult,
    QueryResultColumnMetadata,
    QueryResultRow,
    QueryResultValueKind,
)
from backend.app.schemas.semantic import (
    SemanticColumnReference,
    SemanticDimension,
    SemanticMetric,
)
from backend.app.schemas.semantic_context import (
    CompactGroundingContext,
)
from backend.app.schemas.sql_generation import (
    SQLGenerationResult,
)
from backend.app.schemas.sql_validation import (
    ValidatedSQL,
)
from backend.app.services.analytics_engine import (
    AnalyticsInputError,
    DeterministicAnalyticsEngine,
    create_analytics_engine,
)


def build_summary(
    *,
    metric_name: str = "approved_revenue",
) -> AnalyticsMetricSummary:
    return AnalyticsMetricSummary(
        metric_name=metric_name,
        unit="brl",
        value_count=2,
        total=Decimal("100.0000"),
        average=Decimal("50.0000"),
        minimum=Decimal("40.0000"),
        maximum=Decimal("60.0000"),
    )


def build_ranking(
    *,
    metric_name: str = "approved_revenue",
) -> AnalyticsRanking:
    return AnalyticsRanking(
        metric_name=metric_name,
        dimension_name="region",
        items=(
            AnalyticsRankingItem(
                rank=1,
                dimension_value="North",
                value=Decimal("60.0000"),
                share_percent=Decimal("60.0000"),
            ),
            AnalyticsRankingItem(
                rank=2,
                dimension_value="South",
                value=Decimal("40.0000"),
                share_percent=Decimal("40.0000"),
            ),
        ),
    )


def build_series() -> AnalyticsSeries:
    return AnalyticsSeries(
        metric_name="approved_revenue",
        dimension_name="order_month",
        points=(
            AnalyticsSeriesPoint(
                position=1,
                dimension_value="2026-01",
                value=Decimal("40.0000"),
            ),
            AnalyticsSeriesPoint(
                position=2,
                dimension_value="2026-02",
                value=Decimal("60.0000"),
                previous_value=Decimal("40.0000"),
                absolute_change=Decimal("20.0000"),
                percentage_change=Decimal("50.0000"),
            ),
        ),
    )


def test_analytics_contract_serializes_decimal_values() -> None:
    result = DeterministicAnalyticsResult(
        execution_version="1",
        semantic_version="1",
        catalog_version="1",
        source_row_count=2,
        metric_summaries=(build_summary(),),
        rankings=(build_ranking(),),
        series=(build_series(),),
    )

    serialized = result.model_dump(
        mode="json",
    )

    assert result.analytics_version == "1"
    assert result.analytics_status == "analyzed"
    assert result.deterministic is True
    assert result.calculation_scale == 4
    assert serialized["metric_summaries"][0]["total"] == "100.0000"
    assert serialized["rankings"][0]["items"][0]["value"] == "60.0000"
    assert serialized["series"][0]["points"][1]["percentage_change"] == "50.0000"


def test_metric_summary_rejects_non_finite_value() -> None:
    with pytest.raises(
        ValidationError,
        match="finite",
    ):
        AnalyticsMetricSummary(
            metric_name="approved_revenue",
            unit="brl",
            value_count=1,
            total=Decimal("NaN"),
            average=Decimal("1"),
            minimum=Decimal("1"),
            maximum=Decimal("1"),
        )


def test_ranking_requires_contiguous_positions() -> None:
    with pytest.raises(
        ValidationError,
        match="positions must be contiguous",
    ):
        AnalyticsRanking(
            metric_name="approved_revenue",
            dimension_name="region",
            items=(
                AnalyticsRankingItem(
                    rank=2,
                    dimension_value="North",
                    value=Decimal("60"),
                ),
            ),
        )


def test_series_requires_first_point_without_variation() -> None:
    with pytest.raises(
        ValidationError,
        match="first analytics series point",
    ):
        AnalyticsSeriesPoint(
            position=1,
            dimension_value="2026-01",
            value=Decimal("40"),
            previous_value=Decimal("30"),
            absolute_change=Decimal("10"),
            percentage_change=Decimal("33.3333"),
        )


def test_result_rejects_unknown_metric_reference() -> None:
    with pytest.raises(
        ValidationError,
        match="summarized metric",
    ):
        DeterministicAnalyticsResult(
            execution_version="1",
            semantic_version="1",
            catalog_version="1",
            source_row_count=2,
            metric_summaries=(build_summary(),),
            rankings=(
                build_ranking(
                    metric_name="order_count",
                ),
            ),
        )


def test_analytics_contract_is_immutable() -> None:
    result = DeterministicAnalyticsResult(
        execution_version="1",
        semantic_version="1",
        catalog_version="1",
        source_row_count=2,
        metric_summaries=(build_summary(),),
    )

    with pytest.raises(
        ValidationError,
        match="frozen",
    ):
        result.source_row_count = 0


def build_semantic_reference(
    column_name: str,
) -> SemanticColumnReference:
    return SemanticColumnReference(
        schema_name="retail",
        table_name="analytics_source",
        column_name=column_name,
    )


def build_metric(
    *,
    name: str = "approved_revenue",
) -> SemanticMetric:
    return SemanticMetric(
        name=name,
        label="Approved revenue",
        description="Governed test metric.",
        aggregation="sum",
        source=build_semantic_reference(name),
        unit="brl",
    )


def build_dimension(
    *,
    name: str = "region",
    kind: str = "categorical",
) -> SemanticDimension:
    return SemanticDimension.model_validate(
        {
            "name": name,
            "label": name.replace(
                "_",
                " ",
            ).title(),
            "description": "Governed test dimension.",
            "source": build_semantic_reference(name),
            "kind": kind,
        }
    )


def build_context(
    *,
    dimension: SemanticDimension | None = None,
    metrics: tuple[
        SemanticMetric,
        ...,
    ]
    | None = None,
) -> CompactGroundingContext:
    resolved_dimension = build_dimension() if dimension is None else dimension

    return CompactGroundingContext(
        semantic_version="1",
        catalog_version="1",
        grounding_status="grounded",
        normalized_question="governed analytics question",
        metrics=(build_metric(),) if metrics is None else metrics,
        dimensions=(resolved_dimension,),
    )


def build_engine_execution(
    *,
    columns: tuple[str, ...],
    rows: tuple[
        QueryResultRow,
        ...,
    ],
    metadata: tuple[
        tuple[
            int,
            QueryResultValueKind,
        ],
        ...,
    ],
) -> QueryExecutionResult:
    validated = ValidatedSQL.model_construct(
        validation_version="1",
        validation_status="validated",
        semantic_version="1",
        catalog_version="1",
        row_limit=max(
            1,
            len(rows),
        ),
    )
    generation = SQLGenerationResult.model_construct(
        generation_version="1",
        validated_sql=validated,
        generation_attempts=1,
        repair_attempts=0,
    )

    return QueryExecutionResult(
        generation=generation,
        columns=columns,
        internal_column_metadata=tuple(
            QueryResultColumnMetadata(
                name=column,
                postgres_type_code=type_code,
                value_kind=value_kind,
            )
            for column, (
                type_code,
                value_kind,
            ) in zip(
                columns,
                metadata,
                strict=True,
            )
        ),
        rows=rows,
        row_count=len(rows),
        statement_timeout_ms=8000,
        query_timeout_seconds=10.0,
        execution_time_ms=1.0,
    )


def test_engine_calculates_summary_and_ranking() -> None:
    execution = build_engine_execution(
        columns=(
            "region",
            "approved_revenue",
        ),
        rows=(
            (
                "South",
                "40.00",
            ),
            (
                "North",
                "60.00",
            ),
        ),
        metadata=(
            (
                1043,
                "text",
            ),
            (
                1700,
                "number",
            ),
        ),
    )

    result = DeterministicAnalyticsEngine().analyze(
        execution,
        build_context(),
    )

    summary = result.metric_summaries[0]
    ranking = result.rankings[0]

    assert summary.total == Decimal("100.0000")
    assert summary.average == Decimal("50.0000")
    assert summary.minimum == Decimal("40.0000")
    assert summary.maximum == Decimal("60.0000")
    assert tuple(item.dimension_value for item in ranking.items) == (
        "North",
        "South",
    )
    assert tuple(item.share_percent for item in ranking.items) == (
        Decimal("60.0000"),
        Decimal("40.0000"),
    )
    assert result.series == ()


def test_engine_calculates_temporal_series_and_variations() -> None:
    dimension = build_dimension(
        name="order_month",
        kind="temporal",
    )
    execution = build_engine_execution(
        columns=(
            "order_month",
            "approved_revenue",
        ),
        rows=(
            (
                "2026-02",
                "60",
            ),
            (
                "2026-01",
                "40",
            ),
            (
                "2026-03",
                "0",
            ),
        ),
        metadata=(
            (
                1043,
                "text",
            ),
            (
                1700,
                "number",
            ),
        ),
    )

    result = DeterministicAnalyticsEngine().analyze(
        execution,
        build_context(
            dimension=dimension,
        ),
    )

    points = result.series[0].points

    assert tuple(point.dimension_value for point in points) == (
        "2026-01",
        "2026-02",
        "2026-03",
    )
    assert points[0].previous_value is None
    assert points[1].absolute_change == Decimal("20.0000")
    assert points[1].percentage_change == Decimal("50.0000")
    assert points[2].absolute_change == Decimal("-60.0000")
    assert points[2].percentage_change == Decimal("-100.0000")


def test_engine_omits_percentage_after_zero() -> None:
    dimension = build_dimension(
        name="order_month",
        kind="temporal",
    )
    execution = build_engine_execution(
        columns=(
            "order_month",
            "approved_revenue",
        ),
        rows=(
            (
                "2026-01",
                "0",
            ),
            (
                "2026-02",
                "10",
            ),
        ),
        metadata=(
            (
                1043,
                "text",
            ),
            (
                1700,
                "number",
            ),
        ),
    )

    result = DeterministicAnalyticsEngine().analyze(
        execution,
        build_context(
            dimension=dimension,
        ),
    )

    second_point = result.series[0].points[1]

    assert second_point.previous_value == Decimal("0.0000")
    assert second_point.absolute_change == Decimal("10.0000")
    assert second_point.percentage_change is None


def test_engine_does_not_guess_numeric_strings() -> None:
    execution = build_engine_execution(
        columns=(
            "region",
            "approved_revenue",
        ),
        rows=(
            (
                "North",
                "60.00",
            ),
        ),
        metadata=(
            (
                1043,
                "text",
            ),
            (
                1043,
                "text",
            ),
        ),
    )

    with pytest.raises(
        AnalyticsInputError,
        match="cannot be analyzed",
    ):
        DeterministicAnalyticsEngine().analyze(
            execution,
            build_context(),
        )


def test_engine_rejects_unknown_column_metadata() -> None:
    execution = build_engine_execution(
        columns=(
            "region",
            "approved_revenue",
        ),
        rows=(
            (
                "North",
                "60",
            ),
        ),
        metadata=(
            (
                1043,
                "text",
            ),
            (
                999_999,
                "unknown",
            ),
        ),
    )

    with pytest.raises(
        AnalyticsInputError,
        match="cannot be analyzed",
    ):
        DeterministicAnalyticsEngine().analyze(
            execution,
            build_context(),
        )


def test_engine_requires_internal_column_metadata() -> None:
    execution = build_engine_execution(
        columns=(
            "region",
            "approved_revenue",
        ),
        rows=(
            (
                "North",
                "60",
            ),
        ),
        metadata=(
            (
                1043,
                "text",
            ),
            (
                1700,
                "number",
            ),
        ),
    ).model_copy(
        update={
            "internal_column_metadata": (),
        }
    )

    with pytest.raises(
        AnalyticsInputError,
        match="cannot be analyzed",
    ):
        DeterministicAnalyticsEngine().analyze(
            execution,
            build_context(),
        )


def test_engine_rejects_multiple_dimensions() -> None:
    context = build_context().model_copy(
        update={
            "dimensions": (
                build_dimension(
                    name="region",
                ),
                build_dimension(
                    name="payment_method",
                ),
            )
        }
    )
    execution = build_engine_execution(
        columns=(
            "region",
            "payment_method",
            "approved_revenue",
        ),
        rows=(
            (
                "North",
                "card",
                "60",
            ),
        ),
        metadata=(
            (
                1043,
                "text",
            ),
            (
                1043,
                "text",
            ),
            (
                1700,
                "number",
            ),
        ),
    )

    with pytest.raises(
        AnalyticsInputError,
        match="cannot be analyzed",
    ):
        DeterministicAnalyticsEngine().analyze(
            execution,
            context,
        )


def test_analytics_engine_factory_is_stateless() -> None:
    first = create_analytics_engine()
    second = create_analytics_engine()

    assert isinstance(
        first,
        DeterministicAnalyticsEngine,
    )
    assert isinstance(
        second,
        DeterministicAnalyticsEngine,
    )
    assert first is not second
