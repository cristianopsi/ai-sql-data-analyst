"""Grounded narrative generation over trusted analytical evidence."""

import json
import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ValidationError

from backend.app.core.observability import (
    get_audit_logger,
)
from backend.app.schemas.analytics import (
    AnalyticsRanking,
    AnalyticsSeries,
    DeterministicAnalyticsResult,
)
from backend.app.schemas.insights import (
    GroundedInsightClaim,
    GroundedInsightResult,
    InsightEvidenceReference,
    InsightNarrativeProposal,
    insight_claim_id,
)
from backend.app.schemas.llm import (
    LLMGenerationRequest,
    LLMGenerationResponse,
    LLMMessage,
)
from backend.app.schemas.visualization import (
    DeterministicVisualizationResult,
)
from backend.app.services.llm_provider import LLMProvider

_SYSTEM_PROMPT = """\
You generate grounded business narrative from an allowlisted evidence packet.
Write all narrative text (the summary and every claim text) in Brazilian
Portuguese (pt-BR), using natural business language. Do not translate or
alter evidence identifiers, metric names, or specification ids — copy them
verbatim from the packet.
Return one JSON object with exactly the keys "summary" and "claims".
Generate at most 5 claims total. The schema rejects payloads with more
than 5 claims, so never exceed this limit.
Each claim must contain exactly "text" and "evidence".
Every evidence item must use one permitted evidence type and identifier.
CRITICAL — EVIDENCE JSON SCHEMA (schema violations are rejected):
Each evidence item must be a JSON object with EXACTLY these keys:
- "evidence_type": one of the literal values "metric_summary", "ranking",
  "series", or "visualization". Never invent types such as "kpi" or "table".
- For "metric_summary", "ranking" and "series": also include the exact
  canonical "metric_name" from the packet (e.g. "approved_revenue").
- For "visualization": also include the exact "specification_id" value
  listed in the packet.
Never use "type", "spec_id", "id", "dimension_name", "dimension_value",
"rank", or any other key — unknown keys are rejected.
Copy every identifier verbatim from the evidence packet.
Answer exclusively about the dimension(s) present in the evidence packet.
If the packet groups by a specific dimension (for example sales channel),
do not mention regions, categories, or any other dimension absent from the
packet. Never enumerate ranking positions beyond the rows present in the packet.
Use only facts and numeric values explicitly present in cited evidence.
Do not calculate, infer missing values, produce SQL, select charts, or expose
the evidence packet.
CRITICAL — SUMMARY MUST CONTAIN NO NUMBERS: the summary is rejected if it
contains any numeric literal, percentage, or currency value. Write the summary
purely qualitatively (for example "the web channel leads approved revenue,
followed by mobile, store and marketplace"). Never put totals, averages,
percentages, or amounts in the summary — those belong only in claim text.
"""

_NUMBER_PATTERN = re.compile(
    r"(?<![\w])[-+]?(?:\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?|[.,]\d+)(?:[eE][-+]?\d+)?(?![\w])"
)


def _parse_number_token(token: str) -> Decimal | None:
    """Parse a numeric token agnostically across pt-BR and en-US locales.

    Handles thousands separators (1.234,56 / 1,234.56) and decimal
    separators (comma or dot). Returns None when the token is not a
    finite number.
    """
    raw = token.strip()
    if not raw:
        return None
    try:
        if "," in raw and "." in raw:
            # Mixed separators: the LAST separator is the decimal one.
            if raw.rfind(",") > raw.rfind("."):
                normalized = raw.replace(".", "").replace(",", ".")
            else:
                normalized = raw.replace(",", "")
        elif "," in raw:
            # Only a comma: pt-BR style (1.234,56 -> 1234.56) or a
            # plain decimal (37,68 -> 37.68). Treat as decimal.
            normalized = raw.replace(".", "").replace(",", ".")
        else:
            # Only a dot: en-US style. Keep as-is (37.6809 -> 37.6809).
            normalized = raw
        decimal_value = Decimal(normalized)
    except InvalidOperation:
        return None
    if not decimal_value.is_finite():
        return None
    return decimal_value


