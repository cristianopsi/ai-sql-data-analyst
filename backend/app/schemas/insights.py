"""Strict contracts for grounded narrative insights."""

import json
from hashlib import sha256
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.schemas.llm import LLMTokenUsage

type InsightEvidenceType = Literal[
    "metric_summary",
    "ranking",
    "series",
    "visualization",
]


def _evidence_keys(
    evidence: tuple["InsightEvidenceReference", ...],
) -> tuple[tuple[str, str | None, str | None], ...]:
    return tuple(
        sorted(
            (
                (
                    reference.evidence_type,
                    reference.metric_name,
                    reference.specification_id,
                )
                for reference in evidence
            ),
            key=lambda key: (
                key[0],
                key[1] or "",
                key[2] or "",
            ),
        )
    )


def insight_claim_id(
    text: str,
    evidence: tuple["InsightEvidenceReference", ...],
) -> str:
    """Return the canonical identifier for one grounded claim."""
    canonical = json.dumps(
        {
            "text": text,
            "evidence": [
                {
                    "evidence_type": evidence_type,
                    "metric_name": metric_name,
                    "specification_id": specification_id,
                }
                for (
                    evidence_type,
                    metric_name,
                    specification_id,
                ) in _evidence_keys(evidence)
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = sha256(canonical.encode("utf-8")).hexdigest()
    return f"claim-{digest[:24]}"


class GroundedInsightRequest(BaseModel):
    """Public question-only request for the governed insight pipeline."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    question: str = Field(min_length=1, max_length=2000)


class InsightEvidenceReference(BaseModel):
    """Reference to one trusted analytics or visualization artifact."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    evidence_type: InsightEvidenceType
    metric_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    specification_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )

    @model_validator(mode="after")
    def require_exact_reference_target(self) -> Self:
        """Require the identifier appropriate for the evidence type."""
        if self.evidence_type == "visualization":
            if self.specification_id is None or self.metric_name is not None:
                raise ValueError("Visualization evidence requires only a specification ID")

            return self

        if self.metric_name is None or self.specification_id is not None:
            raise ValueError("Analytics evidence requires only a metric name")

        return self


class InsightNarrativeClaimProposal(BaseModel):
    """Untrusted structured claim proposed by the provider."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    text: str = Field(min_length=1, max_length=600)
    evidence: tuple[InsightEvidenceReference, ...] = Field(
        min_length=1,
        max_length=8,
    )

    @model_validator(mode="after")
    def reject_duplicate_evidence(self) -> Self:
        """Reject repeated evidence references within one claim."""
        keys = _evidence_keys(self.evidence)

        if len(keys) != len(set(keys)):
            raise ValueError("Insight evidence references must be unique")

        return self


class InsightNarrativeProposal(BaseModel):
    """Strict JSON payload expected from the controlled provider."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    summary: str = Field(min_length=1, max_length=1200)
    claims: tuple[InsightNarrativeClaimProposal, ...] = Field(
        min_length=1,
        max_length=5,
    )


class GroundedInsightClaim(BaseModel):
    """Validated narrative claim linked to trusted evidence."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    claim_id: str = Field(
        min_length=30,
        max_length=30,
        pattern=r"^claim-[0-9a-f]{24}$",
    )
    text: str = Field(min_length=1, max_length=600)
    evidence: tuple[InsightEvidenceReference, ...] = Field(
        min_length=1,
        max_length=8,
    )

    @model_validator(mode="after")
    def validate_grounded_claim(self) -> Self:
        """Require unique evidence and canonical claim identity."""
        keys = _evidence_keys(self.evidence)

        if len(keys) != len(set(keys)):
            raise ValueError("Insight evidence references must be unique")

        expected_identifier = insight_claim_id(
            self.text,
            self.evidence,
        )

        if self.claim_id != expected_identifier:
            raise ValueError("Insight claim ID must be canonical")

        return self


class GroundedInsightResult(BaseModel):
    """Versioned grounded narrative produced from internal evidence."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    insight_version: Literal["1"] = "1"
    insight_status: Literal["generated"] = "generated"
    grounded: Literal[True] = True
    calculated_by_llm: Literal[False] = False
    analytics_version: str = Field(min_length=1)
    visualization_version: str = Field(min_length=1)
    execution_version: str = Field(min_length=1)
    semantic_version: str = Field(min_length=1)
    catalog_version: str = Field(min_length=1)
    source_row_count: int = Field(ge=1)
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    usage: LLMTokenUsage
    summary: str = Field(min_length=1, max_length=1200)
    claims: tuple[GroundedInsightClaim, ...] = Field(
        min_length=1,
        max_length=5,
    )

    @model_validator(mode="after")
    def validate_claim_uniqueness(self) -> Self:
        """Require unique semantic claims and canonical identifiers."""
        semantic_keys = tuple(
            (
                claim.text,
                _evidence_keys(claim.evidence),
            )
            for claim in self.claims
        )

        if len(semantic_keys) != len(set(semantic_keys)):
            raise ValueError("Insight semantic claims must be unique")

        claim_ids = tuple(claim.claim_id for claim in self.claims)

        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("Insight claim IDs must be unique")

        return self
