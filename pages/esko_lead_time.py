import streamlit as st
import pandas as pd
import io

from utils.auth import require_auth

from esko.load import load_export
from esko.stages import (
    load_stage_map, apply_stage_taxonomy, drop_excluded, save_stage_map, existing_stages,
    add_step_to_stage, rename_stage, next_available_stage_no,
)
from esko.filters import split_completed_open, filter_truncated, filter_inactive_projects, split_rework_projects, apply_sidebar_filters, ExclusionLog
from esko.metrics import per_project_metrics, per_step_metrics, weighted_lead_time, sanity_check_weighting
from esko.pending import find_pending_task, open_project_ages, count_by_pending_stage, count_by_pending_owner, ageing_buckets_by_stage, oldest_open_projects
from esko import charts
from esko.deck import build_pdf


@st.cache_data
def _load(file):
    return load_export(file)


def show():
    require_auth()
    st.title("⏱️ Esko Lead-Time Analytics")

    uploaded = st.file_uploader("Upload the raw Esko 'Search Results' export (.xlsx/.csv)", type=["xlsx", "xls", "csv"])
    if not uploaded:
        st.info("Upload an export to begin.")
        st.stop()

    raw_df = _load(uploaded)
    stage_map = load_stage_map("config/stage_map.csv")

    # ---- Sidebar filters (F3) ----
    st.sidebar.header("Filters")
    templates = st.sidebar.multiselect("Project Template", sorted(raw_df["project_template"].dropna().unique()))
    completed_by = st.sidebar.multiselect("Completed by", sorted(raw_df["completed_by"].dropna().unique()))
    date_min, date_max = raw_df["project_created_date"].min(), raw_df["project_created_date"].max()
    date_range = st.sidebar.date_input("Project Created Date range", (date_min, date_max))
    exclude_cliche = st.sidebar.toggle("Exclude Cliche Production", value=False)
    min_n = st.sidebar.slider("Min n for owner heatmap cells", 1, 10, 3)
    weight_basis = st.sidebar.radio("Weighting basis", ["global", "stage"], index=0,
                                     help="DECIDED default is 'global' (spec 4.3). 'stage' is old workbook behaviour, for reconciliation only.")

    # ---- Pipeline: F0 -> F1 -> F2 -> stage taxonomy -> F3 ----
    log = ExclusionLog()
    raw_df = filter_inactive_projects(raw_df, log)
    completed_raw, open_raw = split_completed_open(raw_df)
    completed_raw = filter_truncated(completed_raw, log)

    completed_mapped, completed_unmapped = apply_stage_taxonomy(completed_raw, stage_map)
    open_mapped, open_unmapped = apply_stage_taxonomy(open_raw, stage_map)
    completed_mapped = drop_excluded(completed_mapped)
    open_mapped = drop_excluded(open_mapped)

    if len(date_range) == 2:
        completed_mapped = apply_sidebar_filters(completed_mapped, templates or None, completed_by or None, date_range, exclude_cliche)
        open_mapped = apply_sidebar_filters(open_mapped, templates or None, completed_by or None, date_range, False)

    unmapped_base_steps = sorted(
        set(completed_unmapped["base_step"].dropna().unique())
        | set(open_unmapped["base_step"].dropna().unique())
    )

    with st.expander("Data quality", expanded=bool(unmapped_base_steps)):
        st.write(f"Rows excluded as truncated projects (F2): {len(log.reasons.get('F2_truncated_project', []))} projects")
        if len(log.reasons.get("F2_truncated_project", [])):
            st.dataframe(log.reasons["F2_truncated_project"])

        if unmapped_base_steps:
            st.error(
                f"⚠️ {len(unmapped_base_steps)} step(s) are not in the stage taxonomy. "
                "These are excluded from all metrics until you assign them below."
            )
            stage_options = existing_stages(stage_map)
            stage_choices = {
                f"{int(r.stage_no)} - {r.stage_name}": (int(r.stage_no), r.stage_name)
                for r in stage_options.itertuples()
            }

            for step in unmapped_base_steps:
                n_rows = (
                    (completed_unmapped["base_step"] == step).sum()
                    + (open_unmapped["base_step"] == step).sum()
                )
                with st.form(f"assign_{step}"):
                    st.markdown(f"**{step}**  ·  {n_rows} row(s) affected")
                    mode = st.radio(
                        "Assign to", ["Existing stage", "New stage"],
                        key=f"mode_{step}", horizontal=True,
                    )
                    if mode == "Existing stage":
                        choice = st.selectbox("Stage", list(stage_choices.keys()), key=f"stage_{step}")
                        target_no, target_label = stage_choices[choice]
                    else:
                        target_no = st.number_input(
                            "New stage number", min_value=1,
                            value=next_available_stage_no(stage_map), step=1, key=f"newno_{step}",
                        )
                        target_label = st.text_input("New stage name", key=f"newname_{step}")

                    submitted = st.form_submit_button("Assign")
                    if submitted:
                        if mode == "New stage" and not target_label.strip():
                            st.warning("Enter a name for the new stage.")
                        else:
                            stage_map = add_step_to_stage(stage_map, step, target_no, target_label)
                            save_stage_map(stage_map, "config/stage_map.csv")
                            st.success(f"'{step}' assigned to stage {target_no} - {target_label}.")
                            st.rerun()

    with st.expander("⚙️ Manage stages"):
        st.caption(
            "Edit stage names, or the stage a step belongs to, directly. Add rows for "
            "new steps, delete rows to unmap a step. Click Save to apply."
        )

        st.markdown("**Rename a stage** (applies to every step currently in it)")
        rn1, rn2, rn3 = st.columns([1, 2, 1])
        stage_opts = existing_stages(stage_map)
        with rn1:
            rn_stage_no = st.selectbox(
                "Stage #", stage_opts["stage_no"].tolist(), key="rename_stage_no",
            )
        with rn2:
            current_label = stage_opts.loc[stage_opts["stage_no"] == rn_stage_no, "stage_name"].iloc[0]
            rn_new_label = st.text_input("New name", value=current_label, key="rename_stage_label")
        with rn3:
            st.write("")
            st.write("")
            if st.button("Rename"):
                stage_map = rename_stage(stage_map, rn_stage_no, rn_new_label)
                save_stage_map(stage_map, "config/stage_map.csv")
                st.success(f"Stage {rn_stage_no} renamed to '{rn_new_label}'.")
                st.rerun()

        st.divider()
        edited = st.data_editor(
            stage_map, num_rows="dynamic", use_container_width=True, key="stage_map_editor",
        )
        col_save, col_download = st.columns([1, 1])
        with col_save:
            if st.button("💾 Save stage map"):
                try:
                    save_stage_map(edited, "config/stage_map.csv")
                    st.success("Saved. Reloading with the updated taxonomy...")
                    st.rerun()
                except ValueError as e:
                    st.error(f"Error: {e}")
        with col_download:
            st.download_button(
                "⬇️ Download stage_map.csv",
                edited.to_csv(index=False).encode("utf-8"),
                file_name="stage_map.csv", mime="text/csv",
            )
        st.caption(
            "Note: Save writes to the app's local config/stage_map.csv, which persists "
            "while this app instance is running. If you redeploy from git, that deploy "
            "will use whatever stage_map.csv is committed in the repo - download the "
            "updated file above and commit it if you want the change to stick permanently."
        )

    # ---- Metrics ----
    per_project = per_project_metrics(completed_mapped)
    step_metrics = per_step_metrics(completed_mapped)
    weighted = weighted_lead_time(step_metrics, completed_mapped, basis=weight_basis)
    stage_lead_time = weighted.attrs["stage_lead_time"]
    total_system_lead_time = weighted.attrs["total_system_lead_time"]

    ok, max_count, sanity_msg = sanity_check_weighting(weighted, completed_mapped["project_name"].nunique())
    if not ok:
        st.warning(sanity_msg)

    pending = find_pending_task(open_mapped)
    aged = open_project_ages(pending)
    open_stage_counts = count_by_pending_stage(aged)
    open_owner_counts = count_by_pending_owner(aged)
    ageing = ageing_buckets_by_stage(aged)

    kpis = {
        "Projects analysed": completed_mapped["project_name"].nunique(),
        "Projects excluded (F2)": len(log.reasons.get("F2_truncated_project", [])),
        "Total system lead time": f"{total_system_lead_time:.1f} d",
        "Median project lead time": f"{per_project['calc_lead_time'].median():.1f} d",
        "Open project count": open_mapped["project_name"].nunique(),
        "Oldest open project": f"{aged['project_age'].max():.0f} d" if len(aged) else "n/a",
    }

    tab_completed, tab_open = st.tabs(["Completed Projects", "Open Projects"])

    with tab_completed:
        cols = st.columns(len(kpis))
        for col, (label, value) in zip(cols, kpis.items()):
            col.metric(label, value)

        st.plotly_chart(charts.flow_overview(stage_lead_time), use_container_width=True)
        st.plotly_chart(charts.lead_time_by_stage(stage_lead_time), use_container_width=True)
        st.plotly_chart(charts.stage_by_owner_grouped_bar(completed_mapped), use_container_width=True)
        st.plotly_chart(charts.stage_owner_heatmap(completed_mapped, min_n=min_n), use_container_width=True)

        st.subheader("Stage deep-dive")
        stage_choice = st.selectbox("Stage", sorted(completed_mapped["stage_label"].dropna().unique()))
        stage_no_choice = int(completed_mapped.loc[completed_mapped["stage_label"] == stage_choice, "stage_no"].iloc[0])
        st.dataframe(charts.stage_deep_dive_table(step_metrics, weighted, stage_no_choice))
        st.plotly_chart(charts.duration_distribution(completed_mapped, stage_no_choice), use_container_width=True)

        st.plotly_chart(charts.pareto_top10(step_metrics), use_container_width=True)

        st.subheader("Per-project table (descending by variance)")
        st.dataframe(charts.per_project_table(per_project))

        st.divider()

        # ---- Split closed projects: clean pipeline projects vs. named '_RE-WORK' cliché
        # reorders - verified these are a structurally different kind of project (100% of
        # them touch only Cliche Ordering Process, never the rest of the pipeline), so they
        # get their own separate analysis instead of distorting the main weighted numbers.
        completed_clean, completed_rework = split_rework_projects(completed_mapped)
        n_clean = completed_clean["project_name"].nunique()

        # ---- Section 1 metrics, recomputed on the CLEAN (non-rework) population, with
        # basis='project_count' instead of 'global' - see esko/metrics.py docstring: 'global'
        # caps every step's weight at <=1.0 by construction (denominator = the busiest step's
        # own count), which structurally under-credits any step that genuinely recurs more than
        # once per project on average. 'project_count' removes that ceiling.
        per_project_clean = per_project_metrics(completed_clean)
        step_metrics_clean = per_step_metrics(completed_clean)
        weighted_clean = weighted_lead_time(step_metrics_clean, completed_clean,
                                             basis="project_count", n_projects=n_clean)
        stage_lead_time_clean = weighted_clean.attrs["stage_lead_time"]
        total_system_lead_time_clean = weighted_clean.attrs["total_system_lead_time"]

        # ---- Slowest average task duration by assignee (min. 10 tasks, NOT total volume) ----
        # Ranking by total days summed would credit high-volume-but-fast people (e.g. someone who
        # touches nearly every project but is quick per task) as if they were the biggest problem,
        # purely because of activity volume - the same frequency-vs-duration conflation already
        # fixed for steps. avg_days per task is the genuine per-person speed signal; a minimum task
        # count avoids one or two atypical tasks making someone look artificially slow or fast.
        internal_assignments = completed_clean[
            ~completed_clean["assigned_to"].str.contains("REQUESTOR", case=False, na=False)
        ]
        owner_stats = internal_assignments.groupby("assigned_to")["task_duration_days"].agg(
            count="count", avg_days="mean"
        ).reset_index()
        owner_stats_filtered = owner_stats[owner_stats["count"] >= 10]
        top_owners = owner_stats_filtered.sort_values("avg_days", ascending=False).head(7)

        # ---- Internal revision-cycle recurrence (NOT the same as named rework projects -
        # verified zero overlap between this population and the '_RE-WORK'-named ones) ----
        busiest_step_name = step_metrics_clean.sort_values("count", ascending=False).iloc[0]["stage_name"]
        rework_cycle_by_project = completed_clean[completed_clean["stage_name"] == busiest_step_name].groupby("project_name").size()
        rework_cycle_occurrences = rework_cycle_by_project.values

        # ---- Open projects over 15 days: internal vs customer-side hold ----
        # "Pending on customer" is TWO signals, combined with OR:
        #   (a) the pending task's NAME contains both "customer" and "approval" - this is a real
        #       customer-approval gate regardless of who happens to be assigned to shepherd it
        #       (an internal CS team member sometimes is), and
        #   (b) the pending task is literally assigned to _REQUESTOR - catches genuine
        #       customer-side data-entry steps (e.g. "Customer Requirement", "Update Project
        #       Input") that aren't named "...Approval" but are still sitting with the customer.
        # Checking only (a) would wrongly reclassify those as "internal"; checking only (b) would
        # miss the case this whole rule exists for - an internal person doing approval-named work.
        over15 = aged[aged["project_age"] > 15].copy()
        step_lower = over15["pending_step"].str.lower()
        is_customer_approval_task = step_lower.str.contains("customer") & step_lower.str.contains("approval")
        is_assigned_to_requestor = over15["pending_owner"].str.contains("REQUESTOR", case=False, na=False)
        over15["is_external"] = is_customer_approval_task | is_assigned_to_requestor
        over15_internal = over15.loc[~over15["is_external"]].sort_values("project_age", ascending=False)
        over15_internal_count = len(over15_internal)
        over15_external_count = int(over15["is_external"].sum())

        # ---- Open-project age distribution, standardized 0-3/3-15/15-30/30+ buckets ----
        age_bucket_labels = ["0-3 days", "3-15 days", "15-30 days", "30+ days"]
        age_bins = [-1, 2, 14, 29, float("inf")]
        aged_bucketed = aged.copy()
        aged_bucketed["proj_age_bucket"] = pd.cut(
            aged_bucketed["project_age"], bins=age_bins, labels=age_bucket_labels
        )
        age_dist = aged_bucketed["proj_age_bucket"].value_counts().reindex(age_bucket_labels).fillna(0)

        # ---- Section 3: rework (named '_RE-WORK') project stats ----
        rework_project_count = completed_rework["project_name"].nunique()
        rw_dates = completed_rework.groupby("project_name").agg(
            created=("project_created_date", "min"), completed=("project_completed_date", "max")
        )
        import numpy as _np
        rw_dates["lead_time"] = rw_dates.apply(
            lambda r: _np.busday_count(r["created"].date(), r["completed"].date()), axis=1
        ) if len(rw_dates) else pd.Series(dtype=float)
        rework_median_lead_time = float(rw_dates["lead_time"].median()) if len(rw_dates) else 0.0
        rework_mean_lead_time = float(rw_dates["lead_time"].mean()) if len(rw_dates) else 0.0
        rework_step_breakdown = (
            completed_rework.groupby("stage_name")["task_duration_days"].mean()
            .reset_index().rename(columns={"task_duration_days": "avg_days"})
            .sort_values("avg_days", ascending=False)
        )

        col_pdf, col_xl1, col_xl2, col_xl3 = st.columns(4)
        with col_pdf:
            if st.button("📥 Generate PDF report"):
                buf = build_pdf(
                    closed_project_count=n_clean,
                    closed_weighted_lead_time=total_system_lead_time_clean,
                    open_project_count=open_mapped["project_name"].nunique(),
                    open_age_bucket_labels=age_bucket_labels,
                    open_age_bucket_counts=age_dist.values.tolist(),
                    stage_lead_time_df=stage_lead_time_clean,
                    weighted_full_df=weighted_clean,
                    top_owners_df=top_owners,
                    over15_internal_df=over15_internal,
                    over15_internal_count=over15_internal_count,
                    over15_external_count=over15_external_count,
                    rework_cycle_step_name=busiest_step_name,
                    rework_cycle_occurrence_counts=rework_cycle_occurrences,
                    rework_project_count=rework_project_count,
                    rework_median_lead_time=rework_median_lead_time,
                    rework_mean_lead_time=rework_mean_lead_time,
                    rework_step_breakdown_df=rework_step_breakdown,
                    logo_path="static/napco_logo.png",
                )
                st.download_button("Download esko_lead_time_report.pdf", buf,
                                    file_name="esko_lead_time_report.pdf",
                                    mime="application/pdf")
        with col_xl1:
            xl_buf = io.BytesIO()
            with pd.ExcelWriter(xl_buf, engine="openpyxl") as writer:
                per_project.to_excel(writer, sheet_name="Completed Projects", index=False)
            xl_buf.seek(0)
            st.download_button("⬇️ Completed projects (.xlsx)", xl_buf,
                                file_name="completed_projects.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        with col_xl2:
            xl_buf2 = io.BytesIO()
            with pd.ExcelWriter(xl_buf2, engine="openpyxl") as writer:
                aged.to_excel(writer, sheet_name="Open Projects Summary", index=False)
            xl_buf2.seek(0)
            st.download_button("⬇️ Open projects summary (.xlsx)", xl_buf2,
                                file_name="open_projects_summary.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        with col_xl3:
            xl_buf3 = io.BytesIO()
            with pd.ExcelWriter(xl_buf3, engine="openpyxl") as writer:
                open_mapped.to_excel(writer, sheet_name="Open Projects Raw Data", index=False)
            xl_buf3.seek(0)
            st.download_button("⬇️ Open projects raw data (.xlsx)", xl_buf3,
                                file_name="open_projects_raw.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


    with tab_open:
        if len(aged):
            st.plotly_chart(charts.open_by_stage(open_stage_counts), use_container_width=True)
            st.plotly_chart(charts.open_by_owner(open_owner_counts), use_container_width=True)
            st.plotly_chart(charts.ageing_stacked(ageing), use_container_width=True)
            st.subheader("Escalation list - oldest open projects")
            st.dataframe(oldest_open_projects(aged, n=25))
        else:
            st.info("No open projects in the current filtered population.")

    with st.expander("Methodology notes"):
        st.markdown("""
        - **calc_lead_time** is a real Sun-Thu business-day count (`np.busday_count`), not the
          workbook's `span-(span/7)*2` approximation. Both are reported; only the real count is charted.
        - **Truncated projects** (F2): dropped when the first task's start date isn't the same
          calendar day as the project's created date (exact match, tol_days=0).
        - **Weighted lead time** uses the GLOBAL weighting basis by default (denominator = max count
          across all steps), per the decided spec. Switch to 'stage' in the sidebar only for
          reconciliation against the old workbook - do not use it for reporting, since it needs a
          second stage-level re-weighting that this app deliberately does not apply.
        - Per-step counts are computed with `groupby().mean()`/`.count()` directly from the filtered
          data, not pasted from a pivot - this fixes the scrambled COUNTIF references and the
          hardcoded, non-reactive 'Average Days' column in the original workbook.
        """)