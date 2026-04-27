import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import date
from io import BytesIO
from collections import defaultdict
from utils.auth import require_auth, can_write, get_profile
from utils.supabase_client import get_supabase
from utils.helpers import get_departments

ANNUAL_COQ_TARGET = 1_900_000
QUARTER_MONTHS = {1: "Q1", 4: "Q2", 7: "Q3", 10: "Q4"}


def get_quarter_start(m):
    return ((m - 1) // 3) * 3 + 1


def get_target_for_month(sb, kpi_id, year, month, period_type):
    if period_type == "Quarterly":
        q_start = get_quarter_start(month)
        res = sb.table("kpi_targets").select("target_value")\
            .eq("kpi_id", kpi_id).eq("year", year).eq("month", q_start).execute()
    elif period_type == "Annual":
        res = sb.table("kpi_targets").select("target_value")\
            .eq("kpi_id", kpi_id).eq("year", year).is_("month", "null").execute()
    else:
        res = sb.table("kpi_targets").select("target_value")\
            .eq("kpi_id", kpi_id).eq("year", year).eq("month", month).execute()
    return float(res.data[0]["target_value"]) if res.data else None


def check_achieved(actual, target, target_type):
    if target_type == ">=":   return actual >= target
    elif target_type == "<=": return actual <= target
    else:                     return abs(actual - target) < 0.01


def show():
    require_auth()
    sb = get_supabase()

    st.title("📊 KPI Tracking")
    st.caption("Department performance · Cost of Quality · Trends · Targets")

    tab_dash, tab_entry, tab_trends, tab_settings = st.tabs([
        "🏠 Dashboard", "📝 Entry", "📈 Trends", "⚙️ Settings",
    ])

    # ══════════════════════════════════════════════════════════
    # TAB 1 — DASHBOARD
    # ══════════════════════════════════════════════════════════
    with tab_dash:
        depts    = get_departments()
        dept_map = {d["name"]: d["id"] for d in depts}
        today    = date.today()

        col_yr, _ = st.columns([1, 3])
        with col_yr:
            dash_year = st.selectbox("Year", [2026, 2025], key="dash_year")

        start = f"{dash_year}-01-01"
        end   = f"{dash_year}-12-31"

        entries_res = sb.table("v_kpi_full").select("*").gte("month", start).lte("month", end).execute()
        all_entries = pd.DataFrame(entries_res.data or [])

        # COQ Section
        st.markdown("### 💰 Cost of Quality")
        coq_data = all_entries[all_entries["kpi_name"] == "Cost of Quality"].copy() if not all_entries.empty else pd.DataFrame()

        if coq_data.empty:
            st.info("No Cost of Quality data for this year.")
        else:
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
            prorated    = ANNUAL_COQ_TARGET * (today.month / 12) if dash_year == today.year else ANNUAL_COQ_TARGET

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("YTD Spent",        f"{total_spent:,.0f} SR")
            c2.metric("Annual Target",    f"{ANNUAL_COQ_TARGET:,.0f} SR")
            c3.metric("Prorated Target",  f"{prorated:,.0f} SR",
                delta=f"{'Over' if total_spent > prorated else 'Under'} prorated",
                delta_color="inverse" if total_spent > prorated else "normal")
            c4.metric("Projected Annual", f"{projected:,.0f} SR",
                delta="On track" if on_track else "At risk",
                delta_color="normal" if on_track else "inverse")

            if on_track:
                st.success(f"On track — {pct:.1f}% of annual budget used across {months_done} months.")
            elif projected > ANNUAL_COQ_TARGET * 1.1:
                st.error(f"At risk — Projected to exceed target by {projected - ANNUAL_COQ_TARGET:,.0f} SR")
            else:
                st.warning(f"Borderline — Projected {projected:,.0f} SR vs target {ANNUAL_COQ_TARGET:,.0f} SR")

            fig_coq = go.Figure()
            fig_coq.add_bar(x=coq_data["month_label"], y=coq_data["actual_value"],
                name="Monthly COQ", marker_color="#4a90d9", opacity=0.75)
            fig_coq.add_scatter(x=coq_data["month_label"], y=coq_data["cumulative"],
                name="Cumulative", mode="lines+markers",
                line=dict(color="#e85d04", width=2.5), marker=dict(size=6))
            fig_coq.add_hline(y=ANNUAL_COQ_TARGET, line_dash="dash", line_color="red",
                annotation_text=f"Annual Target: {ANNUAL_COQ_TARGET:,.0f} SR",
                annotation_position="top right")
            fig_coq.add_hline(y=prorated, line_dash="dot", line_color="orange",
                annotation_text=f"Prorated: {prorated:,.0f} SR",
                annotation_position="bottom right")
            fig_coq.update_layout(xaxis_title="Month", yaxis_title="SR",
                plot_bgcolor="white", height=320,
                legend=dict(orientation="h", y=-0.25),
                margin=dict(t=20, b=10, l=10, r=10))
            st.plotly_chart(fig_coq, use_container_width=True)

        st.markdown("---")
        st.markdown("### 📋 Department Overview")

        if all_entries.empty:
            st.info("No KPI data for this year.")
        else:
            all_entries["month"] = pd.to_datetime(all_entries["month"])
            dept_summary = all_entries.groupby("department_name").agg(
                total_kpis   =("kpi_name", "nunique"),
                months_logged=("month",    "nunique"),
                achieved_pct =("achieved", lambda x: round(x.mean() * 100, 1)),
            ).reset_index()

            current_month     = today.replace(day=1).isoformat()
            logged_this_month = set(all_entries[all_entries["month"] == pd.Timestamp(current_month)]["department_name"].unique())
            dept_names        = [d["name"] for d in depts]
            overdue_depts     = [d for d in dept_names if d not in logged_this_month]
            if overdue_depts:
                st.warning(f"No entry for {today.strftime('%B %Y')} yet: {', '.join(overdue_depts)}")

            cols = st.columns(3)
            for i, row in dept_summary.iterrows():
                color_hex = "#4CAF50" if row["achieved_pct"] >= 80 else ("#FFC107" if row["achieved_pct"] >= 60 else "#F44336")
                emoji     = "🟢" if row["achieved_pct"] >= 80 else ("🟡" if row["achieved_pct"] >= 60 else "🔴")
                with cols[i % 3]:
                    st.markdown(f"""<div style="background:#f8f9fa;border-radius:8px;padding:14px;margin-bottom:10px;border-left:4px solid {color_hex}">
<b>{row['department_name']}</b><br>{emoji} Achievement: <b>{row['achieved_pct']}%</b><br>
KPIs tracked: {row['total_kpis']} | Months logged: {row['months_logged']}</div>""", unsafe_allow_html=True)

            st.markdown("---")
            st.markdown("### 🔴 Missed Targets This Year")
            missed = all_entries[all_entries["achieved"] == False][[
                "department_name","kpi_name","month","actual_value",
                "kpi_unit","target_value","root_cause","corrective_action"]].copy()
            missed["month"] = missed["month"].dt.strftime("%b %Y")
            missed.columns  = ["Department","KPI","Period","Actual","Unit","Target","Root Cause","Corrective Action"]
            if missed.empty:
                st.success("No missed targets this year!")
            else:
                dept_f = st.selectbox("Filter", ["All"] + sorted(missed["Department"].unique().tolist()), key="dash_dept_f")
                if dept_f != "All":
                    missed = missed[missed["Department"] == dept_f]
                st.dataframe(missed, use_container_width=True, hide_index=True)

    # ══════════════════════════════════════════════════════════
    # TAB 2 — ENTRY
    # ══════════════════════════════════════════════════════════
    with tab_entry:
        depts    = get_departments()
        dept_map = {d["name"]: d["id"] for d in depts}
        today_e  = date.today()

        c1, c2 = st.columns(2)
        with c1:
            entry_dept = st.selectbox("Department", list(dept_map.keys()), key="entry_dept")
        with c2:
            months            = pd.date_range(end=today_e, periods=36, freq="MS").to_list()
            month_labels      = [m.strftime("%B %Y") for m in months]
            entry_month_label = st.selectbox("Month", month_labels[::-1], key="entry_month")
            entry_month       = pd.to_datetime(entry_month_label, format="%B %Y").date()

        dept_id  = dept_map[entry_dept]
        kpis_res = sb.table("kpi_definitions").select("*")\
            .eq("department_id", dept_id).eq("is_active", True).execute()
        kpis = kpis_res.data or []

        if not kpis:
            st.info(f"No KPIs defined for {entry_dept} yet. Add them in Settings.")
        else:
            kpi_ids     = [k["id"] for k in kpis]
            period_types = list({k.get("period_type", "Monthly") for k in kpis})
            dept_period  = period_types[0] if len(period_types) == 1 else "Monthly"
            last_month   = (pd.Timestamp(today_e) - pd.DateOffset(months=1)).date().replace(day=1)

            # Missing periods check
            all_entries_res   = sb.table("kpi_entries").select("kpi_id, month")\
                .in_("kpi_id", kpi_ids).gte("month", "2025-01-01").execute()
            entries_by_period = defaultdict(set)
            for e in (all_entries_res.data or []):
                entries_by_period[e["month"][:7]].add(e["kpi_id"])

            missing_periods = []
            if dept_period == "Quarterly":
                for yr in [2025, 2026]:
                    for qm in [1, 4, 7, 10]:
                        qdate = date(yr, qm, 1)
                        if qdate <= last_month:
                            month_key    = qdate.strftime("%Y-%m")
                            missing_kpis = [k["name"] for k in kpis if k["id"] not in entries_by_period.get(month_key, set())]
                            if missing_kpis:
                                missing_periods.append((f"Q{(qm-1)//3+1} {yr}", missing_kpis))
            else:
                for m in pd.date_range(start="2025-01-01", end=last_month, freq="MS"):
                    month_key    = m.strftime("%Y-%m")
                    missing_kpis = [k["name"] for k in kpis if k["id"] not in entries_by_period.get(month_key, set())]
                    if missing_kpis:
                        missing_periods.append((m.strftime("%B %Y"), missing_kpis))

            if missing_periods:
                with st.expander(f"⚠️ {len(missing_periods)} period(s) with missing entries", expanded=True):
                    for lbl, names in missing_periods:
                        st.markdown(f"**{lbl}** — Missing: {', '.join(names)}")
                    st.caption("Select the period from the dropdown above and fill in the missing values.")

            # Fetch existing entries
            existing_res = sb.table("kpi_entries").select("*")\
                .in_("kpi_id", kpi_ids).eq("month", entry_month.isoformat()).execute()
            existing_map = {e["kpi_id"]: e for e in (existing_res.data or [])}

            if dept_period == "Quarterly":
                q_num          = (entry_month.month - 1) // 3 + 1
                period_display = f"Q{q_num} {entry_month.year}"
            else:
                period_display = entry_month_label

            st.markdown(f"#### {entry_dept} — {period_display}")
            st.caption("Enter actuals. System auto-calculates if target is achieved.")

            has_oee = any(k.get("aggregation_type") == "weighted_avg" for k in kpis)

            if has_oee:
                hcols = st.columns([2.2, 1.2, 1, 1, 0.8, 1.8, 1.8])
                labels = ["**KPI**","**Target**","**Actual**","**MT Produced**","**Unit**","**Root Cause**","**Corrective Action**"]
            else:
                hcols = st.columns([2.5, 1.3, 1, 0.8, 2, 2])
                labels = ["**KPI**","**Target**","**Actual**","**Unit**","**Root Cause**","**Corrective Action**"]
            for col, lbl in zip(hcols, labels):
                col.markdown(lbl)
            st.markdown("---")

            entry_data = {}
            for kpi in kpis:
                kpi_id      = kpi["id"]
                rec         = existing_map.get(kpi_id)
                unit        = kpi.get("unit", "") or ""
                period_type = kpi.get("period_type", "Monthly")
                agg_type    = kpi.get("aggregation_type", "monthly")
                ttype       = kpi.get("target_type", ">=")
                target      = get_target_for_month(sb, kpi_id, entry_month.year, entry_month.month, period_type)
                target_str  = f"{ttype} {target} {unit}" if target is not None else "No target"

                if has_oee:
                    rcols = st.columns([2.2, 1.2, 1, 1, 0.8, 1.8, 1.8])
                else:
                    rcols = st.columns([2.5, 1.3, 1, 0.8, 2, 2])

                with rcols[0]: st.markdown(f"**{kpi['name']}**")
                with rcols[1]: st.markdown(target_str)
                with rcols[2]:
                    actual = st.number_input("", value=float(rec["actual_value"]) if rec and rec.get("actual_value") is not None else 0.0,
                        key=f"act_{kpi_id}", label_visibility="collapsed")

                mt_produced = None
                if has_oee:
                    with rcols[3]:
                        if agg_type == "weighted_avg":
                            mt_produced = st.number_input("",
                                value=float(rec["mt_produced"]) if rec and rec.get("mt_produced") is not None else 0.0,
                                key=f"mt_{kpi_id}", label_visibility="collapsed")
                        else:
                            st.markdown("—")

                achieved   = check_achieved(actual, target, ttype) if target is not None else True
                ach_icon   = "✅" if achieved else "❌"
                unit_idx   = 3 if not has_oee else 4
                rc_idx     = 4 if not has_oee else 5
                ca_idx     = 5 if not has_oee else 6

                with rcols[unit_idx]: st.markdown(unit)
                with rcols[rc_idx]:
                    rc = st.text_input("", value=rec.get("root_cause","") if rec else "",
                        key=f"rc_{kpi_id}", label_visibility="collapsed",
                        placeholder="Root cause required" if not achieved else f"{ach_icon} On target",
                        disabled=achieved)
                with rcols[ca_idx]:
                    ca = st.text_input("", value=rec.get("corrective_action","") if rec else "",
                        key=f"ca_{kpi_id}", label_visibility="collapsed",
                        placeholder="Corrective action required" if not achieved else "",
                        disabled=achieved)

                entry_data[kpi_id] = {"actual": actual, "achieved": achieved,
                    "rc": rc, "ca": ca, "rec": rec, "mt_produced": mt_produced}

            st.markdown("---")
            if can_write():
                if st.button("💾 Save All", use_container_width=True, key="save_all"):
                    errors  = []
                    profile = get_profile()
                    saved   = 0
                    for kpi_id, vals in entry_data.items():
                        if not vals["achieved"] and not (vals["rc"].strip() and vals["ca"].strip()):
                            name = next(k["name"] for k in kpis if k["id"] == kpi_id)
                            errors.append(f"**{name}** — target not achieved, root cause and corrective action required.")
                            continue
                        payload = {
                            "kpi_id": kpi_id, "month": entry_month.isoformat(),
                            "actual_value": vals["actual"], "achieved": vals["achieved"],
                            "root_cause": vals["rc"] or None,
                            "corrective_action": vals["ca"] or None,
                            "entered_by": profile["id"],
                        }
                        if vals["mt_produced"] is not None:
                            payload["mt_produced"] = vals["mt_produced"]
                        try:
                            if vals["rec"]:
                                sb.table("kpi_entries").update(payload)\
                                    .eq("kpi_id", kpi_id).eq("month", entry_month.isoformat()).execute()
                            else:
                                sb.table("kpi_entries").insert(payload).execute()
                            saved += 1
                        except Exception as e:
                            errors.append(f"Error: {e}")
                    for err in errors:
                        st.error(err)
                    if saved:
                        st.success(f"✅ Saved {saved} KPI(s) for {period_display}.")
                        st.rerun()
            else:
                st.info("View-only access.")

    # ══════════════════════════════════════════════════════════
    # TAB 3 — TRENDS
    # ══════════════════════════════════════════════════════════
    with tab_trends:
        depts     = get_departments()
        dept_map3 = {d["name"]: d["id"] for d in depts}

        c1, c2, c3 = st.columns(3)
        with c1:
            dept_t = st.selectbox("Department", list(dept_map3.keys()), key="trend_dept")
        with c2:
            date_options     = [m.strftime("%B %Y") for m in pd.date_range("2025-01-01", date.today(), freq="MS")]
            from_month_label = st.selectbox("From", date_options, index=0, key="trend_from")
        with c3:
            to_month_label = st.selectbox("To", date_options[::-1], index=0, key="trend_to")

        from_date = pd.to_datetime(from_month_label, format="%B %Y").date()
        to_date   = pd.to_datetime(to_month_label,   format="%B %Y").date()

        if from_date > to_date:
            st.error("'From' must be before 'To'.")
        else:
            res  = sb.table("v_kpi_full").select("*")\
                .eq("department_name", dept_t)\
                .gte("month", from_date.isoformat())\
                .lte("month", to_date.replace(day=28).isoformat())\
                .order("month").execute()
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

                    kdef   = sb.table("kpi_definitions").select("aggregation_type","target_type")\
                        .eq("name", kpi_name).execute()
                    agg    = kdef.data[0]["aggregation_type"] if kdef.data else "monthly"

                    if agg == "weighted_avg" and "mt_produced" in kdf.columns:
                        kdf["mt_produced"] = pd.to_numeric(kdf.get("mt_produced", 0), errors="coerce").fillna(0)
                        total_mt = kdf["mt_produced"].sum()
                        ytd_avg  = (kdf["actual_value"] * kdf["mt_produced"]).sum() / total_mt if total_mt > 0 else kdf["actual_value"].mean()
                        subtitle = f"YTD Weighted Avg: {ytd_avg:.2f} {unit}"
                    elif agg == "cumulative":
                        subtitle = f"YTD Total: {kdf['actual_value'].sum():,.0f} {unit}"
                    elif agg == "sum":
                        subtitle = f"YTD Sum: {kdf['actual_value'].sum():,.2f} {unit}"
                    else:
                        subtitle = None

                    fig = px.line(kdf, x="month_label", y="actual_value",
                        title=f"{kpi_name}" + (f" — {subtitle}" if subtitle else ""),
                        markers=True, color_discrete_sequence=["#1a73e8"])
                    if target:
                        fig.add_hline(y=float(target), line_dash="dash", line_color="red",
                            annotation_text=f"Target: {target} {unit}",
                            annotation_position="bottom right")
                    for _, row in kdf[kdf["achieved"] == False].iterrows():
                        fig.add_vrect(x0=row["month_label"], x1=row["month_label"],
                            fillcolor="red", opacity=0.08, line_width=0)
                    fig.update_layout(xaxis_title="Period", yaxis_title=unit,
                        plot_bgcolor="white", height=280,
                        margin=dict(t=40, b=20, l=20, r=20))
                    st.plotly_chart(fig, use_container_width=True)

                    missed = kdf[kdf["achieved"] == False][["month_label","actual_value","root_cause","corrective_action"]]
                    if not missed.empty:
                        with st.expander(f"📋 Missed — {kpi_name}"):
                            for _, row in missed.iterrows():
                                st.markdown(f"**{row['month_label']}** — Actual: {row['actual_value']} {unit}")
                                st.markdown(f"- Root Cause: {row['root_cause'] or '—'}")
                                st.markdown(f"- Corrective Action: {row['corrective_action'] or '—'}")

                st.markdown("---")
                exp_df = df[df["kpi_name"].isin(selected_kpis)][
                    ["month_label","kpi_name","kpi_unit","target_value",
                     "actual_value","achieved","root_cause","corrective_action","entered_by_name"]].copy()
                exp_df.columns = ["Period","KPI","Unit","Target","Actual","Achieved","Root Cause","Corrective Action","Entered By"]
                buf = BytesIO()
                with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                    exp_df.to_excel(writer, index=False, sheet_name="KPI Trends")
                buf.seek(0)
                st.download_button("📥 Export to Excel", data=buf,
                    file_name=f"KPI_{dept_t}_{date.today()}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="dl_trends")

    # ══════════════════════════════════════════════════════════
    # TAB 4 — SETTINGS
    # ══════════════════════════════════════════════════════════
    with tab_settings:
        if not can_write():
            st.info("View-only access.")
        else:
            sub_add, sub_targets, sub_list = st.tabs(["➕ Add KPI","🎯 Targets","📋 All KPIs"])

            with sub_add:
                depts     = get_departments()
                dept_map4 = {d["name"]: d["id"] for d in depts}
                with st.form("add_kpi", clear_on_submit=True):
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        kpi_name    = st.text_input("KPI Name *")
                        dept_sel    = st.selectbox("Department *", list(dept_map4.keys()))
                        period_type = st.selectbox("Period Type", ["Monthly","Quarterly","Annual"])
                    with c2:
                        target_val  = st.number_input("Target Value", value=0.0)
                        target_type = st.selectbox("Target Type", [">=","<=","="])
                        agg_type    = st.selectbox("Aggregation", ["monthly","weighted_avg","cumulative","sum"])
                    with c3:
                        unit    = st.text_input("Unit")
                        audit_t = st.selectbox("Audit", ["Both","ISO9001","BRCGS"])
                    add = st.form_submit_button("➕ Add KPI")
                if add:
                    if not kpi_name:
                        st.error("KPI name required.")
                    else:
                        try:
                            sb.table("kpi_definitions").insert({
                                "name": kpi_name, "department_id": dept_map4[dept_sel],
                                "target_value": target_val, "target_type": target_type,
                                "unit": unit or None, "audit_type": audit_t,
                                "period_type": period_type, "aggregation_type": agg_type,
                            }).execute()
                            st.success(f"KPI '{kpi_name}' added. Set targets in the Targets tab.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error: {e}")

            with sub_targets:
                depts     = get_departments()
                dept_map5 = {d["name"]: d["id"] for d in depts}
                tc1, tc2  = st.columns(2)
                with tc1:
                    t_dept = st.selectbox("Department", list(dept_map5.keys()), key="t_dept")
                with tc2:
                    t_year = st.selectbox("Year", [2026, 2025, 2027], key="t_year")

                t_kpis = sb.table("kpi_definitions").select("*")\
                    .eq("department_id", dept_map5[t_dept]).eq("is_active", True).execute().data or []

                if not t_kpis:
                    st.info("No KPIs for this department.")
                else:
                    for kpi in t_kpis:
                        kpi_id      = kpi["id"]
                        period_type = kpi.get("period_type","Monthly")
                        unit        = kpi.get("unit","") or ""
                        with st.expander(f"**{kpi['name']}** — {period_type}", expanded=False):
                            if period_type == "Annual":
                                ex = sb.table("kpi_targets").select("target_value")\
                                    .eq("kpi_id", kpi_id).eq("year", t_year).is_("month","null").execute()
                                cur = float(ex.data[0]["target_value"]) if ex.data else 0.0
                                nv  = st.number_input(f"Annual target ({unit})", value=cur, key=f"t_{kpi_id}_a")
                                if st.button("💾 Save", key=f"ts_{kpi_id}_a"):
                                    sb.table("kpi_targets").upsert({"kpi_id": kpi_id, "year": t_year,
                                        "month": None, "target_value": nv},
                                        on_conflict="kpi_id,year,month").execute()
                                    st.success("Saved.")

                            elif period_type == "Quarterly":
                                qc   = st.columns(4)
                                qv   = {}
                                for qi, (qm, ql) in enumerate(QUARTER_MONTHS.items()):
                                    ex  = sb.table("kpi_targets").select("target_value")\
                                        .eq("kpi_id", kpi_id).eq("year", t_year).eq("month", qm).execute()
                                    cur = float(ex.data[0]["target_value"]) if ex.data else 0.0
                                    with qc[qi]:
                                        qv[qm] = st.number_input(f"{ql} ({unit})", value=cur, key=f"t_{kpi_id}_{qm}")
                                if st.button("💾 Save All Quarters", key=f"ts_{kpi_id}_q"):
                                    for qm, val in qv.items():
                                        sb.table("kpi_targets").upsert({"kpi_id": kpi_id, "year": t_year,
                                            "month": qm, "target_value": val},
                                            on_conflict="kpi_id,year,month").execute()
                                    st.success("All quarters saved.")

                            else:
                                m_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
                                mc      = st.columns(6)
                                mv      = {}
                                for mi in range(12):
                                    ex  = sb.table("kpi_targets").select("target_value")\
                                        .eq("kpi_id", kpi_id).eq("year", t_year).eq("month", mi+1).execute()
                                    cur = float(ex.data[0]["target_value"]) if ex.data else 0.0
                                    with mc[mi % 6]:
                                        mv[mi+1] = st.number_input(m_names[mi], value=cur, key=f"t_{kpi_id}_{mi+1}")
                                cs, cr = st.columns(2)
                                with cs:
                                    save_m = st.button("💾 Save All Months", key=f"ts_{kpi_id}_m")
                                with cr:
                                    repeat_m = st.button("🔁 Repeat Jan for all months", key=f"tr_{kpi_id}")
                                if save_m:
                                    for mn, val in mv.items():
                                        sb.table("kpi_targets").upsert({"kpi_id": kpi_id, "year": t_year,
                                            "month": mn, "target_value": val},
                                            on_conflict="kpi_id,year,month").execute()
                                    st.success("All months saved.")
                                if repeat_m:
                                    for mn in range(1, 13):
                                        sb.table("kpi_targets").upsert({"kpi_id": kpi_id, "year": t_year,
                                            "month": mn, "target_value": mv.get(1, 0.0)},
                                            on_conflict="kpi_id,year,month").execute()
                                    st.success(f"Target {mv.get(1,0.0)} {unit} repeated for all 12 months.")
                                    st.rerun()

            with sub_list:
                all_k = sb.table("kpi_definitions").select("*,departments(name)")\
                    .eq("is_active", True).order("department_id").execute()
                if all_k.data:
                    rows = [{"KPI": k["name"],
                        "Department": (k.get("departments") or {}).get("name","—"),
                        "Target": f"{k['target_type']} {k['target_value']} {k.get('unit','') or ''}",
                        "Period": k.get("period_type","Monthly"),
                        "Aggregation": k.get("aggregation_type","monthly"),
                    } for k in all_k.data]
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
