from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from backend.app.schemas.llm import (
    LLMTokenUsage,
)
from backend.app.schemas.sql_validation import (
    ValidatedSQL,
)


class SQLProposalPayload(BaseModel):
    """Strict JSON payload accepted from an LLM provider."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    sql: str = Field(
        min_length=1,
        max_length=20_000,
    )
    explanation: str = Field(
        min_length=1,
        max_length=4_000,
    )


class SQLProposal(BaseModel):
    """Untrusted SQL proposal awaiting AST validation."""

    model_config = ConfigDict(
        frozen=True,
        str_strip_whitespace=True,
    )

    proposal_version: Literal["1"] = "1"
    validation_status: Literal["unvalidated"] = "unvalidated"
    context_version: str = Field(min_length=1)
    semantic_version: str = Field(min_length=1)
    catalog_version: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    sql: str = Field(
        min_length=1,
        max_length=20_000,
    )
    explanation: str = Field(
        min_length=1,
        max_length=4_000,
    )
    usage: LLMTokenUsage


class SQLGenerationResult(BaseModel):
    """Validated SQL plus controlled generation metadata."""

    model_config = ConfigDict(frozen=True)

    generation_version: Literal["1"] = "1"
    validated_sql: ValidatedSQL
    generation_attempts: int = Field(ge=1)
    repair_attempts: int = Field(ge=0)


type SQLGenerationApiErrorDetail = Literal[
    "Question is invalid",
    "SQL could not be generated safely",
    "SQL generation is unavailable",
]


class SQLGenerationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    question: str


class SQLGenerationApiErrorResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    detail: SQLGenerationApiErrorDetail
