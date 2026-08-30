from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from backend.app.schemas.llm import (
    LLMTokenUsage,
)


class ValidatedSQL(BaseModel):
    """Canonical read-only SQL approved by AST validation."""

    model_config = ConfigDict(
        frozen=True,
        str_strip_whitespace=True,
    )

    validation_version: Literal["1"] = "1"
    validation_status: Literal["validated"] = "validated"
    proposal_version: str = Field(min_length=1)
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
    row_limit: int = Field(ge=1)
    referenced_tables: tuple[
        str,
        ...,
    ] = Field(min_length=1)
    referenced_columns: tuple[
        str,
        ...,
    ]
    usage: LLMTokenUsage
