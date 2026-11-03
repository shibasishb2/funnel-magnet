"""Plotly charts for the dashboard."""

import plotly.graph_objects as go

INK = "#16324f"
MUTED_INK = "#5b6b7a"
GRID = "#e6eaee"
ADDS = "#0ca30c"
SUBTRACTS = "#d03b3b"
FONT = "Inter, 'Segoe UI', Helvetica, Arial, sans-serif"


def _step_text(step):
    return f"{step.value:.2f}" if step.measure != "relative" else f"{step.value:+.2f}"


def contribution_waterfall(steps):
    """Waterfall from prior to current ROAS. Bars start at zero so sizes stay honest."""
    figure = go.Figure(
        go.Waterfall(
            x=[step.label for step in steps],
            y=[step.value for step in steps],
            measure=[step.measure for step in steps],
            text=[_step_text(step) for step in steps],
            textposition="outside",
            textfont={"color": INK, "size": 13},
            hovertemplate="%{x}<br>%{text}<extra></extra>",
            increasing={"marker": {"color": ADDS}},
            decreasing={"marker": {"color": SUBTRACTS}},
            totals={"marker": {"color": INK}},
            connector={"line": {"color": MUTED_INK, "width": 1}},
            cliponaxis=False,
        )
    )
    figure.update_layout(
        height=360,
        margin={"l": 8, "r": 8, "t": 28, "b": 8},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": FONT, "color": MUTED_INK, "size": 13},
        showlegend=False,
        yaxis={
            "title": {"text": "ROAS", "standoff": 12},
            "automargin": True,
            "gridcolor": GRID,
            "zeroline": False,
            "rangemode": "tozero",
        },
        # Labels such as 2026-10 would otherwise be read as dates, which breaks the steps.
        xaxis={"showgrid": False, "automargin": True, "type": "category"},
    )
    return figure
