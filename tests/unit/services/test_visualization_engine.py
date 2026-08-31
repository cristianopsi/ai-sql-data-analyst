from decimal import Decimal

import pytest
from pydantic import (
    ValidationError,
)

from backend.app.schemas.analytics import (
    AnalyticsMetricSummary,
    AnalyticsRanking,
    AnalyticsRankingItem,
    AnalyticsSeries,
    AnalyticsSeriesPoint,
    DeterministicAnalyticsResult,
)
from backend.app.schemas.visualization import (
    MAX_BAR_CATEGORIES,
    MAX_LINE_POINTS,
    MAX_TABLE_ROWS,
    BarVisualizationItem,
    BarVisualizationSpec,
    DeterministicVisualizationResult,
    KPIVisualizationSpec,
    LineVisualizationPoint,
    LineVisualizationSpec,
    TableVisualizationRow,
    TableVisualizationSpec,
    VisualizationRequest,
)
from backend.app.services.visualization_engine import (
    DeterministicVisualizationEngine,
    VisualizationInputError,
    create_visualization_engine,
)


def build_analytics_result() -> DeterministicAnalyticsResult:
    return DeterministicAnalyticsResult(
        execution_version="1",
        semantic_version="1",
        catalog_version="1",
        source_row_count=3,
        metric_summaries=(
            AnalyticsMetricSummary(
                metric_name="approved_revenue",
                unit="brl",
                value_count=3,
                total=Decimal("600.0000"),
                average=Decimal("200.0000"),
                minimum=Decimal("100.0000"),
                maximum=Decimal("300.0000"),
            ),
        ),
        rankings=(
            AnalyticsRanking(
                metric_name="approved_revenue",
                dimension_name="region",
                items=(
                    AnalyticsRankingItem(
                        rank=1,
                        dimension_value="South",
                        value=Decimal("300.0000"),
                        share_percent=Decimal("50.0000"),
                    ),
                    AnalyticsRankingItem(
                        rank=2,
                        dimension_value="Southeast",
                        value=Decimal("200.0000"),
                        share_percent=Decimal("33.3333"),
                    ),
                    AnalyticsRankingItem(
                        rank=3,
                        dimension_value="North",
                        value=Decimal("100.0000"),
                        share_percent=Decimal("16.6667"),
                    ),
                ),
            ),
        ),
        series=(
            AnalyticsSeries(
                metric_name="approved_revenue",
                dimension_name="order_month",
                points=(
                    AnalyticsSeriesPoint(
                        position=1,
                        dimension_value="2026-01",
                        value=Decimal("100.0000"),
                    ),
                    AnalyticsSeriesPoint(
                        position=2,
                        dimension_value="2026-02",
                        value=Decimal("200.0000"),
                        previous_value=Decimal("100.0000"),
                        absolute_change=Decimal("100.0000"),
                        percentage_change=Decimal("100.0000"),
                    ),
                    AnalyticsSeriesPoint(
                        position=3,
                        dimension_value="2026-03",
                        value=Decimal("300.0000"),
                        previous_value=Decimal("200.0000"),
                        absolute_change=Decimal("100.0000"),
                        percentage_change=Decimal("50.0000"),
                    ),
                ),
            ),
        ),
    )


def test_visualization_request_is_question_only_and_strict() -> None:
    request = VisualizationRequest(
        question="Receita aprovada por região",
    )

    assert request.question == "Receita aprovada por região"

    with pytest.raises(ValidationError):
        VisualizationRequest.model_validate(
            {
                "question": "Receita aprovada",
                "sql": "SELECT 1",
            }
        )

    with pytest.raises(ValidationError):
        VisualizationRequest(
            question="   ",
        )


def test_visualization_contract_serializes_decimal_values() -> None:
    result = DeterministicVisualizationEngine().specify(build_analytics_result())

    payload = result.model_dump(
        mode="json",
    )

    assert payload["visualization_version"] == "1"
    assert payload["visualization_status"] == "specified"
    assert payload["deterministic"] is True
    assert payload["specifications"][0]["value"] == "600.0000"
    assert payload["specifications"][1]["rows"][0]["value"] == "300.0000"
    assert payload["specifications"][2]["items"][0]["value"] == "300.0000"
    assert payload["specifications"][3]["points"][1]["percentage_change"] == "100.0000"


def test_visualization_contract_is_immutable() -> None:
    result = DeterministicVisualizationEngine().specify(build_analytics_result())

    with pytest.raises(ValidationError):
        result.source_row_count = 99  # type: ignore[misc]


