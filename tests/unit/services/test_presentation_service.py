"""Tests for controlled analytical presentation orchestration."""

from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from backend.app.schemas.analytics import DeterministicAnalyticsResult
from backend.app.schemas.insights import GroundedInsightResult
from backend.app.schemas.llm import LLMTokenUsage
from backend.app.schemas.presentation import (
    AnalyticalPresentationResult,
    PresentationApiErrorResponse,
    PresentationQueryResult,
    PresentationRequest,
)
from backend.app.schemas.query_execution import QueryExecutionResult
from backend.app.schemas.sql_generation import SQLGenerationResult
from backend.app.schemas.visualization import (
    DeterministicVisualizationResult,
    KPIVisualizationSpec,
)
from backend.app.services.analytics_engine import (
    DeterministicAnalyticsEngine,
)
from backend.app.services.insight_engine import GroundedInsightEngine
from backend.app.services.presentation_service import (
    AnalyticalPresentationService,
    PresentationInputError,
    create_presentation_service,
)
from backend.app.services.query_executor import QueryExecutor
from backend.app.services.sql_generation import (
    SQLGenerationExhaustedError,
    SQLGenerationPipeline,
)
from backend.app.services.visualization_engine import (
    DeterministicVisualizationEngine,
)


def _analytics_result() -> DeterministicAnalyticsResult:
    return DeterministicAnalyticsResult.model_construct(
        analytics_version="1",
        analytics_status="analyzed",
        deterministic=True,
        calculation_scale=2,
        execution_version="1",
        semantic_version="1",
        catalog_version="1",
        source_row_count=5,
        metric_summaries=(),
        rankings=(),
        series=(),
    )


def _visualization_result(
    **updates: object,
) -> DeterministicVisualizationResult:
    first = KPIVisualizationSpec.model_construct(
        spec_id="specification-a",
        chart_type="kpi",
        title="Approved revenue",
        metric_name="approved_revenue",
        unit="brl",
        value_count=5,
        value=Decimal("100.10"),
        average=Decimal("20.02"),
        minimum=Decimal("10.01"),
        maximum=Decimal("30.03"),
    )
    second = KPIVisualizationSpec.model_construct(
        spec_id="specification-b",
        chart_type="kpi",
        title="Approved revenue average",
        metric_name="approved_revenue",
        unit="brl",
        value_count=5,
        value=Decimal("20.02"),
        average=Decimal("20.02"),
        minimum=Decimal("10.01"),
        maximum=Decimal("30.03"),
    )

    result = DeterministicVisualizationResult.model_construct(
        visualization_version="1",
        visualization_status="specified",
        deterministic=True,
        analytics_version="1",
        execution_version="1",
        semantic_version="1",
        catalog_version="1",
        source_row_count=5,
        specifications=(first, second),
    )

    return result.model_copy(update=updates)


def _insight_result(**updates: object) -> GroundedInsightResult:
    result = GroundedInsightResult.model_construct(
        insight_version="1",
        insight_status="generated",
        grounded=True,
        calculated_by_llm=False,
        analytics_version="1",
        visualization_version="1",
        execution_version="1",
        semantic_version="1",
        catalog_version="1",
        source_row_count=5,
        provider="mock",
        model="presentation-model",
        usage=LLMTokenUsage.model_construct(
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
        ),
        summary="Approved revenue is distributed across five regions.",
        claims=(),
    )

    return result.model_copy(update=updates)


def _query_result(
    **updates: object,
) -> PresentationQueryResult:
    result = PresentationQueryResult(
        validated_sql=("SELECT region, approved_revenue FROM retail.approved_revenue"),
        columns=("region", "approved_revenue"),
        rows=(
            ("North", "30.03"),
            ("South", "25.02"),
            ("East", "20.02"),
            ("West", "15.02"),
            ("Central", "10.01"),
        ),
        row_count=5,
    )

    return result.model_copy(update=updates)


@dataclass
class _Harness:
    service: AnalyticalPresentationService
    pipeline: Mock
    executor: Mock
    analytics_engine: Mock
    visualization_engine: Mock
    insight_engine: Mock
    execution: QueryExecutionResult
    analytics: DeterministicAnalyticsResult
    visualizations: DeterministicVisualizationResult
    insights: GroundedInsightResult
    events: list[str]


