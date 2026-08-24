"""Tests for the controlled grounded insight API."""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Never
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import insights as insights_api
from backend.app.schemas.insights import GroundedInsightResult
from backend.app.schemas.llm import LLMTokenUsage
from backend.app.services.analytics_engine import (
    AnalyticsEngineError,
    AnalyticsInputError,
    DeterministicAnalyticsEngine,
)
from backend.app.services.grounding_context import GroundingContextError
from backend.app.services.insight_engine import (
    GroundedInsightEngine,
    InsightEngineError,
    InsightInputError,
    InsightProviderResponseError,
)
from backend.app.services.llm_provider import LLMProviderUnavailableError
from backend.app.services.query_executor import (
    QueryExecutionResultError,
    QueryExecutionSecurityError,
    QueryExecutionUnavailableError,
    QueryExecutor,
)
from backend.app.services.question_grounding import QuestionGroundingError
from backend.app.services.sql_generation import (
    SQLGenerationExhaustedError,
    SQLGenerationPipeline,
)
from backend.app.services.text_to_sql import (
    TextToSQLGroundingError,
    TextToSQLResponseError,
    TextToSQLUnavailableError,
)
from backend.app.services.visualization_engine import (
    DeterministicVisualizationEngine,
    VisualizationEngineError,
)

QUESTION = "Mostre a receita aprovada por região"


def _result() -> GroundedInsightResult:
    return GroundedInsightResult.model_construct(
        insight_version=1,
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
        model="insight-api-test-model",
        usage=LLMTokenUsage(
            input_tokens=20,
            output_tokens=10,
        ),
        summary="A análise possui evidências internas governadas.",
        claims=(),
    )


def _configured_application() -> FastAPI:
    application = FastAPI()
    application.include_router(insights_api.router)

    application.state.database_ready = True
    application.state.sql_generation_pipeline = Mock(spec=SQLGenerationPipeline)
    application.state.query_executor = Mock(spec=QueryExecutor)
    application.state.analytics_engine = Mock(spec=DeterministicAnalyticsEngine)
    application.state.visualization_engine = Mock(spec=DeterministicVisualizationEngine)
    application.state.insight_engine = Mock(spec=GroundedInsightEngine)

    return application


def test_generate_helper_runs_controlled_pipeline_in_order() -> None:
    events: list[str] = []
    context = object()
    generation = SimpleNamespace(internal_context=context)
    execution = object()
    analytics = object()
    visualizations = object()
    result = object()

    pipeline = Mock(spec=SQLGenerationPipeline)
    executor = Mock(spec=QueryExecutor)
    analytics_engine = Mock(spec=DeterministicAnalyticsEngine)
    visualization_engine = Mock(spec=DeterministicVisualizationEngine)
    insight_engine = Mock(spec=GroundedInsightEngine)

    def generate(question: str) -> object:
        events.append("generate")
        assert question == QUESTION
        return generation

    def execute(generation_value: object) -> object:
        events.append("execute")
        assert generation_value is generation
        return execution

    def analyze(
        execution_value: object,
        context_value: object,
    ) -> object:
        events.append("analyze")
        assert execution_value is execution
        assert context_value is context
        return analytics

    def specify(analytics_value: object) -> object:
        events.append("specify")
        assert analytics_value is analytics
        return visualizations

    def explain(
        analytics_value: object,
        visualization_value: object,
    ) -> object:
        events.append("explain")
        assert analytics_value is analytics
        assert visualization_value is visualizations
        return result

    pipeline.generate.side_effect = generate
    executor.execute.side_effect = execute
    analytics_engine.analyze.side_effect = analyze
    visualization_engine.specify.side_effect = specify
    insight_engine.generate.side_effect = explain

    actual = insights_api._generate_execute_analyze_specify_explain(
        pipeline,
        executor,
        analytics_engine,
        visualization_engine,
        insight_engine,
        QUESTION,
    )

    assert actual is result
    assert tuple(events) == (
        "generate",
        "execute",
        "analyze",
        "specify",
        "explain",
    )


def test_generate_helper_fails_closed_without_context() -> None:
    pipeline = Mock(spec=SQLGenerationPipeline)
    pipeline.generate.return_value = SimpleNamespace(internal_context=None)

    executor = Mock(spec=QueryExecutor)
    analytics_engine = Mock(spec=DeterministicAnalyticsEngine)
    visualization_engine = Mock(spec=DeterministicVisualizationEngine)
    insight_engine = Mock(spec=GroundedInsightEngine)

    with pytest.raises(
        AnalyticsInputError,
        match="Validated generation context is unavailable",
    ):
        insights_api._generate_execute_analyze_specify_explain(
            pipeline,
            executor,
            analytics_engine,
            visualization_engine,
            insight_engine,
            QUESTION,
        )

    executor.execute.assert_not_called()
    analytics_engine.analyze.assert_not_called()
    visualization_engine.specify.assert_not_called()
    insight_engine.generate.assert_not_called()


