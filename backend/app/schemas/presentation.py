"""Strict contracts for composed analytical presentations."""

from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from backend.app.schemas.insights import GroundedInsightResult
from backend.app.schemas.query_execution import QueryResultRow
from backend.app.schemas.visualization import DeterministicVisualizationResult

PRESENTATION_VERSION: Literal["1"] = "1"


class PresentationRequest(BaseModel):
    """Question-only request accepted by presentation orchestration."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )

    question: str = Field(min_length=1)

    @field_validator("question")
    @classmethod
    def require_non_blank_question(cls, value: str) -> str:
        """Reject questions that contain only whitespace."""

        if not value.strip():
            raise ValueError("Question must not be blank")

        return value


class PresentationApiErrorResponse(BaseModel):
    """Sanitized public error response for presentation requests."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )

    detail: str = Field(min_length=1)


class PresentationQueryResult(BaseModel):
    """Minimal public projection of validated and bounded query data."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )

    validated_sql: str = Field(min_length=1)
    columns: tuple[str, ...] = Field(min_length=1)
    rows: tuple[QueryResultRow, ...]
    row_count: int = Field(ge=0)

    @field_validator("validated_sql")
    @classmethod
    def require_non_blank_sql(cls, value: str) -> str:
        """Reject missing validated SQL text."""

        if not value.strip():
            raise ValueError("Validated SQL must not be blank")

        return value

    @field_validator("columns")
    @classmethod
    def require_safe_columns(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Require nonblank, unique public column names."""

        if any(not column.strip() for column in value):
            raise ValueError("Query columns must not be blank")

        if len(set(value)) != len(value):
            raise ValueError("Query columns must be unique")

        return value

    @model_validator(mode="after")
    def validate_result_shape(self) -> Self:
        """Require row counts and row widths to match the columns."""

        if self.row_count != len(self.rows):
            raise ValueError("Query row count does not match rows")

        expected_width = len(self.columns)

        if any(len(row) != expected_width for row in self.rows):
            raise ValueError("Query row width does not match columns")

        return self


class AnalyticalPresentationResult(BaseModel):
    """Validated composition of deterministic visuals and grounded insights."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )

    presentation_version: Literal["1"] = PRESENTATION_VERSION
    presentation_status: Literal["generated"] = "generated"
    source_row_count: int = Field(ge=0)
    query: PresentationQueryResult
    visualizations: DeterministicVisualizationResult
    insights: GroundedInsightResult

    @model_validator(mode="after")
    def require_consistent_trusted_results(self) -> Self:
        """Require both nested results to describe the same execution."""

        visualizations = self.visualizations
        insights = self.insights

        if visualizations.visualization_status != "specified":
            raise ValueError("Visualizations are not ready")

        if insights.insight_status != "generated":
            raise ValueError("Insights are not generated")

        if not insights.grounded:
            raise ValueError("Insights must be grounded")

        if insights.calculated_by_llm:
            raise ValueError("Insights must not be calculated by the LLM")

        matching_fields = (
            "visualization_version",
            "analytics_version",
            "execution_version",
            "semantic_version",
            "catalog_version",
            "source_row_count",
        )

        for field_name in matching_fields:
            if getattr(visualizations, field_name) != getattr(
                insights,
                field_name,
            ):
                raise ValueError(f"Presentation source mismatch: {field_name}")

        if self.source_row_count != self.query.row_count:
            raise ValueError("Presentation source row count does not match query")

        if self.source_row_count != visualizations.source_row_count:
            raise ValueError("Presentation source row count does not match")

        return self
