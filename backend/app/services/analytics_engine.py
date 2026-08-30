from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import (
    ROUND_HALF_EVEN,
    Decimal,
    DecimalException,
    localcontext,
)
from re import fullmatch
from typing import Never

from pydantic import ValidationError

from backend.app.schemas.analytics import (
    AnalyticsMetricSummary,
    AnalyticsRanking,
    AnalyticsRankingItem,
    AnalyticsSeries,
    AnalyticsSeriesPoint,
    DeterministicAnalyticsResult,
)
from backend.app.schemas.query_execution import (
    QueryExecutionResult,
    QueryResultColumnMetadata,
    QueryResultValue,
)
from backend.app.schemas.semantic import (
    SemanticDimension,
    SemanticMetric,
)
from backend.app.schemas.semantic_context import (
    CompactGroundingContext,
)

DERIVED_VALUE_QUANTUM = Decimal("0.0001")
DECIMAL_PRECISION = 38


class AnalyticsEngineError(RuntimeError):
    """Base error raised by deterministic analytics."""


class AnalyticsInputError(AnalyticsEngineError):
    """Raised when executed rows cannot be analyzed safely."""


def _reject() -> Never:
    raise AnalyticsInputError("Query result cannot be analyzed deterministically")


def _quantize(
    value: Decimal,
) -> Decimal:
    try:
        with localcontext() as decimal_context:
            decimal_context.prec = DECIMAL_PRECISION

            result = value.quantize(
                DERIVED_VALUE_QUANTUM,
                rounding=ROUND_HALF_EVEN,
            )
    except DecimalException:
        _reject()

    if not result.is_finite():
        _reject()

    return result


def _parse_numeric_value(
    value: QueryResultValue,
    metadata: QueryResultColumnMetadata,
) -> Decimal:
    if metadata.value_kind not in {
        "integer",
        "number",
    }:
        _reject()

    if value is None or isinstance(
        value,
        bool,
    ):
        _reject()

    try:
        if isinstance(
            value,
            int,
        ):
            parsed = Decimal(value)
        elif isinstance(
            value,
            float,
        ):
            parsed = Decimal(str(value))
        elif isinstance(
            value,
            str,
        ):
            parsed = Decimal(value)
        else:
            _reject()
    except DecimalException:
        _reject()

    if not parsed.is_finite():
        _reject()

    return parsed


def _dimension_label(
    value: QueryResultValue,
) -> str:
    if value is None:
        _reject()

    if isinstance(
        value,
        bool,
    ):
        return "true" if value else "false"

    if isinstance(
        value,
        str,
    ):
        normalized = value.strip()

        if not normalized:
            _reject()

        return normalized

    if isinstance(
        value,
        int,
    ):
        return str(value)

    if isinstance(
        value,
        float,
    ):
        return str(value)

    _reject()


def _calendar_granularity(
    value: date,
    dimension: SemanticDimension,
) -> str:
    granularities = dimension.time_granularities

    if "day" in granularities:
        return "day"

    if "month" in granularities and value.day == 1:
        return "month"

    if (
        "quarter" in granularities
        and value.day == 1
        and value.month
        in {
            1,
            4,
            7,
            10,
        }
    ):
        return "quarter"

    if "year" in granularities and value.month == 1 and value.day == 1:
        return "year"

    _reject()


def _temporal_sort_key(
    label: str,
    dimension: SemanticDimension,
) -> tuple[
    str,
    tuple[
        int,
        int,
        int,
        int,
        int,
        int,
        int,
    ],
]:
    if not dimension.time_granularities:
        _reject()

    year_match = fullmatch(
        r"(\d{4})",
        label,
    )

    if year_match is not None and "year" in dimension.time_granularities:
        year = int(year_match.group(1))

        if not 1 <= year <= 9999:
            _reject()

        return (
            "year",
            (
                year,
                1,
                1,
                0,
                0,
                0,
                0,
            ),
        )

    month_match = fullmatch(
        r"(\d{4})-(\d{2})",
        label,
    )

    if month_match is not None and "month" in dimension.time_granularities:
        year = int(month_match.group(1))
        month = int(month_match.group(2))

        try:
            month_start = date(
                year,
                month,
                1,
            )
        except ValueError:
            _reject()

        return (
            "month",
            (
                month_start.year,
                month_start.month,
                1,
                0,
                0,
                0,
                0,
            ),
        )

    quarter_match = fullmatch(
        r"(\d{4})-Q([1-4])",
        label,
    )

    if quarter_match is not None and "quarter" in dimension.time_granularities:
        year = int(quarter_match.group(1))
        quarter = int(quarter_match.group(2))
        month = (quarter - 1) * 3 + 1

        try:
            quarter_start = date(
                year,
                month,
                1,
            )
        except ValueError:
            _reject()

        return (
            "quarter",
            (
                quarter_start.year,
                quarter_start.month,
                1,
                0,
                0,
                0,
                0,
            ),
        )

    try:
        parsed_date = date.fromisoformat(label)
    except ValueError:
        parsed_date = None

    if parsed_date is not None:
        granularity = _calendar_granularity(
            parsed_date,
            dimension,
        )

        return (
            granularity,
            (
                parsed_date.year,
                parsed_date.month,
                parsed_date.day,
                0,
                0,
                0,
                0,
            ),
        )

    try:
        parsed_datetime = datetime.fromisoformat(label)
    except ValueError:
        _reject()

    if parsed_datetime.utcoffset() is not None:
        parsed_datetime = parsed_datetime.astimezone(UTC).replace(
            tzinfo=None,
        )

    granularity = _calendar_granularity(
        parsed_datetime.date(),
        dimension,
    )

    if granularity != "day" and any(
        (
            parsed_datetime.hour,
            parsed_datetime.minute,
            parsed_datetime.second,
            parsed_datetime.microsecond,
        )
    ):
        _reject()

    return (
        granularity,
        (
            parsed_datetime.year,
            parsed_datetime.month,
            parsed_datetime.day,
            parsed_datetime.hour,
            parsed_datetime.minute,
            parsed_datetime.second,
            parsed_datetime.microsecond,
        ),
    )


