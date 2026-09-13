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
    open_age_bucket_labels: list,
    open_age_bucket_counts: list,
    closed_only_total: float,
    blended_total: float,
    stage_lead_time_df,
    top_owners_df,
    over15_internal_df,
    over15_internal_count: int,
    over15_external_count: int,
    rework_step_name: str,
    rework_occurrence_counts,
    per_project_df,
    logo_path=None,
    subtitle="Lead-Time Analytics",
) -> io.BytesIO:
    """Redesigned structure - a narrative, not a chart dump:
      1. Cover
      2. At a glance: closed vs. open (split page)
      3. True system lead time: two lenses (closed-only vs. blended)
      -- Where is the time going --
      4. Flow overview by stage
      5. Lead time by stage, ranked (the single biggest driver)
      -- The open backlog --
      6. Internal vs. external split (single stacked bar)
      7. The internally-delayed projects, by name
      -- People & process signals --
      8. Top 7 lead-time contributors by owner
      9. Rework signal (occurrence histogram)
      10. What this suggests
      Appendix: per-project variance table
    'Total days impact' / Pareto is deliberately dropped - it's dominated by
    step frequency, not step duration, so it doesn't actually show where time
    is lost."""
    buf = io.BytesIO()
    c = pdf.new_canvas(buf)

    pdf.add_cover_page(
        c, "Esko Lead-Time Analytics", subtitle,
        date_str=date.today().strftime("%d %b %Y"), logo_path=logo_path,
    )
    c.showPage()

    # ---- Slide 2: at a glance ----
    fig = nc.ranked_bar(open_age_bucket_labels, open_age_bucket_counts,
                        title="", ylabel="Open project count", color=px.theme.NAPCO_BLUE, rotation=0)
    pdf.add_split_page(
        c, "At a Glance: Closed vs. Open Projects",
        left_title="Closed Projects",
        left_stats=[
            (closed_project_count, "Projects analysed"),
            (f"{closed_weighted_lead_time:.1f} d", "Weighted lead time"),
        ],
        right_title="Open Projects - Age Distribution",
        right_fig=fig,
    )
    c.showPage()

    # ---- Slide 3: true system lead time, two lenses ----
    pdf.add_headline_comparison_page(
        c, "True System Lead Time - Two Lenses",
        cards=[
            (f"{closed_only_total:.1f} d", "Closed-only view", "Projects that finished the full journey"),
            (f"{blended_total:.1f} d", "Blended view", "Every completed task, incl. work inside open projects"),
        ],
        note=(
            "These differ because the blended view is dominated by common early-stage steps that "
            "many open projects have already finished, while the closed-only view only reflects "
            "projects that made it all the way through - including the slower later stages. Neither "
            "number is 'more correct'; they answer different questions."
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
    pdf.add_chart_page(c, "Lead Time by Stage - The Single Biggest Driver", fig)
    c.showPage()

    # ---- The open backlog ----
    pdf.add_section_page(c, "The Open Backlog")
    c.showPage()

    fig = nc.single_stacked_bar(
        segments=[
            ("Pending on customer", over15_external_count, px.theme.GOLD),
            ("Pending internally", over15_internal_count, px.theme.NAPCO_BLUE),
        ],
        title="", ylabel="Open projects aged over 15 days",
    )
    pdf.add_chart_page(c, "95% of the Aged Backlog Is Waiting on the Customer, Not on Us", fig)
    c.showPage()

    over15_cols = ["project_name", "pending_stage_label", "pending_owner", "project_age"]
    pdf.add_table_page(
        c, "The 26 Projects Actually Stuck Internally (sorted oldest first)",
        over15_internal_df[over15_cols],
    )
    c.showPage()

    # ---- People and process signals ----
    pdf.add_section_page(c, "People & Process Signals")
    c.showPage()

    fig = nc.ranked_bar(top_owners_df["completed_by"], top_owners_df["total_days"],
                        title="", ylabel="Total lead-time contribution (days)", color=px.theme.GOLD)
    pdf.add_chart_page(c, "Lead-Time Delivery Is Concentrated in One Person", fig)
    c.showPage()

    fig = nc.occurrence_histogram(rework_occurrence_counts, title="")
    pdf.add_chart_page(
        c, f"'{rework_step_name}' Recurs More Than Once on 42% of Projects", fig,
    )
    c.showPage()

    pdf.add_bullets_page(
        c, "What This Suggests",
        bullets=[
            "Customer Artwork Approval is the single largest driver of lead time - and it's the "
            "customer's clock, not ours. Worth a conversation about setting a customer-facing SLA.",
            "The aged open-project backlog looks alarming (511 projects over 15 days) until it's "
            "split: 485 are simply waiting on the customer. Only 26 are genuinely stuck internally, "
            "concentrated in 3 teams - a small, specific problem, not a systemic one.",
            "Lead-time delivery is concentrated in one person (Sajesh Madathil, more than double the "
            "next two combined) - worth understanding whether that's a capacity risk.",
            "Technical Approval recurring 2+ times on 42% of projects is a possible rework/first-pass-"
            "yield signal worth investigating at the source.",
        ],
    )
    c.showPage()

    # ---- Appendix ----
    pdf.add_section_page(c, "Appendix")
    c.showPage()

    table_cols = ["project_name", "sys_lead_time", "calc_lead_time", "variance", "task_count"]
    pdf.add_table_page(
        c, "Per-Project Variance (idle/queue time, descending, top 20 shown)",
        per_project_df[table_cols], max_rows=20,
    )
    # no trailing showPage() - this is the last page

    return pdf.finish(c, buf)