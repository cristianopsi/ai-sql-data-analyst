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
    GroundedInsightClaim,
    GroundedInsightRequest,
    GroundedInsightResult,
    InsightEvidenceReference,
    InsightNarrativeProposal,
    insight_claim_id,
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
        response_provider: str | None = None,
        response_model: str | None = None,
    ) -> None:
        self._content = content
        self._finish_reason = finish_reason
        self._response_provider = response_provider or self.provider_name
        self._response_model = response_model or self.model_name
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
            provider=self._response_provider,
            model=self._response_model,
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
        aggregation="sum",
        total=Decimal("300.00"),
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


def test_claim_requires_canonical_identifier() -> None:
    evidence = InsightEvidenceReference(
        evidence_type="metric_summary",
        metric_name="approved_revenue",
    )

    with pytest.raises(
        ValidationError,
        match="claim ID must be canonical",
    ):
        GroundedInsightClaim(
            claim_id=f"claim-{'0' * 24}",
            text="Approved revenue is grounded.",
            evidence=(evidence,),
        )


def test_result_requires_positive_source_row_count() -> None:
    result = GroundedInsightEngine(StubLLMProvider(_valid_response())).generate(
        _analytics_result(),
        _visualization_result(),
    )

    with pytest.raises(ValidationError):
        GroundedInsightResult(
            analytics_version=result.analytics_version,
            visualization_version=result.visualization_version,
            execution_version=result.execution_version,
            semantic_version=result.semantic_version,
            catalog_version=result.catalog_version,
            source_row_count=0,
            provider=result.provider,
            model=result.model,
            usage=result.usage,
            summary=result.summary,
            claims=result.claims,
        )


def test_result_rejects_semantic_duplicate_claim_by_evidence_order() -> None:
    metric_evidence = InsightEvidenceReference(
        evidence_type="metric_summary",
        metric_name="approved_revenue",
    )
    visualization_evidence = InsightEvidenceReference(
        evidence_type="visualization",
        specification_id="kpi-approved-revenue",
    )
    text = "Approved revenue is grounded."
    ordered_evidence = (
        metric_evidence,
        visualization_evidence,
    )
    reversed_evidence = tuple(reversed(ordered_evidence))

    first = GroundedInsightClaim(
        claim_id=insight_claim_id(
            text,
            ordered_evidence,
        ),
        text=text,
        evidence=ordered_evidence,
    )
    second = GroundedInsightClaim(
        claim_id=insight_claim_id(
            text,
            reversed_evidence,
        ),
        text=text,
        evidence=reversed_evidence,
    )

    assert first.claim_id == second.claim_id

    with pytest.raises(
        ValidationError,
        match="semantic claims must be unique",
    ):
        GroundedInsightResult(
            analytics_version="1",
            visualization_version="1",
            execution_version="1",
            semantic_version="1",
            catalog_version="1",
            source_row_count=2,
            provider="stub",
            model="stub-insight-model",
            usage=LLMTokenUsage(
                input_tokens=1,
                output_tokens=1,
            ),
            summary="Approved revenue is grounded.",
            claims=(
                first,
                second,
            ),
        )


def test_engine_rejects_sql_with_nonspace_whitespace() -> None:
    payload = json.loads(_valid_response())
    payload["claims"][0]["text"] = "SELECT\napproved_revenue FROM retail.orders."
    engine = GroundedInsightEngine(StubLLMProvider(json.dumps(payload)))

    with pytest.raises(
        InsightProviderResponseError,
        match="prohibited material",
    ):
        engine.generate(
            _analytics_result(),
            _visualization_result(),
        )


def test_engine_rejects_shorthand_uncited_decimal() -> None:
    payload = json.loads(_valid_response())
    payload["claims"][0]["text"] = "A variação não fundamentada é .2."
    engine = GroundedInsightEngine(StubLLMProvider(json.dumps(payload)))

    with pytest.raises(
        InsightProviderResponseError,
        match="uncited numeric value",
    ):
        engine.generate(
            _analytics_result(),
            _visualization_result(),
        )


def test_engine_rejects_duplicate_json_keys() -> None:
    content = _valid_response().replace(
        '"summary":',
        '"summary": "Resumo duplicado.", "summary":',
        1,
    )
    engine = GroundedInsightEngine(StubLLMProvider(content))

    with pytest.raises(
        InsightProviderResponseError,
        match="duplicate JSON keys",
    ):
        engine.generate(
            _analytics_result(),
            _visualization_result(),
        )


@pytest.mark.parametrize(
    (
        "response_provider",
        "response_model",
        "expected_message",
    ),
    (
        (
            "forged-provider",
            None,
            "provider identity",
        ),
        (
            None,
            "forged-model",
            "model identity",
        ),
    ),
)
def test_engine_rejects_provider_identity_mismatch(
    response_provider: str | None,
    response_model: str | None,
    expected_message: str,
) -> None:
    provider = StubLLMProvider(
        _valid_response(),
        response_provider=response_provider,
        response_model=response_model,
    )
    engine = GroundedInsightEngine(provider)

    with pytest.raises(
        InsightProviderResponseError,
        match=expected_message,
    ):
        engine.generate(
            _analytics_result(),
            _visualization_result(),
        )
