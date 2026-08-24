"""Unit tests for grounded narrative insight generation."""

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.app.schemas.analytics import (
    AnalyticsMetricSummary,
    DeterministicAnalyticsResult,
)
from backend.app.schemas.insights import (
    GroundedInsightRequest,
    InsightEvidenceReference,
    InsightNarrativeProposal,
)
from backend.app.schemas.llm import (
    LLMFinishReason,
    LLMGenerationRequest,
    LLMGenerationResponse,
    LLMTokenUsage,
)
from backend.app.schemas.visualization import (
    DeterministicVisualizationResult,
    KPIVisualizationSpec,
)
from backend.app.services.insight_engine import (
    GroundedInsightEngine,
    InsightInputError,
    InsightProviderResponseError,
    create_insight_engine,
)


class StubLLMProvider:
    """Record requests and return one controlled response."""

    def __init__(
        self,
        content: str,
        *,
        finish_reason: LLMFinishReason = "stop",
    ) -> None:
        self._content = content
        self._finish_reason = finish_reason
        self.requests: list[LLMGenerationRequest] = []
        self.closed = False

    @property
    def provider_name(self) -> str:
        return "stub"

    @property
    def model_name(self) -> str:
        return "stub-insight-model"

    def generate(
        self,
        request: LLMGenerationRequest,
    ) -> LLMGenerationResponse:
        self.requests.append(request)
        return LLMGenerationResponse(
            provider=self.provider_name,
            model=self.model_name,
            content=self._content,
            finish_reason=self._finish_reason,
            usage=LLMTokenUsage(
                input_tokens=120,
                output_tokens=40,
            ),
        )

    def close(self) -> None:
        self.closed = True


def _analytics_result() -> DeterministicAnalyticsResult:
    summary = AnalyticsMetricSummary(
        metric_name="approved_revenue",
        unit="brl",
        value_count=2,
        total=Decimal("300.00"),
        average=Decimal("150.00"),
        minimum=Decimal("100.00"),
        maximum=Decimal("200.00"),
    )

    return DeterministicAnalyticsResult.model_construct(
        analytics_version="1",
        analytics_status="analyzed",
        deterministic=True,
        calculation_scale=4,
        execution_version="1",
        semantic_version="1",
        catalog_version="1",
        source_row_count=2,
        metric_summaries=(summary,),
        rankings=(),
        series=(),
    )


def _visualization_result() -> DeterministicVisualizationResult:
    specification = KPIVisualizationSpec(
        spec_id="kpi-approved-revenue",
        title="Approved revenue",
        metric_name="approved_revenue",
        unit="brl",
        value_count=2,
        value=Decimal("300.00"),
        average=Decimal("150.00"),
        minimum=Decimal("100.00"),
        maximum=Decimal("200.00"),
    )

    return DeterministicVisualizationResult.model_construct(
        visualization_version="1",
        visualization_status="specified",
        deterministic=True,
        analytics_version="1",
        execution_version="1",
        semantic_version="1",
        catalog_version="1",
        source_row_count=2,
        specifications=(specification,),
    )


def _valid_response() -> str:
    return json.dumps(
        {
            "summary": ("A receita aprovada está fundamentada nos dados."),
            "claims": [
                {
                    "text": ("A receita aprovada totaliza 300.00."),
                    "evidence": [
                        {
                            "evidence_type": "metric_summary",
                            "metric_name": "approved_revenue",
                            "specification_id": None,
                        }
                    ],
                }
            ],
        }
    )


def _generate_valid_result() -> tuple[
    StubLLMProvider,
    object,
]:
    provider = StubLLMProvider(_valid_response())
    result = GroundedInsightEngine(provider).generate(
        _analytics_result(),
        _visualization_result(),
    )
    return provider, result


def test_insight_request_is_question_only_and_strict() -> None:
    request = GroundedInsightRequest(
        question="Explique a receita aprovada",
    )

    assert tuple(type(request).model_fields) == ("question",)

    with pytest.raises(ValidationError):
        GroundedInsightRequest.model_validate(
            {
                "question": "Explique a receita",
                "sql": "SELECT 1",
            }
        )


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        (
            {
                "evidence_type": "visualization",
                "metric_name": "approved_revenue",
                "specification_id": "kpi-approved-revenue",
            },
            "Visualization evidence requires only",
        ),
        (
            {
                "evidence_type": "metric_summary",
                "metric_name": None,
                "specification_id": None,
            },
            "Analytics evidence requires only",
        ),
    ],
)
def test_evidence_reference_requires_exact_target(
    payload: dict[str, str | None],
    expected_message: str,
) -> None:
    with pytest.raises(
        ValidationError,
        match=expected_message,
    ):
        InsightEvidenceReference.model_validate(payload)


