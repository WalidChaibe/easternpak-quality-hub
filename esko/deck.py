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
    closed_project_count: int,
    closed_weighted_lead_time: float,
    open_project_count: int,
    open_age_bucket_labels: list,
    open_age_bucket_counts: list,
    closed_only_total: float,
    blended_total: float,
    closed_only_median_project_days: float,
    blended_median_project_days: float,
    stage_lead_time_df,
    weighted_full_df,
    top_owners_df,
    over15_internal_df,
    over15_internal_count: int,
    over15_external_count: int,
    rework_step_name: str,
    rework_occurrence_counts,
    logo_path=None,
    subtitle="Lead-Time Analytics",
) -> io.BytesIO:
    """Structure:
      1. Cover
      2. Analysis Overview - Closed vs Open (single stat panel + chart)
      3. Weighted Lead Time - Closed vs Blended View (stage-weighted lens)
      4. Median Project Lead Time - Closed vs Blended View (project-level lens,
         blended = closed projects' actual lead time + open projects' current age)
      -- Where is the time going --
      5. Flow Overview - Weighted Lead Time by Stage (process order)
      6. Flow Overview - Weighted Lead Time by Stage (sorted)
      7-13. Stage N - <label> - Deep Dive (top 7, non-zero only)
      -- The Open Backlog --
      14. Status of Open Projects (single stacked bar, every segment labeled)
      15. Internally Pending Open Projects (sorted oldest first)
      -- People & Process Signals --
      16. Average Task Duration by Assignee (min. 10 tasks)
      17. Occurrences of '<step>' per Project
    Titles are deliberately plain/descriptive, not conclusions - the numbers
    and charts make the case, the title just says what's on the page."""
    buf = io.BytesIO()
    c = pdf.new_canvas(buf)

    pdf.add_cover_page(
        c, "Esko Lead-Time Analytics", subtitle,
        date_str=date.today().strftime("%d %b %Y"), logo_path=logo_path,
    )
    c.showPage()

    # ---- Slide 2: Analysis Overview ----
    total_projects = closed_project_count + open_project_count
    closed_pct = f"{closed_project_count / total_projects:.0%}" if total_projects else None
    open_pct = f"{open_project_count / total_projects:.0%}" if total_projects else None

    fig = nc.ranked_bar(open_age_bucket_labels, open_age_bucket_counts,
                        title="", ylabel="Open project count", color=px.theme.NAPCO_BLUE, rotation=0)
    pdf.add_overview_page(
        c, "Analysis Overview - Closed vs Open",
        stats=[
            (total_projects, "Total Projects", None),
            (closed_project_count, "Projects Closed", closed_pct),
            (open_project_count, "Projects Open", open_pct),
            (f"{closed_weighted_lead_time:.1f} d", "Weighted Lead Time", None),
        ],
        fig=fig,
    )
    c.showPage()

    # ---- Slide 3: stage-weighted lens ----
    pdf.add_headline_comparison_page(
        c, "Weighted Lead Time - Closed vs Blended View",
        cards=[
            (f"{closed_only_total:.1f} d", "Closed-only view", "Projects that finished the full journey"),
            (f"{blended_total:.1f} d", "Blended view", "Every completed task, incl. work inside open projects"),
        ],
        note=(
            "Weighted view: each process stage's time is the average duration of its tasks, scaled "
            "by how often that task actually occurs. Closed-only uses only finished projects; "
            "blended also counts individually-completed tasks from projects that are still open."
        ),
    )
    c.showPage()

    # ---- Slide 4: project-level lens ----
    pdf.add_headline_comparison_page(
        c, "Median Project Lead Time - Closed vs Blended View",
        cards=[
            (f"{closed_only_median_project_days:.1f} d", "Closed-only view", "Median actual lead time, finished projects only"),
            (f"{blended_median_project_days:.1f} d", "Blended view", "Also includes open projects' current age"),
        ],
        note=(
            "Project-level view (different from the weighted view above): closed-only is the "
            "median actual start-to-finish time for the 386 finished projects. Blended pools those "
            "same finish times together with every open project's current age (days since creation, "
            "since it hasn't finished yet) into one combined median."
        ),
    )
    c.showPage()

    # ---- Where is the time going ----
    pdf.add_section_page(c, "Where Is the Time Going?")
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

    # ---- Stage deep-dives: which specific steps drive each stage's total (top 7, non-zero only) ----
    for _, stage_row in stage_lead_time_df.sort_values("stage_lead_time", ascending=False).iterrows():
        stage_no, stage_label = stage_row["stage_no"], stage_row["stage_label"]
        stage_steps = (
            weighted_full_df[weighted_full_df["stage_no"] == stage_no]
            .sort_values("weighted_lead_time", ascending=False)
        )
        # Drop anything that rounds to 0.0 at the chart's own 1-decimal display -
        # a bar and a crowded x-axis label for a value too small to read isn't useful.
        stage_steps = stage_steps[stage_steps["weighted_lead_time"].round(1) > 0].head(7)
        if len(stage_steps) < 2:
            continue  # a single-step stage has nothing to "deep dive" into
        fig = nc.ranked_bar(stage_steps["stage_name"], stage_steps["weighted_lead_time"],
                            title="", ylabel="Weighted lead time (days)", color=px.theme.NAPCO_BLUE)
        pdf.add_chart_page(c, f"Stage {int(stage_no)} - {stage_label} - Deep Dive", fig)
        c.showPage()

    # ---- The Open Backlog ----
    pdf.add_section_page(c, "The Open Backlog")
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

    # ---- People and process signals ----
    pdf.add_section_page(c, "People & Process Signals")
    c.showPage()

    fig = nc.ranked_bar(top_owners_df["assigned_to"], top_owners_df["avg_days"],
                        title="", ylabel="Average days per task", color=px.theme.GOLD)
    pdf.add_chart_page(c, "Average Task Duration by Assignee (min. 10 tasks)", fig)
    c.showPage()

    fig = nc.occurrence_histogram(rework_occurrence_counts, title="")
    pdf.add_chart_page(c, f"Occurrences of '{rework_step_name}' per Project", fig)
    # no trailing showPage() - this is the last page

    return pdf.finish(c, buf)