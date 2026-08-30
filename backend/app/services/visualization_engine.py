from collections.abc import Callable
from hashlib import sha256

from pydantic import ValidationError

from backend.app.schemas.analytics import (
    AnalyticsMetricSummary,
    DeterministicAnalyticsResult,
)
from backend.app.schemas.visualization import (
    MAX_BAR_CATEGORIES,
    MAX_LINE_POINTS,
    MAX_TABLE_ROWS,
    BarVisualizationItem,
    BarVisualizationSpec,
    DeterministicVisualizationResult,
    KPIVisualizationSpec,
    LineVisualizationPoint,
    LineVisualizationSpec,
    TableVisualizationRow,
    TableVisualizationSpec,
)


class VisualizationEngineError(RuntimeError):
    """Base error for controlled visualization failures."""


class VisualizationInputError(VisualizationEngineError):
    """Raised when analytics cannot produce safe specifications."""


def _display_name(
    identifier: str,
) -> str:
    words = identifier.replace("_", " ").split()

    if not words:
        raise VisualizationInputError("Visualization identifier is invalid")

    return " ".join(word.capitalize() for word in words)


def _specification_id(
    chart_type: str,
    *identifiers: str,
) -> str:
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


def _summary_by_metric(
    analytics: DeterministicAnalyticsResult,
) -> dict[str, AnalyticsMetricSummary]:
    summaries: dict[str, AnalyticsMetricSummary] = {}

    for summary in analytics.metric_summaries:
        key = summary.metric_name.casefold()

        if key in summaries:
            raise VisualizationInputError("Analytics metric summaries are not unique")

        summaries[key] = summary

    if not summaries:
        raise VisualizationInputError("Analytics contains no chartable metrics")

    return summaries


class DeterministicVisualizationEngine:
    """Create typed chart specifications without rendering or AI."""

    def specify(
        self,
        analytics: DeterministicAnalyticsResult,
    ) -> DeterministicVisualizationResult:
        """Transform deterministic analytics into chart specifications."""
        if (
            analytics.analytics_version != "1"
            or analytics.analytics_status != "analyzed"
            or analytics.deterministic is not True
        ):
            raise VisualizationInputError("Analytics result is not trusted")

        summaries = _summary_by_metric(analytics)

        kpis = tuple(
            KPIVisualizationSpec(
                spec_id=_specification_id(
                    "kpi",
                    summary.metric_name,
                ),
                title=_display_name(summary.metric_name),
                metric_name=summary.metric_name,
                unit=summary.unit,
                value_count=summary.value_count,
                value=summary.primary_value,
                average=summary.average,
                minimum=summary.minimum,
                maximum=summary.maximum,
            )
            for summary in analytics.metric_summaries
        )

        tables: list[TableVisualizationSpec] = []
        bars: list[BarVisualizationSpec] = []

        for ranking in analytics.rankings:
            summary = summaries.get(ranking.metric_name.casefold())

            if summary is None:
                raise VisualizationInputError("Ranking references an unknown metric")

            tables.append(
                TableVisualizationSpec(
                    spec_id=_specification_id(
                        "table",
                        summary.metric_name,
                        ranking.dimension_name,
                    ),
                    title=(
                        f"{_display_name(summary.metric_name)} by "
                        f"{_display_name(ranking.dimension_name)}"
                    ),
                    metric_name=summary.metric_name,
                    dimension_name=ranking.dimension_name,
                    unit=summary.unit,
                    rows=tuple(
                        TableVisualizationRow(
                            position=item.rank,
                            label=item.dimension_value,
                            value=item.value,
                            share_percent=item.share_percent,
                        )
                        for item in ranking.items[:MAX_TABLE_ROWS]
                    ),
                )
            )

            bars.append(
                BarVisualizationSpec(
                    spec_id=_specification_id(
                        "bar",
                        summary.metric_name,
                        ranking.dimension_name,
                    ),
                    title=(
                        f"{_display_name(summary.metric_name)} by "
                        f"{_display_name(ranking.dimension_name)}"
                    ),
                    metric_name=summary.metric_name,
                    dimension_name=ranking.dimension_name,
                    unit=summary.unit,
                    items=tuple(
                        BarVisualizationItem(
                            position=item.rank,
                            label=item.dimension_value,
                            value=item.value,
                            share_percent=item.share_percent,
                        )
                        for item in ranking.items[:MAX_BAR_CATEGORIES]
                    ),
                )
            )

        lines: list[LineVisualizationSpec] = []

        for series in analytics.series:
            summary = summaries.get(series.metric_name.casefold())

            if summary is None:
                raise VisualizationInputError("Series references an unknown metric")

            lines.append(
                LineVisualizationSpec(
                    spec_id=_specification_id(
                        "line",
                        summary.metric_name,
                        series.dimension_name,
                    ),
                    title=(
                        f"{_display_name(summary.metric_name)} by "
                        f"{_display_name(series.dimension_name)}"
                    ),
                    metric_name=summary.metric_name,
                    dimension_name=series.dimension_name,
                    unit=summary.unit,
                    points=tuple(
                        LineVisualizationPoint(
                            position=point.position,
                            label=point.dimension_value,
                            value=point.value,
                            previous_value=point.previous_value,
                            absolute_change=point.absolute_change,
                            percentage_change=point.percentage_change,
                        )
                        for point in series.points[:MAX_LINE_POINTS]
                    ),
                )
            )

        try:
            return DeterministicVisualizationResult(
                analytics_version=analytics.analytics_version,
                execution_version=analytics.execution_version,
                semantic_version=analytics.semantic_version,
                catalog_version=analytics.catalog_version,
                source_row_count=analytics.source_row_count,
                specifications=(
                    *kpis,
                    *tables,
                    *bars,
                    *lines,
                ),
            )
        except ValidationError:
            raise VisualizationInputError(
                "Analytics could not produce safe specifications"
            ) from None


type VisualizationEngineFactory = Callable[
    [],
    DeterministicVisualizationEngine,
]


def create_visualization_engine() -> DeterministicVisualizationEngine:
    """Create a stateless deterministic visualization engine."""
    return DeterministicVisualizationEngine()
