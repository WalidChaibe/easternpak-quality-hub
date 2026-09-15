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
    all_closed_project_count: int,   # ALL closed projects, including rework - for the front overview only
    closed_project_count: int,       # non-rework closed only - used by Section 1
    closed_weighted_lead_time: float,
    open_project_count: int,
    open_age_bucket_labels: list,
    open_age_bucket_counts: list,
    stage_lead_time_df,
    weighted_full_df,
    top_owners_df,
    over15_internal_df,
    over15_internal_count: int,
    over15_external_count: int,
    rework_cycle_step_name: str,
    rework_cycle_occurrence_counts,
    rework_project_count: int,
    rework_median_lead_time: float,
    rework_mean_lead_time: float,
    rework_step_breakdown_df,     # columns: stage_name, avg_days (the 3 Cliché-reorder steps)
    logo_path=None,
    subtitle="Lead-Time Analytics",
) -> io.BytesIO:
    """Three clean, separate analyses - not one mixed report:
      1. Cover
      2. Analysis Overview - Closed vs Open (front-page snapshot, all closed
         projects incl. rework + open, before the detailed 3-way split below)
      -- Closed Project Lead Time Analysis --
      (non-rework closed projects only; weighted by basis='project_count', NOT
      'global' - see esko/metrics.py for why: 'global' caps every step's weight
      at <=1.0 by construction, which structurally under-credits steps that
      genuinely recur more than once per project. project_count removes that
      ceiling, so a step's weight is literally its average occurrences/project.)
      3. Overview (count + weighted lead time + nothing else mixed in)
      4-5. Flow Overview (process order, then sorted)
      6-12. Stage deep-dives (top 7 non-zero steps per stage)
      13. Average task duration by assignee (min. 10 tasks)
      14. Rejection-cycle recurrence histogram (internal revision loops - NOT
          the same thing as the named '_RE-WORK' projects in section 3 below;
          verified zero overlap between the two populations)
      -- Open Project Analysis --
      (unchanged from before)
      15. Age distribution overview
      16. Status of open projects (customer- vs internally-pending split)
      17. Internally-pending projects, named
      -- Rework Project Analysis --
      (projects literally named '_RE-WORK' - verified these are a structurally
      different kind of project: 100% of them touch ONLY the Cliché Ordering
      Process stage, never Artwork Development/Approval/PDN, since they're
      reprint-only requests for already-approved jobs. Not mixed into section 1.)
      17. Rework project stats (count, median/mean lead time)
      18. Which of their 3 steps takes the longest
    """
    buf = io.BytesIO()
    c = pdf.new_canvas(buf)

    pdf.add_cover_page(
        c, "Esko Lead-Time Analytics", subtitle,
        date_str=date.today().strftime("%d %b %Y"), logo_path=logo_path,
    )
    c.showPage()

    # ---- Front-page overview: all closed (incl. rework) vs open, before the detailed split ----
    total_projects = all_closed_project_count + open_project_count
    closed_pct = f"{all_closed_project_count / total_projects:.0%}" if total_projects else None
    open_pct = f"{open_project_count / total_projects:.0%}" if total_projects else None

    fig = nc.ranked_bar(open_age_bucket_labels, open_age_bucket_counts,
                        title="", ylabel="Open project count", color=px.theme.NAPCO_BLUE, rotation=0)
    pdf.add_overview_page(
        c, "Analysis Overview - Closed vs Open",
        stats=[
            (total_projects, "Total Projects", None),
            (all_closed_project_count, "Projects Closed", closed_pct),
            (open_project_count, "Projects Open", open_pct),
            (f"{closed_weighted_lead_time:.1f} d", "Weighted Lead Time (excl. rework)", None),
        ],
        fig=fig,
    )
    c.showPage()

    # ==================== SECTION 1: CLOSED PROJECT LEAD TIME ====================
    pdf.add_section_page(c, "Closed Project Lead Time Analysis")
    c.showPage()

    pdf.add_kpi_page(
        c, "Overview",
        {
            "Projects analysed": (closed_project_count, "Closed, excluding named rework projects"),
            "Weighted lead time": (f"{closed_weighted_lead_time:.1f} d", "Weight = avg. occurrences per project"),
        },
    )
    c.showPage()

    flow_sorted = stage_lead_time_df.sort_values("stage_no")
    fig = nc.ranked_bar(flow_sorted["stage_label"], flow_sorted["stage_lead_time"], title="")
    pdf.add_chart_page(c, "Flow Overview - Weighted Lead Time by Stage (process order)", fig)
    c.showPage()

    lt_sorted = stage_lead_time_df.sort_values("stage_lead_time", ascending=False)
    fig = nc.ranked_bar(lt_sorted["stage_label"], lt_sorted["stage_lead_time"],
                        title="", color=px.theme.NAPCO_BLUE)
    pdf.add_chart_page(c, "Flow Overview - Weighted Lead Time by Stage (sorted)", fig)
    c.showPage()

    for _, stage_row in stage_lead_time_df.sort_values("stage_lead_time", ascending=False).iterrows():
        stage_no, stage_label = stage_row["stage_no"], stage_row["stage_label"]
        stage_steps = (
            weighted_full_df[weighted_full_df["stage_no"] == stage_no]
            .sort_values("weighted_lead_time", ascending=False)
        )
        stage_steps = stage_steps[stage_steps["weighted_lead_time"].round(1) > 0].head(7)
        if len(stage_steps) < 2:
            continue
        fig = nc.ranked_bar(stage_steps["stage_name"], stage_steps["weighted_lead_time"],
                            title="", ylabel="Weighted lead time (days)", color=px.theme.NAPCO_BLUE)
        pdf.add_chart_page(c, f"Stage {int(stage_no)} - {stage_label} - Deep Dive", fig)
        c.showPage()

    fig = nc.ranked_bar(top_owners_df["assigned_to"], top_owners_df["avg_days"],
                        title="", ylabel="Average days per task", color=px.theme.GOLD)
    pdf.add_chart_page(c, "Average Task Duration by Assignee (min. 10 tasks)", fig)
    c.showPage()

    fig = nc.occurrence_histogram(rework_cycle_occurrence_counts, title="")
    pdf.add_chart_page(c, f"Occurrences of '{rework_cycle_step_name}' per Project (Internal Revision Cycles)", fig)
    c.showPage()

    # ==================== SECTION 2: OPEN PROJECT ANALYSIS ====================
    pdf.add_section_page(c, "Open Project Analysis")
    c.showPage()

    fig = nc.ranked_bar(open_age_bucket_labels, open_age_bucket_counts,
                        title="", ylabel="Open project count", color=px.theme.NAPCO_BLUE, rotation=0)
    pdf.add_overview_page(
        c, "Overview",
        stats=[
            (open_project_count, "Total Open Projects", None),
        ],
        fig=fig,
    )
    c.showPage()

    fig = nc.single_stacked_bar(
        segments=[
            ("Pending on customer", over15_external_count, px.theme.GOLD),
            ("Pending internally", over15_internal_count, px.theme.NAPCO_BLUE),
        ],
        title="", ylabel="Open projects aged over 15 days",
    )
    pdf.add_chart_page(c, "Status of Open Projects", fig)
    c.showPage()

    over15_cols = ["project_name", "pending_stage_label", "pending_owner", "project_age"]
    pdf.add_table_page(
        c, "Internally Pending Open Projects (sorted oldest first)",
        over15_internal_df[over15_cols],
    )
    c.showPage()

    # ==================== SECTION 3: REWORK PROJECT ANALYSIS ====================
    pdf.add_section_page(c, "Rework Project Analysis")
    c.showPage()

    pdf.add_kpi_page(
        c, "Overview",
        {
            "Rework projects": (rework_project_count, "Named '_RE-WORK' - cliché reprint requests only"),
            "Median lead time": (f"{rework_median_lead_time:.1f} d", "Creation to completion"),
            "Mean lead time": (f"{rework_mean_lead_time:.1f} d", "Pulled up by a small number of outliers"),
        },
        footnote=(
            "Every rework project touches only the Cliche Ordering Process stage - never Artwork "
            "Development, Customer Approval, or PDN - since these are reprint requests for artwork "
            "that was already approved previously, not a variant of a normal full-pipeline project."
        ),
    )
    c.showPage()

    fig = nc.ranked_bar(rework_step_breakdown_df["stage_name"], rework_step_breakdown_df["avg_days"],
                        title="", ylabel="Average days per task", color=px.theme.GOLD, rotation=0)
    pdf.add_chart_page(c, "Rework Projects - Average Duration by Step", fig)
    # no trailing showPage() - this is the last page

    return pdf.finish(c, buf)