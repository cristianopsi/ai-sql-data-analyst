"""Streamlit entrypoint for governed analytical presentations."""

from __future__ import annotations

from typing import Protocol

import streamlit as st

from backend.app.core.config import Settings
from backend.app.schemas.presentation import (
    AnalyticalPresentationResult,
)
from frontend.api_client import (
    PresentationClientError,
    generate_presentation,
)
from frontend.rendering import render_presentation

_RESULT_SESSION_KEY = "analytical_presentation_result"


class PresentationGenerator(Protocol):
    """Callable boundary used to isolate the Streamlit application."""

    def __call__(
        self,
        *,
        api_base_url: str,
        question: str,
    ) -> AnalyticalPresentationResult:
        """Generate one validated analytical presentation."""
        ...


def run_application(
    *,
    generator: PresentationGenerator = generate_presentation,
    settings: Settings | None = None,
) -> None:
    """Compose the question-only Streamlit application."""
    st.set_page_config(
        page_title="AI SQL Data Analyst",
        page_icon="📊",
        layout="wide",
    )

    st.title("AI SQL Data Analyst")
    st.caption(
        "Ask a governed analytical question. "
        "SQL execution, calculations and evidence validation "
        "remain controlled by the backend."
    )

    resolved_settings = settings or Settings()

    with st.form("analytical-question-form"):
        question = st.text_input(
            "Question",
            placeholder="Show approved revenue by region",
            max_chars=2000,
        )
        submitted = st.form_submit_button(
            "Generate presentation",
            type="primary",
        )

    if submitted:
        st.session_state.pop(
            _RESULT_SESSION_KEY,
            None,
        )

        try:
            with st.spinner("Generating the analytical presentation..."):
                result = generator(
                    api_base_url=(resolved_settings.api_base_url),
                    question=question,
                )
        except PresentationClientError as error:
            st.error(error.public_message)
        except Exception:  # noqa: BLE001
            st.error("Presentation could not be displayed safely")
        else:
            st.session_state[_RESULT_SESSION_KEY] = result

    stored_result = st.session_state.get(_RESULT_SESSION_KEY)

    if isinstance(
        stored_result,
        AnalyticalPresentationResult,
    ):
        render_presentation(stored_result)
    elif not submitted:
        st.info(
            "Submit a question to generate a table, "
            "deterministic visualizations and grounded insights."
        )


def main() -> None:
    """Run the production Streamlit application."""
    run_application()


if __name__ == "__main__":
    main()
