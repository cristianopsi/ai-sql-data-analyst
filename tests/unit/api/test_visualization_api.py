from collections.abc import (
    Iterator,
)
from contextlib import contextmanager
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.visualization import (
    router,
)
from backend.app.schemas.analytics import (
    AnalyticsMetricSummary,
    AnalyticsRanking,
    AnalyticsRankingItem,
    DeterministicAnalyticsResult,
)
from backend.app.schemas.query_execution import (
    QueryExecutionResult,
)
from backend.app.schemas.semantic_context import (
    CompactGroundingContext,
)
from backend.app.schemas.sql_generation import (
    SQLGenerationResult,
)
from backend.app.schemas.visualization import (
    DeterministicVisualizationResult,
)
from backend.app.services.analytics_engine import (
    AnalyticsInputError,
)
from backend.app.services.grounding_context import (
    GroundingContextError,
)
from backend.app.services.query_executor import (
    QueryExecutionSecurityError,
    QueryExecutionUnavailableError,
)
from backend.app.services.sql_generation import (
    SQLGenerationExhaustedError,
)
from backend.app.services.text_to_sql import (
    TextToSQLUnavailableError,
)
from backend.app.services.visualization_engine import (
    DeterministicVisualizationEngine,
    VisualizationInputError,
)


def build_context() -> CompactGroundingContext:
    return CompactGroundingContext(
        semantic_version="1",
        catalog_version="1",
        grounding_status="grounded",
        normalized_question="receita aprovada por região",
    )


def build_generation_result(
    *,
    include_context: bool = True,
) -> SQLGenerationResult:
    return SQLGenerationResult.model_construct(
        generation_version="1",
        validated_sql=None,
        internal_context=(build_context() if include_context else None),
        generation_attempts=1,
        repair_attempts=0,
    )


def build_execution_result() -> QueryExecutionResult:
    return QueryExecutionResult.model_construct(
        execution_version="1",
    )


def build_analytics_result() -> DeterministicAnalyticsResult:
    return DeterministicAnalyticsResult(
        execution_version="1",
        semantic_version="1",
        catalog_version="1",
        source_row_count=2,
        metric_summaries=(
            AnalyticsMetricSummary(
                metric_name="approved_revenue",
                unit="brl",
                value_count=2,
                total=Decimal("300.0000"),
                average=Decimal("150.0000"),
                minimum=Decimal("100.0000"),
                maximum=Decimal("200.0000"),
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
                        value=Decimal("200.0000"),
                        share_percent=Decimal("66.6667"),
                    ),
                    AnalyticsRankingItem(
                        rank=2,
                        dimension_value="North",
                        value=Decimal("100.0000"),
                        share_percent=Decimal("33.3333"),
                    ),
                ),
            ),
        ),
        series=(),
    )


class StubGenerationPipeline:
    def __init__(
        self,
        generation: SQLGenerationResult,
        events: list[str],
        *,
        error: Exception | None = None,
    ) -> None:
        self.generation = generation
        self.events = events
        self.error = error
        self.questions: list[str] = []

    def generate(
        self,
        question: str,
    ) -> SQLGenerationResult:
        self.events.append("generate")
        self.questions.append(question)

        if self.error is not None:
            raise self.error

        return self.generation


class StubQueryExecutor:
    def __init__(
        self,
        execution: QueryExecutionResult,
        events: list[str],
        *,
        error: Exception | None = None,
    ) -> None:
        self.execution = execution
        self.events = events
        self.error = error
        self.generations: list[SQLGenerationResult] = []

    def execute(
        self,
        generation: SQLGenerationResult,
    ) -> QueryExecutionResult:
        self.events.append("execute")
        self.generations.append(generation)

        if self.error is not None:
            raise self.error

        return self.execution


