"""Safe Streamlit rendering for validated analytical presentations."""

from __future__ import annotations

from decimal import Decimal
from typing import Never

import pandas as pd  # type: ignore[import-untyped]
import plotly.graph_objects as go  # type: ignore[import-untyped]
import streamlit as st

from backend.app.schemas.insights import InsightEvidenceReference
from backend.app.schemas.presentation import (
    AnalyticalPresentationResult,
    PresentationQueryResult,
)
from backend.app.schemas.visualization import (
    BarVisualizationSpec,
    KPIVisualizationSpec,
    LineVisualizationSpec,
    VisualizationSpecification,
)


def _unit_key(unit: object) -> str:
    """Normalize a literal or enum unit without changing its meaning."""
    value = getattr(unit, "value", unit)
    return str(value).strip().casefold()


def _grouped_decimal(
    value: Decimal,
    *,
    decimal_places: int,
) -> str:
    """Format a decimal using Brazilian grouping deterministically."""
    formatted = f"{value:,.{decimal_places}f}"
    return formatted.replace(",", "\0").replace(".", ",").replace("\0", ".")


def format_metric_value(
    value: Decimal,
    unit: object,
) -> str:
    """Format one trusted metric value without recalculating it."""
    normalized_unit = _unit_key(unit)

    if normalized_unit in {"brl", "currency_brl"}:
        return f"R$ {_grouped_decimal(value, decimal_places=2)}"

    if normalized_unit in {"percent", "percentage"}:
        return f"{_grouped_decimal(value, decimal_places=2)}%"

    if normalized_unit == "count":
        return _grouped_decimal(value, decimal_places=0)

    plain = format(value, "f")

    if "." in plain:
        plain = plain.rstrip("0").rstrip(".")

    return plain or "0"


def query_dataframe(
    query: PresentationQueryResult,
) -> pd.DataFrame:
    """Project the validated public rows into a display-only frame."""
    return pd.DataFrame(
        query.rows,
        columns=list(query.columns),
    )


def _axis_unit_label(unit: object) -> str:
    normalized_unit = _unit_key(unit)

    if normalized_unit in {"brl", "currency_brl"}:
        return "BRL"

    if normalized_unit in {"percent", "percentage"}:
        return "Percent"

    if normalized_unit == "count":
        return "Count"

    return str(getattr(unit, "value", unit))


def build_bar_figure(
    specification: BarVisualizationSpec,
) -> go.Figure:
    """Build a deterministic Plotly bar chart in specification order."""
    labels = [item.label for item in specification.items]
    values = [float(item.value) for item in specification.items]
    customdata = [
        [
            format_metric_value(
                item.value,
                specification.unit,
            ),
            (
                format_metric_value(
                    item.share_percent,
                    "percentage",
                )
                if item.share_percent is not None
                else ""
            ),
        ]
        for item in specification.items
    ]

    figure = go.Figure(
        data=[
            go.Bar(
                x=labels,
                y=values,
                customdata=customdata,
                hovertemplate=("%{x}<br>%{customdata[0]}<br>%{customdata[1]}<extra></extra>"),
            )
        ]
    )
    figure.update_layout(
        title=specification.title,
        xaxis_title=specification.dimension_name,
        yaxis_title=_axis_unit_label(specification.unit),
        template="plotly_white",
        showlegend=False,
    )
    return figure