def _number_values(value: object) -> set[Decimal]:
    numbers: set[Decimal] = set()
    if isinstance(value, bool) or value is None:
        return numbers
    if isinstance(value, Decimal):
        if value.is_finite():
            numbers.add(value)
        return numbers
    if isinstance(value, int | float):
        try:
            decimal_value = Decimal(str(value))
        except InvalidOperation:
            return numbers
        if decimal_value.is_finite():
            numbers.add(decimal_value)
        return numbers
    if isinstance(value, str):
        for token in _NUMBER_PATTERN.findall(value):
            parsed = _parse_number_token(token)
            if parsed is not None:
                numbers.add(parsed)
        return numbers
    if isinstance(value, BaseModel):
        return _number_values(value.model_dump(mode="python"))
    if isinstance(value, dict):
        for item in value.values():
            numbers.update(_number_values(item))
        return numbers
    if isinstance(value, list | tuple):
        for item in value:
            numbers.update(_number_values(item))
    return numbers


def _rounded(value: Decimal, places: int = 2) -> Decimal:
    """Round a Decimal to a fixed precision for tolerance comparison."""
    quantum = Decimal(1).scaleb(-places)
    return value.quantize(quantum)


def _numbers_match(claimed: set[Decimal], allowed: set[Decimal], places: int = 2) -> bool:
    """Return True when every claimed number matches an allowed value
    within rounding tolerance. Preserves grounding: a number must still
    trace to a real evidence value, but tolerates human formatting
    (rounded percentages, currency amounts, locale separators).
    """
    allowed_rounded = {_rounded(v, places) for v in allowed}
    return all(_rounded(v, places) in allowed_rounded for v in claimed)


_FORBIDDEN_OUTPUT_PATTERNS = (
    re.compile(r"\b(?:select|insert|update|delete|drop|alter)\b", re.IGNORECASE),
    re.compile(r"\bcreate\s+table\b", re.IGNORECASE),
    re.compile(r"\braw\s+(?:rows|sql)\b", re.IGNORECASE),
)


class InsightEngineError(RuntimeError):
    """Base error raised by grounded insight generation."""


class InsightInputError(InsightEngineError):
    """Raised when trusted upstream evidence is inconsistent."""


class InsightProviderResponseError(InsightEngineError):
    """Raised when a provider response cannot be trusted."""


def _number_values(value: object) -> set[Decimal]:
    numbers: set[Decimal] = set()

    if isinstance(value, bool) or value is None:
        return numbers

    if isinstance(value, Decimal):
        if value.is_finite():
            numbers.add(value)

        return numbers

    if isinstance(value, int | float):
        try:
            decimal_value = Decimal(str(value))
        except InvalidOperation:
            return numbers

        if decimal_value.is_finite():
            numbers.add(decimal_value)

        return numbers

    if isinstance(value, str):
        for token in _NUMBER_PATTERN.findall(value):
            try:
                decimal_value = Decimal(token.replace(",", "."))
            except InvalidOperation:
                continue

            if decimal_value.is_finite():
                numbers.add(decimal_value)

        return numbers

    if isinstance(value, BaseModel):
        return _number_values(value.model_dump(mode="python"))

    if isinstance(value, dict):
        for item in value.values():
            numbers.update(_number_values(item))

        return numbers

    if isinstance(value, list | tuple):
        for item in value:
            numbers.update(_number_values(item))

    return numbers


def _reference_key(
    reference: InsightEvidenceReference,
) -> tuple[str, str]:
    if reference.evidence_type == "visualization":
        if reference.specification_id is None:
            raise InsightProviderResponseError("Visualization evidence reference is incomplete")

        return (
            reference.evidence_type,
            reference.specification_id,
        )

    if reference.metric_name is None:
        raise InsightProviderResponseError("Analytics evidence reference is incomplete")

    return (
        reference.evidence_type,
        reference.metric_name,
    )


