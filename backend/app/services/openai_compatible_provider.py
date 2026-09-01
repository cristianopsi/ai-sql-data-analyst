import time
from collections.abc import Callable
from threading import RLock
from typing import Literal, cast
from urllib.parse import urlsplit

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
)

from backend.app.core.config import Settings
from backend.app.schemas.llm import (
    LLMFinishReason,
    LLMGenerationRequest,
    LLMGenerationResponse,
    LLMTokenUsage,
)
from backend.app.services.llm_provider import (
    LLMProviderConfigurationError,
    LLMProviderResponseError,
    LLMProviderUnavailableError,
)

type OpenAICompatibleProviderName = Literal[
    "openai", "gemini", "groq", "openrouter", "ollama", "deepseek"
]

ALLOWED_FINISH_REASONS = {
    "stop",
    "length",
    "content_filter",
}

LOOPBACK_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
}

DEFAULT_MAX_RESPONSE_BYTES = 1_000_000

DEFAULT_OPENAI_COMPATIBLE_BASE_URLS: dict[
    OpenAICompatibleProviderName,
    str,
] = {
    "openai": "https://api.openai.com/v1",
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai"),
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


class _ChatMessageResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: str


class _ChatChoiceResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    finish_reason: str
    message: _ChatMessageResponse


class _ChatUsageResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int = Field(
        default=0,
        ge=0,
    )
    completion_tokens: int = Field(
        default=0,
        ge=0,
    )


class _ChatCompletionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = Field(min_length=1)
    choices: tuple[
        _ChatChoiceResponse,
        ...,
    ] = Field(min_length=1)
    usage: _ChatUsageResponse | None = None


def _normalize_base_url(
    provider_name: OpenAICompatibleProviderName,
    base_url: str,
) -> str:
    normalized = base_url.strip().rstrip("/")

    if not normalized:
        raise LLMProviderConfigurationError("LLM provider base URL cannot be empty")

    parsed = urlsplit(normalized)

    if (
        not parsed.scheme
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise LLMProviderConfigurationError("LLM provider base URL is invalid")

    if provider_name == "ollama":
        if parsed.scheme not in {
            "http",
            "https",
        }:
            raise LLMProviderConfigurationError("Ollama base URL scheme is invalid")

        if parsed.scheme == "http" and parsed.hostname.casefold() not in LOOPBACK_HOSTS:
            raise LLMProviderConfigurationError("Plain HTTP Ollama URL must use loopback")
    elif parsed.scheme != "https":
        raise LLMProviderConfigurationError("External LLM providers require HTTPS")

    return normalized


DEEPSEEK_API_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_PRIMARY_MODEL = "deepseek-v4-flash"
DEEPSEEK_FALLBACK_MODEL = "deepseek-v4-pro"
_DEEPSEEK_PROVIDER = "deepseek"
_DEEPSEEK_ALLOWED_MODELS = frozenset({DEEPSEEK_PRIMARY_MODEL, DEEPSEEK_FALLBACK_MODEL})
_DEEPSEEK_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_DEEPSEEK_MAX_ATTEMPTS = 3
_DEEPSEEK_MAX_BACKOFF_SECONDS = 1.0


class _DeepSeekRetryableHTTPError(LLMProviderUnavailableError):
    """Internal signal for a bounded DeepSeek retry."""


class OpenAICompatibleLLMProvider:
    """Controlled non-streaming Chat Completions adapter."""

    def __init__(
        self,
        *,
        provider_name: OpenAICompatibleProviderName,
        model_name: str,
        base_url: str,
        api_key: SecretStr,
        timeout_seconds: float,
        max_response_bytes: int = (DEFAULT_MAX_RESPONSE_BYTES),
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        normalized_model = model_name.strip()

        if not normalized_model:
            raise LLMProviderConfigurationError("LLM model name cannot be empty")

        if timeout_seconds <= 0:
            raise LLMProviderConfigurationError("LLM timeout must be positive")

        if max_response_bytes < 1:
            raise LLMProviderConfigurationError("LLM response limit must be positive")

        if provider_name != "ollama" and not api_key.get_secret_value():
            raise LLMProviderConfigurationError("External LLM provider requires an API key")

        self._provider_name = provider_name
        self._model_name = normalized_model
        self._base_url = _normalize_base_url(
            provider_name,
            base_url,
        )
        if self._provider_name == _DEEPSEEK_PROVIDER:
            normalized_base_url = self._base_url.rstrip("/")
            official_root = "https://api.deepseek.com"
            if not (
                normalized_base_url == official_root
                or normalized_base_url.startswith(official_root + "/")
            ):
                raise LLMProviderConfigurationError("DeepSeek requires the official API endpoint")
            if self._model_name not in _DEEPSEEK_ALLOWED_MODELS:
                raise LLMProviderConfigurationError("Unsupported DeepSeek model")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
        )
        self._sleeper = sleeper
        self._lock = RLock()
        self._closed = False

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def endpoint_url(self) -> str:
        return f"{self._base_url}/chat/completions"

    @property
    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    def _request_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        api_key = self._api_key.get_secret_value()

        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        return headers

    def _request_payload(
        self,
        request: LLMGenerationRequest,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model_name,
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                }
                for message in request.messages
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": False,
        }

        if request.response_format == "json":
            payload["response_format"] = {
                "type": "json_object",
            }

        if self._provider_name == _DEEPSEEK_PROVIDER:
            payload["thinking"] = {"type": "disabled"}
        return payload

    def generate(self, request: LLMGenerationRequest) -> LLMGenerationResponse:
        if self._provider_name != _DEEPSEEK_PROVIDER:
            return self._generate_once(request)

        last_error: _DeepSeekRetryableHTTPError | LLMProviderResponseError | None = None
        for attempt in range(_DEEPSEEK_MAX_ATTEMPTS):
            try:
                return self._generate_once(request)
            except (_DeepSeekRetryableHTTPError, LLMProviderResponseError) as exc:
                last_error = exc
                if attempt + 1 >= _DEEPSEEK_MAX_ATTEMPTS:
                    break
                delay = min(0.25 * (2**attempt), _DEEPSEEK_MAX_BACKOFF_SECONDS)
                self._sleeper(delay)

        if last_error is None:
            raise LLMProviderUnavailableError("DeepSeek retry state is invalid")
        raise last_error

    def _generate_once(
        self,
        request: LLMGenerationRequest,
    ) -> LLMGenerationResponse:
        with self._lock:
            if self._closed:
                raise LLMProviderUnavailableError("LLM provider is closed")

            try:
                response = self._client.post(
                    self.endpoint_url,
                    headers=self._request_headers(),
                    json=self._request_payload(request),
                    timeout=self._timeout_seconds,
                )
            except httpx.HTTPError:
                raise LLMProviderUnavailableError("LLM provider request failed") from None

            if (
                self._provider_name == _DEEPSEEK_PROVIDER
                and response.status_code in _DEEPSEEK_RETRYABLE_STATUS_CODES
            ):
                raise _DeepSeekRetryableHTTPError("DeepSeek API is temporarily unavailable")
            if response.status_code != 200:
                raise LLMProviderUnavailableError("LLM provider request failed")

            if len(response.content) > self._max_response_bytes:
                raise LLMProviderResponseError("LLM provider response is too large")

            content_type = response.headers.get(
                "content-type",
                "",
            )
            media_type = (
                content_type.split(
                    ";",
                    1,
                )[0]
                .strip()
                .casefold()
            )

            if media_type != "application/json":
                raise LLMProviderResponseError("LLM provider returned invalid content type")

            try:
                parsed_response = _ChatCompletionResponse.model_validate(response.json())
            except (
                ValueError,
                ValidationError,
            ):
                raise LLMProviderResponseError("LLM provider returned invalid JSON") from None

            choice = parsed_response.choices[0]
            content = choice.message.content.strip()

            if not content:
                raise LLMProviderResponseError("LLM provider returned empty content")

            if choice.finish_reason not in ALLOWED_FINISH_REASONS:
                raise LLMProviderResponseError("LLM provider returned unsupported finish reason")

            finish_reason = cast(
                LLMFinishReason,
                choice.finish_reason,
            )
            usage = parsed_response.usage or _ChatUsageResponse()

            return LLMGenerationResponse(
                provider=self.provider_name,
                model=self.model_name,
                content=content,
                finish_reason=finish_reason,
                usage=LLMTokenUsage(
                    input_tokens=(usage.prompt_tokens),
                    output_tokens=(usage.completion_tokens),
                ),
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return

            self._client.close()
            self._closed = True


type OpenAICompatibleProviderFactory = Callable[
    [Settings],
    OpenAICompatibleLLMProvider,
]


def create_openai_compatible_provider(
    settings: Settings,
    *,
    client: httpx.Client | None = None,
) -> OpenAICompatibleLLMProvider:
    """Create a configured OpenAI-compatible provider."""
    if settings.llm_provider == "mock":
        raise LLMProviderConfigurationError("Mock provider does not use the HTTP adapter")

    provider_name = settings.llm_provider

    if provider_name == "ollama":
        base_url = settings.llm_base_url

        if not base_url:
            raise LLMProviderConfigurationError("Ollama requires an explicit base URL")
    else:
        base_url = settings.llm_base_url or DEFAULT_OPENAI_COMPATIBLE_BASE_URLS[provider_name]

    return OpenAICompatibleLLMProvider(
        provider_name=provider_name,
        model_name=settings.llm_model,
        base_url=base_url,
        api_key=settings.llm_api_key,
        timeout_seconds=(settings.llm_request_timeout_seconds),
        client=client,
    )
