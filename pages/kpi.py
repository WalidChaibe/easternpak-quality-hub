import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import date
from io import BytesIO
from utils.auth import require_auth, can_write, get_profile
from utils.supabase_client import get_supabase
from utils.helpers import get_departments


def show():
    require_auth()
    st.title("📊 KPI Tracking")
    st.caption("Monthly KPI entry, trend analysis, and 2026 target tracking")

    sb = get_supabase()

    tab_coq, tab_entry, tab_trends, tab_targets, tab_manage = st.tabs([
        "💰 Cost of Quality",
        "📝 Monthly Entry",
        "📈 Trends & Export",
        "🎯 2026 Targets",
        "⚙️ Manage KPIs"
    ])

    # ──────────────────────────────────────────────────────────
    # TAB 1 — COST OF QUALITY TRACKER
    # ──────────────────────────────────────────────────────────
    with tab_coq:
        st.subheader("💰 Cost of Quality — Annual Tracker")
        st.caption("Annual target: 1,900,000 SR")

        coq_def = sb.table("kpi_definitions").select("id").eq("name","Cost of Quality").single().execute()
        if not coq_def.data:
            st.warning("Cost of Quality KPI not found.")
        else:
            coq_id = coq_def.data["id"]
            year = st.selectbox("Year", [2025, 2026], key="coq_year")
            start = f"{year}-01-01"
            end   = f"{year}-12-31"

            entries = sb.table("kpi_entries").select("month, actual_value")\
                .eq("kpi_id", coq_id)\
                .gte("month", start).lte("month", end)\
                .order("month").execute()

            annual_target = 1900000

            if not entries.data:
                st.info("No Cost of Quality data entered yet for this year.")
            else:
                df = pd.DataFrame(entries.data)
                df["month"]        = pd.to_datetime(df["month"])
                df["month_label"]  = df["month"].dt.strftime("%b %Y")
                df["actual_value"] = df["actual_value"].astype(float)
                df["cumulative"]   = df["actual_value"].cumsum()

                total_spent  = df["actual_value"].sum()
                months_done  = len(df)
                monthly_avg  = total_spent / months_done if months_done > 0 else 0
                projected    = monthly_avg * 12
                remaining    = annual_target - total_spent
                on_track     = projected <= annual_target

                # Status cards
                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    st.metric("YTD Spent", f"{total_spent:,.0f} SR")
                with c2:
                    st.metric("Annual Target", f"{annual_target:,.0f} SR")
                with c3:
                    st.metric("Remaining Budget", f"{remaining:,.0f} SR",
                        delta=f"{'Over' if remaining < 0 else 'Under'} budget",
                        delta_color="inverse" if remaining < 0 else "normal")
                with c4:
                    st.metric("Projected Annual", f"{projected:,.0f} SR",
                        delta="On track ✓" if on_track else "At risk ⚠️",
                        delta_color="normal" if on_track else "inverse")

                # Status banner
                pct = (total_spent / annual_target) * 100
                if on_track:
                    st.success(f"✅ On track — {pct:.1f}% of annual budget used across {months_done} months. Projected year-end: {projected:,.0f} SR")
                elif projected > annual_target * 1.1:
                    st.error(f"🔴 At risk — Projected to exceed target by {projected - annual_target:,.0f} SR")
                else:
                    st.warning(f"🟡 Borderline — Monitor closely. Projected: {projected:,.0f} SR vs target {annual_target:,.0f} SR")

                # Monthly bar + cumulative line chart
                fig = go.Figure()
                fig.add_bar(
                    x=df["month_label"], y=df["actual_value"],
                    name="Monthly COQ", marker_color="#4a90d9", opacity=0.7
                )
                fig.add_scatter(
                    x=df["month_label"], y=df["cumulative"],
                    name="Cumulative", mode="lines+markers",
                    line=dict(color="#e85d04", width=2.5),
                    marker=dict(size=6)
                )
                # Target line
                fig.add_hline(
                    y=annual_target, line_dash="dash", line_color="red",
                    annotation_text=f"Annual Target: {annual_target:,.0f} SR",
                    annotation_position="top right"
                )
                fig.update_layout(
                    title="Monthly COQ vs Cumulative vs Annual Target",
                    xaxis_title="Month", yaxis_title="SR",
                    plot_bgcolor="white", height=380,
                    legend=dict(orientation="h", y=-0.2),
                    margin=dict(t=50, b=20, l=20, r=20)
                )
                st.plotly_chart(fig, use_container_width=True)

                # Monthly table
                st.markdown("#### Monthly Breakdown")
                display = df[["month_label","actual_value","cumulative"]].copy()
                display.columns = ["Month","Monthly (SR)","Cumulative (SR)"]
                display["Monthly (SR)"]    = display["Monthly (SR)"].apply(lambda x: f"{x:,.0f}")
                display["Cumulative (SR)"] = display["Cumulative (SR)"].apply(lambda x: f"{x:,.0f}")
                st.dataframe(display, use_container_width=True, hide_index=True)

    # ──────────────────────────────────────────────────────────
    # TAB 2 — MONTHLY ENTRY
    # ──────────────────────────────────────────────────────────
    with tab_entry:
        depts = get_departments()
        dept_map = {d["name"]: d["id"] for d in depts}

        col1, col2 = st.columns([1,1])
        with col1:
            selected_dept = st.selectbox("Department", list(dept_map.keys()), key="entry_dept")
        with col2:
            today  = date.today()
            months = pd.date_range(end=today, periods=24, freq="MS").to_list()
            month_labels = [m.strftime("%B %Y") for m in months]
            selected_month_label = st.selectbox("Month", month_labels[::-1], key="entry_month")
            selected_month = pd.to_datetime(selected_month_label, format="%B %Y").date()

        dept_id = dept_map[selected_dept]
        kpis_res = sb.table("kpi_definitions").select("*")\
            .eq("department_id", dept_id).eq("is_active", True).execute()
        kpis = kpis_res.data or []

        if not kpis:
            st.info(f"No KPIs defined for {selected_dept} yet.")
        else:
            st.markdown(f"#### {selected_dept} — {selected_month_label}")
            for kpi in kpis:
                kpi_id = kpi["id"]
                existing = sb.table("kpi_entries").select("*")\
                    .eq("kpi_id", kpi_id)\
                    .eq("month", selected_month.isoformat()).execute()
                rec = existing.data[0] if existing.data else None

                target_str = f"{kpi['target_type']} {kpi['target_value']} {kpi.get('unit','')}" if kpi.get("target_value") else "TBD"

                with st.expander(f"**{kpi['name']}** — Target: {target_str}", expanded=not rec):
                    with st.form(f"kpi_{kpi_id}_{selected_month}"):
                        actual = st.number_input(
                            f"Actual value ({kpi.get('unit','')})",
                            value=float(rec["actual_value"]) if rec and rec.get("actual_value") is not None else 0.0,
                            key=f"actual_{kpi_id}"
                        )
                        # Auto compute achieved
                        t  = kpi.get("target_type",">=")
                        tv = float(kpi.get("target_value") or 0)
                        if t == ">=":   auto_achieved = actual >= tv
                        elif t == "<=": auto_achieved = actual <= tv
                        else:           auto_achieved = actual == tv

                        achieved = st.checkbox("Achieved?", value=rec["achieved"] if rec else auto_achieved)

                        rc = ""
                        ca = ""
                        if not achieved:
                            st.warning("Target not achieved — root cause and corrective action required.")
                            rc = st.text_area("Root Cause", value=rec.get("root_cause","") if rec else "", height=70, key=f"rc_{kpi_id}")
                            ca = st.text_area("Corrective Action", value=rec.get("corrective_action","") if rec else "", height=70, key=f"ca_{kpi_id}")

                        save = st.form_submit_button("💾 Save", disabled=not can_write())

                    if save:
                        if not achieved and not (rc.strip() and ca.strip()):
                            st.error("Root cause and corrective action required when target not achieved.")
                        else:
                            profile = get_profile()
                            payload = {
                                "kpi_id":            kpi_id,
                                "month":             selected_month.isoformat(),
                                "actual_value":      actual,
                                "achieved":          achieved,
                                "root_cause":        rc or None,
                                "corrective_action": ca or None,
                                "entered_by":        profile["id"],
                            }
                            try:
                                if rec:
                                    sb.table("kpi_entries").update(payload)\
                                        .eq("kpi_id", kpi_id)\
                                        .eq("month", selected_month.isoformat()).execute()
                                else:
                                    sb.table("kpi_entries").insert(payload).execute()
                                st.success("✅ Saved.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error: {e}")

    # ──────────────────────────────────────────────────────────
    # TAB 3 — TRENDS & EXPORT
    # ──────────────────────────────────────────────────────────
    with tab_trends:
        depts = get_departments()
        dept_map2 = {d["name"]: d["id"] for d in depts}

        col1, col2, col3 = st.columns([1,1,1])
        with col1:
            dept_t = st.selectbox("Department", list(dept_map2.keys()), key="trend_dept")
        with col2:
            months_back = st.selectbox("Period", [6,12,24], format_func=lambda x: f"Last {x} months", key="trend_period")
        with col3:
            year_t = st.selectbox("Year", [2025, 2026], key="trend_year")

        cutoff = (pd.Timestamp.now() - pd.DateOffset(months=months_back)).date().replace(day=1).isoformat()

        res = sb.table("v_kpi_full").select("*")\
            .eq("department_name", dept_t)\
            .gte("month", cutoff)\
            .order("month").execute()

        data = res.data or []

        if not data:
            st.info("No data for this period.")
        else:
            df = pd.DataFrame(data)
            df["month"]       = pd.to_datetime(df["month"])
            df["month_label"] = df["month"].dt.strftime("%b %Y")

            kpi_names = df["kpi_name"].unique().tolist()
            selected_kpis = st.multiselect("Select KPIs to display", kpi_names, default=kpi_names[:4], key="trend_kpis")

            for kpi_name in selected_kpis:
                kdf = df[df["kpi_name"] == kpi_name].copy()
                target = kdf["target_value"].iloc[0]
                unit   = kdf["kpi_unit"].iloc[0] or ""

                fig = px.line(
                    kdf, x="month_label", y="actual_value",
                    title=f"{kpi_name}",
                    markers=True,
                    color_discrete_sequence=["#1a73e8"],
                )
                if target:
                    fig.add_hline(
                        y=float(target), line_dash="dash", line_color="red",
                        annotation_text=f"Target {target}{unit}",
                        annotation_position="bottom right"
                    )
                # Shade missed months
                for _, row in kdf[kdf["achieved"] == False].iterrows():
                    fig.add_vrect(
                        x0=row["month_label"], x1=row["month_label"],
                        fillcolor="red", opacity=0.08, line_width=0
                    )
                fig.update_layout(
                    xaxis_title="Month", yaxis_title=unit,
                    plot_bgcolor="white", height=280,
                    margin=dict(t=40,b=20,l=20,r=20)
                )
                st.plotly_chart(fig, use_container_width=True)

                # Show RCA for missed months
                missed = kdf[kdf["achieved"] == False][["month_label","actual_value","root_cause","corrective_action"]]
                if not missed.empty:
                    with st.expander(f"📋 Root causes for missed months — {kpi_name}"):
                        for _, row in missed.iterrows():
                            st.markdown(f"**{row['month_label']}** — Actual: {row['actual_value']} {unit}")
                            st.markdown(f"- Root Cause: {row['root_cause'] or '—'}")
                            st.markdown(f"- Corrective Action: {row['corrective_action'] or '—'}")

            # Excel export
            st.markdown("---")
            if st.button("⬇️ Export to Excel", key="export_btn"):
                export_df = df[["month_label","kpi_name","kpi_unit","target_value",
                                "actual_value","achieved","root_cause","corrective_action","entered_by_name"]]
                export_df.columns = ["Month","KPI","Unit","Target","Actual","Achieved",
                                     "Root Cause","Corrective Action","Entered By"]
                buf = BytesIO()
                with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                    export_df.to_excel(writer, index=False, sheet_name="KPI Data")
                buf.seek(0)
                st.download_button(
                    "📥 Download Excel",
                    data=buf,
                    file_name=f"KPI_{dept_t}_{date.today()}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="download_excel"
                )

    # ──────────────────────────────────────────────────────────
    # TAB 4 — 2026 TARGETS FORMULA TABLE
    # ──────────────────────────────────────────────────────────
    with tab_targets:
        st.subheader("🎯 2026 Target Reference Table")
        st.caption("How each 2026 target was calculated from 2025 performance")

        all_kpis = sb.table("kpi_definitions").select("*,departments(name)").eq("is_active", True).order("department_id").execute()

        if not all_kpis.data:
            st.info("No KPIs found.")
        else:
            # Get 2025 averages
            avg_res = sb.table("kpi_entries").select("kpi_id, actual_value")\
                .gte("month","2025-01-01").lte("month","2025-12-31").execute()

            avg_map = {}
            if avg_res.data:
                avg_df = pd.DataFrame(avg_res.data)
                avg_map = avg_df.groupby("kpi_id")["actual_value"].mean().to_dict()

            rows = []
            for k in all_kpis.data:
                dept     = (k.get("departments") or {}).get("name","—")
                avg_2025 = avg_map.get(k["id"])
                target   = k.get("target_value")
                ttype    = k.get("target_type",">=")
                unit     = k.get("unit","") or ""

                # Determine if achieved
                if avg_2025 is not None and target is not None:
                    if ttype == ">=":   achieved = avg_2025 >= float(target)
                    elif ttype == "<=": achieved = avg_2025 <= float(target)
                    else:               achieved = abs(avg_2025 - float(target)) < 0.01
                else:
                    achieved = None

                if k["name"] == "Cost of Quality":
                    method = "Fixed at 1,900,000 SR"
                elif achieved is True:
                    method = "10% improvement from 2025 avg"
                elif achieved is False:
                    method = "Kept same (2025 target not achieved)"
                else:
                    method = "TBD — no 2025 data"

                rows.append({
                    "Department":    dept,
                    "KPI":           k["name"],
                    "Unit":          unit,
                    "2025 Avg":      f"{avg_2025:.2f}" if avg_2025 is not None else "—",
                    "2025 Achieved": "✅" if achieved is True else ("❌" if achieved is False else "—"),
                    "2026 Target":   f"{target} {unit}" if target is not None else "TBD",
                    "Method":        method,
                })

            target_df = pd.DataFrame(rows)

            # Filter by department
            depts_list = ["All"] + sorted(target_df["Department"].unique().tolist())
            dept_filter = st.selectbox("Filter by department", depts_list, key="target_dept_filter")
            if dept_filter != "All":
                target_df = target_df[target_df["Department"] == dept_filter]

            st.dataframe(target_df, use_container_width=True, hide_index=True,
                column_config={
                    "2025 Achieved": st.column_config.TextColumn("2025 Achieved", width="small"),
                    "Method": st.column_config.TextColumn("Calculation Method", width="large"),
                })

            # Export targets table
            if st.button("⬇️ Export Targets to Excel", key="export_targets"):
                buf = BytesIO()
                with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                    target_df.to_excel(writer, index=False, sheet_name="2026 Targets")
                buf.seek(0)
                st.download_button(
                    "📥 Download Targets Excel",
                    data=buf,
                    file_name=f"2026_KPI_Targets_{date.today()}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="download_targets"
                )

    # ──────────────────────────────────────────────────────────
    # TAB 5 — MANAGE KPI DEFINITIONS
    # ──────────────────────────────────────────────────────────
    with tab_manage:
        if not can_write():
            st.info("View-only access.")
            return

        st.markdown("#### Add New KPI")
        depts = get_departments()
        dept_map3 = {d["name"]: d["id"] for d in depts}

        with st.form("add_kpi", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                kpi_name = st.text_input("KPI Name *")
                dept_sel = st.selectbox("Department *", list(dept_map3.keys()))
            with c2:
                target_val  = st.number_input("Target Value *", value=0.0)
                target_type = st.selectbox("Target Type", [">=","<=","="])
            with c3:
                unit    = st.text_input("Unit (e.g. %, days, count)")
                audit_t = st.selectbox("Audit", ["Both","ISO9001","BRCGS"])

            add = st.form_submit_button("➕ Add KPI")

        if add:
            if not kpi_name:
                st.error("KPI name required.")
            else:
                try:
                    sb.table("kpi_definitions").insert({
                        "name":          kpi_name,
                        "department_id": dept_map3[dept_sel],
                        "target_value":  target_val,
                        "target_type":   target_type,
                        "unit":          unit or None,
                        "audit_type":    audit_t,
                    }).execute()
                    st.success(f"✅ KPI '{kpi_name}' added.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

        st.markdown("#### Existing KPIs")
        all_kpis_m = sb.table("kpi_definitions").select("*,departments(name)").eq("is_active",True).order("department_id").execute()
        if all_kpis_m.data:
            rows = [{"KPI": k["name"],
                     "Department": (k.get("departments") or {}).get("name","—"),
                     "Target": f"{k['target_type']} {k['target_value']} {k.get('unit','') or ''}",
                     "Audit": k["audit_type"]}
                    for k in all_kpis_m.data]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