def test_kpi_rejects_non_finite_values() -> None:
    with pytest.raises(ValidationError) as captured:
        KPIVisualizationSpec(
            spec_id="kpi-test",
            title="Test",
            metric_name="test_metric",
            unit="count",
            value_count=1,
            aggregation="sum",
            total=Decimal("NaN"),
            value=Decimal("NaN"),
            average=Decimal("1"),
            minimum=Decimal("1"),
            maximum=Decimal("1"),
        )

    error_types = {
        str(error["type"])
        for error in captured.value.errors(
            include_url=False,
        )
    }

    assert "finite_number" in error_types


def test_kpi_requires_consistent_bounds() -> None:
    with pytest.raises(
        ValidationError,
        match="average must be within",
    ):
        KPIVisualizationSpec(
            spec_id="kpi-test",
            title="Test",
            metric_name="test_metric",
            unit="count",
            value_count=1,
            aggregation="sum",
            total=Decimal("10"),
            value=Decimal("10"),
            average=Decimal("10"),
            minimum=Decimal("1"),
            maximum=Decimal("5"),
        )


def test_bar_requires_contiguous_positions() -> None:
    with pytest.raises(
        ValidationError,
        match="positions must be contiguous",
    ):
        BarVisualizationSpec(
            spec_id="bar-test",
            title="Test",
            metric_name="test_metric",
            dimension_name="region",
            unit="count",
            ranking_total=Decimal("1"),
            items=(
                BarVisualizationItem(
                    position=2,
                    label="South",
                    value=Decimal("1"),
                ),
            ),
        )


def test_bar_rejects_duplicate_labels() -> None:
    with pytest.raises(
        ValidationError,
        match="labels must be unique",
    ):
        BarVisualizationSpec(
            spec_id="bar-test",
            title="Test",
            metric_name="test_metric",
            dimension_name="region",
            unit="count",
            ranking_total=Decimal("3"),
            items=(
                BarVisualizationItem(
                    position=1,
                    label="South",
                    value=Decimal("2"),
                ),
                BarVisualizationItem(
                    position=2,
                    label="south",
                    value=Decimal("1"),
                ),
            ),
        )


def test_table_requires_contiguous_positions() -> None:
    with pytest.raises(
        ValidationError,
        match="positions must be contiguous",
    ):
        TableVisualizationSpec(
            spec_id="table-test",
            title="Test",
            metric_name="test_metric",
            dimension_name="region",
            unit="count",
            ranking_total=Decimal("1"),
            rows=(
                TableVisualizationRow(
                    position=2,
                    label="South",
                    value=Decimal("1"),
                ),
            ),
        )


def test_line_requires_first_point_without_variation() -> None:
    with pytest.raises(
        ValidationError,
        match="First line point",
    ):
        LineVisualizationSpec(
            spec_id="line-test",
            title="Test",
            metric_name="test_metric",
            dimension_name="month",
            unit="count",
            points=(
                LineVisualizationPoint(
                    position=1,
                    label="2026-01",
                    value=Decimal("1"),
                    previous_value=Decimal("0"),
                    absolute_change=Decimal("1"),
                ),
            ),
        )


def test_line_requires_subsequent_variation_metadata() -> None:
    with pytest.raises(
        ValidationError,
        match="Subsequent line points",
    ):
        LineVisualizationSpec(
            spec_id="line-test",
            title="Test",
            metric_name="test_metric",
            dimension_name="month",
            unit="count",
            points=(
                LineVisualizationPoint(
                    position=1,
                    label="2026-01",
                    value=Decimal("1"),
                ),
                LineVisualizationPoint(
                    position=2,
                    label="2026-02",
                    value=Decimal("2"),
                ),
            ),
        )


def test_result_rejects_duplicate_specification_ids() -> None:
    engine = DeterministicVisualizationEngine()
    generated = engine.specify(build_analytics_result())
    first = generated.specifications[0]

    with pytest.raises(
        ValidationError,
        match="identifiers must be unique",
    ):
        DeterministicVisualizationResult(
            analytics_version="1",
            execution_version="1",
            semantic_version="1",
            catalog_version="1",
            source_row_count=3,
            specifications=(
                first,
                first,
            ),
        )


