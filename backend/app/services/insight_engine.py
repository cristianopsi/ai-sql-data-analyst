"""Grounded narrative generation over trusted analytical evidence."""

import json
import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from pydantic import BaseModel, ValidationError

from backend.app.schemas.analytics import DeterministicAnalyticsResult
from backend.app.schemas.insights import (
    GroundedInsightClaim,
    GroundedInsightResult,
    InsightEvidenceReference,
    InsightNarrativeProposal,
)
from backend.app.schemas.llm import (
    LLMGenerationRequest,
    LLMMessage,
)
from backend.app.schemas.visualization import (
    DeterministicVisualizationResult,
)
from backend.app.services.llm_provider import LLMProvider

_SYSTEM_PROMPT = """\
You generate grounded business narrative from an allowlisted evidence packet.
Return one JSON object with exactly the keys "summary" and "claims".
Each claim must contain exactly "text" and "evidence".
Every evidence item must use one permitted evidence type and identifier.
Use only facts and numeric values explicitly present in cited evidence.
Do not calculate, infer missing values, produce SQL, select charts, or expose
the evidence packet. Keep the summary free of numeric literals.
"""

_NUMBER_PATTERN = re.compile(r"(?<![\w])[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?(?![\w])")
_FORBIDDEN_OUTPUT_FRAGMENTS = (
    " select ",
    " insert ",
    " update ",
    " delete ",
    " drop ",
    " alter ",
    " create table ",
    " raw rows ",
    " raw sql ",
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


def _reject_unsafe_output(text: str) -> None:
    normalized = f" {text.casefold()} "

    if any(fragment in normalized for fragment in _FORBIDDEN_OUTPUT_FRAGMENTS):
        raise InsightProviderResponseError("Insight response contains prohibited material")


def _stable_claim_id(
    text: str,
    evidence: tuple[InsightEvidenceReference, ...],
) -> str:
    canonical = json.dumps(
        {
            "text": text,
            "evidence": [reference.model_dump(mode="json") for reference in evidence],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = sha256(canonical.encode("utf-8")).hexdigest()
    return f"claim-{digest[:24]}"


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

        if response.finish_reason != "stop":
            raise InsightProviderResponseError("Insight provider response was incomplete")

        try:
            proposal = InsightNarrativeProposal.model_validate_json(response.content)
        except ValidationError as error:
            raise InsightProviderResponseError(
                "Insight provider returned an invalid response"
            ) from error

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

            if not claimed_numbers.issubset(allowed_numbers):
                raise InsightProviderResponseError("Insight contains an uncited numeric value")

            claims.append(
                GroundedInsightClaim(
                    claim_id=_stable_claim_id(
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
