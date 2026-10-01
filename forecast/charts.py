"""Plotly charts for the demand forecast page (on-screen only)."""
import pandas as pd
import plotly.graph_objects as go
from esko.napco_theme import NAPCO_BLUE, GOLD, RED_ACCENT, PREV_YEAR_BLUE

GREEN = "#2E8449"
GREY = "#9CA3AF"


def _x(periods) -> list:
    return [p.to_timestamp() for p in periods]


def _layout(fig, title, ytitle="MT"):
    fig.update_layout(
        title=dict(text=title, x=0, font=dict(color=NAPCO_BLUE, size=16)),
        yaxis_title=ytitle, plot_bgcolor="white", hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(l=10, r=10, t=60, b=10), height=420,
    )
    fig.update_yaxes(gridcolor="#EEEEEE", rangemode="tozero")
    return fig


def status_bridge(summary: pd.DataFrame, title: str):
    s = summary[summary["change_mt"].round(1) != 0]
    colors = [GREEN if v > 0 else RED_ACCENT for v in s["change_mt"]]
    fig = go.Figure(go.Bar(
        x=s["change_mt"], y=s["status"].astype(str), orientation="h", marker_color=colors,
        text=[f"{v:+,.0f} MT" for v in s["change_mt"]], textposition="outside",
        customdata=s["customers"], hovertemplate="%{y}<br>%{x:+,.0f} MT<br>%{customdata} customers<extra></extra>",
    ))
    fig = _layout(fig, title, ytitle="")
    fig.update_layout(hovermode="closest", xaxis_title="MT change vs same period last year")
    fig.update_yaxes(autorange="reversed")
    return fig


def forecast_chart(hist: pd.Series, fc_sum: pd.Series, fc_total: pd.Series | None,
                   partial: tuple | None, title: str):
    """hist / fc_*: Series indexed by Period('M'). partial: (Period, actual_to_date) or None."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=_x(hist.index), y=hist.values, name="Actual (complete months)",
                             mode="lines+markers", line=dict(color=NAPCO_BLUE, width=2.5)))
    # connect last actual to first forecast point
    fx = _x(hist.index[-1:]) + _x(fc_sum.index)
    fig.add_trace(go.Scatter(x=fx, y=list(hist.values[-1:]) + list(fc_sum.values),
                             name="Forecast - sum of customers", mode="lines+markers",
                             line=dict(color=GOLD, width=2.5, dash="dash")))
    if fc_total is not None:
        fig.add_trace(go.Scatter(x=fx, y=list(hist.values[-1:]) + list(fc_total.values),
                                 name="Cross-check - company-level model", mode="lines",
                                 line=dict(color=GREY, width=1.5, dash="dot")))
    if partial is not None:
        fig.add_trace(go.Scatter(x=_x([partial[0]]), y=[partial[1]], name="Invoiced so far (partial month)",
                                 mode="markers", marker=dict(color=PREV_YEAR_BLUE, size=10, symbol="diamond")))
    return _layout(fig, title)


def customer_chart(hist: pd.Series, fc: pd.Series, title: str):
    fig = go.Figure()
    fig.add_trace(go.Bar(x=_x(hist.index), y=hist.values, name="Actual", marker_color=NAPCO_BLUE))
    fig.add_trace(go.Bar(x=_x(fc.index), y=fc.values, name="Forecast", marker_color=GOLD))
    return _layout(fig, title)
