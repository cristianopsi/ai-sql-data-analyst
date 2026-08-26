"""Tests for the Streamlit application composition."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from backend.app.core.config import Settings
from backend.app.schemas.presentation import (
    AnalyticalPresentationResult,
)
from frontend import app as streamlit_app
from frontend.api_client import (
    PresentationClientConfigurationError,
    PresentationProtocolError,
    PresentationRequestRejectedError,
    PresentationServiceUnavailableError,
    PresentationTransportError,
)


def _settings() -> Settings:
    return Settings.model_construct(api_base_url="http://backend.test")


def _result() -> AnalyticalPresentationResult:
    return AnalyticalPresentationResult.model_construct()


def _configure_streamlit(
    monkeypatch: pytest.MonkeyPatch,
    *,
    submitted: bool,
    question: str = "Show approved revenue by region",
    session_state: dict[str, object] | None = None,
) -> dict[str, object]:
    state = session_state if session_state is not None else {}
    controls: dict[str, object] = {
        "state": state,
        "set_page_config": Mock(),
        "title": Mock(),
        "caption": Mock(),
        "form": Mock(return_value=nullcontext()),
        "text_input": Mock(return_value=question),
        "form_submit_button": Mock(return_value=submitted),
        "spinner": Mock(return_value=nullcontext()),
        "error": Mock(),
        "info": Mock(),
        "render": Mock(),
    }

    monkeypatch.setattr(
        streamlit_app.st,
        "session_state",
        state,
    )

    for name in (
        "set_page_config",
        "title",
        "caption",
        "form",
        "text_input",
        "form_submit_button",
        "spinner",
        "error",
        "info",
    ):
        monkeypatch.setattr(
            streamlit_app.st,
            name,
            controls[name],
        )

    monkeypatch.setattr(
        streamlit_app,
        "render_presentation",
        controls["render"],
    )
    return controls


def test_application_initial_interface_with_apptest() -> None:
    application_path = Path(__file__).resolve().parents[3] / "frontend" / "app.py"
    application = AppTest.from_file(str(application_path))
    application.run(timeout=10)

    assert len(application.exception) == 0
    assert len(application.title) == 1
    assert application.title[0].value == "AI SQL Data Analyst"
    assert len(application.text_input) == 1
    assert application.text_input[0].label == "Question"
    assert len(application.button) == 1
    assert application.button[0].label == "Generate presentation"
    assert len(application.info) == 1
    assert len(application.error) == 0


def test_successful_submission_uses_question_only_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controls = _configure_streamlit(
        monkeypatch,
        submitted=True,
    )
    expected_result = _result()
    received: list[dict[str, str]] = []

    def generator(
        *,
        api_base_url: str,
        question: str,
    ) -> AnalyticalPresentationResult:
        received.append(
            {
                "api_base_url": api_base_url,
                "question": question,
            }
        )
        return expected_result

    streamlit_app.run_application(
        generator=generator,
        settings=_settings(),
    )

    assert received == [
        {
            "api_base_url": "http://backend.test",
            "question": ("Show approved revenue by region"),
        }
    ]
    assert list(
        controls["state"].values()  # type: ignore[union-attr]
    ) == [expected_result]
    controls["render"].assert_called_once_with(  # type: ignore[union-attr]
        expected_result
    )
    controls["error"].assert_not_called()  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "error",
    [
        PresentationClientConfigurationError("Presentation service URL is invalid"),
        PresentationRequestRejectedError("Presentation request is invalid"),
        PresentationServiceUnavailableError("Presentation service is unavailable"),
        PresentationTransportError("Presentation service could not be reached"),
        PresentationProtocolError("Presentation service returned an invalid response"),
    ],
)
def test_controlled_client_errors_render_only_public_message(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    controls = _configure_streamlit(
        monkeypatch,
        submitted=True,
    )

    def generator(
        *,
        api_base_url: str,
        question: str,
    ) -> AnalyticalPresentationResult:
        del api_base_url, question
        raise error

    streamlit_app.run_application(
        generator=generator,
        settings=_settings(),
    )

    controls["error"].assert_called_once_with(  # type: ignore[union-attr]
        str(error)
    )
    controls["render"].assert_not_called()  # type: ignore[union-attr]
    assert controls["state"] == {}


def test_unexpected_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controls = _configure_streamlit(
        monkeypatch,
        submitted=True,
    )
    sensitive_detail = "database password and internal traceback"

    def generator(
        *,
        api_base_url: str,
        question: str,
    ) -> AnalyticalPresentationResult:
        del api_base_url, question
        raise RuntimeError(sensitive_detail)

    streamlit_app.run_application(
        generator=generator,
        settings=_settings(),
    )

    controls["error"].assert_called_once_with(  # type: ignore[union-attr]
        "Presentation could not be displayed safely"
    )
    assert (
        sensitive_detail not in controls["error"].call_args.args[0]  # type: ignore[union-attr]
    )
    controls["render"].assert_not_called()  # type: ignore[union-attr]


def test_validated_result_persists_across_rerun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_result = _result()
    state: dict[str, object] = {"analytical_presentation_result": (expected_result)}
    controls = _configure_streamlit(
        monkeypatch,
        submitted=False,
        session_state=state,
    )
    generator = Mock()

    streamlit_app.run_application(
        generator=generator,
        settings=_settings(),
    )

    generator.assert_not_called()
    controls["render"].assert_called_once_with(  # type: ignore[union-attr]
        expected_result
    )
    controls["info"].assert_not_called()  # type: ignore[union-attr]


def test_nonvalidated_session_value_is_not_rendered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state: dict[str, object] = {"analytical_presentation_result": {"unsafe": "value"}}
    controls = _configure_streamlit(
        monkeypatch,
        submitted=False,
        session_state=state,
    )

    streamlit_app.run_application(
        generator=Mock(),
        settings=_settings(),
    )

    controls["render"].assert_not_called()  # type: ignore[union-attr]
    controls["info"].assert_called_once()  # type: ignore[union-attr]