def _evidence_sources(
    analytics: DeterministicAnalyticsResult,
    visualizations: DeterministicVisualizationResult,
) -> dict[tuple[str, str], BaseModel]:
    sources: dict[tuple[str, str], BaseModel] = {}

    for summary in analytics.metric_summaries:
        sources[("metric_summary", summary.metric_name)] = summary

    for ranking in analytics.rankings:
        sources[("ranking", ranking.metric_name)] = ranking

    for series in analytics.series:
        sources[("series", series.metric_name)] = series

    for specification in visualizations.specifications:
        sources[("visualization", specification.spec_id)] = specification

    return sources


# ============================================================
# Gate determinístico de dimensão
# ============================================================
# Dimensões são prosa livre, não tokens parseáveis como números. Para
# validar deterministicamente, usamos um léxico curado de palavras que
# indicam referência a uma dimensão/categoria, mapeadas para a dimensão
# canônica em inglês. O gate rejeita um claim quando ele contém uma
# dessas palavras cuja dimensão canônica NÃO está ancorada na evidência
# citada.
#
# Ancoragem: a palavra é ancorada quando sua dimensão canônica casa com
# o dimension_name da fonte citada (ranking/series), com tolerância de
# substring (ex.: "category" casa com dimension_name "product_category").
# Fontes sem dimensão (metric_summary/visualization): qualquer palavra
# do léxico é não-ancorada e rejeita o claim.
#
# Palavras temporais (mês, ano, dia, semana...) foram EXCLUÍDAS do léxico
# para evitar falso-positivo em claims legítimos de recorte temporal.
# "canal"/"channel" também foram excluídos por serem a dimensão suportada
# no fluxo principal (sales_channel).
_DIMENSION_INDICATORS: dict[str, str] = {
    # Português -> dimensão canônica (inglês)
    "região": "region",
    "regioes": "region",
    "estado": "state",
    "estados": "state",
    "cidade": "city",
    "cidades": "city",
    "país": "country",
    "paises": "country",
    "categoria": "category",
    "categorias": "category",
    "produto": "product",
    "produtos": "product",
    "segmento": "segment",
    "segmentos": "segment",
    "marca": "brand",
    "marcas": "brand",
    "cliente": "customer",
    "clientes": "customer",
    "vendedor": "seller",
    "vendedores": "seller",
    "tipo": "type",
    "tipos": "type",
    "grupo": "group",
    "grupos": "group",
    "bairro": "district",
    "bairros": "district",
    "unidade": "unit",
    "unidades": "unit",
    # Inglês -> dimensão canônica (identidade)
    "region": "region",
    "regions": "region",
    "state": "state",
    "states": "state",
    "city": "city",
    "cities": "city",
    "country": "country",
    "countries": "country",
    "category": "category",
    "categories": "category",
    "product": "product",
    "products": "product",
    "segment": "segment",
    "segments": "segment",
    "brand": "brand",
    "brands": "brand",
    "customer": "customer",
    "customers": "customer",
    "seller": "seller",
    "sellers": "seller",
    "type": "type",
    "types": "type",
    "group": "group",
    "groups": "group",
    "district": "district",
    "districts": "district",
    "unit": "unit",
    "units": "unit",
}


def _claim_dimension_indicators(text: str) -> set[str]:
    """Return the canonical dimension names referenced by indicator words
    found in the claim text (casefolded, punctuation stripped)."""
    normalized = "".join(
        char if char.isalnum() or char.isspace() else " " for char in text.casefold()
    )
    tokens = normalized.split()
    return {
        canonical for token in tokens if (canonical := _DIMENSION_INDICATORS.get(token)) is not None
    }