def build_line_figure(
    specification: LineVisualizationSpec,
) -> go.Figure:
    """Build a deterministic Plotly line chart in temporal order."""
    labels = [point.label for point in specification.points]
    values = [float(point.value) for point in specification.points]
    customdata = [
        [
            format_metric_value(
                point.value,
                specification.unit,
            ),
            (
                format_metric_value(
                    point.previous_value,
                    specification.unit,
                )
                if point.previous_value is not None
                else ""
            ),
            (
                format_metric_value(
                    point.absolute_change,
                    specification.unit,
                )
                if point.absolute_change is not None
                else ""
            ),
            (
                format_metric_value(
                    point.percentage_change,
                    "percentage",
                )
                if point.percentage_change is not None
                else ""
            ),
        ]
        for point in specification.points
    ]

    figure = go.Figure(
        data=[
            go.Scatter(
                x=labels,
                y=values,
                customdata=customdata,
                mode="lines+markers",
                hovertemplate=(
                    "%{x}<br>"
                    "%{customdata[0]}<br>"
                    "Previous: %{customdata[1]}<br>"
                    "Change: %{customdata[2]}<br>"
                    "Change %: %{customdata[3]}"
                    "<extra></extra>"
                ),
            )
        ]
    )
    figure.update_layout(
        title=specification.title,
        xaxis_title=specification.dimension_name,
        yaxis_title=_axis_unit_label(specification.unit),
        template="plotly_white",
        showlegend=False,
    )
    return figure


def render_kpi(
    specification: KPIVisualizationSpec,
) -> None:
    """Render one KPI and its deterministic supporting statistics."""
    st.metric(
        label=specification.title,
        value=format_metric_value(
            specification.value,
            specification.unit,
        ),
    )
    st.caption(
        " · ".join(
            (
                f"Values: {specification.value_count}",
                (
                    "Average: "
                    + format_metric_value(
                        specification.average,
                        specification.unit,
                    )
                ),
                (
                    "Minimum: "
                    + format_metric_value(
                        specification.minimum,
                        specification.unit,
                    )
                ),
                (
                    "Maximum: "
                    + format_metric_value(
                        specification.maximum,
                        specification.unit,
                    )
                ),
            )
        )
    )


def _unsupported_specification(
    specification: Never,
) -> Never:
    raise TypeError(f"Unsupported visualization specification {type(specification).__name__}")


def render_visualization(
    specification: VisualizationSpecification,
) -> None:
    """Dispatch one validated specification to its safe renderer."""
    if isinstance(specification, KPIVisualizationSpec):
        render_kpi(specification)
        return

    if isinstance(specification, BarVisualizationSpec):
        st.plotly_chart(
            build_bar_figure(specification),
            width="stretch",
            key=f"visualization-{specification.spec_id}",
        )
        return

    if isinstance(specification, LineVisualizationSpec):
        st.plotly_chart(
            build_line_figure(specification),
            width="stretch",
            key=f"visualization-{specification.spec_id}",
        )
        return

    _unsupported_specification(specification)


def _evidence_caption(
    evidence: InsightEvidenceReference,
) -> str:
    evidence_type = str(
        getattr(
            evidence.evidence_type,
            "value",
            evidence.evidence_type,
        )
    )
    target = evidence.metric_name or evidence.specification_id or "trusted evidence"
    return f"{evidence_type}: {target}"


def render_presentation(
    result: AnalyticalPresentationResult,
) -> None:
    """Render only fields from the validated public presentation."""
    st.subheader("Query results")
    st.dataframe(
        query_dataframe(result.query),
        width="stretch",
        hide_index=True,
    )

    with st.expander("Validated SQL"):
        st.code(
            result.query.validated_sql,
            language="sql",
        )

    st.subheader("Visualizations")

    for specification in result.visualizations.specifications:
        render_visualization(specification)

    st.subheader("Grounded insights")
    st.markdown(
        result.insights.summary,
        unsafe_allow_html=False,
    )

    for claim in result.insights.claims:
        st.markdown(
            f"- {claim.text}",
            unsafe_allow_html=False,
        )
        st.caption(
            "Evidence: " + " · ".join(_evidence_caption(evidence) for evidence in claim.evidence)
        )


__all__ = [
    "build_bar_figure",
    "build_line_figure",
    "format_metric_value",
    "query_dataframe",
    "render_kpi",
    "render_presentation",
    "render_visualization",
]
