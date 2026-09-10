"""
Build the complete Esko Lead-Time Analytics deck from already-computed metrics
DataFrames. build_pdf() is the one wired into app.py's 'Download report' button
(replacing the earlier PPTX approach - static chart images work naturally in a
PDF, unlike a presentation format). build_deck() (PPTX) is left in place and
untouched in case it's needed again later, just not called by the app anymore.
"""
from datetime import date
import io

from esko import export_pptx as px
from esko import export_pdf as pdf
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


def build_pdf(
    *,
    kpis: dict,
    stage_lead_time_df,
    step_metrics_df,
    per_project_df,
    open_by_stage_df,
    open_by_owner_df,
    ageing_df,
    top_owners_df,          # columns: completed_by, total_days (pre-sorted, top 7)
    over15_internal_df,     # columns: project_name, pending_stage_label, pending_owner, project_age
    over15_internal_count: int,
    over15_external_count: int,
    logo_path=None,
    subtitle="Lead-Time Analytics",
) -> io.BytesIO:
    """Same content and section order as build_deck(), as a PDF instead of a
    PPTX - same Napco theme, same chart figures, same KPI-card layout.
    Chart titles are intentionally NOT passed to the matplotlib figures here -
    the slide's own title bar already shows the heading, so an in-chart title
    would just duplicate it and eat vertical space."""
    buf = io.BytesIO()
    c = pdf.new_canvas(buf)

    pdf.add_cover_page(
        c, "Esko Lead-Time Analytics", subtitle,
        date_str=date.today().strftime("%d %b %Y"), logo_path=logo_path,
    )
    c.showPage()

    # ---- Completed projects section ----
    pdf.add_section_page(c, "Completed Projects")
    c.showPage()

    kpis_with_context = dict(kpis)
    if "Total system lead time" in kpis_with_context:
        kpis_with_context["Total system lead time"] = (
            kpis["Total system lead time"], "Weighted active task time"
        )
    if "Median project lead time" in kpis_with_context:
        kpis_with_context["Median project lead time"] = (
            kpis["Median project lead time"], "Actual elapsed days, incl. idle time"
        )
    pdf.add_kpi_page(
        c, "Key Metrics", kpis_with_context,
        footnote=(
            "Total system lead time = weighted sum of active task duration per stage (how much work "
            "a typical project requires). Median project lead time = actual elapsed business days per "
            "project, start to finish - naturally higher, since it also includes queue/idle time between tasks."
        ),
    )
    c.showPage()

    flow_sorted = stage_lead_time_df.sort_values("stage_no")
    fig = nc.ranked_bar(flow_sorted["stage_label"], flow_sorted["stage_lead_time"], title="")
    pdf.add_chart_page(c, "Flow Overview - Weighted Lead Time by Stage (process order)", fig)
    c.showPage()

    lt_sorted = stage_lead_time_df.sort_values("stage_lead_time", ascending=False)
    fig = nc.ranked_bar(lt_sorted["stage_label"], lt_sorted["stage_lead_time"],
                         title="", color=px.theme.NAPCO_BLUE)
    pdf.add_chart_page(c, "Lead Time by Stage (descending)", fig)
    c.showPage()

    top10 = step_metrics_df.head(10)
    fig = nc.pareto_dual_axis(top10["stage_name"], top10["total_days_impact"], top10["cumulative_pct"], title="")
    pdf.add_chart_page(c, "Pareto - Top 10 Steps", fig)
    c.showPage()

    fig = nc.ranked_bar(top_owners_df["completed_by"], top_owners_df["total_days"],
                         title="", ylabel="Total lead-time contribution (days)", color=px.theme.GOLD)
    pdf.add_chart_page(c, "Top 7 Lead-Time Contributors by Owner (Completed By)", fig)
    c.showPage()

    table_cols = ["project_name", "sys_lead_time", "calc_lead_time", "variance", "task_count"]
    pdf.add_table_page(
        c, "Per-Project Variance (idle/queue time, descending, top 20 shown)",
        per_project_df[table_cols], max_rows=20,
    )
    c.showPage()

    # ---- Open projects section ----
    pdf.add_section_page(c, "Open Projects")
    c.showPage()

    ob = open_by_stage_df.sort_values("open_count", ascending=False)
    fig = nc.ranked_bar(ob["pending_stage_label"], ob["open_count"],
                         title="", ylabel="Count", color=px.theme.NAPCO_BLUE)
    pdf.add_chart_page(c, "Open Projects by Pending Stage", fig)
    c.showPage()

    oo = open_by_owner_df.sort_values("open_count", ascending=False)
    fig = nc.ranked_bar(oo["pending_owner"], oo["open_count"],
                         title="", ylabel="Count", color=px.theme.GOLD)
    pdf.add_chart_page(c, "Open Projects by Pending Owner", fig)
    c.showPage()

    bucket_labels = list(dict.fromkeys(ageing_df["age_bucket"].astype(str)))
    stage_series = {
        stage: ageing_df.loc[ageing_df["pending_stage_label"] == stage]
        .set_index("age_bucket").reindex(bucket_labels)["open_count"].fillna(0).tolist()
        for stage in ageing_df["pending_stage_label"].unique()
    }
    fig = nc.ageing_stacked_bar(bucket_labels, stage_series, title="")
    pdf.add_chart_page(c, "Ageing Buckets, Stacked by Stage", fig)
    c.showPage()

    pdf.add_kpi_page(
        c, "Open Projects Over 15 Days - Where Is the Delay?",
        {
            "Pending on customer": (over15_external_count, "Requestor has not yet approved/responded"),
            "Pending internally at Epak": (over15_internal_count, "Genuinely stuck in an internal queue"),
        },
        footnote=(
            "Of all open projects older than 15 days, the table on the next page lists only the ones "
            "pending on an internal Epak owner - i.e. the ones actually worth investigating for internal delay."
        ),
    )
    c.showPage()

    over15_cols = ["project_name", "pending_stage_label", "pending_owner", "project_age"]
    pdf.add_table_page(
        c, "Internally-Delayed Open Projects (>15 days, sorted oldest first)",
        over15_internal_df[over15_cols],
    )
    # no trailing showPage() - this is the last page

    return pdf.finish(c, buf)