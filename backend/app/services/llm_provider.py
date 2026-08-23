from collections.abc import Callable
from threading import RLock
from typing import Protocol

from backend.app.core.config import Settings
from backend.app.schemas.llm import (
    LLMGenerationRequest,
    LLMGenerationResponse,
    LLMTokenUsage,
)

DEFAULT_MOCK_RESPONSE = '{"result":null,"status":"mock"}'

type LLMResponder = Callable[
    [LLMGenerationRequest],
    str,
]


class LLMProviderError(RuntimeError):
    """Base error raised by controlled LLM providers."""


class LLMProviderConfigurationError(LLMProviderError):
    """Raised when a configured adapter is unavailable."""


class LLMProviderUnavailableError(LLMProviderError):
    """Raised when provider generation cannot complete."""


class LLMProviderResponseError(LLMProviderError):
    """Raised when a provider returns an invalid response."""


class LLMProvider(Protocol):
    @property
    def provider_name(self) -> str:
        """Return the stable provider identifier."""

    @property
    def model_name(self) -> str:
        """Return the configured model identifier."""

    def generate(
        self,
        request: LLMGenerationRequest,
    ) -> LLMGenerationResponse:
        """Generate one typed language-model response."""

    def close(self) -> None:
        """Release provider resources idempotently."""


def _default_mock_responder(
    request: LLMGenerationRequest,
) -> str:
    del request

    return DEFAULT_MOCK_RESPONSE


def _estimate_mock_tokens(
    content: str,
) -> int:
    if not content:
        return 0

    return max(
        1,
        (len(content) + 3) // 4,
    )


class DeterministicMockLLMProvider:
    """Deterministic provider for tests and local development."""

    def __init__(
        self,
        model_name: str,
        *,
        responder: LLMResponder = (_default_mock_responder),
    ) -> None:
        normalized_model_name = model_name.strip()

        if not normalized_model_name:
            raise LLMProviderConfigurationError("Mock LLM model name cannot be empty")

        self._model_name = normalized_model_name
        self._responder = responder
        self._lock = RLock()
        self._generation_count = 0
        self._closed = False

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def generation_count(self) -> int:
        with self._lock:
            return self._generation_count

    @property
    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    def generate(
        self,
        request: LLMGenerationRequest,
    ) -> LLMGenerationResponse:
        with self._lock:
            if self._closed:
                raise LLMProviderUnavailableError("LLM provider is closed")

            try:
                content = self._responder(request)
            except LLMProviderError:
                raise
            except Exception as error:
                raise LLMProviderUnavailableError("Mock LLM provider failed") from error

            normalized_content = content.strip()

            if not normalized_content:
                raise LLMProviderResponseError("LLM provider returned empty content")

            input_content = "\n".join(message.content for message in request.messages)

            response = LLMGenerationResponse(
                provider=self.provider_name,
                model=self.model_name,
                content=normalized_content,
                finish_reason="stop",
                usage=LLMTokenUsage(
                    input_tokens=(_estimate_mock_tokens(input_content)),
                    output_tokens=(_estimate_mock_tokens(normalized_content)),
                ),
            )

            self._generation_count += 1

            return response

    def close(self) -> None:
        with self._lock:
            self._closed = True


type LLMProviderFactory = Callable[
    [Settings],
    LLMProvider,
]


def create_llm_provider(
    settings: Settings,
) -> LLMProvider:
    """Create the configured provider without external calls."""
    if settings.llm_provider == "mock":
        return DeterministicMockLLMProvider(
            model_name=settings.llm_model,
        )

    from backend.app.services.openai_compatible_provider import (
        create_openai_compatible_provider,
    )

    return create_openai_compatible_provider(
        settings,
    )
