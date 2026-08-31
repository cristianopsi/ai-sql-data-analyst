from decimal import (
    ROUND_HALF_EVEN,
    Decimal,
    DecimalException,
    localcontext,
)
from hashlib import sha256
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

from backend.app.schemas.semantic import (
    MetricAggregation,
    MetricUnit,
)

MAX_BAR_CATEGORIES = 20
MAX_LINE_POINTS = 200
MAX_TABLE_ROWS = 100
VISUALIZATION_DECIMAL_PRECISION = 38
VISUALIZATION_VALUE_QUANTUM = Decimal("0.0001")

type VisualizationChartType = Literal[
    "kpi",
    "table",
    "bar",
    "line",
]


def visualization_specification_id(
    chart_type: VisualizationChartType,
    metric_name: str,
    dimension_name: str | None = None,
) -> str:
    identifiers: tuple[str, ...]

    if chart_type == "kpi":
        if dimension_name is not None:
            raise ValueError("KPI visualization identifiers cannot include a dimension")

        identifiers = (metric_name,)
    else:
        if dimension_name is None:
            raise ValueError("Chart visualization identifiers require a dimension")

        identifiers = (
            metric_name,
            dimension_name,
        )

    payload = "\x1f".join(
        (
            chart_type,
            *(identifier.casefold() for identifier in identifiers),
        )
    )
    digest = sha256(
        payload.encode("utf-8"),
    ).hexdigest()

    return f"{chart_type}-{digest}"


def _require_finite_decimal(
    value: Decimal,
) -> Decimal:
    if not value.is_finite():
        raise ValueError("Visualization numbers must be finite")

    return value


def _controlled_quantize(
    value: Decimal,
) -> Decimal:
    try:
        with localcontext() as decimal_context:
            decimal_context.prec = VISUALIZATION_DECIMAL_PRECISION
            result = value.quantize(
                VISUALIZATION_VALUE_QUANTUM,
                rounding=ROUND_HALF_EVEN,
            )
    except DecimalException as error:
        raise ValueError("Visualization arithmetic must remain controlled") from error

    return _require_finite_decimal(result)


def _controlled_average(
    total: Decimal,
    value_count: int,
) -> Decimal:
    try:
        with localcontext() as decimal_context:
            decimal_context.prec = VISUALIZATION_DECIMAL_PRECISION
            average = total / Decimal(value_count)
    except DecimalException as error:
        raise ValueError("Visualization arithmetic must remain controlled") from error

    return _controlled_quantize(average)


def _controlled_sum(
    values: tuple[Decimal, ...],
) -> Decimal:
    try:
        with localcontext() as decimal_context:
            decimal_context.prec = VISUALIZATION_DECIMAL_PRECISION
            result = sum(
                values,
                Decimal("0"),
            )
    except DecimalException as error:
        raise ValueError("Visualization arithmetic must remain controlled") from error

    return _require_finite_decimal(result)


def _controlled_percentage(
    value: Decimal,
    total: Decimal,
) -> Decimal:
    try:
        with localcontext() as decimal_context:
            decimal_context.prec = VISUALIZATION_DECIMAL_PRECISION
            percentage = value / total * Decimal("100")
    except DecimalException as error:
        raise ValueError("Visualization arithmetic must remain controlled") from error

    return _controlled_quantize(percentage)


