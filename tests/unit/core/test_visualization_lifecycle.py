import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import create_app
from backend.app.services.llm_provider import (
    DeterministicMockLLMProvider,
    LLMProvider,
)
from backend.app.services.visualization_engine import (
    DeterministicVisualizationEngine,
)


class StubDatabasePools:
    def __init__(self) -> None:
        self.opened = False
        self.closed = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True


def test_lifespan_publishes_visualization_engine() -> None:
    pools = StubDatabasePools()
    provider = DeterministicMockLLMProvider(
        model_name="visualization-lifespan-model",
    )
    engine = DeterministicVisualizationEngine()
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

    def visualization_engine_factory() -> DeterministicVisualizationEngine:
        nonlocal engine_factory_calls
        engine_factory_calls += 1
        return engine

    application = create_app(
        settings=Settings(
            llm_provider="mock",
            llm_model="visualization-lifespan-model",
        ),
        pool_factory=pool_factory,
        llm_provider_factory=provider_factory,
        visualization_engine_factory=visualization_engine_factory,
    )

    with TestClient(application):
        assert application.state.visualization_engine is engine
        assert engine_factory_calls == 1
        assert pools.opened is True
        assert application.state.database_ready is True

    assert provider.is_closed is True
    assert pools.closed is True
    assert application.state.database_ready is False


def test_visualization_engine_factory_failure_closes_provider() -> None:
    pools = StubDatabasePools()
    provider = DeterministicMockLLMProvider(
        model_name="visualization-lifespan-model",
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

    def failing_visualization_engine_factory() -> DeterministicVisualizationEngine:
        nonlocal engine_factory_calls
        engine_factory_calls += 1
        raise RuntimeError("Visualization engine construction failed")

    application = create_app(
        pool_factory=pool_factory,
        llm_provider_factory=provider_factory,
        visualization_engine_factory=failing_visualization_engine_factory,
    )

    with (
        pytest.raises(
            RuntimeError,
            match="Visualization engine construction failed",
        ),
        TestClient(application),
    ):
        pytest.fail("Application started unexpectedly")

    assert engine_factory_calls == 1
    assert provider.is_closed is True
    assert pools.opened is False
    assert pools.closed is False
    assert application.state.database_ready is False
