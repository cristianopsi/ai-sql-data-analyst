from math import isfinite
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from backend.app.schemas.sql_generation import (
    SQLGenerationResult,
)

type QueryResultValue = str | int | float | bool | None
type QueryResultRow = tuple[
    QueryResultValue,
    ...,
]
type QueryResultValueKind = Literal[
    "boolean",
    "integer",
    "number",
    "text",
    "date",
    "time",
    "datetime",
    "uuid",
    "unknown",
]


class QueryResultColumnMetadata(BaseModel):
    """Trusted internal metadata obtained from PostgreSQL."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        strict=True,
    )

    name: str = Field(min_length=1)
    postgres_type_code: int = Field(ge=1)
    value_kind: QueryResultValueKind


type QueryExecutionApiErrorDetail = Literal[
    "Question is invalid",
    "Query could not be generated safely",
    "Query could not be executed safely",
    "Query execution is unavailable",
]


class QueryExecutionRequest(BaseModel):
    """Natural-language request for controlled generation and execution."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        strict=True,
    )

    question: str = Field(
        min_length=1,
        max_length=10_000,
    )


class QueryExecutionApiErrorResponse(BaseModel):
    """Sanitized query-execution API error."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
    )

    detail: QueryExecutionApiErrorDetail


class QueryExecutionResult(BaseModel):
    """JSON-safe rows produced from previously validated SQL."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        strict=True,
    )

    execution_version: Literal["1"] = "1"
    execution_status: Literal["executed"] = "executed"
    generation: SQLGenerationResult
    columns: tuple[
        str,
        ...,
    ] = Field(min_length=1)
    internal_column_metadata: tuple[
        QueryResultColumnMetadata,
        ...,
    ] = Field(
        default=(),
        exclude=True,
    )
    rows: tuple[
        QueryResultRow,
        ...,
    ] = ()
    row_count: int = Field(ge=0)
    statement_timeout_ms: int = Field(
        ge=100,
        le=300_000,
    )
    query_timeout_seconds: float = Field(
        gt=0.0,
        le=300.0,
    )
    execution_time_ms: float = Field(ge=0.0)
    transaction_read_only: Literal[True] = True

    @field_validator("rows", mode="before")
    @classmethod
    def require_json_safe_values(
        cls,
        value: object,
    ) -> object:
        if not isinstance(value, tuple):
            return value

        for row in value:
            if not isinstance(row, tuple):
                continue

            for cell in row:
                if cell is not None and not isinstance(
                    cell,
                    (
                        str,
                        int,
                        float,
                        bool,
                    ),
                ):
                    raise ValueError("Query result values must be JSON-safe primitives")

                if isinstance(
                    cell,
                    float,
                ) and not isfinite(cell):
                    raise ValueError("Query result numbers must be finite")

        return value

    @model_validator(mode="after")
    def validate_result_shape(self) -> Self:
        normalized_columns = tuple(column.strip() for column in self.columns)

        if any(not column for column in normalized_columns):
            raise ValueError("Query result columns cannot be empty")

        if self.internal_column_metadata:
            metadata_names = tuple(metadata.name for metadata in self.internal_column_metadata)

            if metadata_names != normalized_columns:
                raise ValueError("Internal column metadata must match the query result columns")

        normalized_column_keys = tuple(column.casefold() for column in normalized_columns)

        if len(set(normalized_column_keys)) != len(normalized_column_keys):
            raise ValueError("Query result columns must be unique")

        if self.row_count != len(self.rows):
            raise ValueError("row_count must equal the number of rows")

        column_count = len(normalized_columns)

        if any(len(row) != column_count for row in self.rows):
            raise ValueError("Every query result row must match the column count")

        row_limit = self.generation.validated_sql.row_limit

        if self.row_count > row_limit:
            raise ValueError("Query result exceeds the validated row limit")

        return self
