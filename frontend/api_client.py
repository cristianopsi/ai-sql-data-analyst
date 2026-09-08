"""Typed HTTP client for the analytical presentation endpoint."""

from __future__ import annotations

import math
import os
import urllib.request

import httpx
from pydantic import ValidationError

from backend.app.schemas.presentation import (
    AnalyticalPresentationResult,
    PresentationApiErrorResponse,
    PresentationRequest,
)

PRESENTATION_ENDPOINT = "/api/v1/presentations/generate"
DEFAULT_PRESENTATION_TIMEOUT_SECONDS = 120.0


class PresentationClientError(RuntimeError):
    """Base error containing one message safe for the interface."""

    def __init__(self, public_message: str) -> None:
        self.public_message = public_message
        super().__init__(public_message)


class PresentationClientConfigurationError(PresentationClientError):
    """Raised when the internal client configuration is invalid."""


class PresentationRequestRejectedError(PresentationClientError):
    """Raised when the governed presentation request is rejected."""


class PresentationServiceUnavailableError(PresentationClientError):
    """Raised when the managed presentation service is unavailable."""


class PresentationTransportError(PresentationClientError):
    """Raised when the backend cannot be reached safely."""


class PresentationProtocolError(PresentationClientError):
    """Raised when the backend response violates its public contract."""


def _fetch_cloud_run_identity_token(audience: str) -> str | None:
    """Fetch an identity token from the metadata server when running in Cloud Run."""
    if not os.environ.get("K_SERVICE"):
        return None
    try:
        url = (
            "http://metadata.google.internal/computeMetadata/v1/"
            f"instance/service-accounts/default/identity?audience={audience}"
        )
        req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
        token = urllib.request.urlopen(req, timeout=5).read().decode()
        return str(token)
    except Exception:  # noqa: BLE001
        return None


def _normalized_base_url(api_base_url: str) -> str:
    """Validate an internal HTTP base URL without exposing credentials."""
    normalized = api_base_url.strip()

    if not normalized:
        raise PresentationClientConfigurationError(
            "A URL do serviço de apresentação não está configurada"
        )

    try:
        url = httpx.URL(normalized)
    except httpx.InvalidURL as error:
        raise PresentationClientConfigurationError(
            "A URL do serviço de apresentação é inválida"
        ) from error

    if url.scheme not in {"http", "https"} or not url.host:
        raise PresentationClientConfigurationError("A URL do serviço de apresentação é inválida")

    if url.username or url.password:
        raise PresentationClientConfigurationError(
            "A URL do serviço de apresentação não deve conter credenciais"
        )

    return str(url).rstrip("/")


def _validated_timeout(timeout_seconds: float) -> float:
    """Require one finite positive timeout."""
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise PresentationClientConfigurationError(
            "O tempo limite do serviço de apresentação é inválido"
        )

    return float(timeout_seconds)


def _public_error_detail(
    response: httpx.Response,
    *,
    fallback: str,
) -> str:
    """Read only the declared sanitized error response."""
    try:
        error = PresentationApiErrorResponse.model_validate_json(response.content)
    except ValidationError:
        return fallback

    return error.detail


def _validate_version_headers(
    response: httpx.Response,
    result: AnalyticalPresentationResult,
) -> None:
    """Require response headers to match the validated body versions."""
    expected = {
        "x-presentation-version": result.presentation_version,
        "x-insight-version": result.insights.insight_version,
        "x-visualization-version": (result.visualizations.visualization_version),
        "x-analytics-version": result.insights.analytics_version,
        "x-execution-version": result.insights.execution_version,
    }

    if any(response.headers.get(name) != version for name, version in expected.items()):
        raise PresentationProtocolError(
            "Presentation service returned inconsistent version metadata"
        )


def generate_presentation(
    *,
    api_base_url: str,
    question: str,
    timeout_seconds: float = DEFAULT_PRESENTATION_TIMEOUT_SECONDS,
    transport: httpx.BaseTransport | None = None,
) -> AnalyticalPresentationResult:
    """Generate one presentation through the governed backend endpoint."""
    base_url = _normalized_base_url(api_base_url)
    timeout = _validated_timeout(timeout_seconds)

    try:
        request = PresentationRequest(question=question)
    except ValidationError as error:
        raise PresentationRequestRejectedError("Requisição de apresentação inválida") from error

    request_headers: dict[str, str] = {"Accept": "application/json"}
    auth_token = _fetch_cloud_run_identity_token(base_url)
    if auth_token:
        request_headers["Authorization"] = f"Bearer {auth_token}"
    try:
        with httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            headers=request_headers,
        ) as client:
            response = client.post(
                PRESENTATION_ENDPOINT,
                json=request.model_dump(mode="json"),
            )
    except httpx.TimeoutException as error:
        raise PresentationTransportError("Presentation service timed out") from error
    except httpx.RequestError as error:
        raise PresentationTransportError("Presentation service could not be reached") from error

    if response.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
        raise PresentationRequestRejectedError(
            _public_error_detail(
                response,
                fallback="Requisição de apresentação inválida",
            )
        )

    if response.status_code == httpx.codes.SERVICE_UNAVAILABLE:
        raise PresentationServiceUnavailableError(
            _public_error_detail(
                response,
                fallback="Serviço de apresentação indisponível",
            )
        )

    if response.status_code != httpx.codes.OK:
        raise PresentationProtocolError("Presentation service returned an unexpected response")

    try:
        result = AnalyticalPresentationResult.model_validate_json(response.content)
    except ValidationError as error:
        raise PresentationProtocolError(
            "Presentation service returned an invalid response"
        ) from error

    _validate_version_headers(response, result)
    return result


__all__ = [
    "DEFAULT_PRESENTATION_TIMEOUT_SECONDS",
    "PRESENTATION_ENDPOINT",
    "PresentationClientConfigurationError",
    "PresentationClientError",
    "PresentationProtocolError",
    "PresentationRequestRejectedError",
    "PresentationServiceUnavailableError",
    "PresentationTransportError",
    "generate_presentation",
]