def test_result_requires_deterministic_chart_order() -> None:
    generated = DeterministicVisualizationEngine().specify(build_analytics_result())

    with pytest.raises(
        ValidationError,
        match="invalid chart order",
    ):
        DeterministicVisualizationResult(
            analytics_version="1",
            execution_version="1",
            semantic_version="1",
            catalog_version="1",
            source_row_count=3,
            specifications=(
                generated.specifications[1],
                generated.specifications[0],
            ),
        )


def test_engine_maps_analytics_to_kpi_table_bar_and_line() -> None:
    analytics = build_analytics_result()
    result = DeterministicVisualizationEngine().specify(analytics)

    assert result.analytics_version == analytics.analytics_version
    assert result.execution_version == analytics.execution_version
    assert result.semantic_version == analytics.semantic_version
    assert result.catalog_version == analytics.catalog_version
    assert result.source_row_count == analytics.source_row_count
    assert tuple(specification.chart_type for specification in result.specifications) == (
        "kpi",
        "table",
        "bar",
        "line",
    )

    kpi = result.specifications[0]
    table = result.specifications[1]
    bar = result.specifications[2]
    line = result.specifications[3]

    assert isinstance(kpi, KPIVisualizationSpec)
    assert isinstance(table, TableVisualizationSpec)
    assert isinstance(bar, BarVisualizationSpec)
    assert isinstance(line, LineVisualizationSpec)

    assert kpi.title == "Approved Revenue"
    assert kpi.metric_name == "approved_revenue"
    assert kpi.unit == "brl"
    assert kpi.value == Decimal("600.0000")

    assert table.title == "Approved Revenue by Region"
    assert table.metric_name == "approved_revenue"
    assert table.dimension_name == "region"
    assert tuple(row.label for row in table.rows) == (
        "South",
        "Southeast",
        "North",
    )

    assert bar.title == "Approved Revenue by Region"
    assert bar.metric_name == "approved_revenue"
    assert bar.dimension_name == "region"
    assert bar.unit == "brl"
    assert tuple(item.label for item in bar.items) == (
        "South",
        "Southeast",
        "North",
    )

    assert line.title == "Approved Revenue by Order Month"
    assert line.metric_name == "approved_revenue"
    assert line.dimension_name == "order_month"
    assert line.unit == "brl"
    assert tuple(point.label for point in line.points) == (
        "2026-01",
        "2026-02",
        "2026-03",
    )


def test_engine_specification_identifiers_are_stable() -> None:
    engine = DeterministicVisualizationEngine()
    analytics = build_analytics_result()

    first = engine.specify(analytics)
    second = engine.specify(analytics)

    assert tuple(specification.spec_id for specification in first.specifications) == tuple(
        specification.spec_id for specification in second.specifications
    )
    assert first == second


def test_engine_resolves_metric_units_case_insensitively() -> None:
    analytics = build_analytics_result()
    ranking = analytics.rankings[0].model_copy(
        update={
            "metric_name": "APPROVED_REVENUE",
        }
    )
    modified = analytics.model_copy(
        update={
            "rankings": (ranking,),
        }
    )

    result = DeterministicVisualizationEngine().specify(modified)
    bar = result.specifications[2]

    assert isinstance(bar, BarVisualizationSpec)
    assert bar.metric_name == "approved_revenue"
    assert bar.unit == "brl"


def test_engine_applies_deterministic_collection_limits() -> None:
    analytics = build_analytics_result()
    ranking_item_count = MAX_TABLE_ROWS + 1
    ranking_total = Decimal(ranking_item_count)
    ranking_share = (Decimal("100") / ranking_total).quantize(
        Decimal("0.0001"),
    )
    ranking = analytics.rankings[0].model_copy(
        update={
            "items": tuple(
                AnalyticsRankingItem(
                    rank=position,
                    dimension_value=f"Region {position:03d}",
                    value=Decimal("1"),
                    share_percent=ranking_share,
                )
                for position in range(
                    1,
                    ranking_item_count + 1,
                )
            )
        }
    )
    series = analytics.series[0].model_copy(
        update={
            "points": tuple(
                AnalyticsSeriesPoint(
                    position=position,
                    dimension_value=f"2026-{position:03d}",
                    value=Decimal("1"),
                    previous_value=(Decimal("1") if position > 1 else None),
                    absolute_change=(Decimal("0") if position > 1 else None),
                    percentage_change=(Decimal("0") if position > 1 else None),
                )
                for position in range(1, MAX_LINE_POINTS + 2)
            )
        }
    )
    oversized = analytics.model_copy(
        update={
            "rankings": (ranking,),
            "series": (series,),
        }
    )

    result = DeterministicVisualizationEngine().specify(oversized)
    table = next(item for item in result.specifications if isinstance(item, TableVisualizationSpec))
    bar = next(item for item in result.specifications if isinstance(item, BarVisualizationSpec))
    line = next(item for item in result.specifications if isinstance(item, LineVisualizationSpec))

    assert len(table.rows) == MAX_TABLE_ROWS
    assert len(bar.items) == MAX_BAR_CATEGORIES
    assert len(line.points) == MAX_LINE_POINTS


