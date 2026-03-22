import streamlit as st
import pandas as pd
from datetime import date
from utils.auth import require_auth, get_profile
from utils.supabase_client import get_supabase
from utils.helpers import format_date, status_badge


def show():
    require_auth()
    st.title("🏠 Dashboard")
    st.caption("Easternpak Quality Hub — live status overview")

    sb      = get_supabase()
    profile = get_profile()
    user_id = profile["id"]
    role    = profile["role"]

    # ── Metric cards ─────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)

    # All open NCs (global)
    nc_open = sb.table("nc_findings").select("id", count="exact").neq("status", "closed").execute()

    # My open NCs
    my_nc = sb.table("nc_findings").select("id", count="exact")\
        .neq("status", "closed")\
        .eq("action_owner_id", user_id).execute()

    # Overdue NCs assigned to me
    my_overdue = sb.table("nc_findings").select("id", count="exact")\
        .neq("status", "closed")\
        .eq("action_owner_id", user_id)\
        .lt("target_date", date.today().isoformat()).execute()

    # Docs overdue
    doc_due = sb.table("v_overdue_docs").select("id", count="exact").execute()

    # KPIs missed this month
    month_start = date.today().replace(day=1).isoformat()
    kpi_miss = sb.table("kpi_entries").select("id", count="exact")\
        .eq("month", month_start).eq("achieved", False).execute()

    with c1:
        st.metric("Total Open NCs", nc_open.count or 0)
    with c2:
        val = my_nc.count or 0
        st.metric("My Open Tasks", val)
    with c3:
        val = my_overdue.count or 0
        st.metric("My Overdue Tasks", val,
                  delta=f"-{val} overdue" if val else None,
                  delta_color="inverse" if val else "off")
    with c4:
        val = doc_due.count or 0
        st.metric("Docs Overdue Review", val,
                  delta=f"-{val}" if val else None,
                  delta_color="inverse" if val else "off")
    with c5:
        st.metric("KPIs Missed (this month)", kpi_miss.count or 0)

    st.markdown("---")

    # ── My Tasks ─────────────────────────────────────────────
    st.subheader("📌 My Open Tasks")

    my_findings = sb.table("nc_findings").select("*")\
        .eq("action_owner_id", user_id)\
        .neq("status", "closed")\
        .order("target_date").execute()

    if my_findings.data:
        for f in my_findings.data:
            target = f.get("target_date")
            is_overdue = target and date.fromisoformat(target) < date.today()
            badge = "🔴" if is_overdue else "🟡"

            with st.expander(f"{badge} [{f.get('audit_type','')}] {f.get('finding_ref','—')} — {f.get('details','') or ''}…"):
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown(f"**Audit:** {f.get('audit_ref','—')}")
                    st.markdown(f"**Clause:** {f.get('clause_ref','—')}")
                    st.markdown(f"**Status:** {status_badge(f.get('status','open'))}")
                    st.markdown(f"**Target Date:** {format_date(f.get('target_date'))}")
                with c2:
                    st.markdown(f"**Details:**")
                    st.info(f.get("details","—"))
                st.markdown(f"**Root Cause:** {f.get('root_cause','—') or '—'}")
                st.markdown(f"**Correction:** {f.get('correction','—') or '—'}")
                st.markdown(f"**Preventive Action:** {f.get('preventive_action','—') or '—'}")
    else:
        st.success("No open tasks assigned to you. ✓")

    st.markdown("---")

    # ── Global overview (managers/admins only) ────────────────
    if role in ("admin", "quality_manager"):
        st.subheader("🌐 All Open NC/CAPA Findings")

        all_findings = sb.table("nc_findings").select(
            "*, profiles!nc_findings_action_owner_id_fkey(full_name)"
        ).neq("status", "closed").order("target_date").execute()

        if all_findings.data:
            for f in all_findings.data:
                owner  = (f.get("profiles") or {}).get("full_name", "—")
                target = f.get("target_date")
                is_overdue = target and date.fromisoformat(target) < date.today()
                badge  = "🔴" if is_overdue else "🟡"

                with st.expander(f"{badge} [{f.get('audit_type','')}] {f.get('finding_ref','—')} — {f.get('details','') or ''}… | Owner: {owner}"):
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown(f"**Audit:** {f.get('audit_ref','—')}")
                        st.markdown(f"**Clause:** {f.get('clause_ref','—')}")
                        st.markdown(f"**Status:** {status_badge(f.get('status','open'))}")
                        st.markdown(f"**Target Date:** {format_date(f.get('target_date'))}")
                        st.markdown(f"**Action Owner:** {owner}")
                    with c2:
                        st.info(f.get("details","—"))
                    st.markdown(f"**Root Cause:** {f.get('root_cause','—') or '—'}")
                    st.markdown(f"**Correction:** {f.get('correction','—') or '—'}")
                    st.markdown(f"**Preventive Action:** {f.get('preventive_action','—') or '—'}")
        else:
            st.success("No open findings. ✓")

        st.markdown("---")
        st.subheader("📁 Documents Overdue for Review")
        res = sb.table("v_overdue_docs").select("*").order("next_review_due").limit(10).execute()
        if res.data:
            rows = []
            for r in res.data:
                rows.append({
                    "Code":  r.get("doc_code", "—"),
                    "Title": r.get("title", ""),
                    "Audit": r.get("audit_type", ""),
                    "Owner": r.get("owner_name", "—"),
                    "Due":   format_date(r.get("next_review_due")),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.success("All documents are current. ✓")
