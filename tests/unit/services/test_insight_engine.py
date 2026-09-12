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


def _analytics_result_with_total(total: str) -> DeterministicAnalyticsResult:
    total_decimal = Decimal(total)
    summary = AnalyticsMetricSummary(
        metric_name="approved_revenue",
        unit="brl",
        value_count=2,
        total=total_decimal,
        average=total_decimal / Decimal("2"),
        minimum=total_decimal * Decimal("0.25"),
        maximum=total_decimal,
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


def _claim_response(text: str) -> str:
    return json.dumps(
        {
            "summary": ("A receita aprovada está fundamentada nos dados."),
            "claims": [
                {
                    "text": text,
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


def test_engine_accepts_ptbr_comma_decimal_with_rounding() -> None:
    # pt-BR: vírgula decimal + arredondado p/ 2 casas. Fonte 300.009 -> 300.01.
    engine = GroundedInsightEngine(
        StubLLMProvider(_claim_response("A receita aprovada totaliza 300,01."))
    )
    result = engine.generate(
        _analytics_result_with_total("300.009"),
        _visualization_result(),
    )
    assert result.claims[0].text == "A receita aprovada totaliza 300,01."


def test_engine_accepts_english_decimal_with_rounding() -> None:
    # en-US: ponto decimal + arredondado p/ 2 casas. Fonte 300.009 -> 300.01.
    engine = GroundedInsightEngine(
        StubLLMProvider(_claim_response("A receita aprovada totaliza 300.01."))
    )
    result = engine.generate(
        _analytics_result_with_total("300.009"),
        _visualization_result(),
    )
    assert result.claims[0].text == "A receita aprovada totaliza 300.01."


def test_engine_accepts_ptbr_thousands_separator() -> None:
    # pt-BR: separador de milhar + vírgula decimal. 1.234,56 == 1234.56.
    engine = GroundedInsightEngine(
        StubLLMProvider(_claim_response("A receita aprovada totaliza 1.234,56."))
    )
    result = engine.generate(
        _analytics_result_with_total("1234.56"),
        _visualization_result(),
    )
    assert result.claims[0].text == "A receita aprovada totaliza 1.234,56."


def test_engine_accepts_english_thousands_separator() -> None:
    # en-US: separador de milhar + ponto decimal. 1,234.56 == 1234.56.
    engine = GroundedInsightEngine(
        StubLLMProvider(_claim_response("A receita aprovada totaliza 1,234.56."))
    )
    result = engine.generate(
        _analytics_result_with_total("1234.56"),
        _visualization_result(),
    )
    assert result.claims[0].text == "A receita aprovada totaliza 1,234.56."


def test_engine_still_rejects_uncited_value_after_tolerance() -> None:
    # Segurança preservada: número genuinamente não citado continua rejeitado.
    engine = GroundedInsightEngine(
        StubLLMProvider(_claim_response("A receita aprovada totaliza 999.99."))
    )
    with pytest.raises(
        InsightProviderResponseError,
        match="uncited numeric value",
    ):
        engine.generate(
            _analytics_result_with_total("300.00"),
            _visualization_result(),
        )


def test_engine_accepts_claim_mentioning_dimension_absent_from_packet() -> None:
    """GAP DOCUMENTADO: o engine aceita um claim que cita uma dimensão
    ausente do pacote de evidências.

    O pacote agrupa por sales_channel (web, mobile, store, marketplace).
    O claim abaixo menciona 'região'/'Sudeste' — dimensão que NÃO existe
    no pacote. O grounding valida apenas (1) a referência resolve para
    uma fonte real e (2) todo número do claim rastreia um valor da fonte.
    Ele NÃO valida o vocabulário de dimensão da prosa livre do claim.

    Este teste documenta o comportamento atual (aceita) para provar o
    gap: se o engine passar a rejeitar dimensões ausentes, este teste
    deve ser atualizado para esperar InsightProviderResponseError.
    """
    from backend.app.schemas.analytics import (
        AnalyticsRanking,
        AnalyticsRankingItem,
    )

    ranking = AnalyticsRanking.model_construct(
        metric_name="approved_revenue",
        dimension_name="sales_channel",
        items=(
            AnalyticsRankingItem.model_construct(
                rank=1,
                dimension_value="web",
                value=Decimal("58651629.67"),
                share_percent=Decimal("37.6809"),
            ),
            AnalyticsRankingItem.model_construct(
                rank=2,
                dimension_value="mobile",
                value=Decimal("41795929.68"),
                share_percent=Decimal("26.8519"),
            ),
            AnalyticsRankingItem.model_construct(
                rank=3,
                dimension_value="store",
                value=Decimal("32392364.25"),
                share_percent=Decimal("20.8106"),
            ),
            AnalyticsRankingItem.model_construct(
                rank=4,
                dimension_value="marketplace",
                value=Decimal("22813522.13"),
                share_percent=Decimal("14.6566"),
            ),
        ),
    )
    analytics = DeterministicAnalyticsResult.model_construct(
        analytics_version="1",
        analytics_status="analyzed",
        deterministic=True,
        calculation_scale=4,
        execution_version="1",
        semantic_version="1",
        catalog_version="1",
        source_row_count=4,
        metric_summaries=(),
        rankings=(ranking,),
        series=(),
    )
    viz = DeterministicVisualizationResult.model_construct(
        visualization_version="1",
        visualization_status="specified",
        deterministic=True,
        analytics_version="1",
        execution_version="1",
        semantic_version="1",
        catalog_version="1",
        source_row_count=4,
        specifications=(),
    )

    # Claim cita 'região'/'Sudeste' — dimensão AUSENTE do pacote
    # (que agrupa por sales_channel). O número 37,68% rastreia a fonte
    # (share_percent=37.6809) e a referência resolve para o ranking.
    payload = json.loads(_valid_response())
    payload["claims"][0]["text"] = (
        "A receita por região é liderada pelo Sudeste, com participação de 37,68%."
    )
    payload["claims"][0]["evidence"] = [
        {
            "evidence_type": "ranking",
            "metric_name": "approved_revenue",
            "specification_id": None,
        }
    ]
    engine = GroundedInsightEngine(StubLLMProvider(json.dumps(payload)))

    # REGRESSÃO: o gate de dimensão agora rejeita a dimensão ausente.
    with pytest.raises(
        InsightProviderResponseError,
        match="dimension absent from the evidence packet",
    ):
        engine.generate(analytics, viz)


def _ranking_result(
    dimension_name: str,
    dimension_values: tuple[str, ...],
) -> DeterministicAnalyticsResult:
    from backend.app.schemas.analytics import (
        AnalyticsRanking,
        AnalyticsRankingItem,
    )

    items = tuple(
        AnalyticsRankingItem.model_construct(
            rank=index,
            dimension_value=value,
            value=Decimal("58651629.67"),
            share_percent=Decimal("37.6809"),
        )
        for index, value in enumerate(dimension_values, start=1)
    )
    ranking = AnalyticsRanking.model_construct(
        metric_name="approved_revenue",
        dimension_name=dimension_name,
        items=items,
    )
    return DeterministicAnalyticsResult.model_construct(
        analytics_version="1",
        analytics_status="analyzed",
        deterministic=True,
        calculation_scale=4,
        execution_version="1",
        semantic_version="1",
        catalog_version="1",
        source_row_count=len(dimension_values),
        metric_summaries=(),
        rankings=(ranking,),
        series=(),
    )


def _empty_visualization_result(
    source_row_count: int = 0,
) -> DeterministicVisualizationResult:
    return DeterministicVisualizationResult.model_construct(
        visualization_version="1",
        visualization_status="specified",
        deterministic=True,
        analytics_version="1",
        execution_version="1",
        semantic_version="1",
        catalog_version="1",
        source_row_count=source_row_count,
        specifications=(),
    )


def test_engine_rejects_claim_mentioning_dimension_absent_from_packet() -> None:
    """GATE: claim que cita dimensão ausente do pacote é rejeitado.

    O ranking agrupa por sales_channel (web, mobile, store, marketplace).
    O claim cita 'região'/'Sudeste' — dimensão que NÃO existe no pacote.
    'região' -> canônica 'region' não casa com dimension_name
    'sales_channel' -> rejeitado.
    """
    analytics = _ranking_result(
        "sales_channel",
        ("web", "mobile", "store", "marketplace"),
    )
    payload = json.loads(_valid_response())
    payload["claims"][0]["text"] = (
        "A receita por região é liderada pelo Sudeste, com participação de 37,68%."
    )
    payload["claims"][0]["evidence"] = [
        {
            "evidence_type": "ranking",
            "metric_name": "approved_revenue",
            "specification_id": None,
        }
    ]
    engine = GroundedInsightEngine(StubLLMProvider(json.dumps(payload)))
    with pytest.raises(
        InsightProviderResponseError,
        match="dimension absent from the evidence packet",
    ):
        engine.generate(analytics, _empty_visualization_result(source_row_count=4))


def test_engine_accepts_claim_mentioning_supported_dimension() -> None:
    """FALSO-POSITIVO EVITADO: 'canal' é a dimensão suportada.

    'canal'/'channel' não estão no léxico de indicadores, então o claim
    legítimo citando a dimensão real do pacote é aceito.
    """
    analytics = _ranking_result(
        "sales_channel",
        ("web", "mobile", "store", "marketplace"),
    )
    payload = json.loads(_valid_response())
    payload["claims"][0]["text"] = (
        "O canal web é o principal gerador de receita aprovada, com participação de 37,68%."
    )
    payload["claims"][0]["evidence"] = [
        {
            "evidence_type": "ranking",
            "metric_name": "approved_revenue",
            "specification_id": None,
        }
    ]
    engine = GroundedInsightEngine(StubLLMProvider(json.dumps(payload)))
    result = engine.generate(analytics, _empty_visualization_result(source_row_count=4))
    assert result.claims[0].text == (
        "O canal web é o principal gerador de receita aprovada, com participação de 37,68%."
    )


def test_engine_accepts_claim_mentioning_supported_category_dimension() -> None:
    """FALSO-POSITIVO EVITADO: 'categoria' ancorada em 'product_category'.

    O ranking agrupa por product_category. 'categoria' -> canônica
    'category' casa com dimension_name 'product_category' via substring.
    """
    analytics = _ranking_result(
        "product_category",
        ("Eletrônicos", "Moda", "Casa"),
    )
    payload = json.loads(_valid_response())
    payload["claims"][0]["text"] = (
        "A receita por categoria de produto é liderada por Eletrônicos, com participação de 37,68%."
    )
    payload["claims"][0]["evidence"] = [
        {
            "evidence_type": "ranking",
            "metric_name": "approved_revenue",
            "specification_id": None,
        }
    ]
    engine = GroundedInsightEngine(StubLLMProvider(json.dumps(payload)))
    result = engine.generate(analytics, _empty_visualization_result(source_row_count=3))
    assert result.claims[0].text == (
        "A receita por categoria de produto é liderada por Eletrônicos, com participação de 37,68%."
    )


def test_engine_accepts_claim_without_dimension_mention() -> None:
    """FALSO-POSITIVO EVITADO: claim sem palavra de dimensão é aceito."""
    payload = json.loads(_valid_response())
    payload["claims"][0]["text"] = (
        "A receita aprovada totaliza 300.00, conforme indicador principal."
    )
    payload["claims"][0]["evidence"] = [
        {
            "evidence_type": "metric_summary",
            "metric_name": "approved_revenue",
            "specification_id": None,
        }
    ]
    engine = GroundedInsightEngine(StubLLMProvider(json.dumps(payload)))
    result = engine.generate(
        _analytics_result(),
        _visualization_result(),
    )
    assert result.claims[0].text == (
        "A receita aprovada totaliza 300.00, conforme indicador principal."
    )