def test_engine_rejects_unknown_metric_reference() -> None:
    analytics = build_analytics_result()
    ranking = analytics.rankings[0].model_copy(
        update={
            "metric_name": "unknown_metric",
        }
    )
    malformed = analytics.model_copy(
        update={
            "rankings": (ranking,),
        }
    )

    with pytest.raises(
        VisualizationInputError,
        match="unknown metric",
    ):
        DeterministicVisualizationEngine().specify(malformed)


def test_engine_rejects_untrusted_analytics_result() -> None:
    analytics = build_analytics_result().model_copy(
        update={
            "deterministic": False,
        }
    )

    with pytest.raises(
        VisualizationInputError,
        match="not trusted",
    ):
        DeterministicVisualizationEngine().specify(analytics)


def test_visualization_engine_factory_is_stateless() -> None:
    first = create_visualization_engine()
    second = create_visualization_engine()

    assert isinstance(
        first,
        DeterministicVisualizationEngine,
    )
    assert isinstance(
        second,
        DeterministicVisualizationEngine,
    )
    assert first is not second
    assert first.specify(build_analytics_result()) == (second.specify(build_analytics_result()))


def test_schema_rejects_noncanonical_specification_id() -> None:
    generated = DeterministicVisualizationEngine().specify(
        build_analytics_result(),
    )
    kpi = next(
        specification
        for specification in generated.specifications
        if specification.chart_type == "kpi"
    )
    malformed_kpi = kpi.model_copy(
        update={
            "spec_id": f"kpi-{'0' * 64}",
        }
    )
    specifications = tuple(
        malformed_kpi if specification is kpi else specification
        for specification in generated.specifications
    )

    with pytest.raises(
        ValidationError,
        match="canonical",
    ):
        DeterministicVisualizationResult(
            analytics_version=generated.analytics_version,
            execution_version=generated.execution_version,
            semantic_version=generated.semantic_version,
            catalog_version=generated.catalog_version,
            source_row_count=generated.source_row_count,
            specifications=specifications,
        )


def test_result_requires_positive_source_row_count() -> None:
    generated = DeterministicVisualizationEngine().specify(
        build_analytics_result(),
    )
    payload = generated.model_dump(mode="python")
    payload["source_row_count"] = 0

    with pytest.raises(
        ValidationError,
        match="greater than or equal to 1",
    ):
        DeterministicVisualizationResult.model_validate(
            payload,
        )


@pytest.mark.parametrize(
    (
        "field_name",
        "offset",
        "expected_message",
    ),
    (
        (
            "average",
            Decimal("0.0002"),
            "average must match",
        ),
        (
            "value",
            Decimal("0.0001"),
            "value must match its aggregation",
        ),
    ),
)
def test_kpi_rejects_inconsistent_arithmetic(
    field_name: str,
    offset: Decimal,
    expected_message: str,
) -> None:
    generated = DeterministicVisualizationEngine().specify(
        build_analytics_result(),
    )
    kpi = next(
        specification
        for specification in generated.specifications
        if specification.chart_type == "kpi"
    )
    payload = kpi.model_dump(mode="python")
    original_value = payload[field_name]

    assert isinstance(original_value, Decimal)

    payload[field_name] = original_value + offset

    with pytest.raises(
        ValidationError,
        match=expected_message,
    ):
        KPIVisualizationSpec.model_validate(payload)


def test_engine_propagates_full_ranking_total() -> None:
    analytics = build_analytics_result()
    generated = DeterministicVisualizationEngine().specify(
        analytics,
    )
    ranking = analytics.rankings[0]
    expected_total = sum(
        (item.value for item in ranking.items),
        Decimal("0"),
    )
    table = next(
        specification
        for specification in generated.specifications
        if specification.chart_type == "table"
    )
    bar = next(
        specification
        for specification in generated.specifications
        if specification.chart_type == "bar"
    )

    assert table.ranking_total == expected_total
    assert bar.ranking_total == expected_total


