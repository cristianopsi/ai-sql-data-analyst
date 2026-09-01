from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from backend.app.schemas.llm import LLMGenerationRequest, LLMMessage
from backend.app.services.llm_provider import (
    LLMProviderConfigurationError,
    LLMProviderUnavailableError,
)
from backend.app.services.openai_compatible_provider import (
    DEEPSEEK_API_BASE_URL,
    DEEPSEEK_FALLBACK_MODEL,
    DEEPSEEK_PRIMARY_MODEL,
    OpenAICompatibleLLMProvider,
)


def _request() -> LLMGenerationRequest:
    return LLMGenerationRequest(
        messages=(LLMMessage(role="user", content="Return one row."),),
        temperature=0.0,
        max_tokens=64,
        response_format="json",
    )


def _success_payload() -> dict[str, object]:
    return {
        "id": "completion-test",
        "object": "chat.completion",
        "created": 0,
        "model": DEEPSEEK_PRIMARY_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": '{"sql":"SELECT 1"}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _provider(
    handler: Any,
    *,
    model: str = DEEPSEEK_PRIMARY_MODEL,
    base_url: str = DEEPSEEK_API_BASE_URL,
    sleeper: Any = lambda _delay: None,
) -> tuple[OpenAICompatibleLLMProvider, httpx.Client]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleLLMProvider(
        provider_name="deepseek",
        model_name=model,
        api_key=SecretStr("unit-test-placeholder"),
        base_url=base_url,
        timeout_seconds=1.0,
        max_response_bytes=100_000,
        client=client,
        sleeper=sleeper,
    )
    return provider, client


def test_deepseek_payload_disables_thinking_by_default() -> None:
    observed: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(json.loads(request.content))
        return httpx.Response(200, json=_success_payload())

    provider, client = _provider(handler)
    try:
        provider.generate(_request())
    finally:
        client.close()
    assert observed[0]["thinking"] == {"type": "disabled"}


def test_deepseek_retries_rate_limit_and_server_errors_with_bounded_backoff() -> None:
    statuses = iter((429, 503, 200))
    sleeps: list[float] = []
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        status = next(statuses)
        if status == 200:
            return httpx.Response(status, json=_success_payload())
        return httpx.Response(status, json={"error": {"message": "temporary"}})

    provider, client = _provider(handler, sleeper=sleeps.append)
    try:
        provider.generate(_request())
    finally:
        client.close()
    assert calls == 3
    assert sleeps == [0.25, 0.5]


def test_deepseek_authentication_error_fails_fast() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": {"message": "unauthorized"}})

    provider, client = _provider(handler)
    try:
        with pytest.raises(LLMProviderUnavailableError):
            provider.generate(_request())
    finally:
        client.close()
    assert calls == 1


def test_deepseek_invalid_json_retry_is_bounded() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(200, content=b"not-json")
        return httpx.Response(200, json=_success_payload())

    provider, client = _provider(handler)
    try:
        provider.generate(_request())
    finally:
        client.close()
    assert calls == 3


@pytest.mark.parametrize("model", [DEEPSEEK_PRIMARY_MODEL, DEEPSEEK_FALLBACK_MODEL])
def test_deepseek_allows_only_controlled_models(model: str) -> None:
    provider, client = _provider(lambda _request: httpx.Response(200), model=model)
    try:
        assert provider is not None
    finally:
        client.close()


def test_deepseek_rejects_unapproved_model_and_endpoint() -> None:
    with pytest.raises(LLMProviderConfigurationError):
        _provider(lambda _request: httpx.Response(200), model="experimental-vision-model")
    with pytest.raises(LLMProviderConfigurationError):
        _provider(lambda _request: httpx.Response(200), base_url="https://example.invalid")