def visualization_ranking_total(
    values: tuple[Decimal, ...],
) -> Decimal:
    return _controlled_sum(values)


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
    aggregation: MetricAggregation
    unit: MetricUnit
    value_count: int = Field(ge=1)
    total: Decimal
    value: Decimal
    average: Decimal
    minimum: Decimal
    maximum: Decimal

    @field_validator(
        "total",
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

        expected_average = _controlled_average(
            self.total,
            self.value_count,
        )

        if abs(self.average - expected_average) > VISUALIZATION_VALUE_QUANTUM:
            raise ValueError("Visualization average must match total and value count")

        expected_value = self.average if self.aggregation == "average" else self.total

        if self.value != expected_value:
            raise ValueError("Visualization KPI value must match its aggregation")

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
    ranking_total: Decimal
    items: tuple[
        BarVisualizationItem,
        ...,
    ] = Field(min_length=1, max_length=MAX_BAR_CATEGORIES)

    @field_validator("ranking_total")
    @classmethod
    def require_finite_ranking_total(
        cls,
        value: Decimal,
    ) -> Decimal:
        return _require_finite_decimal(value)

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

        if self.ranking_total == 0 and any(item.value != 0 for item in self.items):
            raise ValueError("Bar visualization shares must match the ranking total")

        for item in self.items:
            expected_share = (
                None
                if self.ranking_total == 0
                else _controlled_percentage(
                    item.value,
                    self.ranking_total,
                )
            )

            if item.share_percent != expected_share:
                raise ValueError("Bar visualization shares must match the ranking total")

        return self


class TableVisualizationRow(BaseModel):
    """One bounded row derived from a deterministic ranking."""

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

    @field_validator("value", "share_percent")
    @classmethod
    def require_finite_numbers(
        cls,
        value: Decimal | None,
    ) -> Decimal | None:
        if value is None:
            return None

        return _require_finite_decimal(value)


class TableVisualizationSpec(BaseModel):
    """Deterministic bounded table specification for a ranking."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    spec_id: str = Field(min_length=1, max_length=100)
    chart_type: Literal["table"] = "table"
    title: str = Field(min_length=1, max_length=200)
    metric_name: str = Field(min_length=1)
    dimension_name: str = Field(min_length=1)
    unit: MetricUnit
    ranking_total: Decimal
    rows: tuple[TableVisualizationRow, ...] = Field(
        min_length=1,
        max_length=MAX_TABLE_ROWS,
    )

    @field_validator("ranking_total")
    @classmethod
    def require_finite_ranking_total(
        cls,
        value: Decimal,
    ) -> Decimal:
        return _require_finite_decimal(value)

    @model_validator(mode="after")
    def validate_rows(self) -> Self:
        positions = tuple(row.position for row in self.rows)
        expected_positions = tuple(range(1, len(self.rows) + 1))

        if positions != expected_positions:
            raise ValueError("Table visualization positions must be contiguous")

        labels = tuple(row.label.casefold() for row in self.rows)

        if len(set(labels)) != len(labels):
            raise ValueError("Table visualization labels must be unique")

        if self.ranking_total == 0 and any(row.value != 0 for row in self.rows):
            raise ValueError("Table visualization shares must match the ranking total")

        for row in self.rows:
            expected_share = (
                None
                if self.ranking_total == 0
                else _controlled_percentage(
                    row.value,
                    self.ranking_total,
                )
            )

            if row.share_percent != expected_share:
                raise ValueError("Table visualization shares must match the ranking total")

        return self


def _controlled_difference(
    current: Decimal,
    previous: Decimal,
) -> Decimal:
    try:
        with localcontext() as decimal_context:
            decimal_context.prec = VISUALIZATION_DECIMAL_PRECISION
            difference = current - previous
    except DecimalException as error:
        raise ValueError("Visualization arithmetic is invalid") from error

    return _controlled_quantize(difference)


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
    ] = Field(min_length=1, max_length=MAX_LINE_POINTS)

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

        previous_point = first

        for point in self.points[1:]:
            if point.previous_value is None or point.absolute_change is None:
                raise ValueError("Subsequent line points require variation metadata")

            if point.previous_value != previous_point.value:
                raise ValueError("Line visualization previous values must match prior points")

            expected_change = _controlled_difference(
                point.value,
                previous_point.value,
            )

            if point.absolute_change != expected_change:
                raise ValueError("Line visualization absolute changes must match point values")

            expected_percentage = (
                None
                if previous_point.value == 0
                else _controlled_percentage(
                    expected_change,
                    previous_point.value,
                )
            )

            if point.percentage_change != expected_percentage:
                raise ValueError("Line visualization percentage changes must match point values")

            previous_point = point

        return self


type VisualizationSpecification = Annotated[
    (KPIVisualizationSpec | TableVisualizationSpec | BarVisualizationSpec | LineVisualizationSpec),
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
    source_row_count: int = Field(ge=1)
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

        semantic_keys = tuple(
            (
                specification.chart_type,
                specification.metric_name.casefold(),
            )
            if isinstance(
                specification,
                KPIVisualizationSpec,
            )
            else (
                specification.chart_type,
                specification.metric_name.casefold(),
                specification.dimension_name.casefold(),
            )
            for specification in self.specifications
        )

        if len(set(semantic_keys)) != len(semantic_keys):
            raise ValueError("Visualization semantic specifications must be unique")

        for specification in self.specifications:
            if isinstance(
                specification,
                KPIVisualizationSpec,
            ):
                expected_identifier = visualization_specification_id(
                    specification.chart_type,
                    specification.metric_name,
                )
            else:
                expected_identifier = visualization_specification_id(
                    specification.chart_type,
                    specification.metric_name,
                    specification.dimension_name,
                )

            if specification.spec_id != expected_identifier:
                raise ValueError("Visualization specification identifier is not canonical")

        tables = {
            (
                specification.metric_name.casefold(),
                specification.dimension_name.casefold(),
            ): specification
            for specification in self.specifications
            if isinstance(
                specification,
                TableVisualizationSpec,
            )
        }
        bars = {
            (
                specification.metric_name.casefold(),
                specification.dimension_name.casefold(),
            ): specification
            for specification in self.specifications
            if isinstance(
                specification,
                BarVisualizationSpec,
            )
        }

        for key, table in tables.items():
            bar = bars.get(key)

            if bar is None:
                continue

            table_prefix = tuple(
                (
                    row.position,
                    row.label,
                    row.value,
                    row.share_percent,
                )
                for row in table.rows[: len(bar.items)]
            )
            bar_items = tuple(
                (
                    item.position,
                    item.label,
                    item.value,
                    item.share_percent,
                )
                for item in bar.items
            )

            if (
                table.title != bar.title
                or table.unit != bar.unit
                or table.ranking_total != bar.ranking_total
                or table_prefix != bar_items
            ):
                raise ValueError("Visualization table and bar specifications must match")

        chart_order = {
            "kpi": 0,
            "table": 1,
            "bar": 2,
            "line": 3,
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
