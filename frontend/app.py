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
        "Faça uma pergunta analítica governada. A execução de SQL, os cálculos "
        "e a validação de evidências permanecem controlados pelo backend."
    )

    resolved_settings = settings or Settings()

    with st.form("analytical-question-form"):
        question = st.text_input(
            "Pergunta",
            placeholder="Ex.: receita aprovada por região",
            max_chars=2000,
        )
        submitted = st.form_submit_button(
            "Gerar apresentação",
            type="primary",
        )

    if submitted:
        st.session_state.pop(
            _RESULT_SESSION_KEY,
            None,
        )

        try:
            with st.spinner("Gerando a apresentação analítica..."):
                result = generator(
                    api_base_url=(resolved_settings.api_base_url),
                    question=question,
                )
        except PresentationClientError as error:
            st.error(error.public_message)
        except Exception:  # noqa: BLE001
            st.error("Não foi possível exibir a apresentação com segurança")
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
            "Envie uma pergunta para gerar uma tabela, visualizações "
            "determinísticas e insights fundamentados."
        )


def main() -> None:
    """Run the production Streamlit application."""
    run_application()


if __name__ == "__main__":
    main()