class StubAnalyticsEngine:
    def __init__(
        self,
        analytics: DeterministicAnalyticsResult,
        events: list[str],
        *,
        error: Exception | None = None,
    ) -> None:
        self.analytics = analytics
        self.events = events
        self.error = error
        self.calls: list[
            tuple[
                QueryExecutionResult,
                CompactGroundingContext,
            ]
        ] = []

    def analyze(
        self,
        execution: QueryExecutionResult,
        context: CompactGroundingContext,
    ) -> DeterministicAnalyticsResult:
        self.events.append("analyze")
        self.calls.append(
            (
                execution,
                context,
            )
        )

        if self.error is not None:
            raise self.error

        return self.analytics


class StubVisualizationEngine:
    def __init__(
        self,
        result: DeterministicVisualizationResult,
        events: list[str],
        *,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.events = events
        self.error = error
        self.calls: list[DeterministicAnalyticsResult] = []

    def specify(
        self,
        analytics: DeterministicAnalyticsResult,
    ) -> DeterministicVisualizationResult:
        self.events.append("specify")
        self.calls.append(analytics)

        if self.error is not None:
            raise self.error

        return self.result


def build_stubs(
    *,
    generation_error: Exception | None = None,
    execution_error: Exception | None = None,
    analytics_error: Exception | None = None,
    visualization_error: Exception | None = None,
    include_context: bool = True,
) -> tuple[
    list[str],
    StubGenerationPipeline,
    StubQueryExecutor,
    StubAnalyticsEngine,
    StubVisualizationEngine,
]:
    events: list[str] = []
    generation = build_generation_result(
        include_context=include_context,
    )
    execution = build_execution_result()
    analytics = build_analytics_result()
    visualization = DeterministicVisualizationEngine().specify(analytics)

    return (
        events,
        StubGenerationPipeline(
            generation,
            events,
            error=generation_error,
        ),
        StubQueryExecutor(
            execution,
            events,
            error=execution_error,
        ),
        StubAnalyticsEngine(
            analytics,
            events,
            error=analytics_error,
        ),
        StubVisualizationEngine(
            visualization,
            events,
            error=visualization_error,
        ),
    )


@contextmanager
def visualization_client(
    pipeline: StubGenerationPipeline,
    executor: StubQueryExecutor,
    analytics_engine: StubAnalyticsEngine,
    visualization_engine: StubVisualizationEngine,
    *,
    pipeline_present: bool = True,
    executor_present: bool = True,
    analytics_present: bool = True,
    visualization_present: bool = True,
    database_ready: bool = True,
) -> Iterator[TestClient]:
    application = FastAPI()
    application.include_router(router)

    if pipeline_present:
        application.state.sql_generation_pipeline = pipeline

    if executor_present:
        application.state.query_executor = executor

    if analytics_present:
        application.state.analytics_engine = analytics_engine

    if visualization_present:
        application.state.visualization_engine = visualization_engine

    application.state.database_ready = database_ready

    with TestClient(
        application,
        raise_server_exceptions=False,
    ) as client:
        yield client


def test_specify_endpoint_runs_controlled_pipeline_in_order() -> None:
    (
        events,
        pipeline,
        executor,
        analytics_engine,
        visualization_engine,
    ) = build_stubs()

    with visualization_client(
        pipeline,
        executor,
        analytics_engine,
        visualization_engine,
    ) as client:
        response = client.post(
            "/api/v1/visualizations/specify",
            json={
                "question": "Receita aprovada por região",
            },
        )

    assert response.status_code == 200
    assert events == [
        "generate",
        "execute",
        "analyze",
        "specify",
    ]
    assert pipeline.questions == [
        "Receita aprovada por região",
    ]
    assert executor.generations == [
        pipeline.generation,
    ]
    assert analytics_engine.calls == [
        (
            executor.execution,
            pipeline.generation.internal_context,
        ),
    ]
    assert visualization_engine.calls == [
        analytics_engine.analytics,
    ]

    body = response.json()

    assert body["visualization_status"] == "specified"
    assert body["deterministic"] is True
    assert body["source_row_count"] == 2
    assert tuple(specification["chart_type"] for specification in body["specifications"]) == (
        "kpi",
        "table",
        "bar",
    )
    assert "sql" not in body
    assert "rows" not in body
    assert "internal_context" not in body
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-visualization-version"] == "1"
    assert response.headers["x-analytics-version"] == "1"
    assert response.headers["x-execution-version"] == "1"
    assert response.headers["x-semantic-version"] == "1"
    assert response.headers["x-catalog-version"] == "1"


def test_invalid_visualization_request_is_sanitized() -> None:
    (
        events,
        pipeline,
        executor,
        analytics_engine,
        visualization_engine,
    ) = build_stubs()

    with visualization_client(
        pipeline,
        executor,
        analytics_engine,
        visualization_engine,
    ) as client:
        response = client.post(
            "/api/v1/visualizations/specify",
            json={
                "question": "   ",
                "sql": "SELECT 1",
            },
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Question is invalid",
    }
    assert response.headers["cache-control"] == "no-store"
    assert events == []


def test_missing_internal_context_fails_closed() -> None:
    (
        events,
        pipeline,
        executor,
        analytics_engine,
        visualization_engine,
    ) = build_stubs(
        include_context=False,
    )

    with visualization_client(
        pipeline,
        executor,
        analytics_engine,
        visualization_engine,
    ) as client:
        response = client.post(
            "/api/v1/visualizations/specify",
            json={
                "question": "Receita aprovada",
            },
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Visualization could not be produced safely",
    }
    assert events == ["generate"]


def test_grounding_failure_returns_sanitized_422() -> None:
    (
        events,
        pipeline,
        executor,
        analytics_engine,
        visualization_engine,
    ) = build_stubs(
        generation_error=GroundingContextError("Sensitive grounding detail"),
    )

    with visualization_client(
        pipeline,
        executor,
        analytics_engine,
        visualization_engine,
    ) as client:
        response = client.post(
            "/api/v1/visualizations/specify",
            json={
                "question": "Pergunta inválida",
            },
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Question is invalid",
    }
    assert events == ["generate"]


@pytest.mark.parametrize(
    (
        "stage",
        "controlled_error",
        "expected_events",
    ),
    (
        (
            "generation",
            SQLGenerationExhaustedError("Sensitive generation detail"),
            ["generate"],
        ),
        (
            "execution",
            QueryExecutionSecurityError("Sensitive execution detail"),
            [
                "generate",
                "execute",
            ],
        ),
        (
            "analytics",
            AnalyticsInputError("Sensitive analytics detail"),
            [
                "generate",
                "execute",
                "analyze",
            ],
        ),
        (
            "visualization",
            VisualizationInputError("Sensitive visualization detail"),
            [
                "generate",
                "execute",
                "analyze",
                "specify",
            ],
        ),
    ),
)
def test_controlled_failure_returns_sanitized_422(
    stage: str,
    controlled_error: Exception,
    expected_events: list[str],
) -> None:
    (
        events,
        pipeline,
        executor,
        analytics_engine,
        visualization_engine,
    ) = build_stubs(
        generation_error=(controlled_error if stage == "generation" else None),
        execution_error=(controlled_error if stage == "execution" else None),
        analytics_error=(controlled_error if stage == "analytics" else None),
        visualization_error=(controlled_error if stage == "visualization" else None),
    )

    with visualization_client(
        pipeline,
        executor,
        analytics_engine,
        visualization_engine,
    ) as client:
        response = client.post(
            "/api/v1/visualizations/specify",
            json={
                "question": "Receita aprovada",
            },
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Visualization could not be produced safely",
    }
    assert response.headers["cache-control"] == "no-store"
    assert events == expected_events


@pytest.mark.parametrize(
    (
        "stage",
        "unavailable_error",
        "expected_events",
    ),
    (
        (
            "generation",
            TextToSQLUnavailableError("Sensitive provider detail"),
            ["generate"],
        ),
        (
            "execution",
            QueryExecutionUnavailableError("Sensitive database detail"),
            [
                "generate",
                "execute",
            ],
        ),
    ),
)
def test_unavailable_failure_returns_sanitized_503(
    stage: str,
    unavailable_error: Exception,
    expected_events: list[str],
) -> None:
    (
        events,
        pipeline,
        executor,
        analytics_engine,
        visualization_engine,
    ) = build_stubs(
        generation_error=(unavailable_error if stage == "generation" else None),
        execution_error=(unavailable_error if stage == "execution" else None),
    )

    with visualization_client(
        pipeline,
        executor,
        analytics_engine,
        visualization_engine,
    ) as client:
        response = client.post(
            "/api/v1/visualizations/specify",
            json={
                "question": "Receita aprovada",
            },
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Visualization service is unavailable",
    }
    assert response.headers["cache-control"] == "no-store"
    assert events == expected_events


def test_unexpected_runtime_failure_returns_sanitized_503() -> None:
    sensitive_detail = "Sensitive unexpected runtime detail"
    (
        events,
        pipeline,
        executor,
        analytics_engine,
        visualization_engine,
    ) = build_stubs(
        visualization_error=RuntimeError(sensitive_detail),
    )

    with visualization_client(
        pipeline,
        executor,
        analytics_engine,
        visualization_engine,
    ) as client:
        response = client.post(
            "/api/v1/visualizations/specify",
            json={
                "question": "Receita aprovada",
            },
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Visualization service is unavailable",
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith(
        "application/json",
    )
    assert sensitive_detail not in response.text
    assert events == [
        "generate",
        "execute",
        "analyze",
        "specify",
    ]


@pytest.mark.parametrize(
    (
        "pipeline_present",
        "executor_present",
        "analytics_present",
        "visualization_present",
        "database_ready",
    ),
    (
        (
            False,
            True,
            True,
            True,
            True,
        ),
        (
            True,
            False,
            True,
            True,
            True,
        ),
        (
            True,
            True,
            False,
            True,
            True,
        ),
        (
            True,
            True,
            True,
            False,
            True,
        ),
        (
            True,
            True,
            True,
            True,
            False,
        ),
    ),
)
def test_missing_runtime_dependency_returns_503(
    pipeline_present: bool,
    executor_present: bool,
    analytics_present: bool,
    visualization_present: bool,
    database_ready: bool,
) -> None:
    (
        events,
        pipeline,
        executor,
        analytics_engine,
        visualization_engine,
    ) = build_stubs()

    with visualization_client(
        pipeline,
        executor,
        analytics_engine,
        visualization_engine,
        pipeline_present=pipeline_present,
        executor_present=executor_present,
        analytics_present=analytics_present,
        visualization_present=visualization_present,
        database_ready=database_ready,
    ) as client:
        response = client.post(
            "/api/v1/visualizations/specify",
            json={
                "question": "Receita aprovada",
            },
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Visualization service is unavailable",
    }
    assert response.headers["cache-control"] == "no-store"
    assert events == []


def test_openapi_documents_visualization_endpoint() -> None:
    (
        _,
        pipeline,
        executor,
        analytics_engine,
        visualization_engine,
    ) = build_stubs()

    with visualization_client(
        pipeline,
        executor,
        analytics_engine,
        visualization_engine,
    ) as client:
        document = client.get("/openapi.json").json()

    operation = document["paths"]["/api/v1/visualizations/specify"]["post"]

    assert operation["summary"] == (
        "Generate deterministic visualization specifications for a governed question"
    )
    assert set(operation["responses"]) >= {
        "200",
        "422",
        "503",
    }

    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]

    assert request_schema["$ref"].endswith("/VisualizationRequest")

    schemas = document["components"]["schemas"]
    request_properties = schemas["VisualizationRequest"]["properties"]

    assert tuple(request_properties) == ("question",)
    assert "internal_context" not in str(operation)
    assert "column_metadata" not in str(operation)