def test_proposal_rejects_duplicate_evidence() -> None:
    content = json.dumps(
        {
            "summary": "A receita possui evidência.",
            "claims": [
                {
                    "text": "A receita está confirmada.",
                    "evidence": [
                        {
                            "evidence_type": "metric_summary",
                            "metric_name": "approved_revenue",
                            "specification_id": None,
                        },
                        {
                            "evidence_type": "metric_summary",
                            "metric_name": "approved_revenue",
                            "specification_id": None,
                        },
                    ],
                }
            ],
        }
    )

    with pytest.raises(
        ValidationError,
        match="references must be unique",
    ):
        InsightNarrativeProposal.model_validate_json(content)


def test_engine_generates_grounded_claim() -> None:
    provider = StubLLMProvider(_valid_response())
    engine = GroundedInsightEngine(provider)

    result = engine.generate(
        _analytics_result(),
        _visualization_result(),
    )

    assert result.insight_version == "1"
    assert result.insight_status == "generated"
    assert result.grounded is True
    assert result.calculated_by_llm is False
    assert result.provider == "stub"
    assert result.model == "stub-insight-model"
    assert result.usage.input_tokens == 120
    assert result.usage.output_tokens == 40
    assert result.source_row_count == 2
    assert len(result.claims) == 1
    assert result.claims[0].claim_id.startswith("claim-")
    assert provider.requests[0].temperature == 0.0
    assert provider.requests[0].response_format == "json"
    assert tuple(message.role for message in provider.requests[0].messages) == ("system", "user")


def test_provider_receives_only_allowlisted_evidence() -> None:
    provider = StubLLMProvider(_valid_response())
    engine = GroundedInsightEngine(provider)

    engine.generate(
        _analytics_result(),
        _visualization_result(),
    )

    evidence_content = provider.requests[0].messages[1].content
    evidence = json.loads(evidence_content)

    assert tuple(evidence) == (
        "allowed_metric_names",
        "allowed_specification_ids",
        "analytics",
        "visualizations",
    )
    assert "sql" not in evidence_content.casefold()
    assert "rows" not in evidence
    assert "internal_context" not in evidence_content
    assert "column_metadata" not in evidence_content


def test_claim_identifiers_are_stable() -> None:
    provider = StubLLMProvider(_valid_response())
    engine = GroundedInsightEngine(provider)
    analytics = _analytics_result()
    visualizations = _visualization_result()

    first = engine.generate(analytics, visualizations)
    second = engine.generate(analytics, visualizations)

    assert first.claims[0].claim_id == second.claims[0].claim_id
    assert len(provider.requests) == 2


def test_engine_rejects_unknown_metric_reference() -> None:
    content = json.dumps(
        {
            "summary": "A afirmação não possui evidência conhecida.",
            "claims": [
                {
                    "text": "A métrica desconhecida aumentou.",
                    "evidence": [
                        {
                            "evidence_type": "metric_summary",
                            "metric_name": "unknown_metric",
                            "specification_id": None,
                        }
                    ],
                }
            ],
        }
    )
    engine = GroundedInsightEngine(StubLLMProvider(content))

    with pytest.raises(
        InsightProviderResponseError,
        match="unknown evidence",
    ):
        engine.generate(
            _analytics_result(),
            _visualization_result(),
        )


def test_engine_rejects_unknown_visualization_reference() -> None:
    content = json.dumps(
        {
            "summary": "A afirmação não possui gráfico conhecido.",
            "claims": [
                {
                    "text": "O indicador está disponível.",
                    "evidence": [
                        {
                            "evidence_type": "visualization",
                            "metric_name": None,
                            "specification_id": "unknown-spec",
                        }
                    ],
                }
            ],
        }
    )
    engine = GroundedInsightEngine(StubLLMProvider(content))

    with pytest.raises(
        InsightProviderResponseError,
        match="unknown evidence",
    ):
        engine.generate(
            _analytics_result(),
            _visualization_result(),
        )


