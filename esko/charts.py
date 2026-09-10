"""
Plotly figures for the interactive Streamlit dashboard (spec section 8).
Pure functions: (DataFrame, **params) -> go.Figure. No Streamlit calls.
Every chart sorted highest-to-lowest unless it's a process-order flow view.
"""
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

NAPCO_BLUE = "#0D68A3"
LIGHT_BLUE = "#D5E8F0"
GOLD = "#C1A02E"

CATEGORY_PALETTE = {
    "Service": "#C1A02E",
    "Quality": "#006394",
    "Invalid": "#D8C37D",
    "Commercial": "#2E8449",
}

_CHROME = dict(
    plot_bgcolor="white",
    paper_bgcolor="white",
    font=dict(family="Arial, Helvetica, sans-serif", size=12, color="#1F2937"),
    margin=dict(l=60, r=30, t=60, b=60),
)


def flow_overview(stage_lead_time: pd.DataFrame) -> go.Figure:
    """1. FLOW OVERVIEW - the 7 stages in PROCESS order, bar height = stage_lead_time."""
    df = stage_lead_time.sort_values("stage_no")
    fig = go.Figure(go.Bar(
        x=df["stage_label"], y=df["stage_lead_time"],
        marker_color=NAPCO_BLUE,
        text=df["stage_lead_time"].round(2), textposition="outside",
    ))
    fig.update_layout(title="Flow Overview - Weighted Lead Time by Stage (process order)",
                       yaxis_title="Weighted lead time (days)", **_CHROME)
    return fig


def lead_time_by_stage(stage_lead_time: pd.DataFrame) -> go.Figure:
    """2. LEAD TIME BY STAGE - descending."""
    df = stage_lead_time.sort_values("stage_lead_time", ascending=False)
    fig = go.Figure(go.Bar(
        x=df["stage_label"], y=df["stage_lead_time"], marker_color=NAPCO_BLUE,
        text=df["stage_lead_time"].round(2), textposition="outside",
    ))
    fig.update_layout(title="Lead Time by Stage (descending)",
                       yaxis_title="Weighted lead time (days)", **_CHROME)
    return fig


def stage_by_owner_grouped_bar(completed: pd.DataFrame) -> go.Figure:
    """3a. LEAD TIME BY STAGE x COMPLETED BY - grouped bar, descending by total."""
    g = completed.groupby(["stage_label", "completed_by"])["task_duration_days"].sum().reset_index()
    totals = g.groupby("stage_label")["task_duration_days"].sum().sort_values(ascending=False)
    g["stage_label"] = pd.Categorical(g["stage_label"], categories=totals.index, ordered=True)
    g = g.sort_values("stage_label")
    fig = px.bar(g, x="stage_label", y="task_duration_days", color="completed_by", barmode="group")
    fig.update_layout(title="Lead Time by Stage x Completed By", yaxis_title="Total days", **_CHROME)
    return fig


def stage_owner_heatmap(completed: pd.DataFrame, min_n: int = 3) -> go.Figure:
    """3b. Heatmap (stage x owner) of avg_days, with count so a 1-task owner isn't misread."""
    g = completed.groupby(["stage_label", "completed_by"])["task_duration_days"].agg(["mean", "count"]).reset_index()
    g["mean_display"] = g["mean"].where(g["count"] >= min_n)
    pivot_mean = g.pivot(index="stage_label", columns="completed_by", values="mean_display")
    pivot_count = g.pivot(index="stage_label", columns="completed_by", values="count")

    hover = pivot_mean.round(2).astype(str) + " days (n=" + pivot_count.fillna(0).astype(int).astype(str) + ")"
    fig = go.Figure(go.Heatmap(
        z=pivot_mean.values, x=pivot_mean.columns, y=pivot_mean.index,
        colorscale="Blues", text=hover.values, hoverinfo="text",
    ))
    fig.update_layout(title=f"Avg Days by Stage x Owner (cells with n < {min_n} greyed out)", **_CHROME)
    return fig


def pareto_top10(step_metrics: pd.DataFrame) -> go.Figure:
    """5. PARETO of top 10 steps by total_days_impact with cumulative % line."""
    df = step_metrics.head(10)
    fig = go.Figure()
    fig.add_bar(x=df["stage_name"], y=df["total_days_impact"], name="Total days impact", marker_color=NAPCO_BLUE)
    fig.add_trace(go.Scatter(
        x=df["stage_name"], y=df["cumulative_pct"] * 100, name="Cumulative %",
        yaxis="y2", mode="lines+markers", line=dict(color=GOLD, width=2.5),
    ))
    fig.update_layout(
        title="Pareto - Top 10 Steps by Total Days Impact",
        yaxis=dict(title="Total days impact"),
        yaxis2=dict(title="Cumulative %", overlaying="y", side="right", range=[0, 105]),
        **_CHROME,
    )
    return fig


def stage_deep_dive_table(step_metrics: pd.DataFrame, weighted: pd.DataFrame, stage_no: int) -> pd.DataFrame:
    """4. STAGE DEEP-DIVE table for a selected stage."""
    cols = ["stage_name", "count", "avg_days", "total_days_impact", "weighted_lead_time"]
    return weighted.loc[weighted["stage_no"] == stage_no, cols].sort_values("total_days_impact", ascending=False)


def duration_distribution(completed: pd.DataFrame, stage_no: int) -> go.Figure:
    """4. Duration distribution (box) for steps within a stage, to expose outliers."""
    df = completed[completed["stage_no"] == stage_no]
    fig = px.box(df, x="stage_name", y="task_duration_days", points="outliers", color_discrete_sequence=[NAPCO_BLUE])
    fig.update_layout(title="Duration Distribution (outlier check)", yaxis_title="Days", **_CHROME)
    return fig


def per_project_table(per_project: pd.DataFrame) -> pd.DataFrame:
    """7. Per-project table sorted descending by variance (idle/queue time)."""
    return per_project[["project_name", "sys_lead_time", "calc_lead_time", "variance", "task_count"]]


def open_by_stage(counts: pd.DataFrame) -> go.Figure:
    df = counts.sort_values("open_count", ascending=False)
    fig = go.Figure(go.Bar(x=df["pending_stage_label"], y=df["open_count"], marker_color=NAPCO_BLUE))
    fig.update_layout(title="Open Projects by Pending Stage", yaxis_title="Count", **_CHROME)
    return fig


def open_by_owner(counts: pd.DataFrame) -> go.Figure:
    df = counts.sort_values("open_count", ascending=False)
    fig = go.Figure(go.Bar(x=df["pending_owner"], y=df["open_count"], marker_color=GOLD))
    fig.update_layout(title="Open Projects by Pending Owner", yaxis_title="Count", **_CHROME)
    return fig


def ageing_stacked(buckets: pd.DataFrame) -> go.Figure:
    fig = px.bar(buckets, x="age_bucket", y="open_count", color="pending_stage_label", barmode="stack")
    fig.update_layout(title="Ageing Buckets, Stacked by Stage", yaxis_title="Open project count", **_CHROME)
    return fig
