"""Tests for safe deterministic presentation rendering."""

from __future__ import annotations

import json
from contextlib import nullcontext
from decimal import Decimal
from unittest.mock import Mock

import pytest

from backend.app.schemas.presentation import (
    AnalyticalPresentationResult,
    PresentationQueryResult,
)
from backend.app.schemas.visualization import (
    BarVisualizationItem,
    BarVisualizationSpec,
    KPIVisualizationSpec,
    LineVisualizationPoint,
    LineVisualizationSpec,
)
from frontend import rendering


def _kpi() -> KPIVisualizationSpec:
    return KPIVisualizationSpec(
        spec_id="specification-kpi",
        title="Approved Revenue",
        metric_name="approved_revenue",
        unit="brl",
        value_count=2,
        value=Decimal("1234.56"),
        average=Decimal("617.28"),
        minimum=Decimal("500.00"),
        maximum=Decimal("734.56"),
    )


def _bar() -> BarVisualizationSpec:
    return BarVisualizationSpec(
        spec_id="specification-bar",
        title="Approved Revenue by Region",
        metric_name="approved_revenue",
        dimension_name="region",
        unit="brl",
        items=(
            BarVisualizationItem(
                position=1,
                label="North",
                value=Decimal("100.00"),
                share_percent=Decimal("40.00"),
            ),
            BarVisualizationItem(
                position=2,
                label="South",
                value=Decimal("150.00"),
                share_percent=Decimal("60.00"),
            ),
        ),
    )


def _line() -> LineVisualizationSpec:
    return LineVisualizationSpec(
        spec_id="specification-line",
        title="Approved Revenue over Time",
        metric_name="approved_revenue",
        dimension_name="month",
        unit="brl",
        points=(
            LineVisualizationPoint(
                position=1,
                label="2026-01",
                value=Decimal("100.00"),
                previous_value=None,
                absolute_change=None,
                percentage_change=None,
            ),
            LineVisualizationPoint(
                position=2,
                label="2026-02",
                value=Decimal("120.00"),
                previous_value=Decimal("100.00"),
                absolute_change=Decimal("20.00"),
                percentage_change=Decimal("20.00"),
            ),
        ),
    )


def _presentation() -> AnalyticalPresentationResult:
    payload = {
        "presentation_version": "1",
        "presentation_status": "generated",
        "source_row_count": 2,
        "query": {
            "validated_sql": ("SELECT region, approved_revenue FROM retail.orders LIMIT 1000"),
            "columns": [
                "region",
                "approved_revenue",
            ],
            "rows": [
                ["North", "100.00"],
                ["South", "100.00"],
            ],
            "row_count": 2,
        },
        "visualizations": {
            "visualization_version": "1",
            "visualization_status": "specified",
            "deterministic": True,
            "analytics_version": "1",
            "execution_version": "1",
            "semantic_version": "1",
            "catalog_version": "1",
            "source_row_count": 2,
            "specifications": [
                {
                    "spec_id": "specification-kpi",
                    "chart_type": "kpi",
                    "title": "Approved Revenue",
                    "metric_name": "approved_revenue",
                    "unit": "brl",
                    "value_count": 2,
                    "value": "200.00",
                    "average": "100.00",
                    "minimum": "100.00",
                    "maximum": "100.00",
                },
                {
                    "spec_id": "specification-bar",
                    "chart_type": "bar",
                    "title": "Approved Revenue by Region",
                    "metric_name": "approved_revenue",
                    "dimension_name": "region",
                    "unit": "brl",
                    "items": [
                        {
                            "position": 1,
                            "label": "North",
                            "value": "100.00",
                            "share_percent": "50.00",
                        },
                        {
                            "position": 2,
                            "label": "South",
                            "value": "100.00",
                            "share_percent": "50.00",
                        },
                    ],
                },
            ],
        },
        "insights": {
            "insight_version": "1",
            "insight_status": "generated",
            "grounded": True,
            "calculated_by_llm": False,
            "analytics_version": "1",
            "visualization_version": "1",
            "execution_version": "1",
            "semantic_version": "1",
            "catalog_version": "1",
            "source_row_count": 2,
            "provider": "mock",
            "model": "rendering-test",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
            },
            "summary": ("Approved revenue is available by region."),
            "claims": [
                {
                    "claim_id": ("claim-000000000000000000000001"),
                    "text": ("Approved revenue is available."),
                    "evidence": [
                        {
                            "evidence_type": "metric_summary",
                            "metric_name": "approved_revenue",
                            "specification_id": None,
                        }
                    ],
                }
            ],
        },
    }

    return AnalyticalPresentationResult.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (
            Decimal("1234.56"),
            "brl",
            "R$ 1.234,56",
        ),
        (
            Decimal("12.345"),
            "percentage",
            "12,34%",
        ),
        (
            Decimal("1234"),
            "count",
            "1.234",
        ),
        (
            Decimal("10.5000"),
            "number",
            "10.5",
        ),
    ],
)
def test_format_metric_value_is_deterministic(
    value: Decimal,
    unit: str,
    expected: str,
) -> None:
    assert rendering.format_metric_value(value, unit) == expected


