"""Tests for managed analytical presentation service lifecycle."""

from typing import cast
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import create_app
from backend.app.services.analytics_engine import (
    DeterministicAnalyticsEngine,
)
from backend.app.services.insight_engine import GroundedInsightEngine
from backend.app.services.llm_provider import (
    DeterministicMockLLMProvider,
    LLMProvider,
)
from backend.app.services.presentation_artifact_service import (
    PresentationArtifactService,
)
from backend.app.services.presentation_service import (
    AnalyticalPresentationService,
)
from backend.app.services.query_executor import QueryExecutor
from backend.app.services.sql_generation import SQLGenerationPipeline
from backend.app.services.visualization_engine import (
    DeterministicVisualizationEngine,
)
from tests.unit.core.test_visualization_lifecycle import (
    StubDatabasePools,
)

type PresentationDependencies = tuple[
    SQLGenerationPipeline,
    QueryExecutor,
    DeterministicAnalyticsEngine,
    DeterministicVisualizationEngine,
    GroundedInsightEngine,
]


def test_lifespan_publishes_presentation_service() -> None:
    pools = StubDatabasePools()
    provider = DeterministicMockLLMProvider(
        model_name="presentation-lifespan-model",
    )
    service = cast(
        AnalyticalPresentationService,
        Mock(spec=AnalyticalPresentationService),
    )
    factory_calls = 0
    captured_dependencies: PresentationDependencies | None = None

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
        nonlocal factory_calls
        nonlocal captured_dependencies

        factory_calls += 1
        captured_dependencies = (
            pipeline,
            executor,
            analytics_engine,
            visualization_engine,
            insight_engine,
        )
        return service

    application = create_app(
        settings=Settings(
            llm_provider="mock",
            llm_model="presentation-lifespan-model",
        ),
        pool_factory=pool_factory,
        llm_provider_factory=provider_factory,
        presentation_service_factory=presentation_service_factory,
    )

    assert not hasattr(application.state, "presentation_service")
    assert not hasattr(
        application.state,
        "presentation_artifact_service",
    )

    with TestClient(application):
        assert application.state.presentation_service is service
        assert isinstance(
            application.state.presentation_artifact_service,
            PresentationArtifactService,
        )
        assert factory_calls == 1
        assert captured_dependencies == (
            application.state.sql_generation_pipeline,
            application.state.query_executor,
            application.state.analytics_engine,
            application.state.visualization_engine,
            application.state.insight_engine,
        )
        assert provider.generation_count == 0
        assert pools.opened is True
        assert application.state.database_ready is True

    assert provider.is_closed is True
    assert pools.closed is True
    assert application.state.database_ready is False


def test_presentation_service_factory_failure_closes_resources() -> None:
    pools = StubDatabasePools()
    provider = DeterministicMockLLMProvider(
        model_name="presentation-lifespan-model",
    )
    factory_calls = 0

    def pool_factory(settings: Settings) -> StubDatabasePools:
        del settings
        return pools

    def provider_factory(settings: Settings) -> LLMProvider:
        del settings
        return provider

    def failing_presentation_service_factory(
        pipeline: SQLGenerationPipeline,
        executor: QueryExecutor,
        analytics_engine: DeterministicAnalyticsEngine,
        visualization_engine: DeterministicVisualizationEngine,
        insight_engine: GroundedInsightEngine,
    ) -> AnalyticalPresentationService:
        nonlocal factory_calls

        del pipeline
        del executor
        del analytics_engine
        del visualization_engine
        del insight_engine

        factory_calls += 1
        raise RuntimeError("Presentation service construction failed")

    application = create_app(
        pool_factory=pool_factory,
        llm_provider_factory=provider_factory,
        presentation_service_factory=(failing_presentation_service_factory),
    )

    with (
        pytest.raises(
            RuntimeError,
            match="Presentation service construction failed",
        ),
        TestClient(application),
    ):
        pass

    assert factory_calls == 1
    assert provider.generation_count == 0
    assert provider.is_closed is True
    assert pools.opened is False
    assert application.state.database_ready is False
