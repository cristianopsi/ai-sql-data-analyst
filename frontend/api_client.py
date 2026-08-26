"""Typed HTTP client for the analytical presentation endpoint."""

from __future__ import annotations

import math

import httpx
from pydantic import ValidationError

from backend.app.schemas.presentation import (
    AnalyticalPresentationResult,
    PresentationApiErrorResponse,
    PresentationRequest,
)

PRESENTATION_ENDPOINT = "/api/v1/presentations/generate"
DEFAULT_PRESENTATION_TIMEOUT_SECONDS = 15.0


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


def _normalized_base_url(api_base_url: str) -> str:
    """Validate an internal HTTP base URL without exposing credentials."""
    normalized = api_base_url.strip()

    if not normalized:
        raise PresentationClientConfigurationError("Presentation service URL is not configured")

    try:
        url = httpx.URL(normalized)
    except httpx.InvalidURL as error:
        raise PresentationClientConfigurationError("Presentation service URL is invalid") from error

    if url.scheme not in {"http", "https"} or not url.host:
        raise PresentationClientConfigurationError("Presentation service URL is invalid")

    if url.username or url.password:
        raise PresentationClientConfigurationError(
            "Presentation service URL must not contain credentials"
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
        raise PresentationClientConfigurationError("Presentation service timeout is invalid")

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
        raise PresentationRequestRejectedError("Presentation request is invalid") from error

    try:
        with httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            headers={"Accept": "application/json"},
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
                fallback="Presentation request is invalid",
            )
        )

    if response.status_code == httpx.codes.SERVICE_UNAVAILABLE:
        raise PresentationServiceUnavailableError(
            _public_error_detail(
                response,
                fallback="Presentation service is unavailable",
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
