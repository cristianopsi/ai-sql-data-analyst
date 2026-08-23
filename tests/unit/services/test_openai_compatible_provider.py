import json

import httpx
import pytest
from pydantic import SecretStr

from backend.app.core.config import Settings
from backend.app.schemas.llm import (
    LLMGenerationRequest,
    LLMMessage,
)
from backend.app.services.llm_provider import (
    LLMProviderConfigurationError,
    LLMProviderResponseError,
    LLMProviderUnavailableError,
)
from backend.app.services.openai_compatible_provider import (
    DEFAULT_OPENAI_COMPATIBLE_BASE_URLS,
    OpenAICompatibleLLMProvider,
    create_openai_compatible_provider,
)


def build_request(
    *,
    response_format: str = "json",
) -> LLMGenerationRequest:
    return LLMGenerationRequest(
        messages=(
            LLMMessage(
                role="system",
                content="Return safe output.",
            ),
            LLMMessage(
                role="user",
                content="Use supplied context.",
            ),
        ),
        temperature=0.0,
        max_tokens=256,
        response_format=response_format,
    )


def build_provider(
    handler: httpx.MockTransport,
    *,
    provider_name: str = "openai",
    base_url: str = ("https://provider.example/v1"),
    max_response_bytes: int = 1_000_000,
) -> OpenAICompatibleLLMProvider:
    return OpenAICompatibleLLMProvider(
        provider_name=provider_name,
        model_name="controlled-model",
        base_url=base_url,
        api_key=SecretStr("test-key-not-a-real-secret"),
        timeout_seconds=3.0,
        max_response_bytes=max_response_bytes,
        client=httpx.Client(
            transport=handler,
            follow_redirects=False,
        ),
    )


def successful_response() -> dict[str, object]:
    return {
        "model": "controlled-model",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": ('{"sql":"SELECT 1"}'),
                },
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    }


def test_request_is_mapped_and_response_is_parsed() -> None:
    requests: list[httpx.Request] = []

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)

        assert request.url.path == ("/v1/chat/completions")
        assert request.headers["authorization"] == ("Bearer test-key-not-a-real-secret")
        assert payload["model"] == ("controlled-model")
        assert payload["stream"] is False
        assert payload["temperature"] == 0.0
        assert payload["max_tokens"] == 256
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["messages"] == [
            {
                "role": "system",
                "content": "Return safe output.",
            },
            {
                "role": "user",
                "content": "Use supplied context.",
            },
        ]

        return httpx.Response(
            200,
            json=successful_response(),
        )

    provider = build_provider(httpx.MockTransport(handler))

    response = provider.generate(build_request())

    assert len(requests) == 1
    assert response.provider == "openai"
    assert response.model == ("controlled-model")
    assert response.content == ('{"sql":"SELECT 1"}')
    assert response.finish_reason == "stop"
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 5
    assert response.usage.total_tokens == 15

    provider.close()


def test_text_request_omits_response_format() -> None:
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        payload = json.loads(request.content)

        assert "response_format" not in payload

        return httpx.Response(
            200,
            json=successful_response(),
        )

    provider = build_provider(httpx.MockTransport(handler))

    provider.generate(build_request(response_format="text"))
    provider.close()


def test_missing_usage_defaults_to_zero() -> None:
    response_body = successful_response()
    response_body.pop("usage")

    provider = build_provider(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=response_body,
            )
        )
    )

    response = provider.generate(build_request())

    assert response.usage.input_tokens == 0
    assert response.usage.output_tokens == 0

    provider.close()


def test_http_failures_are_sanitized() -> None:
    sensitive_body = "provider-secret-must-not-leak"

    provider = build_provider(
        httpx.MockTransport(
            lambda request: httpx.Response(
                503,
                text=sensitive_body,
            )
        )
    )

    with pytest.raises(
        LLMProviderUnavailableError,
        match="request failed",
    ) as captured:
        provider.generate(build_request())

    assert sensitive_body not in str(captured.value)
    provider.close()


def test_transport_failures_are_sanitized() -> None:
    sensitive_detail = "network-secret-must-not-leak"

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        raise httpx.ConnectError(
            sensitive_detail,
            request=request,
        )

    provider = build_provider(httpx.MockTransport(handler))

    with pytest.raises(
        LLMProviderUnavailableError,
        match="request failed",
    ) as captured:
        provider.generate(build_request())

    assert sensitive_detail not in str(captured.value)
    provider.close()


def test_invalid_content_type_is_rejected() -> None:
    provider = build_provider(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text="not-json",
                headers={"Content-Type": "text/plain"},
            )
        )
    )

    with pytest.raises(
        LLMProviderResponseError,
        match="invalid content type",
    ):
        provider.generate(build_request())

    provider.close()


