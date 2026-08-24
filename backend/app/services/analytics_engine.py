from collections.abc import Callable
from decimal import (
    ROUND_HALF_EVEN,
    Decimal,
    DecimalException,
    localcontext,
)
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

    return _quantize(parsed)


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
    dimension_index: int,
    metric_index: int,
) -> tuple[
    tuple[
        str,
        Decimal,
    ],
    ...,
]:
    metric_metadata = execution.internal_column_metadata[metric_index]

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
    ordered_rows = tuple(
        sorted(
            rows,
            key=lambda item: (
                -item[1],
                item[0].casefold(),
                item[0],
            ),
        )
    )
    total = sum(
        (value for _, value in rows),
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
    ordered_rows = tuple(
        sorted(
            rows,
            key=lambda item: (item[0],),
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
                absolute_change=_quantize(value - previous),
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
    if (
        execution.execution_status != "executed"
        or context.grounding_status != "grounded"
        or execution.row_count < 1
        or not context.metrics
        or len(context.dimensions) > 1
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
            semantic_version=(execution.generation.validated_sql.semantic_version),
            catalog_version=(execution.generation.validated_sql.catalog_version),
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