def _column_indexes(
    execution: QueryExecutionResult,
) -> dict[str, int]:
    metadata = execution.internal_column_metadata

    if len(metadata) != len(execution.columns):
        _reject()

    indexes: dict[str, int] = {}

    for index, (
        column_name,
        column_metadata,
    ) in enumerate(
        zip(
            execution.columns,
            metadata,
            strict=True,
        )
    ):
        if column_metadata.name != column_name or column_metadata.value_kind == "unknown":
            _reject()

        key = column_name.casefold()

        if key in indexes:
            _reject()

        indexes[key] = index

    return indexes


def _metric_values(
    execution: QueryExecutionResult,
    metric: SemanticMetric,
    column_index: int,
) -> tuple[Decimal, ...]:
    metadata = execution.internal_column_metadata[column_index]

    values = tuple(
        _parse_numeric_value(
            row[column_index],
            metadata,
        )
        for row in execution.rows
    )

    if not values:
        _reject()

    return values


def _build_summary(
    metric: SemanticMetric,
    values: tuple[Decimal, ...],
) -> AnalyticsMetricSummary:
    try:
        with localcontext() as decimal_context:
            decimal_context.prec = DECIMAL_PRECISION
            total = sum(
                values,
                Decimal("0"),
            )
            average = total / Decimal(len(values))
    except DecimalException:
        _reject()

    return AnalyticsMetricSummary(
        metric_name=metric.name,
        aggregation=metric.aggregation,
        unit=metric.unit,
        value_count=len(values),
        total=_quantize(total),
        average=_quantize(average),
        minimum=_quantize(min(values)),
        maximum=_quantize(max(values)),
    )


def _dimension_rows(
    execution: QueryExecutionResult,
    *,
    dimension: SemanticDimension,
    dimension_index: int,
    metric_index: int,
) -> tuple[
    tuple[
        str,
        Decimal,
    ],
    ...,
]:
    dimension_metadata = execution.internal_column_metadata[dimension_index]
    metric_metadata = execution.internal_column_metadata[metric_index]

    if dimension.kind == "temporal" and dimension_metadata.value_kind not in {
        "date",
        "datetime",
        "text",
    }:
        _reject()

    rows = tuple(
        (
            _dimension_label(row[dimension_index]),
            _parse_numeric_value(
                row[metric_index],
                metric_metadata,
            ),
        )
        for row in execution.rows
    )

    if dimension.kind == "temporal":
        granularities = tuple(
            _temporal_sort_key(
                label,
                dimension,
            )[0]
            for label, _ in rows
        )

        if len(set(granularities)) != 1:
            _reject()

    dimension_keys = tuple(label.casefold() for label, _ in rows)

    if len(set(dimension_keys)) != len(dimension_keys):
        _reject()

    return rows


def _share_percent(
    value: Decimal,
    total: Decimal,
) -> Decimal | None:
    if total == 0:
        return None

    try:
        with localcontext() as decimal_context:
            decimal_context.prec = DECIMAL_PRECISION
            percentage = value / total * Decimal("100")
    except DecimalException:
        _reject()

    return _quantize(percentage)


def _build_ranking(
    metric: SemanticMetric,
    dimension: SemanticDimension,
    rows: tuple[
        tuple[
            str,
            Decimal,
        ],
        ...,
    ],
) -> AnalyticsRanking:
    normalized_rows = tuple((label, _quantize(value)) for label, value in rows)
    ordered_rows = tuple(
        sorted(
            normalized_rows,
            key=lambda item: (
                -item[1],
                item[0].casefold(),
                item[0],
            ),
        )
    )
    total = sum(
        (value for _, value in normalized_rows),
        Decimal("0"),
    )

    items = tuple(
        AnalyticsRankingItem(
            rank=rank,
            dimension_value=label,
            value=value,
            share_percent=_share_percent(
                value,
                total,
            ),
        )
        for rank, (
            label,
            value,
        ) in enumerate(
            ordered_rows,
            start=1,
        )
    )

    return AnalyticsRanking(
        metric_name=metric.name,
        dimension_name=dimension.name,
        items=items,
    )