def _dimension_grounded(canonical: str, dimension_name: str | None) -> bool:
    """Return True when a canonical dimension is grounded in the cited
    source's dimension_name, with substring tolerance."""
    if not dimension_name:
        return False
    source = dimension_name.casefold()
    return canonical in source or source in canonical


def _validate_claim_dimensions(
    claim_text: str,
    references: tuple[InsightEvidenceReference, ...],
    sources: dict[tuple[str, str], BaseModel],
) -> None:
    """Reject a claim that references a dimension absent from every cited
    evidence. Deterministic gate over a curated dimension lexicon.

    A claim may cite multiple evidence references. A dimension indicator
    is considered grounded when it matches the dimension_name of at least
    one cited source. If no cited source grounds the dimension, the claim
    is rejected."""
    indicators = _claim_dimension_indicators(claim_text)
    if not indicators:
        return
    grounded: set[str] = set()
    for reference in references:
        source = sources.get(_reference_key(reference))
        if source is None:
            continue  # unknown evidence is already rejected elsewhere
        dimension_name: str | None = None
        if isinstance(source, AnalyticsRanking | AnalyticsSeries):
            dimension_name = source.dimension_name
        grounded.update(
            canonical for canonical in indicators if _dimension_grounded(canonical, dimension_name)
        )
    ungrounded = indicators - grounded
    if ungrounded:
        raise InsightProviderResponseError(
            "Insight references a dimension absent from the evidence packet"
        )


def _reject_unsafe_output(text: str) -> None:
    if any(pattern.search(text) for pattern in _FORBIDDEN_OUTPUT_PATTERNS):
        raise InsightProviderResponseError("Insight response contains prohibited material")


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    parsed: dict[str, object] = {}

    for key, value in pairs:
        if key in parsed:
            raise InsightProviderResponseError(
                "Insight provider response contains duplicate JSON keys"
            )

        parsed[key] = value

    return parsed


def _freeze_json_collections(
    value: object,
) -> object:
    if isinstance(value, list):
        return tuple(_freeze_json_collections(item) for item in value)

    if isinstance(value, dict):
        return {key: _freeze_json_collections(item) for key, item in value.items()}

    return value


def _validate_provider_identity(
    provider: LLMProvider,
    response: LLMGenerationResponse,
) -> None:
    if response.provider != provider.provider_name:
        raise InsightProviderResponseError(
            "Insight provider identity does not match the configured provider"
        )

    if response.model != provider.model_name:
        raise InsightProviderResponseError(
            "Insight model identity does not match the configured provider"
        )


