"""Tests for managed grounded insight engine lifecycle."""

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import create_app
from backend.app.services.insight_engine import (
    GroundedInsightEngine,
)
from backend.app.services.llm_provider import (
    DeterministicMockLLMProvider,
    LLMProvider,
)
from tests.unit.core.test_visualization_lifecycle import (
    StubDatabasePools,
)


def test_lifespan_publishes_insight_engine() -> None:
    pools = StubDatabasePools()
    provider = DeterministicMockLLMProvider(
        model_name="insight-lifespan-model",
    )
    engine = GroundedInsightEngine(provider)
    engine_factory_calls = 0

    def pool_factory(
        settings: Settings,
    ) -> StubDatabasePools:
        del settings
        return pools

    def provider_factory(
        settings: Settings,
    ) -> LLMProvider:
        del settings
        return provider

    def insight_engine_factory(
        managed_provider: LLMProvider,
    ) -> GroundedInsightEngine:
        nonlocal engine_factory_calls
        engine_factory_calls += 1
        assert managed_provider is provider
        return engine

    application = create_app(
        settings=Settings(
            llm_provider="mock",
            llm_model="insight-lifespan-model",
        ),
        pool_factory=pool_factory,
        llm_provider_factory=provider_factory,
        insight_engine_factory=insight_engine_factory,
    )

    assert not hasattr(
        application.state,
        "insight_engine",
    )

    with TestClient(application):
        assert application.state.insight_engine is engine
        assert engine_factory_calls == 1
        assert provider.generation_count == 0
        assert pools.opened is True
        assert application.state.database_ready is True

    assert provider.is_closed is True
    assert pools.closed is True
    assert application.state.database_ready is False


def test_insight_engine_factory_failure_closes_provider() -> None:
    pools = StubDatabasePools()
    provider = DeterministicMockLLMProvider(
        model_name="insight-lifespan-model",
    )
    engine_factory_calls = 0

    def pool_factory(
        settings: Settings,
    ) -> StubDatabasePools:
        del settings
        return pools

    def provider_factory(
        settings: Settings,
    ) -> LLMProvider:
        del settings
        return provider

    def failing_insight_engine_factory(
        managed_provider: LLMProvider,
    ) -> GroundedInsightEngine:
        nonlocal engine_factory_calls
        engine_factory_calls += 1
        assert managed_provider is provider
        raise RuntimeError("Insight engine construction failed")

    application = create_app(
        pool_factory=pool_factory,
        llm_provider_factory=provider_factory,
        insight_engine_factory=(failing_insight_engine_factory),
    )

    with (
        pytest.raises(
            RuntimeError,
            match="Insight engine construction failed",
        ),
        TestClient(application),
    ):
        pass

    assert engine_factory_calls == 1
    assert provider.generation_count == 0
    assert provider.is_closed is True
    assert pools.opened is False
    assert application.state.database_ready is False
