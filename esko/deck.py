"""
Build the complete Esko Lead-Time Analytics PPTX deck from already-computed
metrics DataFrames. Called from app.py behind a 'Download PPTX' button.
"""
from datetime import date
import io

from esko import export_pptx as px
from esko import napco_charts as nc


def build_deck(
    *,
    kpis: dict,
    stage_lead_time_df,          # columns: stage_no, stage_label, stage_lead_time (process order)
    step_metrics_df,             # columns: stage_name, count, avg_days, total_days_impact, cumulative_pct
    per_project_df,              # columns: project_name, sys_lead_time, calc_lead_time, variance, task_count
    open_by_stage_df,            # columns: pending_stage_label, open_count
    open_by_owner_df,            # columns: pending_owner, open_count
    ageing_df,                   # columns: age_bucket, pending_stage_label, open_count
    logo_path=None,
    subtitle="Lead-Time Analytics",
) -> io.BytesIO:
    prs = px.new_presentation()

    px.add_cover_slide(
        prs, "Esko Lead-Time Analytics", subtitle,
        date_str=date.today().strftime("%d %b %Y"), logo_path=logo_path,
    )

    # ---- Completed projects section ----
    px.add_section_slide(prs, "Completed Projects")
    px.add_kpi_slide(prs, "Key Metrics", kpis)

    flow_sorted = stage_lead_time_df.sort_values("stage_no")
    fig = nc.ranked_bar(
        flow_sorted["stage_label"], flow_sorted["stage_lead_time"],
        title="Flow Overview - Weighted Lead Time by Stage (process order)",
    )
    px.add_content_slide(prs, "Flow Overview", fig)

    lt_sorted = stage_lead_time_df.sort_values("stage_lead_time", ascending=False)
    fig = nc.ranked_bar(
        lt_sorted["stage_label"], lt_sorted["stage_lead_time"],
        title="Lead Time by Stage (descending)", color=px.theme.NAPCO_BLUE,
    )
    px.add_content_slide(prs, "Lead Time by Stage", fig)

    top10 = step_metrics_df.head(10)
    fig = nc.pareto_dual_axis(top10["stage_name"], top10["total_days_impact"], top10["cumulative_pct"])
    px.add_content_slide(prs, "Pareto - Top 10 Steps", fig)

    table_cols = ["project_name", "sys_lead_time", "calc_lead_time", "variance", "task_count"]
    px.add_table_slide(
        prs, "Per-Project Variance (idle/queue time, descending)",
        per_project_df[table_cols],
    )

    # ---- Open projects section ----
    px.add_section_slide(prs, "Open Projects")

    ob = open_by_stage_df.sort_values("open_count", ascending=False)
    fig = nc.ranked_bar(ob["pending_stage_label"], ob["open_count"],
                         title="Open Projects by Pending Stage", ylabel="Count", color=px.theme.NAPCO_BLUE)
    px.add_content_slide(prs, "Open Projects by Pending Stage", fig)

    oo = open_by_owner_df.sort_values("open_count", ascending=False)
    fig = nc.ranked_bar(oo["pending_owner"], oo["open_count"],
                         title="Open Projects by Pending Owner", ylabel="Count", color=px.theme.GOLD)
    px.add_content_slide(prs, "Open Projects by Pending Owner", fig)

    bucket_labels = list(dict.fromkeys(ageing_df["age_bucket"].astype(str)))
    stage_series = {
        stage: ageing_df.loc[ageing_df["pending_stage_label"] == stage]
        .set_index("age_bucket").reindex(bucket_labels)["open_count"].fillna(0).tolist()
        for stage in ageing_df["pending_stage_label"].unique()
    }
    fig = nc.ageing_stacked_bar(bucket_labels, stage_series)
    px.add_content_slide(prs, "Ageing Buckets, Stacked by Stage", fig)

    return px.save_to_bytes(prs)
