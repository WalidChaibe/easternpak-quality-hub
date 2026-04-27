import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import date
from io import BytesIO
from utils.auth import require_auth, can_write, get_profile
from utils.supabase_client import get_supabase
from utils.helpers import get_departments

ANNUAL_COQ_TARGET = 1_900_000


def show():
    require_auth()
    sb = get_supabase()

    st.title("📊 KPI Tracking")
    st.caption("Department performance, Cost of Quality, trends and targets")

    tab_dash, tab_entry, tab_trends, tab_settings = st.tabs([
        "🏠 Dashboard",
        "📝 Entry",
        "📈 Trends",
        "⚙️ Settings",
    ])

    # ══════════════════════════════════════════════════════════
    # TAB 1 — DASHBOARD
    # ══════════════════════════════════════════════════════════
    with tab_dash:
        depts      = get_departments()
        dept_names = [d["name"] for d in depts]
        dept_map   = {d["name"]: d["id"] for d in depts}
        today      = date.today()
        year       = today.year

        # ── Year selector ─────────────────────────────────────
        col_yr, _ = st.columns([1, 3])
        with col_yr:
            dash_year = st.selectbox("Year", [2026, 2025], key="dash_year")

        start = f"{dash_year}-01-01"
        end   = f"{dash_year}-12-31"

        # ── Fetch all entries for the year ────────────────────
        entries_res = sb.table("v_kpi_full").select("*").gte("month", start).lte("month", end).execute()
        all_entries = pd.DataFrame(entries_res.data or [])

        # ── COST OF QUALITY SECTION ───────────────────────────
        st.markdown("### 💰 Cost of Quality")

        coq_def = sb.table("kpi_definitions").select("id").eq("name", "Cost of Quality").execute()
        coq_data = all_entries[all_entries["kpi_name"] == "Cost of Quality"] if not all_entries.empty else pd.DataFrame()

        if coq_data.empty:
            st.info("No Cost of Quality data for this year.")
        else:
            coq_data = coq_data.copy()
            coq_data["month"]        = pd.to_datetime(coq_data["month"])
            coq_data                 = coq_data.sort_values("month")
            coq_data["month_label"]  = coq_data["month"].dt.strftime("%b %Y")
            coq_data["actual_value"] = coq_data["actual_value"].astype(float)
            coq_data["cumulative"]   = coq_data["actual_value"].cumsum()

            total_spent = coq_data["actual_value"].sum()
            months_done = len(coq_data)
            monthly_avg = total_spent / months_done if months_done else 0
            projected   = monthly_avg * 12
            remaining   = ANNUAL_COQ_TARGET - total_spent
            on_track    = projected <= ANNUAL_COQ_TARGET
            pct         = (total_spent / ANNUAL_COQ_TARGET) * 100

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("YTD Spent",       f"{total_spent:,.0f} SR")
            c2.metric("Annual Target",   f"{ANNUAL_COQ_TARGET:,.0f} SR")
            c3.metric("Remaining",       f"{remaining:,.0f} SR",
                delta=f"{'Over' if remaining < 0 else 'Under'} budget",
                delta_color="inverse" if remaining < 0 else "normal")
            c4.metric("Projected Annual", f"{projected:,.0f} SR",
                delta="On track ✓" if on_track else "At risk ⚠️",
                delta_color="normal" if on_track else "inverse")

            if on_track:
                st.success(f"✅ On track — {pct:.1f}% of annual budget used across {months_done} months.")
            elif projected > ANNUAL_COQ_TARGET * 1.1:
                st.error(f"🔴 At risk — Projected to exceed target by {projected - ANNUAL_COQ_TARGET:,.0f} SR")
            else:
                st.warning(f"🟡 Borderline — Projected {projected:,.0f} SR vs target {ANNUAL_COQ_TARGET:,.0f} SR")

            fig_coq = go.Figure()
            fig_coq.add_bar(x=coq_data["month_label"], y=coq_data["actual_value"],
                name="Monthly COQ", marker_color="#4a90d9", opacity=0.75)
            fig_coq.add_scatter(x=coq_data["month_label"], y=coq_data["cumulative"],
                name="Cumulative", mode="lines+markers",
                line=dict(color="#e85d04", width=2.5), marker=dict(size=6))
            fig_coq.add_hline(y=ANNUAL_COQ_TARGET, line_dash="dash", line_color="red",
                annotation_text=f"Annual Target: {ANNUAL_COQ_TARGET:,.0f} SR",
                annotation_position="top right")
            fig_coq.update_layout(
                xaxis_title="Month", yaxis_title="SR",
                plot_bgcolor="white", height=320,
                legend=dict(orientation="h", y=-0.25),
                margin=dict(t=20, b=10, l=10, r=10)
            )
            st.plotly_chart(fig_coq, use_container_width=True)

        st.markdown("---")

        # ── DEPARTMENT OVERVIEW ───────────────────────────────
        st.markdown("### 📋 Department Overview")

        if all_entries.empty:
            st.info("No KPI data for this year.")
        else:
            all_entries["month"] = pd.to_datetime(all_entries["month"])

            # Summary per department
            dept_summary = all_entries.groupby("department_name").agg(
                total_kpis   =("kpi_name",     "nunique"),
                months_logged=("month",         "nunique"),
                achieved_pct =("achieved",      lambda x: round(x.mean() * 100, 1) if len(x) > 0 else 0),
            ).reset_index()

            # Overdue entries — departments with no entry this month
            current_month = today.replace(day=1).isoformat()
            logged_this_month = set(
                all_entries[all_entries["month"] == pd.Timestamp(current_month)]["department_name"].unique()
            )
            overdue_depts = [d for d in dept_names if d not in logged_this_month]

            if overdue_depts:
                st.warning(f"⚠️ No entry for **{today.strftime('%B %Y')}** yet: {', '.join(overdue_depts)}")

            # Department cards
            cols = st.columns(3)
            for i, row in dept_summary.iterrows():
                with cols[i % 3]:
                    color = "🟢" if row["achieved_pct"] >= 80 else ("🟡" if row["achieved_pct"] >= 60 else "🔴")
                    st.markdown(f"""
<div style="background:#f8f9fa;border-radius:8px;padding:14px;margin-bottom:10px;border-left:4px solid {'#4CAF50' if row['achieved_pct']>=80 else ('#FFC107' if row['achieved_pct']>=60 else '#F44336')}">
<b>{row['department_name']}</b><br>
{color} Achievement: <b>{row['achieved_pct']}%</b><br>
📊 KPIs tracked: {row['total_kpis']}<br>
📅 Months logged: {row['months_logged']}
</div>
""", unsafe_allow_html=True)

            st.markdown("---")

            # Missed targets this year
            st.markdown("### 🔴 Missed Targets This Year")
            missed = all_entries[all_entries["achieved"] == False][
                ["department_name", "kpi_name", "month", "actual_value", "kpi_unit",
                 "target_value", "root_cause", "corrective_action"]
            ].copy()
            missed["month"] = missed["month"].dt.strftime("%b %Y")
            missed = missed.rename(columns={
                "department_name":  "Department",
                "kpi_name":         "KPI",
                "month":            "Month",
                "actual_value":     "Actual",
                "kpi_unit":         "Unit",
                "target_value":     "Target",
                "root_cause":       "Root Cause",
                "corrective_action":"Corrective Action",
            })

            if missed.empty:
                st.success("✅ No missed targets this year!")
            else:
                dept_filter_dash = st.selectbox("Filter by department",
                    ["All"] + sorted(missed["Department"].unique().tolist()), key="dash_dept_filter")
                if dept_filter_dash != "All":
                    missed = missed[missed["Department"] == dept_filter_dash]
                st.dataframe(missed, use_container_width=True, hide_index=True)

    # ══════════════════════════════════════════════════════════
    # TAB 2 — ENTRY (table view, all KPIs for a dept + month)
    # ══════════════════════════════════════════════════════════
    with tab_entry:
        depts    = get_departments()
        dept_map = {d["name"]: d["id"] for d in depts}

        c1, c2 = st.columns(2)
        with c1:
            entry_dept = st.selectbox("Department", list(dept_map.keys()), key="entry_dept")
        with c2:
            today_e      = date.today()
            months       = pd.date_range(end=today_e, periods=24, freq="MS").to_list()
            month_labels = [m.strftime("%B %Y") for m in months]
            entry_month_label = st.selectbox("Month", month_labels[::-1], key="entry_month")
            entry_month = pd.to_datetime(entry_month_label, format="%B %Y").date()

        dept_id  = dept_map[entry_dept]
        kpis_res = sb.table("kpi_definitions").select("*")\
            .eq("department_id", dept_id).eq("is_active", True).execute()
        kpis = kpis_res.data or []

        if not kpis:
            st.info(f"No KPIs defined for {entry_dept} yet. Add them in Settings.")
        else:
            kpi_ids = [k["id"] for k in kpis]

            # Missing months indicator
            from collections import defaultdict
            today_check = date.today()
            last_month  = (pd.Timestamp(today_check) - pd.DateOffset(months=1)).date().replace(day=1)
            all_months  = pd.date_range(start="2025-01-01", end=last_month, freq="MS").tolist()

            all_entries_res  = sb.table("kpi_entries").select("kpi_id, month")                .in_("kpi_id", kpi_ids).gte("month", "2025-01-01").execute()
            entries_by_month = defaultdict(set)
            for e in (all_entries_res.data or []):
                entries_by_month[e["month"][:7]].add(e["kpi_id"])

            missing_months = []
            for m in all_months:
                month_key    = m.strftime("%Y-%m")
                missing_kpis = [k["name"] for k in kpis if k["id"] not in entries_by_month.get(month_key, set())]
                if missing_kpis:
                    missing_months.append((m.strftime("%B %Y"), missing_kpis))

            if missing_months:
                with st.expander(f"\u26a0\ufe0f {len(missing_months)} month(s) with missing entries", expanded=True):
                    for month_lbl, missing_kpi_names in missing_months:
                        st.markdown(f"**{month_lbl}** \u2014 Missing: {', '.join(missing_kpi_names)}")
                    st.caption("Select the month from the dropdown above and fill in the missing values.")

            # Fetch existing entries for selected month
            existing_res = sb.table("kpi_entries").select("*")                .in_("kpi_id", kpi_ids)                .eq("month", entry_month.isoformat()).execute()
            existing_map = {e["kpi_id"]: e for e in (existing_res.data or [])}

            st.markdown(f"#### {entry_dept} \u2014 {entry_month_label}")
            st.caption("Fill actuals for all KPIs below and click Save All at the bottom.")

            # Build table header
            header_cols = st.columns([2.5, 1.2, 1, 0.8, 1.2, 2, 2])
            header_cols[0].markdown("**KPI**")
            header_cols[1].markdown("**Target**")
            header_cols[2].markdown("**Actual**")
            header_cols[3].markdown("**Achieved**")
            header_cols[4].markdown("**Unit**")
            header_cols[5].markdown("**Root Cause**")
            header_cols[6].markdown("**Corrective Action**")
            st.markdown("---")

            # Collect all inputs outside a form so we can do conditional rendering
            entry_data = {}
            for kpi in kpis:
                kpi_id = kpi["id"]
                rec    = existing_map.get(kpi_id)
                t      = kpi.get("target_type", ">=")
                tv     = float(kpi.get("target_value") or 0)
                unit   = kpi.get("unit", "") or ""
                target_str = f"{t} {tv} {unit}"

                row_cols = st.columns([2.5, 1.2, 1, 0.8, 1.2, 2, 2])

                with row_cols[0]:
                    st.markdown(f"**{kpi['name']}**")
                with row_cols[1]:
                    st.markdown(target_str)
                with row_cols[2]:
                    actual = st.number_input("",
                        value=float(rec["actual_value"]) if rec and rec.get("actual_value") is not None else 0.0,
                        key=f"act_{kpi_id}", label_visibility="collapsed")
                with row_cols[3]:
                    if t == ">=":   auto_ach = actual >= tv
                    elif t == "<=": auto_ach = actual <= tv
                    else:           auto_ach = actual == tv
                    achieved = st.checkbox("",
                        value=rec["achieved"] if rec else auto_ach,
                        key=f"ach_{kpi_id}", label_visibility="collapsed")
                with row_cols[4]:
                    st.markdown(unit)
                with row_cols[5]:
                    rc = st.text_input("",
                        value=rec.get("root_cause", "") if rec else "",
                        key=f"rc_{kpi_id}", label_visibility="collapsed",
                        placeholder="Root cause" if not achieved else "—",
                        disabled=achieved)
                with row_cols[6]:
                    ca = st.text_input("",
                        value=rec.get("corrective_action", "") if rec else "",
                        key=f"ca_{kpi_id}", label_visibility="collapsed",
                        placeholder="Corrective action" if not achieved else "—",
                        disabled=achieved)

                entry_data[kpi_id] = {
                    "actual": actual, "achieved": achieved,
                    "rc": rc, "ca": ca, "rec": rec
                }

            st.markdown("---")

            if can_write():
                if st.button("💾 Save All", use_container_width=True, key="save_all_entry"):
                    errors   = []
                    profile  = get_profile()
                    saved    = 0

                    for kpi_id, vals in entry_data.items():
                        if not vals["achieved"] and not (vals["rc"].strip() and vals["ca"].strip()):
                            kpi_name = next(k["name"] for k in kpis if k["id"] == kpi_id)
                            errors.append(f"**{kpi_name}** — root cause and corrective action required when not achieved.")
                            continue

                        payload = {
                            "kpi_id":            kpi_id,
                            "month":             entry_month.isoformat(),
                            "actual_value":      vals["actual"],
                            "achieved":          vals["achieved"],
                            "root_cause":        vals["rc"] or None,
                            "corrective_action": vals["ca"] or None,
                            "entered_by":        profile["id"],
                        }
                        try:
                            if vals["rec"]:
                                sb.table("kpi_entries").update(payload)\
                                    .eq("kpi_id", kpi_id)\
                                    .eq("month", entry_month.isoformat()).execute()
                            else:
                                sb.table("kpi_entries").insert(payload).execute()
                            saved += 1
                        except Exception as e:
                            errors.append(f"Error saving {kpi_id}: {e}")

                    if errors:
                        for err in errors:
                            st.error(err)
                    if saved:
                        st.success(f"✅ Saved {saved} KPI(s) for {entry_month_label}.")
                        st.rerun()
            else:
                st.info("View-only access.")

    # ══════════════════════════════════════════════════════════
    # TAB 3 — TRENDS & EXPORT
    # ══════════════════════════════════════════════════════════
    with tab_trends:
        depts     = get_departments()
        dept_map3 = {d["name"]: d["id"] for d in depts}

        c1, c2 = st.columns(2)
        with c1:
            dept_t = st.selectbox("Department", list(dept_map3.keys()), key="trend_dept")
        with c2:
            months_back = st.selectbox("Period", [6, 12, 24],
                format_func=lambda x: f"Last {x} months", key="trend_period")

        cutoff = (pd.Timestamp.now() - pd.DateOffset(months=months_back)).date().replace(day=1).isoformat()

        res  = sb.table("v_kpi_full").select("*")\
            .eq("department_name", dept_t).gte("month", cutoff).order("month").execute()
        data = res.data or []

        if not data:
            st.info("No data for this period.")
        else:
            df = pd.DataFrame(data)
            df["month"]       = pd.to_datetime(df["month"])
            df["month_label"] = df["month"].dt.strftime("%b %Y")

            kpi_names     = df["kpi_name"].unique().tolist()
            selected_kpis = st.multiselect("KPIs to display", kpi_names,
                default=kpi_names[:4], key="trend_kpis")

            for kpi_name in selected_kpis:
                kdf    = df[df["kpi_name"] == kpi_name].copy()
                target = kdf["target_value"].iloc[0]
                unit   = kdf["kpi_unit"].iloc[0] or ""

                fig = px.line(kdf, x="month_label", y="actual_value",
                    title=kpi_name, markers=True,
                    color_discrete_sequence=["#1a73e8"])
                if target:
                    fig.add_hline(y=float(target), line_dash="dash", line_color="red",
                        annotation_text=f"Target: {target} {unit}",
                        annotation_position="bottom right")
                for _, row in kdf[kdf["achieved"] == False].iterrows():
                    fig.add_vrect(x0=row["month_label"], x1=row["month_label"],
                        fillcolor="red", opacity=0.08, line_width=0)
                fig.update_layout(
                    xaxis_title="Month", yaxis_title=unit,
                    plot_bgcolor="white", height=280,
                    margin=dict(t=40, b=20, l=20, r=20)
                )
                st.plotly_chart(fig, use_container_width=True)

                missed = kdf[kdf["achieved"] == False][
                    ["month_label", "actual_value", "root_cause", "corrective_action"]]
                if not missed.empty:
                    with st.expander(f"📋 Missed months — {kpi_name}"):
                        for _, row in missed.iterrows():
                            st.markdown(f"**{row['month_label']}** — Actual: {row['actual_value']} {unit}")
                            st.markdown(f"- Root Cause: {row['root_cause'] or '—'}")
                            st.markdown(f"- Corrective Action: {row['corrective_action'] or '—'}")

            # Export
            st.markdown("---")
            export_df = df[df["kpi_name"].isin(selected_kpis)][
                ["month_label","kpi_name","kpi_unit","target_value",
                 "actual_value","achieved","root_cause","corrective_action","entered_by_name"]
            ].copy()
            export_df.columns = ["Month","KPI","Unit","Target","Actual",
                                  "Achieved","Root Cause","Corrective Action","Entered By"]
            buf = BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                export_df.to_excel(writer, index=False, sheet_name="KPI Trends")
            buf.seek(0)
            st.download_button("📥 Export to Excel", data=buf,
                file_name=f"KPI_{dept_t}_{date.today()}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_trends")

    # ══════════════════════════════════════════════════════════
    # TAB 4 — SETTINGS (Manage KPIs + 2026 Targets)
    # ══════════════════════════════════════════════════════════
    with tab_settings:
        if not can_write():
            st.info("View-only access.")
        else:
            st.markdown("### ⚙️ Manage KPI Definitions")

            sub_tab_add, sub_tab_targets, sub_tab_list = st.tabs([
                "➕ Add KPI", "🎯 2026 Targets", "📋 All KPIs"
            ])

            with sub_tab_add:
                depts    = get_departments()
                dept_map4 = {d["name"]: d["id"] for d in depts}

                with st.form("add_kpi", clear_on_submit=True):
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        kpi_name = st.text_input("KPI Name *")
                        dept_sel = st.selectbox("Department *", list(dept_map4.keys()))
                    with c2:
                        target_val  = st.number_input("Target Value *", value=0.0)
                        target_type = st.selectbox("Target Type", [">=", "<=", "="])
                    with c3:
                        unit    = st.text_input("Unit (e.g. %, days, count)")
                        audit_t = st.selectbox("Audit", ["Both", "ISO9001", "BRCGS"])

                    add = st.form_submit_button("➕ Add KPI")

                if add:
                    if not kpi_name:
                        st.error("KPI name required.")
                    else:
                        try:
                            sb.table("kpi_definitions").insert({
                                "name":          kpi_name,
                                "department_id": dept_map4[dept_sel],
                                "target_value":  target_val,
                                "target_type":   target_type,
                                "unit":          unit or None,
                                "audit_type":    audit_t,
                            }).execute()
                            st.success(f"✅ KPI '{kpi_name}' added.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error: {e}")

            with sub_tab_targets:
                st.markdown("#### 2026 Target Reference")
                st.caption("How each 2026 target was calculated from 2025 performance")

                all_kpis = sb.table("kpi_definitions").select("*,departments(name)")\
                    .eq("is_active", True).order("department_id").execute()

                if not all_kpis.data:
                    st.info("No KPIs found.")
                else:
                    avg_res = sb.table("kpi_entries").select("kpi_id, actual_value")\
                        .gte("month", "2025-01-01").lte("month", "2025-12-31").execute()
                    avg_map = {}
                    if avg_res.data:
                        avg_df  = pd.DataFrame(avg_res.data)
                        avg_map = avg_df.groupby("kpi_id")["actual_value"].mean().to_dict()

                    rows = []
                    for k in all_kpis.data:
                        dept     = (k.get("departments") or {}).get("name", "—")
                        avg_2025 = avg_map.get(k["id"])
                        target   = k.get("target_value")
                        ttype    = k.get("target_type", ">=")
                        unit     = k.get("unit", "") or ""

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

                    tdf = pd.DataFrame(rows)
                    dept_f = st.selectbox("Filter by department",
                        ["All"] + sorted(tdf["Department"].unique().tolist()), key="target_dept_f")
                    if dept_f != "All":
                        tdf = tdf[tdf["Department"] == dept_f]

                    st.dataframe(tdf, use_container_width=True, hide_index=True)

                    buf = BytesIO()
                    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                        tdf.to_excel(writer, index=False, sheet_name="2026 Targets")
                    buf.seek(0)
                    st.download_button("📥 Export Targets", data=buf,
                        file_name=f"2026_Targets_{date.today()}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="dl_targets")

            with sub_tab_list:
                all_kpis_m = sb.table("kpi_definitions").select("*,departments(name)")\
                    .eq("is_active", True).order("department_id").execute()
                if all_kpis_m.data:
                    rows_m = [{
                        "KPI":        k["name"],
                        "Department": (k.get("departments") or {}).get("name", "—"),
                        "Target":     f"{k['target_type']} {k['target_value']} {k.get('unit','') or ''}",
                        "Audit":      k["audit_type"],
                    } for k in all_kpis_m.data]
                    st.dataframe(pd.DataFrame(rows_m), use_container_width=True, hide_index=True)
