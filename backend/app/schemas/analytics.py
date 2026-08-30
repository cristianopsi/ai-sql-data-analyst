from decimal import (
    ROUND_HALF_EVEN,
    Decimal,
    DecimalException,
    localcontext,
)
from typing import Literal, Self

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

ANALYTICS_VALUE_QUANTUM = Decimal("0.0001")
ANALYTICS_DECIMAL_PRECISION = 38


def _require_finite_decimal(
    value: Decimal,
) -> Decimal:
    if not value.is_finite():
        raise ValueError("Analytics numbers must be finite")

    return value


def _controlled_quantize(
    value: Decimal,
) -> Decimal:
    try:
        with localcontext() as decimal_context:
            decimal_context.prec = ANALYTICS_DECIMAL_PRECISION
            result = value.quantize(
                ANALYTICS_VALUE_QUANTUM,
                rounding=ROUND_HALF_EVEN,
            )
    except DecimalException as error:
        raise ValueError("Analytics arithmetic must remain controlled") from error

    return _require_finite_decimal(result)


def _controlled_sum(
    values: tuple[Decimal, ...],
) -> Decimal:
    try:
        with localcontext() as decimal_context:
            decimal_context.prec = ANALYTICS_DECIMAL_PRECISION
            result = sum(
                values,
                Decimal("0"),
            )
    except DecimalException as error:
        raise ValueError("Analytics arithmetic must remain controlled") from error

    return _require_finite_decimal(result)


def _controlled_average(
    total: Decimal,
    value_count: int,
) -> Decimal:
    try:
        with localcontext() as decimal_context:
            decimal_context.prec = ANALYTICS_DECIMAL_PRECISION
            average = total / Decimal(value_count)
    except DecimalException as error:
        raise ValueError("Analytics arithmetic must remain controlled") from error

    return _controlled_quantize(average)


def _controlled_difference(
    current: Decimal,
    previous: Decimal,
) -> Decimal:
    try:
        with localcontext() as decimal_context:
            decimal_context.prec = ANALYTICS_DECIMAL_PRECISION
            difference = current - previous
    except DecimalException as error:
        raise ValueError("Analytics arithmetic must remain controlled") from error

    return _controlled_quantize(difference)


def _controlled_percentage(
    value: Decimal,
    total: Decimal,
) -> Decimal:
    try:
        with localcontext() as decimal_context:
            decimal_context.prec = ANALYTICS_DECIMAL_PRECISION
            percentage = value / total * Decimal("100")
    except DecimalException as error:
        raise ValueError("Analytics arithmetic must remain controlled") from error

    return _controlled_quantize(percentage)


class AnalyticsMetricSummary(BaseModel):
    """Deterministic summary calculated for one governed metric."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    metric_name: str = Field(min_length=1)
    aggregation: MetricAggregation = "sum"
    unit: MetricUnit
    value_count: int = Field(ge=1)
    total: Decimal
    average: Decimal
    minimum: Decimal
    maximum: Decimal

    @property
    def primary_value(self) -> Decimal:
        if self.aggregation == "average":
            return self.average

        return self.total

    @field_validator(
        "total",
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
    def validate_summary_range(self) -> Self:
        if not self.minimum <= self.average <= self.maximum:
            raise ValueError("Analytics average must be within the minimum and maximum")

        expected_average = _controlled_average(
            self.total,
            self.value_count,
        )

        if abs(self.average - expected_average) > ANALYTICS_VALUE_QUANTUM:
            raise ValueError("Analytics average must match total and value count")

        return self


class AnalyticsRankingItem(BaseModel):
    """One deterministic position in a metric ranking."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    rank: int = Field(ge=1)
    dimension_value: str = Field(min_length=1)
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


class AnalyticsRanking(BaseModel):
    """Ordered ranking for one metric and dimension."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    metric_name: str = Field(min_length=1)
    dimension_name: str = Field(min_length=1)
    items: tuple[
        AnalyticsRankingItem,
        ...,
    ] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_ranking(self) -> Self:
        ranks = tuple(item.rank for item in self.items)
        expected_ranks = tuple(
            range(
                1,
                len(self.items) + 1,
            )
        )

        if ranks != expected_ranks:
            raise ValueError("Analytics ranking positions must be contiguous")

        dimension_keys = tuple(item.dimension_value.casefold() for item in self.items)

        if len(set(dimension_keys)) != len(dimension_keys):
            raise ValueError("Analytics ranking dimension values must be unique")

        observed_order = tuple(
            (
                item.dimension_value,
                item.value,
            )
            for item in self.items
        )
        expected_order = tuple(
            sorted(
                observed_order,
                key=lambda item: (
                    -item[1],
                    item[0].casefold(),
                    item[0],
                ),
            )
        )

        if observed_order != expected_order:
            raise ValueError("Analytics ranking items must use deterministic value order")

        total = _controlled_sum(
            tuple(item.value for item in self.items),
        )

        for item in self.items:
            expected_share = (
                None
                if total == 0
                else _controlled_percentage(
                    item.value,
                    total,
                )
            )

            if item.share_percent != expected_share:
                raise ValueError("Analytics ranking shares must match the ranked values")

        return self


class AnalyticsSeriesPoint(BaseModel):
    """One ordered point and its deterministic variation."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    position: int = Field(ge=1)
    dimension_value: str = Field(min_length=1)
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

    @model_validator(mode="after")
    def validate_variation_contract(self) -> Self:
        if self.position == 1:
            if any(
                value is not None
                for value in (
                    self.previous_value,
                    self.absolute_change,
                    self.percentage_change,
                )
            ):
                raise ValueError("The first analytics series point cannot have variation")

            return self

        if self.previous_value is None or self.absolute_change is None:
            raise ValueError("Subsequent analytics series points require prior-value metadata")

        if self.previous_value != 0 and self.percentage_change is None:
            raise ValueError("Percentage change is required when the previous value is nonzero")

        if self.previous_value == 0 and self.percentage_change is not None:
            raise ValueError("Percentage change must be absent when the previous value is zero")

        return self