def test_engine_rejects_uncited_numeric_value() -> None:
    content = _valid_response().replace(
        "300.00",
        "999.00",
    )
    engine = GroundedInsightEngine(StubLLMProvider(content))

    with pytest.raises(
        InsightProviderResponseError,
        match="uncited numeric value",
    ):
        engine.generate(
            _analytics_result(),
            _visualization_result(),
        )


def test_engine_rejects_numeric_summary() -> None:
    content = json.dumps(
        {
            "summary": "A receita totaliza 300.00.",
            "claims": [
                {
                    "text": "A receita aprovada totaliza 300.00.",
                    "evidence": [
                        {
                            "evidence_type": "metric_summary",
                            "metric_name": "approved_revenue",
                            "specification_id": None,
                        }
                    ],
                }
            ],
        }
    )
    engine = GroundedInsightEngine(StubLLMProvider(content))

    with pytest.raises(
        InsightProviderResponseError,
        match="summary must not contain uncited numbers",
    ):
        engine.generate(
            _analytics_result(),
            _visualization_result(),
        )


def test_engine_rejects_invalid_json() -> None:
    engine = GroundedInsightEngine(StubLLMProvider("not-json"))

    with pytest.raises(
        InsightProviderResponseError,
        match="invalid response",
    ):
        engine.generate(
            _analytics_result(),
            _visualization_result(),
        )


def test_engine_rejects_incomplete_provider_response() -> None:
    engine = GroundedInsightEngine(
        StubLLMProvider(
            _valid_response(),
            finish_reason="length",
        )
    )

    with pytest.raises(
        InsightProviderResponseError,
        match="incomplete",
    ):
        engine.generate(
            _analytics_result(),
            _visualization_result(),
        )


def test_engine_rejects_prohibited_output_material() -> None:
    content = json.dumps(
        {
            "summary": "A receita possui evidência válida.",
            "claims": [
                {
                    "text": "SELECT dados para apresentar a receita.",
                    "evidence": [
                        {
                            "evidence_type": "metric_summary",
                            "metric_name": "approved_revenue",
                            "specification_id": None,
                        }
                    ],
                }
            ],
        }
    )
    engine = GroundedInsightEngine(StubLLMProvider(content))

    with pytest.raises(
        InsightProviderResponseError,
        match="prohibited material",
    ):
        engine.generate(
            _analytics_result(),
            _visualization_result(),
        )


def test_engine_rejects_untrusted_analytics() -> None:
    analytics = _analytics_result().model_copy(
        update={
            "analytics_status": "invalid",
        }
    )
    engine = GroundedInsightEngine(StubLLMProvider(_valid_response()))

    with pytest.raises(
        InsightInputError,
        match="Analytics result is not trusted",
    ):
        engine.generate(
            analytics,
            _visualization_result(),
        )


def test_engine_requires_matching_evidence_versions() -> None:
    visualizations = _visualization_result().model_copy(
        update={
            "source_row_count": 3,
        }
    )
    engine = GroundedInsightEngine(StubLLMProvider(_valid_response()))

    with pytest.raises(
        InsightInputError,
        match="evidence do not match",
    ):
        engine.generate(
            _analytics_result(),
            visualizations,
        )


def test_engine_requires_positive_max_tokens() -> None:
    with pytest.raises(
        ValueError,
        match="max tokens must be positive",
    ):
        GroundedInsightEngine(
            StubLLMProvider(_valid_response()),
            max_tokens=0,
        )


def test_insight_result_is_immutable() -> None:
    provider = StubLLMProvider(_valid_response())
    result = GroundedInsightEngine(provider).generate(
        _analytics_result(),
        _visualization_result(),
    )

    with pytest.raises(ValidationError):
        result.summary = "Changed"


def test_insight_engine_factory_uses_managed_provider() -> None:
    provider = StubLLMProvider(_valid_response())

    engine = create_insight_engine(provider)
    result = engine.generate(
        _analytics_result(),
        _visualization_result(),
    )

    assert isinstance(engine, GroundedInsightEngine)
    assert result.provider == provider.provider_name
    assert len(provider.requests) == 1
