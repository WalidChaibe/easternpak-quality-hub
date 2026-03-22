import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import date
from io import BytesIO
from utils.auth import require_auth, can_write, get_profile
from utils.supabase_client import get_supabase
from utils.helpers import get_departments, format_date


def show():
    require_auth()
    st.title("📊 KPI Tracking")
    st.caption("Monthly KPI entry, root cause on misses, and trend analysis")

    sb = get_supabase()

    tab_entry, tab_trends, tab_manage = st.tabs(["📝 Monthly Entry", "📈 Trends & Export", "⚙️ Manage KPIs"])

    # ──────────────────────────────────────────────────────────
    # TAB 1 — Monthly Entry
    # ──────────────────────────────────────────────────────────
    with tab_entry:
        depts = get_departments()
        dept_map = {d["name"]: d["id"] for d in depts}

        col1, col2 = st.columns([1, 1])
        with col1:
            selected_dept = st.selectbox("Department", list(dept_map.keys()))
        with col2:
            # Month selector — first day of each month
            today = date.today()
            months = pd.date_range(end=today, periods=12, freq="MS").to_list()
            month_labels = [m.strftime("%B %Y") for m in months]
            selected_month_label = st.selectbox("Month", month_labels[::-1])
            selected_month = pd.to_datetime(selected_month_label, format="%B %Y").date()

        dept_id = dept_map[selected_dept]

        # Load KPIs for this department
        kpis_res = sb.table("kpi_definitions").select("*")\
            .eq("department_id", dept_id).eq("is_active", True).execute()
        kpis = kpis_res.data or []

        if not kpis:
            st.info(f"No KPIs defined for {selected_dept} yet. Add them in the Manage KPIs tab.")
        else:
            st.markdown(f"#### {selected_dept} — {selected_month_label}")

            for kpi in kpis:
                kpi_id = kpi["id"]
                # Check if entry already exists
                existing = sb.table("kpi_entries").select("*")\
                    .eq("kpi_id", kpi_id).eq("month", selected_month.isoformat()).execute()
                rec = existing.data[0] if existing.data else None

                with st.expander(f"**{kpi['name']}** — Target: {kpi['target_type']} {kpi['target_value']} {kpi.get('unit','')}", expanded=not rec):
                    with st.form(f"kpi_form_{kpi_id}_{selected_month}"):
                        actual = st.number_input(
                            f"Actual value ({kpi.get('unit','')})",
                            value=float(rec["actual_value"]) if rec and rec.get("actual_value") is not None else 0.0,
                            key=f"actual_{kpi_id}"
                        )
                        # Auto-compute achieved
                        t = kpi.get("target_type",">=")
                        tv = float(kpi.get("target_value") or 0)
                        if t == ">=":    auto_achieved = actual >= tv
                        elif t == "<=":  auto_achieved = actual <= tv
                        else:            auto_achieved = actual == tv

                        achieved = st.checkbox("Achieved?", value=rec["achieved"] if rec else auto_achieved)

                        rc = ""
                        ca = ""
                        if not achieved:
                            st.warning("Target not achieved — please complete root cause and corrective action.")
                            rc = st.text_area("Root Cause", value=rec.get("root_cause","") if rec else "", height=70, key=f"rc_{kpi_id}")
                            ca = st.text_area("Corrective Action", value=rec.get("corrective_action","") if rec else "", height=70, key=f"ca_{kpi_id}")

                        can = can_write()
                        save = st.form_submit_button("💾 Save", disabled=not can)

                    if save:
                        if not achieved and not (rc.strip() and ca.strip()):
                            st.error("Root cause and corrective action are required when target is not achieved.")
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
                                        .eq("kpi_id", kpi_id).eq("month", selected_month.isoformat()).execute()
                                else:
                                    sb.table("kpi_entries").insert(payload).execute()
                                st.success("✅ Saved.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error: {e}")

    # ──────────────────────────────────────────────────────────
    # TAB 2 — Trends & Export
    # ──────────────────────────────────────────────────────────
    with tab_trends:
        depts = get_departments()
        dept_map2 = {d["name"]: d["id"] for d in depts}

        col1, col2 = st.columns([1, 1])
        with col1:
            dept_t = st.selectbox("Department", list(dept_map2.keys()), key="trend_dept")
        with col2:
            months_back = st.selectbox("Period", [6, 12, 24], format_func=lambda x: f"Last {x} months")

        dept_id2 = dept_map2[dept_t]
        cutoff = pd.Timestamp.now() - pd.DateOffset(months=months_back)
        cutoff_str = cutoff.date().replace(day=1).isoformat()

        res = sb.table("v_kpi_full").select("*")\
            .eq("department_name", dept_t)\
            .gte("month", cutoff_str)\
            .order("month").execute()

        data = res.data or []

        if not data:
            st.info("No data recorded for this period.")
        else:
            df = pd.DataFrame(data)
            df["month"] = pd.to_datetime(df["month"])
            df["month_label"] = df["month"].dt.strftime("%b %Y")

            kpi_names = df["kpi_name"].unique().tolist()
            selected_kpis = st.multiselect("KPIs to display", kpi_names, default=kpi_names[:4])

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
                fig.add_hline(y=target, line_dash="dash", line_color="red",
                              annotation_text=f"Target {target}{unit}", annotation_position="bottom right")
                fig.update_layout(
                    xaxis_title="Month", yaxis_title=unit,
                    plot_bgcolor="white", height=280,
                    margin=dict(t=40, b=20, l=20, r=20),
                )
                # Colour missed months
                for _, row in kdf[kdf["achieved"] == False].iterrows():
                    fig.add_vrect(
                        x0=row["month_label"], x1=row["month_label"],
                        fillcolor="red", opacity=0.08, line_width=0
                    )
                st.plotly_chart(fig, use_container_width=True)

            # ── Excel export ──────────────────────────────────
            st.markdown("---")
            if st.button("⬇️ Export to Excel"):
                export_df = df[["month_label","kpi_name","kpi_unit","target_value","actual_value",
                                "achieved","root_cause","corrective_action","entered_by_name"]]
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
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

    # ──────────────────────────────────────────────────────────
    # TAB 3 — Manage KPI Definitions
    # ──────────────────────────────────────────────────────────
    with tab_manage:
        if not can_write():
            st.info("View-only access. Contact Quality Manager to manage KPI definitions.")
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
                target_type = st.selectbox("Target Type", [">=", "<=", "="])
            with c3:
                unit = st.text_input("Unit (e.g. %, days, count)")
                audit_t = st.selectbox("Audit", ["Both","ISO9001","BRCGS"])

            add = st.form_submit_button("➕ Add KPI")

        if add:
            if not kpi_name:
                st.error("KPI name is required.")
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
        all_kpis = sb.table("kpi_definitions").select("*, departments(name)").eq("is_active", True).execute()
        if all_kpis.data:
            rows = [{"KPI": k["name"], "Department": (k.get("departments") or {}).get("name","—"),
                     "Target": f"{k['target_type']} {k['target_value']} {k.get('unit','')}", "Audit": k["audit_type"]}
                    for k in all_kpis.data]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