class AnalyticsSeries(BaseModel):
    """Ordered deterministic series for a temporal dimension."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    metric_name: str = Field(min_length=1)
    dimension_name: str = Field(min_length=1)
    points: tuple[
        AnalyticsSeriesPoint,
        ...,
    ] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_series_positions(self) -> Self:
        positions = tuple(point.position for point in self.points)
        expected_positions = tuple(
            range(
                1,
                len(self.points) + 1,
            )
        )

        if positions != expected_positions:
            raise ValueError("Analytics series positions must be contiguous")

        dimension_keys = tuple(point.dimension_value for point in self.points)

        if len(set(dimension_keys)) != len(dimension_keys):
            raise ValueError("Analytics series dimension values must be unique")

        previous_point: AnalyticsSeriesPoint | None = None

        for point in self.points:
            if previous_point is None:
                previous_point = point
                continue

            if point.previous_value != previous_point.value:
                raise ValueError("Analytics series previous values must match prior points")

            expected_change = _controlled_difference(
                point.value,
                previous_point.value,
            )

            if point.absolute_change != expected_change:
                raise ValueError("Analytics series absolute changes must match point values")

            expected_percentage = (
                None
                if previous_point.value == 0
                else _controlled_percentage(
                    expected_change,
                    previous_point.value,
                )
            )

            if point.percentage_change != expected_percentage:
                raise ValueError("Analytics series percentage changes must match point values")

            previous_point = point

        return self


class DeterministicAnalyticsResult(BaseModel):
    """Software-calculated analytics derived from executed query rows."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    analytics_version: Literal["1"] = "1"
    analytics_status: Literal["analyzed"] = "analyzed"
    deterministic: Literal[True] = True
    calculation_scale: Literal[4] = 4
    execution_version: str = Field(min_length=1)
    semantic_version: str = Field(min_length=1)
    catalog_version: str = Field(min_length=1)
    source_row_count: int = Field(ge=1)
    metric_summaries: tuple[
        AnalyticsMetricSummary,
        ...,
    ] = Field(min_length=1)
    rankings: tuple[
        AnalyticsRanking,
        ...,
    ] = ()
    series: tuple[
        AnalyticsSeries,
        ...,
    ] = ()

    @model_validator(mode="after")
    def validate_metric_references(self) -> Self:
        metric_names = tuple(summary.metric_name for summary in self.metric_summaries)
        metric_keys = tuple(name.casefold() for name in metric_names)

        if len(set(metric_keys)) != len(metric_keys):
            raise ValueError("Analytics metric summaries must be unique")

        if any(summary.value_count != self.source_row_count for summary in self.metric_summaries):
            raise ValueError("Analytics metric value counts must match the source row count")

        if any(len(ranking.items) != self.source_row_count for ranking in self.rankings):
            raise ValueError("Analytics rankings must match the source row count")

        if any(len(item.points) != self.source_row_count for item in self.series):
            raise ValueError("Analytics series must match the source row count")

        known_metrics = set(metric_keys)

        for ranking in self.rankings:
            if ranking.metric_name.casefold() not in known_metrics:
                raise ValueError("Analytics outputs must reference a summarized metric")

        for series in self.series:
            if series.metric_name.casefold() not in known_metrics:
                raise ValueError("Analytics outputs must reference a summarized metric")

        ranking_keys = tuple(
            (
                ranking.metric_name.casefold(),
                ranking.dimension_name.casefold(),
            )
            for ranking in self.rankings
        )

        if len(set(ranking_keys)) != len(ranking_keys):
            raise ValueError("Analytics rankings must be unique")

        series_keys = tuple(
            (
                item.metric_name.casefold(),
                item.dimension_name.casefold(),
            )
            for item in self.series
        )

        if len(set(series_keys)) != len(series_keys):
            raise ValueError("Analytics series must be unique")

        return self


type AnalyticsApiErrorDetail = Literal[
    "Question is invalid",
    "Analytics could not be produced safely",
    "Analytics service is unavailable",
]


class AnalyticsRequest(BaseModel):
    """Natural-language request for deterministic analytics."""

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


class AnalyticsApiErrorResponse(BaseModel):
    """Sanitized analytics API error response."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
    )

    detail: AnalyticsApiErrorDetail
