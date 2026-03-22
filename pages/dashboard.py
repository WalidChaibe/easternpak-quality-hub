import streamlit as st
import pandas as pd
from utils.auth import require_auth
from utils.supabase_client import get_supabase
from utils.helpers import format_date, status_badge


def show():
    require_auth()
    st.title("🏠 Dashboard")
    st.caption("Easternpak Quality Hub — live status overview")

    sb = get_supabase()

    # ── Metric cards ─────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)

    nc_open = sb.table("nc_findings").select("id", count="exact").neq("status","closed").execute()
    nc_over = sb.table("v_overdue_nc").select("id", count="exact").execute()
    doc_due  = sb.table("v_overdue_docs").select("id", count="exact").execute()

    # KPIs missed this month
    from datetime import date
    month_start = date.today().replace(day=1).isoformat()
    kpi_miss = sb.table("kpi_entries").select("id", count="exact").eq("month", month_start).eq("achieved", False).execute()

    audits_sched = sb.table("audits").select("id", count="exact").eq("status","scheduled").execute()

    with c1:
        st.metric("Open NC/CAPA", nc_open.count or 0)
    with c2:
        val = nc_over.count or 0
        st.metric("Overdue Findings", val, delta=f"-{val} overdue" if val else None,
                  delta_color="inverse" if val else "off")
    with c3:
        val = doc_due.count or 0
        st.metric("Docs Overdue Review", val, delta=f"-{val}" if val else None,
                  delta_color="inverse" if val else "off")
    with c4:
        st.metric("KPIs Missed (this month)", kpi_miss.count or 0)
    with c5:
        st.metric("Audits Scheduled", audits_sched.count or 0)

    st.markdown("---")

    # ── Overdue NC table ─────────────────────────────────────
    col_l, col_r = st.columns(2)

    with col_l:
        st.subheader("⚠️ Overdue NC/CAPA Findings")
        res = sb.table("v_overdue_nc").select("*").order("target_date").limit(10).execute()
        if res.data:
            rows = []
            for r in res.data:
                rows.append({
                    "Ref":       r.get("finding_ref","—"),
                    "Audit":     r.get("audit_type",""),
                    "Details":   (r.get("details","") or "")[:60] + "…",
                    "Owner":     r.get("owner_name","—"),
                    "Due":       format_date(r.get("target_date")),
                    "Status":    status_badge(r.get("status","open")),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.success("No overdue findings. ✓")

    with col_r:
        st.subheader("📁 Documents Overdue for Review")
        res = sb.table("v_overdue_docs").select("*").order("next_review_due").limit(10).execute()
        if res.data:
            rows = []
            for r in res.data:
                rows.append({
                    "Code":      r.get("doc_code","—"),
                    "Title":     (r.get("title","") or "")[:45] + "…",
                    "Audit":     r.get("audit_type",""),
                    "Owner":     r.get("owner_name","—"),
                    "Due":       format_date(r.get("next_review_due")),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.success("All documents are current. ✓")
