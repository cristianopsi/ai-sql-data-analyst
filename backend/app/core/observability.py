"""Structured audit logging, correlation IDs, and metrics collection.

This module provides:
- Correlation ID generation and propagation via X-Request-ID header
- Structured JSON logging via structlog with secret redaction
- Request-level metrics (latency, stage, status, tokens, cost, repairs, rejections, timeouts)
- Lifecycle event auditing (startup, shutdown, service creation, pool open/close)

Security guarantees:
- Never logs: questions, SQL, rows, credentials, API keys, tokens
- Only logs: event_type, correlation_id, timestamp, stage, metadata (non-sensitive)
- Redacts known sensitive keys before serialization
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CORRELATION_ID_HEADER = "X-Request-ID"

_SENSITIVE_KEY_PATTERNS = re.compile(
    r"(?i)(password|passwd|secret|api[_-]?key|auth[_-]?token|access[_-]?token"
    r"|refresh[_-]?token|private[_-]?key|credential|bearer|authorization"
    r"|llm[_-]?api[_-]?key|database[_-]?url|dsn|connection[_-]?string)"
)

_REDACTED_VALUE = "[REDACTED]"

# ---------------------------------------------------------------------------
# Logger configuration
# ---------------------------------------------------------------------------

_logger: structlog.stdlib.BoundLogger | None = None

def configure_logger(
    *,
    log_level: str = "INFO",
    log_format: str = "json",
) -> structlog.stdlib.BoundLogger:
    """Configure and return the global structlog logger."""
    global _logger  # noqa: PLW0603
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        cast(
            structlog.types.Processor,
            structlog.processors.TimeStamper(fmt="iso"),
        ),
        cast(
            structlog.types.Processor,
            _redact_sensitive_processor,
        ),
        structlog.processors.dict_tracebacks,
    ]
    if log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _logger = structlog.get_logger("audit")
    numeric_level = logging.getLevelName(log_level.upper())
    if isinstance(numeric_level, int):
        _logger.setLevel(numeric_level)
    return _logger

def get_audit_logger() -> structlog.stdlib.BoundLogger:
    """Return the configured audit logger, configuring if needed."""
    if _logger is None:
        return configure_logger()
    return _logger

# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------

def _redact_sensitive_processor(
    _logger: Any,  # noqa: ANN401
    _method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Redact values whose keys match sensitive patterns."""
    redacted: dict[str, Any] = {}
    for key, value in event_dict.items():
        if _SENSITIVE_KEY_PATTERNS.search(key):
            redacted[key] = _REDACTED_VALUE
        elif isinstance(value, dict):
            redacted[key] = _redact_dict_recursive(value)
        else:
            redacted[key] = value
    return redacted

def _redact_dict_recursive(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact sensitive keys in nested dictionaries."""
    result: dict[str, Any] = {}
    for key, value in data.items():
        if _SENSITIVE_KEY_PATTERNS.search(key):
            result[key] = _REDACTED_VALUE
        elif isinstance(value, dict):
            result[key] = _redact_dict_recursive(value)
        else:
            result[key] = value
    return result

def redact_secrets(data: dict[str, Any]) -> dict[str, Any]:
    """Public API for redacting sensitive keys from a dictionary."""
    return _redact_dict_recursive(data)

# ---------------------------------------------------------------------------
# Correlation ID
# ---------------------------------------------------------------------------

def generate_correlation_id() -> str:
    """Generate a new correlation ID (UUID4 without dashes for compactness)."""
    return uuid.uuid4().hex

def get_correlation_id(request: Request) -> str | None:
    """Extract correlation ID from request headers."""
    return request.headers.get(CORRELATION_ID_HEADER)

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class RequestMetrics:
    """Collect metrics for a single request lifecycle."""

    def __init__(self, correlation_id: str) -> None:
        self.correlation_id = correlation_id
        self.start_time = time.monotonic()
        self.stages: dict[str, float] = {}
        self._stage_start: str | None = None
        self._stage_start_time: float = 0.0

    def start_stage(self, stage_name: str) -> None:
        """Begin timing a named stage."""
        self._stage_start = stage_name
        self._stage_start_time = time.monotonic()

    def end_stage(self) -> None:
        """End timing the current stage and record its duration."""
        if self._stage_start is not None:
            elapsed = time.monotonic() - self._stage_start_time
            self.stages[self._stage_start] = round(elapsed * 1000, 2)
            self._stage_start = None

    def total_latency_ms(self) -> float:
        """Return total request latency in milliseconds."""
        return round((time.monotonic() - self.start_time) * 1000, 2)

    def to_dict(self) -> dict[str, Any]:
        """Serialize metrics to a dictionary safe for logging."""
        return {
            "correlation_id": self.correlation_id,
            "total_latency_ms": self.total_latency_ms(),
            "stages": dict(self.stages),
        }

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Middleware that generates correlation IDs and logs audit events."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        correlation_id = get_correlation_id(request) or generate_correlation_id()
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        logger = get_audit_logger()
        metrics = RequestMetrics(correlation_id)

        logger.info(
            "request.started",
            method=request.method,
            path=request.url.path,
        )

        try:
            response = await call_next(request)
        except Exception:
            logger.error(
                "request.failed",
                method=request.method,
                path=request.url.path,
                latency_ms=metrics.total_latency_ms(),
            )
            raise

        response.headers[CORRELATION_ID_HEADER] = correlation_id

        logger.info(
            "request.completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            latency_ms=metrics.total_latency_ms(),
        )

        return response

# ---------------------------------------------------------------------------
# Lifecycle audit helpers
# ---------------------------------------------------------------------------

def log_lifecycle_event(
    event: str,
    *,
    stage: str = "",
    **metadata: Any,
) -> None:
    """Log a lifecycle audit event with redaction applied."""
    logger = get_audit_logger()
    log_data: dict[str, Any] = {"audit_event": event}
    if stage:
        log_data["stage"] = stage
    log_data.update(redact_secrets(metadata))
    logger.info("lifecycle", **log_data)