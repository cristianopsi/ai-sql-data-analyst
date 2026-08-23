import pytest
from pydantic import ValidationError

from backend.app.core.config import Settings
from backend.app.schemas.llm import (
    LLMGenerationRequest,
    LLMMessage,
)
from backend.app.services.llm_provider import (
    DEFAULT_MOCK_RESPONSE,
    DeterministicMockLLMProvider,
    LLMProviderResponseError,
    LLMProviderUnavailableError,
    create_llm_provider,
)


def build_request() -> LLMGenerationRequest:
    return LLMGenerationRequest(
        messages=(
            LLMMessage(
                role="system",
                content="Return controlled JSON.",
            ),
            LLMMessage(
                role="user",
                content="Summarize the safe context.",
            ),
        ),
        temperature=0.0,
        max_tokens=256,
        response_format="json",
    )


def test_llm_request_requires_messages() -> None:
    with pytest.raises(
        ValidationError,
        match="at least 1",
    ):
        LLMGenerationRequest(
            messages=(),
        )


def test_llm_request_requires_user_message() -> None:
    with pytest.raises(
        ValidationError,
        match="at least one user message",
    ):
        LLMGenerationRequest(
            messages=(
                LLMMessage(
                    role="system",
                    content="System only.",
                ),
            ),
        )


def test_llm_contract_is_immutable() -> None:
    request = build_request()

    with pytest.raises(
        ValidationError,
        match="frozen",
    ):
        request.temperature = 1.0


def test_mock_provider_is_deterministic() -> None:
    provider = DeterministicMockLLMProvider(
        model_name="deterministic-test",
    )
    request = build_request()

    first = provider.generate(request)
    second = provider.generate(request)

    assert first == second
    assert first.provider == "mock"
    assert first.model == "deterministic-test"
    assert first.content == DEFAULT_MOCK_RESPONSE
    assert first.finish_reason == "stop"
    assert first.usage.input_tokens > 0
    assert first.usage.output_tokens > 0
    assert first.usage.total_tokens == (first.usage.input_tokens + first.usage.output_tokens)
    assert provider.generation_count == 2


def test_mock_provider_accepts_controlled_responder() -> None:
    received_requests: list[LLMGenerationRequest] = []

    def responder(
        request: LLMGenerationRequest,
    ) -> str:
        received_requests.append(request)

        return '{"sql":"SELECT 1"}'

    provider = DeterministicMockLLMProvider(
        model_name="controlled-model",
        responder=responder,
    )
    request = build_request()

    response = provider.generate(request)

    assert received_requests == [request]
    assert response.content == ('{"sql":"SELECT 1"}')
    assert provider.generation_count == 1


def test_empty_provider_response_is_rejected() -> None:
    provider = DeterministicMockLLMProvider(
        model_name="controlled-model",
        responder=lambda request: "",
    )

    with pytest.raises(
        LLMProviderResponseError,
        match="empty content",
    ):
        provider.generate(build_request())

    assert provider.generation_count == 0


def test_provider_failure_is_sanitized() -> None:
    sensitive_detail = "provider-secret-must-not-leak"

    def failing_responder(
        request: LLMGenerationRequest,
    ) -> str:
        del request

        raise RuntimeError(sensitive_detail)

    provider = DeterministicMockLLMProvider(
        model_name="controlled-model",
        responder=failing_responder,
    )

    with pytest.raises(
        LLMProviderUnavailableError,
        match="Mock LLM provider failed",
    ) as captured:
        provider.generate(build_request())

    assert sensitive_detail not in str(captured.value)
    assert provider.generation_count == 0


def test_closed_mock_provider_rejects_generation() -> None:
    provider = DeterministicMockLLMProvider(
        model_name="controlled-model",
    )

    provider.close()
    provider.close()

    assert provider.is_closed is True

    with pytest.raises(
        LLMProviderUnavailableError,
        match="provider is closed",
    ):
        provider.generate(build_request())

    assert provider.generation_count == 0


def test_provider_factory_builds_configured_provider() -> None:
    mock_provider = create_llm_provider(
        Settings(
            llm_provider="mock",
            llm_model="deterministic-test",
        )
    )

    assert mock_provider.provider_name == "mock"
    assert mock_provider.model_name == "deterministic-test"

    external_provider = create_llm_provider(
        Settings(
            llm_provider="openai",
            llm_model="external-model",
            llm_api_key="not-a-real-key",
        )
    )

    assert external_provider.provider_name == "openai"
    assert external_provider.model_name == "external-model"

    mock_provider.close()
    external_provider.close()
