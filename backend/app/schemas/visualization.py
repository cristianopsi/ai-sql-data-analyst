from decimal import Decimal
from typing import (
    Annotated,
    Literal,
    Self,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from backend.app.schemas.semantic import MetricUnit


def _require_finite_decimal(
    value: Decimal,
) -> Decimal:
    if not value.is_finite():
        raise ValueError("Visualization numbers must be finite")

    return value


class KPIVisualizationSpec(BaseModel):
    """Deterministic single-metric visualization specification."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    spec_id: str = Field(min_length=1, max_length=100)
    chart_type: Literal["kpi"] = "kpi"
    title: str = Field(min_length=1, max_length=200)
    metric_name: str = Field(min_length=1)
    unit: MetricUnit
    value_count: int = Field(ge=1)
    value: Decimal
    average: Decimal
    minimum: Decimal
    maximum: Decimal

    @field_validator(
        "value",
        "average",
        "minimum",
        "maximum",
    )
    @classmethod
    def require_finite_numbers(
        cls,
        value: Decimal,
    ) -> Decimal:
        return _require_finite_decimal(value)

    @model_validator(mode="after")
    def validate_summary_bounds(self) -> Self:
        if self.minimum > self.maximum:
            raise ValueError("Visualization minimum cannot exceed maximum")

        if not self.minimum <= self.average <= self.maximum:
            raise ValueError("Visualization average must be within its bounds")

        return self


class BarVisualizationItem(BaseModel):
    """One ordered category in a deterministic bar specification."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    position: int = Field(ge=1)
    label: str = Field(min_length=1)
    value: Decimal
    share_percent: Decimal | None = None

    @field_validator(
        "value",
        "share_percent",
    )
    @classmethod
    def require_finite_numbers(
        cls,
        value: Decimal | None,
    ) -> Decimal | None:
        if value is None:
            return None

        return _require_finite_decimal(value)


class BarVisualizationSpec(BaseModel):
    """Deterministic categorical ranking specification."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    spec_id: str = Field(min_length=1, max_length=100)
    chart_type: Literal["bar"] = "bar"
    title: str = Field(min_length=1, max_length=200)
    metric_name: str = Field(min_length=1)
    dimension_name: str = Field(min_length=1)
    unit: MetricUnit
    items: tuple[
        BarVisualizationItem,
        ...,
    ] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_items(self) -> Self:
        positions = tuple(item.position for item in self.items)
        expected_positions = tuple(
            range(
                1,
                len(self.items) + 1,
            )
        )

        if positions != expected_positions:
            raise ValueError("Bar visualization positions must be contiguous")

        labels = tuple(item.label.casefold() for item in self.items)

        if len(set(labels)) != len(labels):
            raise ValueError("Bar visualization labels must be unique")

        return self


class LineVisualizationPoint(BaseModel):
    """One ordered point in a deterministic line specification."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    position: int = Field(ge=1)
    label: str = Field(min_length=1)
    value: Decimal
    previous_value: Decimal | None = None
    absolute_change: Decimal | None = None
    percentage_change: Decimal | None = None

    @field_validator(
        "value",
        "previous_value",
        "absolute_change",
        "percentage_change",
    )
    @classmethod
    def require_finite_numbers(
        cls,
        value: Decimal | None,
    ) -> Decimal | None:
        if value is None:
            return None

        return _require_finite_decimal(value)


class LineVisualizationSpec(BaseModel):
    """Deterministic temporal-series specification."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    spec_id: str = Field(min_length=1, max_length=100)
    chart_type: Literal["line"] = "line"
    title: str = Field(min_length=1, max_length=200)
    metric_name: str = Field(min_length=1)
    dimension_name: str = Field(min_length=1)
    unit: MetricUnit
    points: tuple[
        LineVisualizationPoint,
        ...,
    ] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_points(self) -> Self:
        positions = tuple(point.position for point in self.points)
        expected_positions = tuple(
            range(
                1,
                len(self.points) + 1,
            )
        )

        if positions != expected_positions:
            raise ValueError("Line visualization positions must be contiguous")

        labels = tuple(point.label for point in self.points)

        if len(set(labels)) != len(labels):
            raise ValueError("Line visualization labels must be unique")

        first = self.points[0]

        if any(
            value is not None
            for value in (
                first.previous_value,
                first.absolute_change,
                first.percentage_change,
            )
        ):
            raise ValueError("First line point cannot contain variation metadata")

        for point in self.points[1:]:
            if point.previous_value is None or point.absolute_change is None:
                raise ValueError("Subsequent line points require variation metadata")

        return self


type VisualizationSpecification = Annotated[
    (KPIVisualizationSpec | BarVisualizationSpec | LineVisualizationSpec),
    Field(discriminator="chart_type"),
]


class DeterministicVisualizationResult(BaseModel):
    """Typed chart specifications derived only from analytics."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    visualization_version: Literal["1"] = "1"
    visualization_status: Literal["specified"] = "specified"
    deterministic: Literal[True] = True
    analytics_version: str = Field(min_length=1)
    execution_version: str = Field(min_length=1)
    semantic_version: str = Field(min_length=1)
    catalog_version: str = Field(min_length=1)
    source_row_count: int = Field(ge=0)
    specifications: tuple[
        VisualizationSpecification,
        ...,
    ] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_specifications(self) -> Self:
        identifiers = tuple(
            specification.spec_id.casefold() for specification in self.specifications
        )

        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Visualization specification identifiers must be unique")

        chart_order = {
            "kpi": 0,
            "bar": 1,
            "line": 2,
        }
        positions = tuple(
            chart_order[specification.chart_type] for specification in self.specifications
        )

        if positions != tuple(sorted(positions)):
            raise ValueError("Visualization specifications have invalid chart order")

        return self


type VisualizationApiErrorDetail = Literal[
    "Question is invalid",
    "Visualization could not be produced safely",
    "Visualization service is unavailable",
]


class VisualizationRequest(BaseModel):
    """Natural-language request for deterministic specifications."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    question: str = Field(
        min_length=1,
        max_length=10_000,
    )


class VisualizationApiErrorResponse(BaseModel):
    """Sanitized visualization API error response."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
    )

    detail: VisualizationApiErrorDetail
