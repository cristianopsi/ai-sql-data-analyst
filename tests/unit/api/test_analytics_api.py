from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.analytics import router
from backend.app.schemas.analytics import (
    AnalyticsMetricSummary,
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
from backend.app.schemas.sql_validation import (
    ValidatedSQL,
)
from backend.app.services.analytics_engine import (
    AnalyticsInputError,
)
from backend.app.services.grounding_context import (
    GroundingContextError,
)
from backend.app.services.query_executor import (
    QueryExecutionResultError,
    QueryExecutionUnavailableError,
)
from backend.app.services.sql_generation import (
    SQLGenerationExhaustedError,
)
from backend.app.services.text_to_sql import (
    TextToSQLUnavailableError,
)


def build_context() -> CompactGroundingContext:
    return CompactGroundingContext(
        semantic_version="1",
        catalog_version="1",
        grounding_status="grounded",
        normalized_question="receita aprovada",
    )


def build_generation_result(
    *,
    include_context: bool = True,
) -> SQLGenerationResult:
    validated = ValidatedSQL.model_construct(
        validation_version="1",
        validation_status="validated",
        context_version="1",
        semantic_version="1",
        catalog_version="1",
        sql=("SELECT 100 AS approved_revenue LIMIT 1"),
        row_limit=1,
        referenced_tables=("orders",),
        referenced_columns=("orders.total_amount",),
    )

    return SQLGenerationResult.model_construct(
        generation_version="1",
        validated_sql=validated,
        internal_context=(build_context() if include_context else None),
        generation_attempts=1,
        repair_attempts=0,
    )


def build_execution_result(
    generation: SQLGenerationResult,
) -> QueryExecutionResult:
    return QueryExecutionResult.model_construct(
        execution_version="1",
        execution_status="executed",
        generation=generation,
        columns=("approved_revenue",),
        rows=(("100.0000",),),
        row_count=1,
        statement_timeout_ms=8000,
        query_timeout_seconds=10.0,
        execution_time_ms=1.0,
        transaction_read_only=True,
    )


def build_analytics_result() -> DeterministicAnalyticsResult:
    return DeterministicAnalyticsResult(
        execution_version="1",
        semantic_version="1",
        catalog_version="1",
        source_row_count=1,
        metric_summaries=(
            AnalyticsMetricSummary(
                metric_name="approved_revenue",
                unit="brl",
                value_count=1,
                total=Decimal("100.0000"),
                average=Decimal("100.0000"),
                minimum=Decimal("100.0000"),
                maximum=Decimal("100.0000"),
            ),
        ),
    )


class StubGenerationPipeline:
    def __init__(
        self,
        generation: SQLGenerationResult,
        *,
        error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.generation = generation
        self.error = error
        self.events = events
        self.questions: list[str] = []

    def generate(
        self,
        question: str,
    ) -> SQLGenerationResult:
        self.questions.append(question)

        if self.events is not None:
            self.events.append("generate")

        if self.error is not None:
            raise self.error

        return self.generation


class StubQueryExecutor:
    def __init__(
        self,
        result: QueryExecutionResult,
        *,
        error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.events = events
        self.generations: list[SQLGenerationResult] = []

    def execute(
        self,
        generation: SQLGenerationResult,
    ) -> QueryExecutionResult:
        self.generations.append(generation)

        if self.events is not None:
            self.events.append("execute")

        if self.error is not None:
            raise self.error

        return self.result


class StubAnalyticsEngine:
    def __init__(
        self,
        result: DeterministicAnalyticsResult,
        *,
        error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.events = events
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
        self.calls.append(
            (
                execution,
                context,
            )
        )

        if self.events is not None:
            self.events.append("analyze")

        if self.error is not None:
            raise self.error

        return self.result


@contextmanager
def controlled_client(
    pipeline: StubGenerationPipeline | None,
    executor: StubQueryExecutor | None,
    engine: StubAnalyticsEngine | None,
    *,
    database_ready: bool = True,
) -> Iterator[TestClient]:
    application = FastAPI()
    application.include_router(router)
    application.state.database_ready = database_ready

    if pipeline is not None:
        application.state.sql_generation_pipeline = pipeline

    if executor is not None:
        application.state.query_executor = executor

    if engine is not None:
        application.state.analytics_engine = engine

    with TestClient(application) as client:
        yield client


def test_analyze_endpoint_runs_controlled_pipeline_in_order() -> None:
    events: list[str] = []
    generation = build_generation_result()
    execution = build_execution_result(generation)
    analytics = build_analytics_result()
    pipeline = StubGenerationPipeline(
        generation,
        events=events,
    )
    executor = StubQueryExecutor(
        execution,
        events=events,
    )
    engine = StubAnalyticsEngine(
        analytics,
        events=events,
    )

    with controlled_client(
        pipeline,
        executor,
        engine,
    ) as client:
        response = client.post(
            "/api/v1/analytics/analyze",
            json={
                "question": "Receita aprovada",
            },
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-analytics-version"] == "1"
    assert response.headers["x-query-execution-version"] == "1"
    assert response.headers["x-catalog-version"] == "1"
    assert response.headers["x-semantic-version"] == "1"
    assert events == [
        "generate",
        "execute",
        "analyze",
    ]
    assert pipeline.questions == ["Receita aprovada"]
    assert executor.generations == [generation]
    assert len(engine.calls) == 1
    assert engine.calls[0][0] is execution
    assert engine.calls[0][1] is generation.internal_context

    body = response.json()

    assert body["analytics_status"] == "analyzed"
    assert body["deterministic"] is True
    assert body["source_row_count"] == 1
    assert body["metric_summaries"][0]["total"] == "100.0000"


def test_invalid_analytics_request_is_sanitized() -> None:
    generation = build_generation_result()
    pipeline = StubGenerationPipeline(generation)
    executor = StubQueryExecutor(build_execution_result(generation))
    engine = StubAnalyticsEngine(build_analytics_result())

    with controlled_client(
        pipeline,
        executor,
        engine,
    ) as client:
        response = client.post(
            "/api/v1/analytics/analyze",
            json={
                "question": "   ",
                "unexpected": True,
            },
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Question is invalid",
    }
    assert response.headers["cache-control"] == "no-store"
    assert pipeline.questions == []
    assert executor.generations == []
    assert engine.calls == []


def test_missing_internal_context_fails_closed() -> None:
    generation = build_generation_result(
        include_context=False,
    )
    pipeline = StubGenerationPipeline(generation)
    executor = StubQueryExecutor(build_execution_result(generation))
    engine = StubAnalyticsEngine(build_analytics_result())

    with controlled_client(
        pipeline,
        executor,
        engine,
    ) as client:
        response = client.post(
            "/api/v1/analytics/analyze",
            json={"question": "Receita aprovada"},
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Analytics could not be produced safely",
    }
    assert pipeline.questions == ["Receita aprovada"]
    assert executor.generations == []
    assert engine.calls == []


def test_grounding_failure_returns_sanitized_422() -> None:
    sensitive = "grounding-sensitive"
    generation = build_generation_result()
    pipeline = StubGenerationPipeline(
        generation,
        error=GroundingContextError(sensitive),
    )
    executor = StubQueryExecutor(build_execution_result(generation))
    engine = StubAnalyticsEngine(build_analytics_result())

    with controlled_client(
        pipeline,
        executor,
        engine,
    ) as client:
        response = client.post(
            "/api/v1/analytics/analyze",
            json={"question": "Pergunta inválida"},
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Question is invalid",
    }
    assert sensitive not in response.text


@pytest.mark.parametrize(
    (
        "layer",
        "controlled_error",
    ),
    (
        (
            "generation",
            SQLGenerationExhaustedError("generation-sensitive"),
        ),
        (
            "execution",
            QueryExecutionResultError("execution-sensitive"),
        ),
        (
            "analytics",
            AnalyticsInputError("analytics-sensitive"),
        ),
    ),
)
def test_controlled_unsafe_failure_returns_sanitized_422(
    layer: str,
    controlled_error: Exception,
) -> None:
    generation = build_generation_result()
    execution = build_execution_result(generation)
    pipeline = StubGenerationPipeline(
        generation,
        error=(controlled_error if layer == "generation" else None),
    )
    executor = StubQueryExecutor(
        execution,
        error=(controlled_error if layer == "execution" else None),
    )
    engine = StubAnalyticsEngine(
        build_analytics_result(),
        error=(controlled_error if layer == "analytics" else None),
    )

    with controlled_client(
        pipeline,
        executor,
        engine,
    ) as client:
        response = client.post(
            "/api/v1/analytics/analyze",
            json={"question": "Receita aprovada"},
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Analytics could not be produced safely",
    }
    assert str(controlled_error) not in response.text


@pytest.mark.parametrize(
    (
        "layer",
        "unavailable_error",
    ),
    (
        (
            "generation",
            TextToSQLUnavailableError("provider-sensitive"),
        ),
        (
            "execution",
            QueryExecutionUnavailableError("database-sensitive"),
        ),
        (
            "analytics",
            RuntimeError("unexpected-sensitive"),
        ),
    ),
)
def test_unavailable_failure_returns_sanitized_503(
    layer: str,
    unavailable_error: Exception,
) -> None:
    generation = build_generation_result()
    execution = build_execution_result(generation)
    pipeline = StubGenerationPipeline(
        generation,
        error=(unavailable_error if layer == "generation" else None),
    )
    executor = StubQueryExecutor(
        execution,
        error=(unavailable_error if layer == "execution" else None),
    )
    engine = StubAnalyticsEngine(
        build_analytics_result(),
        error=(unavailable_error if layer == "analytics" else None),
    )

    with controlled_client(
        pipeline,
        executor,
        engine,
    ) as client:
        response = client.post(
            "/api/v1/analytics/analyze",
            json={"question": "Receita aprovada"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Analytics service is unavailable",
    }
    assert str(unavailable_error) not in response.text


@pytest.mark.parametrize(
    (
        "pipeline_present",
        "executor_present",
        "engine_present",
        "database_ready",
    ),
    (
        (False, True, True, True),
        (True, False, True, True),
        (True, True, False, True),
        (True, True, True, False),
    ),
)
def test_missing_runtime_dependency_returns_503(
    pipeline_present: bool,
    executor_present: bool,
    engine_present: bool,
    database_ready: bool,
) -> None:
    generation = build_generation_result()
    pipeline = StubGenerationPipeline(generation) if pipeline_present else None
    executor = StubQueryExecutor(build_execution_result(generation)) if executor_present else None
    engine = StubAnalyticsEngine(build_analytics_result()) if engine_present else None

    with controlled_client(
        pipeline,
        executor,
        engine,
        database_ready=database_ready,
    ) as client:
        response = client.post(
            "/api/v1/analytics/analyze",
            json={"question": "Receita aprovada"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Analytics service is unavailable",
    }


def test_openapi_documents_analytics_endpoint() -> None:
    generation = build_generation_result()
    pipeline = StubGenerationPipeline(generation)
    executor = StubQueryExecutor(build_execution_result(generation))
    engine = StubAnalyticsEngine(build_analytics_result())

    with controlled_client(
        pipeline,
        executor,
        engine,
    ) as client:
        document = client.get("/openapi.json").json()

    operation = document["paths"]["/api/v1/analytics/analyze"]["post"]

    assert operation["summary"] == ("Generate, execute, and analyze a governed question")
    assert set(operation["responses"]) >= {
        "200",
        "422",
        "503",
    }

    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]

    assert request_schema["$ref"].endswith("/AnalyticsRequest")
