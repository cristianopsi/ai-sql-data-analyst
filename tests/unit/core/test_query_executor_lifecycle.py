import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.db.pools import DatabasePools
from backend.app.main import create_app
from backend.app.services.llm_provider import (
    DeterministicMockLLMProvider,
    LLMProvider,
)
from backend.app.services.query_executor import (
    QueryExecutor,
)


class StubDatabasePools:
    def __init__(self) -> None:
        self.opened = False
        self.closed = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True


def test_lifespan_publishes_query_executor_lazily() -> None:
    pools = StubDatabasePools()
    provider = DeterministicMockLLMProvider(
        model_name="executor-lifespan-model",
    )

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

    application = create_app(
        settings=Settings(
            llm_provider="mock",
            llm_model="executor-lifespan-model",
            statement_timeout_ms=4321,
            query_timeout_seconds=7.5,
        ),
        pool_factory=pool_factory,
        llm_provider_factory=provider_factory,
    )

    with TestClient(application):
        executor = application.state.query_executor

        assert isinstance(
            executor,
            QueryExecutor,
        )
        assert executor.statement_timeout_ms == 4321
        assert executor.query_timeout_seconds == 7.5
        assert pools.opened is True
        assert application.state.database_ready is True

    assert provider.is_closed is True
    assert pools.closed is True
    assert application.state.database_ready is False


def test_query_executor_factory_failure_closes_provider() -> None:
    pools = StubDatabasePools()
    provider = DeterministicMockLLMProvider(
        model_name="executor-lifespan-model",
    )

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

    def failing_executor_factory(
        settings: Settings,
        database_pools: DatabasePools,
    ) -> QueryExecutor:
        del (
            settings,
            database_pools,
        )

        raise RuntimeError("Query executor construction failed")

    application = create_app(
        pool_factory=pool_factory,
        llm_provider_factory=provider_factory,
        query_executor_factory=(failing_executor_factory),
    )

    with (
        pytest.raises(
            RuntimeError,
            match="executor construction failed",
        ),
        TestClient(application),
    ):
        pytest.fail("Application started unexpectedly")

    assert provider.is_closed is True
    assert pools.opened is False
    assert pools.closed is False
    assert application.state.database_ready is False
