import streamlit as st
import pandas as pd
import io

from utils.auth import require_auth

from esko.load import load_export
from esko.stages import (
    load_stage_map, apply_stage_taxonomy, drop_excluded, save_stage_map, existing_stages,
    add_step_to_stage, rename_stage, next_available_stage_no,
)
from esko.filters import split_completed_open, filter_truncated, apply_sidebar_filters, ExclusionLog
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

    # ---- Pipeline: F1 -> F2 -> stage taxonomy -> F3 ----
    log = ExclusionLog()
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
        col_pdf, col_xl1, col_xl2 = st.columns(3)
        with col_pdf:
            if st.button("📥 Generate PDF report"):
                buf = build_pdf(
                    kpis=kpis,
                    stage_lead_time_df=stage_lead_time,
                    step_metrics_df=step_metrics,
                    per_project_df=per_project,
                    open_by_stage_df=open_stage_counts,
                    open_by_owner_df=open_owner_counts,
                    ageing_df=ageing,
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
                aged.to_excel(writer, sheet_name="Open Projects by Stage", index=False)
            xl_buf2.seek(0)
            st.download_button("⬇️ Open projects by stage (.xlsx)", xl_buf2,
                                file_name="open_projects_by_stage.xlsx",
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