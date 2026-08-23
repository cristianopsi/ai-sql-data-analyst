import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import create_app
from backend.app.schemas.llm import (
    LLMGenerationRequest,
    LLMMessage,
)
from backend.app.services.llm_provider import (
    DeterministicMockLLMProvider,
    LLMProvider,
    LLMProviderConfigurationError,
)
from backend.app.services.openai_compatible_provider import (
    OpenAICompatibleLLMProvider,
)


class StubDatabasePools:
    def __init__(self) -> None:
        self.opened = False
        self.closed = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True


def test_lifespan_publishes_llm_provider() -> None:
    pools = StubDatabasePools()
    provider = DeterministicMockLLMProvider(
        model_name="lifespan-model",
    )
    captured_settings: list[Settings] = []

    def pool_factory(
        settings: Settings,
    ) -> StubDatabasePools:
        del settings

        return pools

    def provider_factory(
        settings: Settings,
    ) -> LLMProvider:
        captured_settings.append(settings)

        return provider

    application = create_app(
        pool_factory=pool_factory,
        llm_provider_factory=provider_factory,
    )

    with TestClient(application):
        assert application.state.llm_provider is provider
        assert pools.opened is True
        assert pools.closed is False
        assert captured_settings == [application.state.settings]

        response = provider.generate(
            LLMGenerationRequest(
                messages=(
                    LLMMessage(
                        role="user",
                        content=("Use only supplied context."),
                    ),
                ),
            )
        )

        assert response.provider == "mock"
        assert response.model == ("lifespan-model")

    assert pools.closed is True
    assert provider.is_closed is True


def test_pool_factory_failure_closes_provider() -> None:
    provider = DeterministicMockLLMProvider(
        model_name="lifespan-model",
    )

    def failing_pool_factory(
        settings: Settings,
    ) -> StubDatabasePools:
        del settings

        raise RuntimeError("Database pool construction failed")

    def provider_factory(
        settings: Settings,
    ) -> LLMProvider:
        del settings

        return provider

    application = create_app(
        pool_factory=failing_pool_factory,
        llm_provider_factory=provider_factory,
    )

    with (
        pytest.raises(
            RuntimeError,
            match="pool construction failed",
        ),
        TestClient(application),
    ):
        pytest.fail("Application started unexpectedly")

    assert provider.is_closed is True
    assert application.state.database_ready is False


def test_invalid_llm_provider_prevents_pool_startup() -> None:
    pool_factory_called = False

    def pool_factory(
        settings: Settings,
    ) -> StubDatabasePools:
        nonlocal pool_factory_called
        del settings

        pool_factory_called = True

        return StubDatabasePools()

    def failing_provider_factory(
        settings: Settings,
    ) -> LLMProvider:
        del settings

        raise LLMProviderConfigurationError("Configured provider is unavailable")

    application = create_app(
        pool_factory=pool_factory,
        llm_provider_factory=(failing_provider_factory),
    )

    with (
        pytest.raises(
            LLMProviderConfigurationError,
            match="provider is unavailable",
        ),
        TestClient(application),
    ):
        pytest.fail("Application started unexpectedly")

    assert application.state.database_ready is False
    assert pool_factory_called is False


def test_external_provider_lifespan_without_generation() -> None:
    pools = StubDatabasePools()

    def pool_factory(
        settings: Settings,
    ) -> StubDatabasePools:
        del settings

        return pools

    application = create_app(
        settings=Settings(
            llm_provider="openai",
            llm_model="controlled-model",
            llm_api_key="not-a-real-key",
        ),
        pool_factory=pool_factory,
    )

    provider: OpenAICompatibleLLMProvider | None = None

    with TestClient(application):
        current_provider = application.state.llm_provider

        assert isinstance(
            current_provider,
            OpenAICompatibleLLMProvider,
        )

        provider = current_provider

        assert provider.provider_name == "openai"
        assert provider.is_closed is False
        assert pools.opened is True

    assert provider is not None
    assert provider.is_closed is True
    assert pools.closed is True
