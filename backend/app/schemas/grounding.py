from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

type GroundingStatus = Literal[
    "grounded",
    "ambiguous",
    "unsupported",
    "restricted",
]
type GroundingMatchType = Literal[
    "metric",
    "dimension",
    "value",
]


class GroundingMatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    match_type: GroundingMatchType
    semantic_name: str = Field(min_length=1)
    matched_term: str = Field(min_length=1)


class GroundedSemanticValue(BaseModel):
    model_config = ConfigDict(frozen=True)

    dimension_name: str = Field(min_length=1)
    value: str | bool
    matched_term: str = Field(min_length=1)


class QuestionGrounding(BaseModel):
    model_config = ConfigDict(frozen=True)

    semantic_version: str = Field(min_length=1)
    status: GroundingStatus
    normalized_question: str = Field(min_length=1)
    metrics: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    values: tuple[GroundedSemanticValue, ...] = ()
    tables: tuple[str, ...] = ()
    relationships: tuple[str, ...] = ()
    business_rules: tuple[str, ...] = ()
    matches: tuple[GroundingMatch, ...] = ()


type GroundingApiErrorDetail = Literal[
    "Question is invalid",
    "Question cannot be grounded safely",
    "Question is outside the supported analytics domain",
    "Grounding context is unavailable",
]


class GroundingContextRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    question: str


class GroundingApiErrorResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    detail: GroundingApiErrorDetail