def test_bar_rejects_inconsistent_share() -> None:
    generated = DeterministicVisualizationEngine().specify(
        build_analytics_result(),
    )
    bar = next(
        specification
        for specification in generated.specifications
        if specification.chart_type == "bar"
    )
    payload = bar.model_dump(mode="python")
    items = tuple(payload["items"])
    first_item = dict(items[0])
    share = first_item["share_percent"]

    assert isinstance(share, Decimal)

    first_item["share_percent"] = share + Decimal("0.0001")
    payload["items"] = (
        first_item,
        *items[1:],
    )

    with pytest.raises(
        ValidationError,
        match=("Bar visualization shares must match the ranking total"),
    ):
        BarVisualizationSpec.model_validate(payload)


def test_table_rejects_inconsistent_share() -> None:
    generated = DeterministicVisualizationEngine().specify(
        build_analytics_result(),
    )
    table = next(
        specification
        for specification in generated.specifications
        if specification.chart_type == "table"
    )
    payload = table.model_dump(mode="python")
    rows = tuple(payload["rows"])
    first_row = dict(rows[0])
    share = first_row["share_percent"]

    assert isinstance(share, Decimal)

    first_row["share_percent"] = share + Decimal("0.0001")
    payload["rows"] = (
        first_row,
        *rows[1:],
    )

    with pytest.raises(
        ValidationError,
        match=("Table visualization shares must match the ranking total"),
    ):
        TableVisualizationSpec.model_validate(payload)


@pytest.mark.parametrize(
    (
        "field_name",
        "offset",
        "expected_message",
    ),
    (
        (
            "previous_value",
            Decimal("1.0000"),
            "previous values must match prior points",
        ),
        (
            "absolute_change",
            Decimal("0.0001"),
            "absolute changes must match point values",
        ),
        (
            "percentage_change",
            Decimal("0.0001"),
            "percentage changes must match point values",
        ),
    ),
)
def test_line_rejects_inconsistent_arithmetic(
    field_name: str,
    offset: Decimal,
    expected_message: str,
) -> None:
    generated = DeterministicVisualizationEngine().specify(
        build_analytics_result(),
    )
    line = next(
        specification
        for specification in generated.specifications
        if specification.chart_type == "line"
    )
    payload = line.model_dump(mode="python")
    points = tuple(payload["points"])
    second_point = dict(points[1])
    original_value = second_point[field_name]

    assert isinstance(original_value, Decimal)

    second_point[field_name] = original_value + offset
    payload["points"] = (
        points[0],
        second_point,
        *points[2:],
    )

    with pytest.raises(
        ValidationError,
        match=expected_message,
    ):
        LineVisualizationSpec.model_validate(payload)


def test_result_rejects_table_bar_mismatch() -> None:
    generated = DeterministicVisualizationEngine().specify(
        build_analytics_result(),
    )
    bar = next(
        specification
        for specification in generated.specifications
        if specification.chart_type == "bar"
    )
    inconsistent_bar = bar.model_copy(
        update={
            "title": f"{bar.title} mismatch",
        }
    )
    specifications = tuple(
        inconsistent_bar if specification is bar else specification
        for specification in generated.specifications
    )

    with pytest.raises(
        ValidationError,
        match=("Visualization table and bar specifications must match"),
    ):
        DeterministicVisualizationResult(
            analytics_version=generated.analytics_version,
            execution_version=generated.execution_version,
            semantic_version=generated.semantic_version,
            catalog_version=generated.catalog_version,
            source_row_count=generated.source_row_count,
            specifications=specifications,
        )


def test_result_rejects_duplicate_semantic_specification() -> None:
    generated = DeterministicVisualizationEngine().specify(
        build_analytics_result(),
    )
    kpi = next(
        specification
        for specification in generated.specifications
        if specification.chart_type == "kpi"
    )
    duplicate = kpi.model_copy(
        update={
            "spec_id": f"kpi-{'f' * 64}",
        }
    )

    with pytest.raises(
        ValidationError,
        match="semantic specifications must be unique",
    ):
        DeterministicVisualizationResult(
            analytics_version=generated.analytics_version,
            execution_version=generated.execution_version,
            semantic_version=generated.semantic_version,
            catalog_version=generated.catalog_version,
            source_row_count=generated.source_row_count,
            specifications=(
                kpi,
                duplicate,
                *generated.specifications[1:],
            ),
        )