def _configured_service() -> _Harness:
    events: list[str] = []
    from backend.app.schemas.semantic import SemanticDimension
    from backend.app.schemas.semantic_context import CompactGroundingContext

    context = CompactGroundingContext.model_construct(
        dimensions=(
            SemanticDimension.model_construct(
                name="payment_status",
            ),
            SemanticDimension.model_construct(
                name="region",
            ),
        ),
    )
    generation = cast(
        SQLGenerationResult,
        SimpleNamespace(
            internal_context=context,
            validated_sql=SimpleNamespace(
                sql=("SELECT region, approved_revenue FROM retail.approved_revenue")
            ),
        ),
    )
    execution = cast(
        QueryExecutionResult,
        SimpleNamespace(
            transaction_read_only=True,
            columns=("region", "approved_revenue"),
            rows=(
                ("North", "30.03"),
                ("South", "25.02"),
                ("East", "20.02"),
                ("West", "15.02"),
                ("Central", "10.01"),
            ),
            row_count=5,
        ),
    )
    analytics = _analytics_result()
    visualizations = _visualization_result()
    insights = _insight_result()

    pipeline = Mock(spec=SQLGenerationPipeline)
    executor = Mock(spec=QueryExecutor)
    analytics_engine = Mock(spec=DeterministicAnalyticsEngine)
    visualization_engine = Mock(spec=DeterministicVisualizationEngine)
    insight_engine = Mock(spec=GroundedInsightEngine)

    def generate(question: str) -> SQLGenerationResult:
        events.append("generate")
        assert question == "Show approved revenue by region"
        return generation

    def execute(value: SQLGenerationResult) -> QueryExecutionResult:
        events.append("execute")
        assert value is generation
        return execution

    def analyze(
        value: QueryExecutionResult,
        supplied_context: object,
    ) -> DeterministicAnalyticsResult:
        events.append("analyze")
        assert value is execution
        assert isinstance(
            supplied_context,
            CompactGroundingContext,
        )
        assert supplied_context is not context
        assert tuple(dimension.name for dimension in supplied_context.dimensions) == ("region",)
        assert tuple(dimension.name for dimension in context.dimensions) == (
            "payment_status",
            "region",
        )
        return analytics

    def specify(
        value: DeterministicAnalyticsResult,
    ) -> DeterministicVisualizationResult:
        events.append("specify")
        assert value is analytics
        return visualizations

    def explain(
        analytics_value: DeterministicAnalyticsResult,
        visualization_value: DeterministicVisualizationResult,
    ) -> GroundedInsightResult:
        events.append("explain")
        assert analytics_value is analytics
        assert visualization_value is visualizations
        return insights

    pipeline.generate.side_effect = generate
    executor.execute.side_effect = execute
    analytics_engine.analyze.side_effect = analyze
    visualization_engine.specify.side_effect = specify
    insight_engine.generate.side_effect = explain

    service = AnalyticalPresentationService(
        pipeline=pipeline,
        executor=executor,
        analytics_engine=analytics_engine,
        visualization_engine=visualization_engine,
        insight_engine=insight_engine,
    )

    return _Harness(
        service=service,
        pipeline=pipeline,
        executor=executor,
        analytics_engine=analytics_engine,
        visualization_engine=visualization_engine,
        insight_engine=insight_engine,
        execution=execution,
        analytics=analytics,
        visualizations=visualizations,
        insights=insights,
        events=events,
    )


def test_presentation_request_is_question_only_strict_and_immutable() -> None:
    request = PresentationRequest(question="Show approved revenue")

    assert tuple(PresentationRequest.model_fields) == ("question",)

    with pytest.raises(ValidationError):
        PresentationRequest.model_validate(
            {
                "question": "Show approved revenue",
                "sql": "SELECT 1",
            }
        )

    with pytest.raises(ValidationError):
        PresentationRequest.model_validate({"question": 1})

    with pytest.raises(ValidationError):
        PresentationRequest(question="   ")

    with pytest.raises(ValidationError):
        request.question = "Changed"


def test_presentation_error_response_is_strict() -> None:
    response = PresentationApiErrorResponse(detail="Request is invalid")

    assert response.detail == "Request is invalid"

    with pytest.raises(ValidationError):
        PresentationApiErrorResponse.model_validate(
            {
                "detail": "Request is invalid",
                "internal_error": "private",
            }
        )


def test_service_executes_each_controlled_stage_once_in_order() -> None:
    harness = _configured_service()

    result = harness.service.generate("Show approved revenue by region")

    assert harness.events == [
        "generate",
        "execute",
        "analyze",
        "specify",
        "explain",
    ]
    harness.pipeline.generate.assert_called_once()
    harness.executor.execute.assert_called_once()
    harness.analytics_engine.analyze.assert_called_once()
    harness.visualization_engine.specify.assert_called_once()
    harness.insight_engine.generate.assert_called_once()
    assert result.presentation_version == "1"
    assert result.presentation_status == "generated"
    assert result.source_row_count == 5