def test_invalid_json_is_rejected() -> None:
    provider = build_provider(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=b"{invalid-json",
                headers={"Content-Type": ("application/json")},
            )
        )
    )

    with pytest.raises(
        LLMProviderResponseError,
        match="invalid JSON",
    ):
        provider.generate(build_request())

    provider.close()


def test_invalid_response_shape_is_rejected() -> None:
    provider = build_provider(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"detail": ("internal provider detail")},
            )
        )
    )

    with pytest.raises(
        LLMProviderResponseError,
        match="invalid JSON",
    ):
        provider.generate(build_request())

    provider.close()


def test_unsupported_finish_reason_is_rejected() -> None:
    response_body = successful_response()
    choices = response_body["choices"]
    assert isinstance(choices, list)
    choices[0]["finish_reason"] = "tool_calls"

    provider = build_provider(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=response_body,
            )
        )
    )

    with pytest.raises(
        LLMProviderResponseError,
        match="unsupported finish reason",
    ):
        provider.generate(build_request())

    provider.close()


def test_oversized_response_is_rejected() -> None:
    response_body = successful_response()
    choices = response_body["choices"]
    assert isinstance(choices, list)
    choices[0]["message"]["content"] = "x" * 500

    provider = build_provider(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=response_body,
            )
        ),
        max_response_bytes=100,
    )

    with pytest.raises(
        LLMProviderResponseError,
        match="too large",
    ):
        provider.generate(build_request())

    provider.close()


def test_base_url_security_is_enforced() -> None:
    invalid_configurations = (
        (
            "openai",
            "http://api.example/v1",
            "require HTTPS",
        ),
        (
            "ollama",
            "http://remote.example/v1",
            "must use loopback",
        ),
        (
            "openai",
            ("https://user:password@api.example/v1"),
            "base URL is invalid",
        ),
        (
            "openai",
            ("https://api.example/v1?key=value"),
            "base URL is invalid",
        ),
    )

    for (
        provider_name,
        base_url,
        error_pattern,
    ) in invalid_configurations:
        with pytest.raises(
            LLMProviderConfigurationError,
            match=error_pattern,
        ):
            OpenAICompatibleLLMProvider(
                provider_name=provider_name,
                model_name="model",
                base_url=base_url,
                api_key=SecretStr("not-a-real-key"),
                timeout_seconds=3.0,
            )


def test_local_ollama_can_omit_api_key() -> None:
    provider = OpenAICompatibleLLMProvider(
        provider_name="ollama",
        model_name="local-model",
        base_url=("http://127.0.0.1:11434/v1"),
        api_key=SecretStr(""),
        timeout_seconds=3.0,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json=successful_response(),
                )
            )
        ),
    )

    response = provider.generate(build_request())

    assert response.provider == "ollama"
    provider.close()


def test_close_is_idempotent_and_blocks_generation() -> None:
    provider = build_provider(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=successful_response(),
            )
        )
    )

    provider.close()
    provider.close()

    assert provider.is_closed is True

    with pytest.raises(
        LLMProviderUnavailableError,
        match="provider is closed",
    ):
        provider.generate(build_request())


def test_factory_uses_official_default_base_urls() -> None:
    for (
        provider_name,
        expected_base_url,
    ) in DEFAULT_OPENAI_COMPATIBLE_BASE_URLS.items():
        provider = create_openai_compatible_provider(
            Settings(
                llm_provider=provider_name,
                llm_model="controlled-model",
                llm_api_key="not-a-real-key",
            ),
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        json=successful_response(),
                    )
                )
            ),
        )

        assert provider.provider_name == provider_name
        assert provider.endpoint_url == (f"{expected_base_url}/chat/completions")

        provider.close()


def test_factory_honors_controlled_base_url_override() -> None:
    provider = create_openai_compatible_provider(
        Settings(
            llm_provider="openai",
            llm_model="controlled-model",
            llm_api_key="not-a-real-key",
            llm_base_url=("https://gateway.example/v1"),
        ),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json=successful_response(),
                )
            )
        ),
    )

    assert provider.endpoint_url == ("https://gateway.example/v1/chat/completions")

    provider.close()


def test_factory_rejects_mock_and_builds_local_ollama() -> None:
    with pytest.raises(
        LLMProviderConfigurationError,
        match="does not use the HTTP adapter",
    ):
        create_openai_compatible_provider(
            Settings(
                llm_provider="mock",
            )
        )

    provider = create_openai_compatible_provider(
        Settings(
            llm_provider="ollama",
            llm_model="local-model",
            llm_base_url=("http://127.0.0.1:11434/v1"),
        ),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json=successful_response(),
                )
            )
        ),
    )

    assert provider.provider_name == "ollama"
    assert provider.endpoint_url == ("http://127.0.0.1:11434/v1/chat/completions")

    provider.close()