def test_query_dataframe_preserves_public_rows() -> None:
    query = PresentationQueryResult(
        validated_sql=("SELECT region, approved_revenue FROM retail.orders LIMIT 1000"),
        columns=("region", "approved_revenue"),
        rows=(
            ("North", "100.01"),
            ("South", "200.02"),
        ),
        row_count=2,
    )

    frame = rendering.query_dataframe(query)

    assert tuple(frame.columns) == (
        "region",
        "approved_revenue",
    )
    assert frame.astype(str).values.tolist() == [
        ["North", "100.01"],
        ["South", "200.02"],
    ]


def test_bar_figure_preserves_order_and_exact_hover_values() -> None:
    figure = rendering.build_bar_figure(_bar())
    trace = figure.data[0]

    assert trace.type == "bar"
    assert tuple(trace.x) == ("North", "South")
    assert tuple(trace.y) == (100.0, 150.0)
    assert list(trace.customdata[0]) == [
        "R$ 100,00",
        "40,00%",
    ]
    assert figure.layout.xaxis.title.text == "region"
    assert figure.layout.yaxis.title.text == "BRL"


def test_line_figure_preserves_temporal_order_and_changes() -> None:
    figure = rendering.build_line_figure(_line())
    trace = figure.data[0]

    assert trace.type == "scatter"
    assert trace.mode == "lines+markers"
    assert tuple(trace.x) == ("2026-01", "2026-02")
    assert tuple(trace.y) == (100.0, 120.0)
    assert list(trace.customdata[1]) == [
        "R$ 120,00",
        "R$ 100,00",
        "R$ 20,00",
        "20,00%",
    ]


def test_render_kpi_uses_metric_and_bounded_caption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metric = Mock()
    caption = Mock()
    monkeypatch.setattr(rendering.st, "metric", metric)
    monkeypatch.setattr(rendering.st, "caption", caption)

    rendering.render_kpi(_kpi())

    metric.assert_called_once_with(
        label="Approved Revenue",
        value="R$ 1.234,56",
    )
    caption.assert_called_once()
    rendered_caption = caption.call_args.args[0]
    assert "Values: 2" in rendered_caption
    assert "Average: R$ 617,28" in rendered_caption
    assert "Minimum: R$ 500,00" in rendered_caption
    assert "Maximum: R$ 734,56" in rendered_caption


def test_render_presentation_uses_only_safe_streamlit_elements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subheader = Mock()
    dataframe = Mock()
    expander = Mock(return_value=nullcontext())
    code = Mock()
    metric = Mock()
    caption = Mock()
    plotly_chart = Mock()
    markdown = Mock()

    monkeypatch.setattr(
        rendering.st,
        "subheader",
        subheader,
    )
    monkeypatch.setattr(
        rendering.st,
        "dataframe",
        dataframe,
    )
    monkeypatch.setattr(
        rendering.st,
        "expander",
        expander,
    )
    monkeypatch.setattr(rendering.st, "code", code)
    monkeypatch.setattr(rendering.st, "metric", metric)
    monkeypatch.setattr(
        rendering.st,
        "caption",
        caption,
    )
    monkeypatch.setattr(
        rendering.st,
        "plotly_chart",
        plotly_chart,
    )
    monkeypatch.setattr(
        rendering.st,
        "markdown",
        markdown,
    )

    result = _presentation()
    rendering.render_presentation(result)

    assert subheader.call_count == 3
    dataframe.assert_called_once()
    assert dataframe.call_args.kwargs == {
        "width": "stretch",
        "hide_index": True,
    }
    expander.assert_called_once_with("Validated SQL")
    code.assert_called_once_with(
        result.query.validated_sql,
        language="sql",
    )
    metric.assert_called_once()
    plotly_chart.assert_called_once()
    assert markdown.call_count == 2

    for call in markdown.call_args_list:
        assert call.kwargs["unsafe_allow_html"] is False