def test_generate_endpoint_returns_grounded_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _configured_application()
    expected = _result()

    async def fake_run_in_threadpool(
        function: Callable[..., object],
        *args: object,
    ) -> GroundedInsightResult:
        assert function is insights_api._generate_execute_analyze_specify_explain
        assert args == (
            application.state.sql_generation_pipeline,
            application.state.query_executor,
            application.state.analytics_engine,
            application.state.visualization_engine,
            application.state.insight_engine,
            QUESTION,
        )
        return expected

    monkeypatch.setattr(
        insights_api,
        "run_in_threadpool",
        fake_run_in_threadpool,
    )

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/insights/generate",
            json={"question": QUESTION},
        )

    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode="json")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-insight-version"] == "1"
    assert response.headers["x-visualization-version"] == "1"
    assert response.headers["x-analytics-version"] == "1"
    assert response.headers["x-execution-version"] == "1"
    assert "sql" not in response.json()
    assert "rows" not in response.json()
    assert "internal_context" not in response.json()
    assert "column_metadata" not in response.json()


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"question": ""},
        {
            "question": QUESTION,
            "sql": "SELECT hidden",
        },
    ),
)
def test_invalid_request_is_sanitized(
    payload: dict[str, object],
) -> None:
    application = _configured_application()

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/insights/generate",
            json=payload,
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Question is invalid",
    }
    assert response.headers["cache-control"] == "no-store"
    assert "sql" not in str(response.json()).casefold()


@pytest.mark.parametrize(
    ("state_name", "state_value"),
    (
        ("database_ready", False),
        ("sql_generation_pipeline", None),
        ("query_executor", None),
        ("analytics_engine", None),
        ("visualization_engine", None),
        ("insight_engine", None),
    ),
)
def test_missing_runtime_dependency_returns_503(
    state_name: str,
    state_value: object,
) -> None:
    application = _configured_application()
    setattr(
        application.state,
        state_name,
        state_value,
    )

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/insights/generate",
            json={"question": QUESTION},
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Insight service is unavailable",
    }
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "error_type",
    (
        GroundingContextError,
        QuestionGroundingError,
    ),
)
def test_grounding_failure_returns_sanitized_422(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    application = _configured_application()

    async def fake_run_in_threadpool(
        _function: Callable[..., object],
        *_args: object,
    ) -> Never:
        raise error_type("private grounding detail")

    monkeypatch.setattr(
        insights_api,
        "run_in_threadpool",
        fake_run_in_threadpool,
    )

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/insights/generate",
            json={"question": QUESTION},
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Question is invalid",
    }
    assert response.headers["cache-control"] == "no-store"
    assert "private" not in str(response.json()).casefold()


@pytest.mark.parametrize(
    "error_type",
    (
        SQLGenerationExhaustedError,
        TextToSQLGroundingError,
        TextToSQLResponseError,
        QueryExecutionResultError,
        QueryExecutionSecurityError,
        AnalyticsEngineError,
        VisualizationEngineError,
        InsightEngineError,
        InsightInputError,
        InsightProviderResponseError,
    ),
)
def test_controlled_failure_returns_sanitized_422(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    application = _configured_application()

    async def fake_run_in_threadpool(
        _function: Callable[..., object],
        *_args: object,
    ) -> Never:
        raise error_type("private controlled detail")

    monkeypatch.setattr(
        insights_api,
        "run_in_threadpool",
        fake_run_in_threadpool,
    )

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/insights/generate",
            json={"question": QUESTION},
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Insight could not be produced safely",
    }
    assert response.headers["cache-control"] == "no-store"
    assert "private" not in str(response.json()).casefold()


@pytest.mark.parametrize(
    "error_type",
    (
        TextToSQLUnavailableError,
        QueryExecutionUnavailableError,
        LLMProviderUnavailableError,
    ),
)
def test_unavailable_failure_returns_sanitized_503(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    application = _configured_application()

    async def fake_run_in_threadpool(
        _function: Callable[..., object],
        *_args: object,
    ) -> Never:
        raise error_type("private unavailable detail")

    monkeypatch.setattr(
        insights_api,
        "run_in_threadpool",
        fake_run_in_threadpool,
    )

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/insights/generate",
            json={"question": QUESTION},
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Insight service is unavailable",
    }
    assert response.headers["cache-control"] == "no-store"
    assert "private" not in str(response.json()).casefold()


def test_unexpected_failure_returns_sanitized_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _configured_application()

    async def fake_run_in_threadpool(
        _function: Callable[..., object],
        *_args: object,
    ) -> Never:
        raise RuntimeError("private unexpected detail")

    monkeypatch.setattr(
        insights_api,
        "run_in_threadpool",
        fake_run_in_threadpool,
    )

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/insights/generate",
            json={"question": QUESTION},
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Insight service is unavailable",
    }
    assert response.headers["cache-control"] == "no-store"
    assert "private" not in str(response.json()).casefold()


def test_openapi_documents_grounded_insight_endpoint() -> None:
    application = FastAPI()
    application.include_router(insights_api.router)

    document = application.openapi()
    endpoint = "/api/v1/insights/generate"
    operation = document["paths"][endpoint]["post"]

    assert operation["summary"] == (
        "Generate grounded insights for a governed natural-language question"
    )
    assert {"200", "422", "503"}.issubset(operation["responses"])

    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    request_name = request_schema["$ref"].rsplit("/", 1)[-1]
    schemas = document["components"]["schemas"]
    request_fields = tuple(schemas[request_name]["properties"])

    assert request_name == "GroundedInsightRequest"
    assert request_fields == ("question",)
    assert "sql" not in request_fields
    assert "rows" not in request_fields
    assert "analytics" not in request_fields
    assert "visualizations" not in request_fields
    assert "internal_context" not in str(operation)
    assert "column_metadata" not in str(operation)