def test_service_preserves_visualization_identity_order_and_decimals() -> None:
    harness = _configured_service()

    result = harness.service.generate("Show approved revenue by region")

    assert result.visualizations is harness.visualizations
    assert result.insights is harness.insights
    assert [specification.spec_id for specification in result.visualizations.specifications] == [
        "specification-a",
        "specification-b",
    ]

    first = result.visualizations.specifications[0]
    assert isinstance(first, KPIVisualizationSpec)
    assert first.value == Decimal("100.10")
    assert first.average == Decimal("20.02")


def test_service_fails_closed_without_internal_context() -> None:
    harness = _configured_service()
    harness.pipeline.generate.side_effect = None
    harness.pipeline.generate.return_value = cast(
        SQLGenerationResult,
        SimpleNamespace(internal_context=None),
    )

    with pytest.raises(
        PresentationInputError,
        match="requires trusted grounding context",
    ):
        harness.service.generate("Show approved revenue by region")

    harness.executor.execute.assert_not_called()
    harness.analytics_engine.analyze.assert_not_called()
    harness.visualization_engine.specify.assert_not_called()
    harness.insight_engine.generate.assert_not_called()


def test_service_rejects_invalid_question_before_pipeline() -> None:
    harness = _configured_service()

    with pytest.raises(
        PresentationInputError,
        match="question is invalid",
    ):
        harness.service.generate("   ")

    harness.pipeline.generate.assert_not_called()


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("analytics_version", "different"),
        ("visualization_version", "different"),
        ("execution_version", "different"),
        ("semantic_version", "different"),
        ("catalog_version", "different"),
        ("source_row_count", 6),
    ],
)
def test_result_rejects_cross_result_mismatch(
    field_name: str,
    value: object,
) -> None:
    visualizations = _visualization_result()
    insights = _insight_result(**{field_name: value})

    with pytest.raises(
        ValidationError,
        match=f"Presentation source mismatch: {field_name}",
    ):
        AnalyticalPresentationResult(
            source_row_count=5,
            query=_query_result(),
            visualizations=visualizations,
            insights=insights,
        )