def _percentage_change(
    current: Decimal,
    previous: Decimal,
) -> Decimal | None:
    if previous == 0:
        return None

    try:
        with localcontext() as decimal_context:
            decimal_context.prec = DECIMAL_PRECISION
            percentage = (current - previous) / previous * Decimal("100")
    except DecimalException:
        _reject()

    return _quantize(percentage)


def _absolute_change(
    current: Decimal,
    previous: Decimal,
) -> Decimal:
    try:
        with localcontext() as decimal_context:
            decimal_context.prec = DECIMAL_PRECISION
            difference = current - previous
    except DecimalException:
        _reject()

    return _quantize(difference)


def _build_series(
    metric: SemanticMetric,
    dimension: SemanticDimension,
    rows: tuple[
        tuple[
            str,
            Decimal,
        ],
        ...,
    ],
) -> AnalyticsSeries:
    normalized_rows = tuple((label, _quantize(value)) for label, value in rows)
    ordered_rows = tuple(
        sorted(
            normalized_rows,
            key=lambda item: _temporal_sort_key(
                item[0],
                dimension,
            )[1],
        )
    )
    points: list[AnalyticsSeriesPoint] = []
    previous: Decimal | None = None

    for position, (
        label,
        value,
    ) in enumerate(
        ordered_rows,
        start=1,
    ):
        if previous is None:
            point = AnalyticsSeriesPoint(
                position=position,
                dimension_value=label,
                value=value,
            )
        else:
            point = AnalyticsSeriesPoint(
                position=position,
                dimension_value=label,
                value=value,
                previous_value=previous,
                absolute_change=_absolute_change(
                    value,
                    previous,
                ),
                percentage_change=_percentage_change(
                    value,
                    previous,
                ),
            )

        points.append(point)
        previous = value

    return AnalyticsSeries(
        metric_name=metric.name,
        dimension_name=dimension.name,
        points=tuple(points),
    )


def analyze_query_result(
    execution: QueryExecutionResult,
    context: CompactGroundingContext,
) -> DeterministicAnalyticsResult:
    """Calculate summaries, rankings, and series without an LLM."""
    validated_sql = execution.generation.validated_sql

    if (
        execution.execution_status != "executed"
        or context.grounding_status != "grounded"
        or execution.row_count < 1
        or not context.metrics
        or len(context.dimensions) > 1
        or validated_sql.semantic_version != context.semantic_version
        or validated_sql.catalog_version != context.catalog_version
    ):
        _reject()

    indexes = _column_indexes(execution)
    dimension = context.dimensions[0] if context.dimensions else None
    dimension_index: int | None = None

    if dimension is not None:
        dimension_index = indexes.get(dimension.name.casefold())

        if dimension_index is None:
            _reject()

    summaries: list[AnalyticsMetricSummary] = []
    rankings: list[AnalyticsRanking] = []
    series: list[AnalyticsSeries] = []

    for metric in context.metrics:
        metric_index = indexes.get(metric.name.casefold())

        if metric_index is None:
            _reject()

        values = _metric_values(
            execution,
            metric,
            metric_index,
        )
        summaries.append(
            _build_summary(
                metric,
                values,
            )
        )

        if dimension is not None and dimension_index is not None:
            rows = _dimension_rows(
                execution,
                dimension=dimension,
                dimension_index=dimension_index,
                metric_index=metric_index,
            )
            rankings.append(
                _build_ranking(
                    metric,
                    dimension,
                    rows,
                )
            )

            if dimension.kind == "temporal":
                series.append(
                    _build_series(
                        metric,
                        dimension,
                        rows,
                    )
                )

    try:
        return DeterministicAnalyticsResult(
            execution_version=(execution.execution_version),
            semantic_version=validated_sql.semantic_version,
            catalog_version=validated_sql.catalog_version,
            source_row_count=execution.row_count,
            metric_summaries=tuple(summaries),
            rankings=tuple(rankings),
            series=tuple(series),
        )
    except ValidationError:
        _reject()


class DeterministicAnalyticsEngine:
    """Pure software engine for governed analytical calculations."""

    def analyze(
        self,
        execution: QueryExecutionResult,
        context: CompactGroundingContext,
    ) -> DeterministicAnalyticsResult:
        return analyze_query_result(
            execution,
            context,
        )


type AnalyticsEngineFactory = Callable[
    [],
    DeterministicAnalyticsEngine,
]


def create_analytics_engine() -> DeterministicAnalyticsEngine:
    """Create the stateless deterministic analytics engine."""
    return DeterministicAnalyticsEngine()
