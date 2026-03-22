import streamlit as st
import pandas as pd
from datetime import date
from utils.auth import require_auth, can_write, get_profile
from utils.supabase_client import get_supabase
from utils.helpers import users_options, status_badge, format_date


def show():
    require_auth()
    st.title("🔍 Internal Audits")
    st.caption("Schedule, conduct and track internal audits for ISO 9001 and BRCGS")

    sb  = get_supabase()
    tab_list, tab_add = st.tabs(["📄 Audit Schedule", "➕ Schedule New Audit"])

    with tab_list:
        col1, col2 = st.columns(2)
        with col1:
            audit_f  = st.selectbox("Audit type", ["All","ISO9001","BRCGS"])
        with col2:
            status_f = st.selectbox("Status", ["All","scheduled","in_progress","completed","cancelled"])

        query = sb.table("audits").select(
            "*, profiles!audits_lead_auditor_id_fkey(full_name)"
        ).order("scheduled_date", desc=True)

        if audit_f != "All":
            query = query.eq("audit_type", audit_f)
        if status_f != "All":
            query = query.eq("status", status_f)

        res = query.execute()
        audits = res.data or []

        if not audits:
            st.info("No audits found.")
        else:
            rows = []
            for a in audits:
                auditor = (a.get("profiles") or {}).get("full_name","—")
                rows.append({
                    "Audit":     a.get("audit_name",""),
                    "Type":      a.get("audit_type",""),
                    "Scheduled": format_date(a.get("scheduled_date")),
                    "Conducted": format_date(a.get("conducted_date")),
                    "Lead":      auditor,
                    "Status":    status_badge(a.get("status","scheduled")),
                    "id":        a["id"],
                })
            df = pd.DataFrame(rows)
            st.dataframe(df.drop(columns=["id"]), use_container_width=True, hide_index=True)

            # Link to NC findings
            st.markdown("---")
            st.markdown("**View findings linked to an audit:**")
            audit_opts = {f"{a['audit_name']} ({a['audit_type']})": a["id"] for a in audits}
            sel_label = st.selectbox("Select audit", list(audit_opts.keys()))
            sel_id = audit_opts[sel_label]

            nc_res = sb.table("nc_findings").select(
                "finding_ref, details, status, target_date, profiles!nc_findings_action_owner_id_fkey(full_name)"
            ).eq("audit_id", sel_id).execute()

            if nc_res.data:
                nc_rows = []
                for f in nc_res.data:
                    nc_rows.append({
                        "Ref":     f.get("finding_ref","—"),
                        "Details": (f.get("details","") or "")[:70]+"…",
                        "Owner":   (f.get("profiles") or {}).get("full_name","—"),
                        "Due":     format_date(f.get("target_date")),
                        "Status":  status_badge(f.get("status","open")),
                    })
                st.dataframe(pd.DataFrame(nc_rows), use_container_width=True, hide_index=True)
            else:
                st.info("No NC findings linked to this audit yet.")

    with tab_add:
        if not can_write():
            st.info("View-only access.")
            return

        with st.form("new_audit", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                audit_name   = st.text_input("Audit Name *", placeholder="e.g. BRCGS Annual Audit 2025")
                audit_type   = st.selectbox("Audit Type *", ["ISO9001","BRCGS"])
                scheduled_dt = st.date_input("Scheduled Date *")
            with c2:
                owner_opts  = users_options()
                lead_label  = st.selectbox("Lead Auditor", list(owner_opts.keys()))
                status      = st.selectbox("Status", ["scheduled","in_progress","completed"])
                scope       = st.text_area("Scope / Areas", height=70)

            submitted = st.form_submit_button("💾 Schedule Audit")

        if submitted:
            if not audit_name:
                st.error("Audit name required.")
            else:
                profile = get_profile()
                try:
                    sb.table("audits").insert({
                        "audit_name":       audit_name,
                        "audit_type":       audit_type,
                        "scheduled_date":   scheduled_dt.isoformat(),
                        "lead_auditor_id":  owner_opts[lead_label],
                        "status":           status,
                        "scope":            scope or None,
                        "created_by":       profile["id"],
                    }).execute()
                    st.success(f"✅ Audit '{audit_name}' scheduled.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")
