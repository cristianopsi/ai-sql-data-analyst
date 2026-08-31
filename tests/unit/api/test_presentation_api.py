"""Tests for the combined analytical presentation HTTP API."""

from decimal import Decimal
from typing import cast
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import create_app
from backend.app.schemas.insights import (
    GroundedInsightClaim,
    GroundedInsightResult,
    InsightEvidenceReference,
)
from backend.app.schemas.llm import LLMTokenUsage
from backend.app.schemas.presentation import (
    AnalyticalPresentationResult,
    PresentationQueryResult,
)
from backend.app.schemas.visualization import (
    DeterministicVisualizationResult,
    KPIVisualizationSpec,
    visualization_specification_id,
)
from backend.app.services.analytics_engine import (
    DeterministicAnalyticsEngine,
)
from backend.app.services.insight_engine import GroundedInsightEngine
from backend.app.services.llm_provider import (
    DeterministicMockLLMProvider,
    LLMProvider,
)
from backend.app.services.presentation_service import (
    AnalyticalPresentationService,
    PresentationServiceError,
)
from backend.app.services.query_executor import (
    QueryExecutionUnavailableError,
    QueryExecutor,
)
from backend.app.services.question_grounding import QuestionGroundingError
from backend.app.services.sql_generation import SQLGenerationPipeline
from backend.app.services.visualization_engine import (
    DeterministicVisualizationEngine,
)
from tests.unit.core.test_visualization_lifecycle import (
    StubDatabasePools,
)


def _presentation_result() -> AnalyticalPresentationResult:
    visualizations = DeterministicVisualizationResult(
        analytics_version="1",
        execution_version="1",
        semantic_version="1",
        catalog_version="1",
        source_row_count=1,
        specifications=(
            KPIVisualizationSpec.model_construct(
                spec_id=visualization_specification_id(
                    "kpi",
                    "approved_revenue",
                ),
                chart_type="kpi",
                title="Approved revenue",
                metric_name="approved_revenue",
                unit="brl",
                value_count=1,
                aggregation="sum",
                total=Decimal("100.01"),
                value=Decimal("100.01"),
                average=Decimal("100.01"),
                minimum=Decimal("100.01"),
                maximum=Decimal("100.01"),
            ),
        ),
    )
    insights = GroundedInsightResult(
        analytics_version="1",
        visualization_version="1",
        execution_version="1",
        semantic_version="1",
        catalog_version="1",
        source_row_count=1,
        provider="mock",
        model="presentation-api-model",
        usage=LLMTokenUsage(
            input_tokens=10,
            output_tokens=5,
        ),
        summary="Approved revenue is available.",
        claims=(
            GroundedInsightClaim(
                claim_id="claim-000000000000000000000001",
                text="Approved revenue is available.",
                evidence=(
                    InsightEvidenceReference(
                        evidence_type="metric_summary",
                        metric_name="approved_revenue",
                    ),
                ),
            ),
        ),
    )

    return AnalyticalPresentationResult(
        source_row_count=1,
        query=PresentationQueryResult(
            validated_sql="SELECT approved_revenue FROM retail.orders",
            columns=("approved_revenue",),
            rows=(("100.01",),),
            row_count=1,
        ),
        visualizations=visualizations,
        insights=insights,
    )


def _configured_application(
    service: AnalyticalPresentationService,
):
    pools = StubDatabasePools()
    provider = DeterministicMockLLMProvider(
        model_name="presentation-api-model",
    )

    def pool_factory(settings: Settings) -> StubDatabasePools:
        del settings
        return pools

    def provider_factory(settings: Settings) -> LLMProvider:
        del settings
        return provider

    def presentation_service_factory(
        pipeline: SQLGenerationPipeline,
        executor: QueryExecutor,
        analytics_engine: DeterministicAnalyticsEngine,
        visualization_engine: DeterministicVisualizationEngine,
        insight_engine: GroundedInsightEngine,
    ) -> AnalyticalPresentationService:
        del pipeline
        del executor
        del analytics_engine
        del visualization_engine
        del insight_engine
        return service

    application = create_app(
        settings=Settings(
            llm_provider="mock",
            llm_model="presentation-api-model",
        ),
        pool_factory=pool_factory,
        llm_provider_factory=provider_factory,
        presentation_service_factory=presentation_service_factory,
    )
    return application


def _service() -> AnalyticalPresentationService:
    service = Mock(spec=AnalyticalPresentationService)
    service.generate.return_value = _presentation_result()
    return cast(AnalyticalPresentationService, service)


def test_openapi_documents_combined_presentation_endpoint() -> None:
    operation = create_app().openapi()["paths"]["/api/v1/presentations/generate"]["post"]

    assert set(operation["responses"]) == {"200", "422", "503"}
    assert operation["requestBody"]["required"] is True


def test_generate_presentation_returns_safe_consolidated_result() -> None:
    service = _service()
    application = _configured_application(service)

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/presentations/generate",
            json={"question": "Show approved revenue"},
        )

    assert response.status_code == 200
    result = AnalyticalPresentationResult.model_validate_json(response.content)
    assert result == _presentation_result()
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-presentation-version"] == "1"
    assert response.headers["x-insight-version"] == "1"
    assert response.headers["x-visualization-version"] == "1"
    assert response.headers["x-analytics-version"] == "1"
    assert response.headers["x-execution-version"] == "1"
    service.generate.assert_called_once_with("Show approved revenue")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {
            "question": "Show approved revenue",
            "validated_sql": "SELECT * FROM retail.orders",
        },
    ],
)
def test_invalid_payload_is_sanitized_before_service(
    payload: dict[str, object],
) -> None:
    service = _service()
    application = _configured_application(service)

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/presentations/generate",
            json=payload,
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "Presentation request is invalid"}
    assert response.headers["cache-control"] == "no-store"
    service.generate.assert_not_called()


def test_unavailable_managed_service_returns_sanitized_503() -> None:
    service = _service()
    application = _configured_application(service)

    with TestClient(application) as client:
        del application.state.presentation_service
        response = client.post(
            "/api/v1/presentations/generate",
            json={"question": "Show approved revenue"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Presentation service is unavailable"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("error", "expected_detail"),
    [
        (
            QuestionGroundingError("invalid question"),
            "Presentation request is invalid",
        ),
        (
            PresentationServiceError("controlled failure"),
            "Presentation could not be produced safely",
        ),
    ],
)
def test_controlled_errors_are_sanitized(
    error: Exception,
    expected_detail: str,
) -> None:
    service = _service()
    service.generate.side_effect = error
    application = _configured_application(service)

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/presentations/generate",
            json={"question": "Show approved revenue"},
        )

    assert response.status_code == 422
    assert response.json() == {"detail": expected_detail}
    assert response.headers["cache-control"] == "no-store"


def test_unavailable_dependency_error_returns_503() -> None:
    service = _service()
    service.generate.side_effect = QueryExecutionUnavailableError("database unavailable")
    application = _configured_application(service)

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/presentations/generate",
            json={"question": "Show approved revenue"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Presentation service is unavailable"}
    assert response.headers["cache-control"] == "no-store"


def test_unexpected_error_is_sanitized() -> None:
    service = _service()
    service.generate.side_effect = RuntimeError("private failure")
    application = _configured_application(service)

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/presentations/generate",
            json={"question": "Show approved revenue"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Presentation service is unavailable"}
    assert "private failure" not in response.text
    assert response.headers["cache-control"] == "no-store"