@pytest.mark.parametrize(
    ("insight_updates", "visualization_updates"),
    [
        ({"insight_status": "invalid"}, {}),
        ({"grounded": False}, {}),
        ({"calculated_by_llm": True}, {}),
        ({}, {"visualization_status": "invalid"}),
    ],
)
def test_result_rejects_untrusted_nested_state(
    insight_updates: dict[str, object],
    visualization_updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        AnalyticalPresentationResult(
            source_row_count=5,
            query=_query_result(),
            visualizations=_visualization_result(**visualization_updates),
            insights=_insight_result(**insight_updates),
        )


def test_result_rejects_presentation_row_count_mismatch() -> None:
    with pytest.raises(
        ValidationError,
        match="Presentation source row count does not match",
    ):
        AnalyticalPresentationResult(
            source_row_count=6,
            query=_query_result(),
            visualizations=_visualization_result(),
            insights=_insight_result(),
        )


def test_service_wraps_result_consistency_failure() -> None:
    harness = _configured_service()
    harness.insight_engine.generate.side_effect = None
    harness.insight_engine.generate.return_value = _insight_result(analytics_version="different")

    with pytest.raises(
        PresentationInputError,
        match="results are inconsistent",
    ):
        harness.service.generate("Show approved revenue by region")


def test_service_preserves_controlled_dependency_error() -> None:
    harness = _configured_service()
    error = SQLGenerationExhaustedError("Generation failed safely")
    harness.pipeline.generate.side_effect = error

    with pytest.raises(SQLGenerationExhaustedError) as captured:
        harness.service.generate("Show approved revenue by region")

    assert captured.value is error
    harness.executor.execute.assert_not_called()


def test_presentation_result_is_exact_and_immutable() -> None:
    result = AnalyticalPresentationResult(
        source_row_count=5,
        query=_query_result(),
        visualizations=_visualization_result(),
        insights=_insight_result(),
    )

    assert tuple(AnalyticalPresentationResult.model_fields) == (
        "presentation_version",
        "presentation_status",
        "source_row_count",
        "query",
        "visualizations",
        "insights",
    )

    with pytest.raises(ValidationError):
        result.source_row_count = 6

    with pytest.raises(ValidationError):
        AnalyticalPresentationResult.model_validate(
            {
                **result.model_dump(),
                "raw_sql": "SELECT 1",
            }
        )


def test_factory_builds_presentation_service() -> None:
    harness = _configured_service()

    service = create_presentation_service(
        pipeline=harness.pipeline,
        executor=harness.executor,
        analytics_engine=harness.analytics_engine,
        visualization_engine=harness.visualization_engine,
        insight_engine=harness.insight_engine,
    )

    assert isinstance(service, AnalyticalPresentationService)


def test_service_projects_only_safe_query_data() -> None:
    harness = _configured_service()

    result = harness.service.generate("Show approved revenue by region")

    assert result.query.validated_sql == (
        "SELECT region, approved_revenue FROM retail.approved_revenue"
    )
    assert result.query.columns == harness.execution.columns
    assert result.query.rows == harness.execution.rows
    assert result.query.row_count == harness.execution.row_count
    assert tuple(PresentationQueryResult.model_fields) == (
        "validated_sql",
        "columns",
        "rows",
        "row_count",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "validated_sql": "   ",
            "columns": ("region",),
            "rows": (("North",),),
            "row_count": 1,
        },
        {
            "validated_sql": "SELECT region",
            "columns": (),
            "rows": (),
            "row_count": 0,
        },
        {
            "validated_sql": "SELECT region, region",
            "columns": ("region", "region"),
            "rows": (("North", "North"),),
            "row_count": 1,
        },
        {
            "validated_sql": "SELECT region",
            "columns": ("region",),
            "rows": (("North",),),
            "row_count": 2,
        },
        {
            "validated_sql": "SELECT region, value",
            "columns": ("region", "value"),
            "rows": (("North",),),
            "row_count": 1,
        },
        {
            "validated_sql": "SELECT region",
            "columns": ("region",),
            "rows": (("North",),),
            "row_count": 1,
            "internal_context": "private",
        },
    ],
)
def test_query_projection_rejects_invalid_payload(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        PresentationQueryResult.model_validate(payload)


def test_service_rejects_non_read_only_execution() -> None:
    harness = _configured_service()
    harness.executor.execute.side_effect = None
    harness.executor.execute.return_value = cast(
        QueryExecutionResult,
        SimpleNamespace(
            transaction_read_only=False,
            columns=("region",),
            rows=(("North",),),
            row_count=1,
        ),
    )

    with pytest.raises(
        PresentationInputError,
        match="requires read-only query execution",
    ):
        harness.service.generate("Show approved revenue by region")

    harness.analytics_engine.analyze.assert_not_called()
    harness.visualization_engine.specify.assert_not_called()
    harness.insight_engine.generate.assert_not_called()


def test_result_requires_matching_query_row_count() -> None:
    query = PresentationQueryResult(
        validated_sql="SELECT region",
        columns=("region",),
        rows=(
            ("North",),
            ("South",),
            ("East",),
            ("West",),
            ("Central",),
            ("Other",),
        ),
        row_count=6,
    )

    with pytest.raises(
        ValidationError,
        match="does not match query",
    ):
        AnalyticalPresentationResult(
            source_row_count=5,
            query=query,
            visualizations=_visualization_result(),
            insights=_insight_result(),
        )


def test_query_projection_serialization_excludes_internal_data() -> None:
    query = _query_result()

    with pytest.raises(ValidationError):
        query.row_count = 6

    payload = query.model_dump(mode="json")

    assert tuple(payload) == (
        "validated_sql",
        "columns",
        "rows",
        "row_count",
    )
    assert "internal_context" not in payload
    assert "internal_column_metadata" not in payload
    assert "provider" not in payload
    assert "model" not in payload
    assert "usage" not in payload
    assert "explanation" not in payload
    assert "transaction_read_only" not in payload
    assert "statement_timeout_ms" not in payload
    assert "query_timeout_seconds" not in payload
    assert "execution_time_ms" not in payload


def test_analytics_context_projection_excludes_nonprojected_filter_dimension() -> None:
    from backend.app.schemas.query_execution import QueryExecutionResult
    from backend.app.schemas.semantic import SemanticDimension
    from backend.app.schemas.semantic_context import CompactGroundingContext
    from backend.app.services.presentation_service import (
        _project_analytics_context,
    )

    filter_dimension = SemanticDimension.model_construct(
        name="payment_status",
    )
    projected_dimension = SemanticDimension.model_construct(
        name="region",
    )
    context = CompactGroundingContext.model_construct(
        dimensions=(
            filter_dimension,
            projected_dimension,
        ),
    )
    execution = QueryExecutionResult.model_construct(
        columns=(
            "REGION",
            "approved_revenue",
        ),
    )

    projected_context = _project_analytics_context(
        context,
        execution,
    )

    assert projected_context is not context
    assert tuple(dimension.name for dimension in projected_context.dimensions) == ("region",)
    assert tuple(dimension.name for dimension in context.dimensions) == (
        "payment_status",
        "region",
    )
