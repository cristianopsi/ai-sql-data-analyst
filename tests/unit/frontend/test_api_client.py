"""Tests for the typed Streamlit presentation client."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from backend.app.schemas.visualization import (
    visualization_specification_id,
)
from frontend.api_client import (
    PresentationClientConfigurationError,
    PresentationClientError,
    PresentationProtocolError,
    PresentationRequestRejectedError,
    PresentationServiceUnavailableError,
    PresentationTransportError,
    generate_presentation,
)


def _valid_payload() -> dict[str, object]:
    return {
        "presentation_version": "1",
        "presentation_status": "generated",
        "source_row_count": 1,
        "query": {
            "validated_sql": ("SELECT approved_revenue FROM retail.orders LIMIT 1000"),
            "columns": ["approved_revenue"],
            "rows": [["100.01"]],
            "row_count": 1,
        },
        "visualizations": {
            "visualization_version": "1",
            "visualization_status": "specified",
            "deterministic": True,
            "analytics_version": "1",
            "execution_version": "1",
            "semantic_version": "1",
            "catalog_version": "1",
            "source_row_count": 1,
            "specifications": [
                {
                    "spec_id": visualization_specification_id(
                        "kpi",
                        "approved_revenue",
                    ),
                    "chart_type": "kpi",
                    "title": "Approved Revenue",
                    "metric_name": "approved_revenue",
                    "unit": "brl",
                    "aggregation": "sum",
                    "value_count": 1,
                    "total": "100.01",
                    "value": "100.01",
                    "average": "100.01",
                    "minimum": "100.01",
                    "maximum": "100.01",
                }
            ],
        },
        "insights": {
            "insight_version": "1",
            "insight_status": "generated",
            "grounded": True,
            "calculated_by_llm": False,
            "analytics_version": "1",
            "visualization_version": "1",
            "execution_version": "1",
            "semantic_version": "1",
            "catalog_version": "1",
            "source_row_count": 1,
            "provider": "mock",
            "model": "frontend-test",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
            },
            "summary": "Approved revenue is available.",
            "claims": [
                {
                    "claim_id": ("claim-000000000000000000000001"),
                    "text": ("Approved revenue is available."),
                    "evidence": [
                        {
                            "evidence_type": "metric_summary",
                            "metric_name": "approved_revenue",
                            "specification_id": None,
                        }
                    ],
                }
            ],
        },
    }


def _version_headers() -> dict[str, str]:
    return {
        "X-Presentation-Version": "1",
        "X-Insight-Version": "1",
        "X-Visualization-Version": "1",
        "X-Analytics-Version": "1",
        "X-Execution-Version": "1",
    }


def _transport(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_generate_presentation_posts_only_the_question() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=_valid_payload(),
            headers=_version_headers(),
        )

    result = generate_presentation(
        api_base_url="http://backend.test",
        question="Show approved revenue",
        transport=_transport(handler),
    )

    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == ("/api/v1/presentations/generate")
    assert json.loads(requests[0].content) == {"question": "Show approved revenue"}
    assert requests[0].headers["accept"] == "application/json"
    assert result.source_row_count == 1
    assert result.query.columns == ("approved_revenue",)
    assert result.query.rows == (("100.01",),)


@pytest.mark.parametrize(
    ("status_code", "error_type", "detail"),
    [
        (
            422,
            PresentationRequestRejectedError,
            "Presentation request is invalid",
        ),
        (
            503,
            PresentationServiceUnavailableError,
            "Presentation service is unavailable",
        ),
    ],
)
def test_declared_errors_preserve_only_public_detail(
    status_code: int,
    error_type: type[PresentationClientError],
    detail: str,
) -> None:
    transport = _transport(
        lambda request: httpx.Response(
            status_code,
            json={"detail": detail},
            request=request,
        )
    )

    with pytest.raises(error_type) as captured:
        generate_presentation(
            api_base_url="http://backend.test",
            question="Show approved revenue",
            transport=transport,
        )

    assert captured.value.public_message == detail


def test_timeout_is_sanitized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(
            "internal timeout detail",
            request=request,
        )

    with pytest.raises(
        PresentationTransportError,
        match="Presentation service timed out",
    ):
        generate_presentation(
            api_base_url="http://backend.test",
            question="Show approved revenue",
            transport=_transport(handler),
        )


def test_network_failure_is_sanitized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "internal connection detail",
            request=request,
        )

    with pytest.raises(
        PresentationTransportError,
        match="Presentation service could not be reached",
    ):
        generate_presentation(
            api_base_url="http://backend.test",
            question="Show approved revenue",
            transport=_transport(handler),
        )


def test_malformed_success_response_is_rejected() -> None:
    transport = _transport(
        lambda request: httpx.Response(
            200,
            content=b'{"unexpected":true}',
            headers=_version_headers(),
            request=request,
        )
    )

    with pytest.raises(
        PresentationProtocolError,
        match="returned an invalid response",
    ):
        generate_presentation(
            api_base_url="http://backend.test",
            question="Show approved revenue",
            transport=transport,
        )


def test_inconsistent_version_headers_are_rejected() -> None:
    headers = _version_headers()
    headers["X-Analytics-Version"] = "different"

    transport = _transport(
        lambda request: httpx.Response(
            200,
            json=_valid_payload(),
            headers=headers,
            request=request,
        )
    )

    with pytest.raises(
        PresentationProtocolError,
        match="inconsistent version metadata",
    ):
        generate_presentation(
            api_base_url="http://backend.test",
            question="Show approved revenue",
            transport=transport,
        )


def test_unexpected_status_does_not_expose_response_detail() -> None:
    sensitive_detail = "internal traceback and database metadata"
    transport = _transport(
        lambda request: httpx.Response(
            500,
            json={"detail": sensitive_detail},
            request=request,
        )
    )

    with pytest.raises(
        PresentationProtocolError,
        match="unexpected response",
    ) as captured:
        generate_presentation(
            api_base_url="http://backend.test",
            question="Show approved revenue",
            transport=transport,
        )

    assert sensitive_detail not in captured.value.public_message


def test_invalid_question_is_rejected_before_transport() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        del request
        called = True
        return httpx.Response(500)

    with pytest.raises(
        PresentationRequestRejectedError,
        match="Presentation request is invalid",
    ):
        generate_presentation(
            api_base_url="http://backend.test",
            question="   ",
            transport=_transport(handler),
        )

    assert called is False


@pytest.mark.parametrize(
    ("api_base_url", "timeout_seconds"),
    [
        ("", 15.0),
        ("backend.test", 15.0),
        ("http://user:password@backend.test", 15.0),
        ("http://backend.test", 0.0),
        ("http://backend.test", float("inf")),
    ],
)
def test_invalid_internal_configuration_is_rejected(
    api_base_url: str,
    timeout_seconds: float,
) -> None:
    with pytest.raises(PresentationClientConfigurationError):
        generate_presentation(
            api_base_url=api_base_url,
            question="Show approved revenue",
            timeout_seconds=timeout_seconds,
        )
