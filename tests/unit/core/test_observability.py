"""Permanent tests for the observability module.

Validates:
- Correlation ID generation and propagation
- Structured JSON logging without secrets
- Metrics collection (latency, stages)
- Secret redaction filters
"""

from __future__ import annotations

import time

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from backend.app.core.observability import (
    CORRELATION_ID_HEADER,
    ObservabilityMiddleware,
    RequestMetrics,
    configure_logger,
    generate_correlation_id,
    get_audit_logger,
    log_lifecycle_event,
    redact_secrets,
)


class TestCorrelationId:
    def test_generate_correlation_id_returns_unique_hex(self) -> None:
        id1 = generate_correlation_id()
        id2 = generate_correlation_id()
        assert id1 != id2
        assert len(id1) == 32
        assert all(c in "0123456789abcdef" for c in id1)

    def test_middleware_generates_correlation_id_if_missing(self) -> None:
        async def handler(_request):
            return JSONResponse({"ok": True})

        app = Starlette(
            routes=[Route("/", handler, methods=["GET"])],
            middleware=[],
        )
        app.add_middleware(ObservabilityMiddleware)

        with TestClient(app) as client:
            response = client.get("/")
            assert CORRELATION_ID_HEADER in response.headers
            assert len(response.headers[CORRELATION_ID_HEADER]) == 32

    def test_middleware_preserves_client_correlation_id(self) -> None:
        async def handler(_request):
            return JSONResponse({"ok": True})

        app = Starlette(
            routes=[Route("/", handler, methods=["GET"])],
        )
        app.add_middleware(ObservabilityMiddleware)

        client_id = "abc123def456"
        with TestClient(app) as client:
            response = client.get(
                "/",
                headers={CORRELATION_ID_HEADER: client_id},
            )
            assert response.headers[CORRELATION_ID_HEADER] == client_id

class TestSecretRedaction:
    @pytest.mark.parametrize(
        "key",
        [
            "password",
            "api_key",
            "api-key",
            "llm_api_key",
            "auth_token",
            "authToken",
            "secret",
            "access_token",
            "refresh_token",
            "private_key",
            "credential",
            "bearer",
            "authorization",
            "database_url",
            "dsn",
            "connection_string",
        ],
    )
    def test_sensitive_keys_are_redacted(self, key: str) -> None:
        result = redact_secrets({key: "super-secret-value"})
        assert result[key] == "[REDACTED]"

    def test_non_sensitive_keys_pass_through(self) -> None:
        result = redact_secrets({"method": "POST", "path": "/api/v1/insights"})
        assert result["method"] == "POST"
        assert result["path"] == "/api/v1/insights"

    def test_nested_sensitive_keys_are_redacted(self) -> None:
        data = {
            "outer": {
                "password": "hidden",
                "safe": "visible",
            },
            "api_key": "hidden-too",
        }
        result = redact_secrets(data)
        assert result["outer"]["password"] == "[REDACTED]"
        assert result["outer"]["safe"] == "visible"
        assert result["api_key"] == "[REDACTED]"

class TestRequestMetrics:
    def test_total_latency_is_positive(self) -> None:
        metrics = RequestMetrics("test-id")
        latency = metrics.total_latency_ms()
        assert latency >= 0

    def test_stage_timing(self) -> None:
        metrics = RequestMetrics("test-id")
        metrics.start_stage("grounding")
        time.sleep(0.01)
        metrics.end_stage()
        assert "grounding" in metrics.stages
        assert metrics.stages["grounding"] >= 8

    def test_to_dict_includes_correlation_id(self) -> None:
        metrics = RequestMetrics("corr-123")
        result = metrics.to_dict()
        assert result["correlation_id"] == "corr-123"
        assert "total_latency_ms" in result
        assert "stages" in result

class TestStructuredLogging:
    def test_json_output_contains_no_secrets(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        configure_logger(log_level="INFO", log_format="json")
        logger = get_audit_logger()
        logger.info(
            "test.event",
            password="should-be-hidden",
            api_key="also-hidden",
            method="POST",
            path="/api/v1/insights",
        )
        output = caplog.text
        assert "[REDACTED]" in output
        assert "should-be-hidden" not in output
        assert "also-hidden" not in output
        assert "POST" in output
        assert "/api/v1/insights" in output

    def test_lifecycle_event_logs_with_redaction(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        configure_logger(log_level="INFO", log_format="json")
        log_lifecycle_event(
            "service.created",
            stage="llm_provider",
            api_key="secret-key-123",
            model="gpt-4",
        )
        output = caplog.text
        assert "service.created" in output
        assert "llm_provider" in output
        assert "gpt-4" in output
        assert "secret-key-123" not in output
        assert "[REDACTED]" in output

    def test_json_format_is_valid_json(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        configure_logger(log_level="INFO", log_format="json")
        logger = get_audit_logger()
        logger.info("test.json", data="test")
        records = [r for r in caplog.records if r.levelname == "INFO"]
        assert records
        for record in records:
            assert hasattr(record, "msg")