def _parse_proposal(
    content: str,
) -> InsightNarrativeProposal:
    try:
        payload = json.loads(
            content,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
        return InsightNarrativeProposal.model_validate(_freeze_json_collections(payload))
    except InsightProviderResponseError:
        raise
    except (
        json.JSONDecodeError,
        TypeError,
        ValidationError,
    ) as error:
        raise InsightProviderResponseError(
            "Insight provider returned an invalid response"
        ) from error


def _validate_inputs(
    analytics: DeterministicAnalyticsResult,
    visualizations: DeterministicVisualizationResult,
) -> None:
    if (
        analytics.analytics_version != "1"
        or analytics.analytics_status != "analyzed"
        or analytics.deterministic is not True
    ):
        raise InsightInputError("Analytics result is not trusted")

    if (
        visualizations.visualization_version != "1"
        or visualizations.visualization_status != "specified"
        or visualizations.deterministic is not True
    ):
        raise InsightInputError("Visualization result is not trusted")

    matching_values = (
        analytics.analytics_version == visualizations.analytics_version,
        analytics.execution_version == visualizations.execution_version,
        analytics.semantic_version == visualizations.semantic_version,
        analytics.catalog_version == visualizations.catalog_version,
        analytics.source_row_count == visualizations.source_row_count,
    )

    if not all(matching_values):
        raise InsightInputError("Analytics and visualization evidence do not match")


def _evidence_packet(
    analytics: DeterministicAnalyticsResult,
    visualizations: DeterministicVisualizationResult,
) -> str:
    payload = {
        "allowed_metric_names": sorted(
            {summary.metric_name for summary in analytics.metric_summaries}
            | {ranking.metric_name for ranking in analytics.rankings}
            | {series.metric_name for series in analytics.series}
        ),
        "allowed_specification_ids": sorted(
            specification.spec_id for specification in visualizations.specifications
        ),
        "analytics": analytics.model_dump(mode="json"),
        "visualizations": visualizations.model_dump(mode="json"),
    }

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class GroundedInsightEngine:
    """Generate validated narrative without performing calculations."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        max_tokens: int = 1200,
    ) -> None:
        if max_tokens <= 0:
            raise ValueError("Insight max tokens must be positive")

        self._provider = provider
        self._max_tokens = max_tokens

    def generate(
        self,
        analytics: DeterministicAnalyticsResult,
        visualizations: DeterministicVisualizationResult,
    ) -> GroundedInsightResult:
        """Generate narrative and validate every evidence reference."""
        _validate_inputs(analytics, visualizations)
        sources = _evidence_sources(analytics, visualizations)

        request = LLMGenerationRequest(
            messages=(
                LLMMessage(
                    role="system",
                    content=_SYSTEM_PROMPT,
                ),
                LLMMessage(
                    role="user",
                    content=_evidence_packet(
                        analytics,
                        visualizations,
                    ),
                ),
            ),
            temperature=0.0,
            max_tokens=self._max_tokens,
            response_format="json",
        )
        response = self._provider.generate(request)
        _validate_provider_identity(
            self._provider,
            response,
        )

        if response.finish_reason != "stop":
            raise InsightProviderResponseError("Insight provider response was incomplete")

        try:
            proposal = _parse_proposal(response.content)
        except InsightProviderResponseError:
            get_audit_logger().error(
                "insight_provider_parse_failed",
                raw_content=response.content[:3000],
                finish_reason=response.finish_reason,
                provider=response.provider,
                model=response.model,
            )
            raise

        _reject_unsafe_output(proposal.summary)

        if _number_values(proposal.summary):
            raise InsightProviderResponseError("Insight summary must not contain uncited numbers")

        claims: list[GroundedInsightClaim] = []

        for proposed_claim in proposal.claims:
            _reject_unsafe_output(proposed_claim.text)
            allowed_numbers: set[Decimal] = set()

            for reference in proposed_claim.evidence:
                source = sources.get(_reference_key(reference))

                if source is None:
                    raise InsightProviderResponseError("Insight references unknown evidence")

                allowed_numbers.update(_number_values(source))

            claimed_numbers = _number_values(proposed_claim.text)

            if not _numbers_match(claimed_numbers, allowed_numbers):
                raise InsightProviderResponseError("Insight contains an uncited numeric value")
            _validate_claim_dimensions(
                proposed_claim.text,
                proposed_claim.evidence,
                sources,
            )

            claims.append(
                GroundedInsightClaim(
                    claim_id=insight_claim_id(
                        proposed_claim.text,
                        proposed_claim.evidence,
                    ),
                    text=proposed_claim.text,
                    evidence=proposed_claim.evidence,
                )
            )

        return GroundedInsightResult(
            analytics_version=analytics.analytics_version,
            visualization_version=(visualizations.visualization_version),
            execution_version=analytics.execution_version,
            semantic_version=analytics.semantic_version,
            catalog_version=analytics.catalog_version,
            source_row_count=analytics.source_row_count,
            provider=response.provider,
            model=response.model,
            usage=response.usage,
            summary=proposal.summary,
            claims=tuple(claims),
        )


type InsightEngineFactory = Callable[
    [LLMProvider],
    GroundedInsightEngine,
]


def create_insight_engine(
    provider: LLMProvider,
) -> GroundedInsightEngine:
    """Create a grounded insight engine for a managed provider."""
    return GroundedInsightEngine(provider)
