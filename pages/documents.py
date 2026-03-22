import streamlit as st
import pandas as pd
from datetime import date, timedelta
from utils.auth import require_auth, can_write, get_profile
from utils.supabase_client import get_supabase
from utils.helpers import users_options, status_badge, format_date


REVIEW_FREQ_MONTHS = {
    "Monthly": 1, "Quarterly": 3, "Bi-Annual": 6, "Annual": 12
}

DOC_TYPES_ISO = [
    "Training Record", "MoM (Minutes of Meeting)", "GMP Audit Closure",
    "Management Review", "Internal Audit Report", "Corrective Action",
    "Procedure", "Work Instruction", "Form / Template", "Other"
]

DOC_TYPES_BRCGS = [
    "Risk Assessment", "Facility Test / Verification", "Supplier Approval",
    "Pest Control Record", "Cleaning Schedule", "Calibration Record",
    "Traceability Test", "Product Specification", "Other"
]


def show():
    require_auth()
    st.title("📁 Document Register")
    st.caption("Required documentation for ISO 9001 and BRCGS — review schedule and status")

    sb = get_supabase()

    tab_list, tab_add = st.tabs(["📄 Document Register", "➕ Add Document"])

    # ──────────────────────────────────────────────────────────
    # TAB 1 — List
    # ──────────────────────────────────────────────────────────
    with tab_list:
        col1, col2, col3 = st.columns(3)
        with col1:
            audit_f = st.selectbox("Audit", ["All","ISO9001","BRCGS","Both"])
        with col2:
            status_f = st.selectbox("Status", ["All","current","due_for_review","overdue","superseded"])
        with col3:
            search = st.text_input("Search title / code", placeholder="e.g. GMP or QP-001")

        query = sb.table("documents").select("*, profiles!documents_owner_id_fkey(full_name)").order("next_review_due")

        if audit_f != "All":
            query = query.eq("audit_type", audit_f)
        if status_f != "All":
            query = query.eq("status", status_f)

        res = query.execute()
        docs = res.data or []

        if search:
            s = search.lower()
            docs = [d for d in docs if s in (d.get("title","") or "").lower() or s in (d.get("doc_code","") or "").lower()]

        if not docs:
            st.info("No documents match your filters.")
        else:
            rows = []
            for d in docs:
                owner = (d.get("profiles") or {}).get("full_name","—")
                due = d.get("next_review_due")
                days = None
                if due:
                    days = (date.fromisoformat(due) - date.today()).days

                rows.append({
                    "Code":        d.get("doc_code","—"),
                    "Title":       d.get("title",""),
                    "Audit":       d.get("audit_type",""),
                    "Type":        d.get("doc_type","—"),
                    "Owner":       owner,
                    "Last Review": format_date(d.get("last_reviewed")),
                    "Next Due":    format_date(due),
                    "Days":        days if days is not None else "—",
                    "Status":      status_badge(d.get("status","current")),
                })

            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)

            overdue = [d for d in docs if d.get("status") == "overdue"]
            if overdue:
                st.warning(f"⚠️ {len(overdue)} document(s) overdue for review.")

    # ──────────────────────────────────────────────────────────
    # TAB 2 — Add
    # ──────────────────────────────────────────────────────────
    with tab_add:
        if not can_write():
            st.info("View-only access.")
            return

        with st.form("add_doc", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                title    = st.text_input("Document Title *")
                doc_code = st.text_input("Document Code", placeholder="e.g. QP-001")
                audit_t  = st.selectbox("Audit Type *", ["ISO9001","BRCGS","Both"])
                doc_type_opts = DOC_TYPES_ISO if audit_t == "ISO9001" else (DOC_TYPES_BRCGS if audit_t == "BRCGS" else DOC_TYPES_ISO + DOC_TYPES_BRCGS)
                doc_type = st.selectbox("Document Type", doc_type_opts)
            with c2:
                owner_opts   = users_options()
                owner_label  = st.selectbox("Document Owner", list(owner_opts.keys()))
                review_freq  = st.selectbox("Review Frequency", ["Annual","Bi-Annual","Quarterly","Monthly"])
                last_reviewed = st.date_input("Last Reviewed Date", value=date.today())
                version      = st.text_input("Version", placeholder="e.g. v1.0")

            location = st.text_input("File Location / Link", placeholder="SharePoint URL or folder path")
            notes    = st.text_area("Notes", height=60)

            submitted = st.form_submit_button("💾 Save Document")

        if submitted:
            if not title:
                st.error("Document title is required.")
            else:
                months = REVIEW_FREQ_MONTHS[review_freq]
                next_due = last_reviewed + timedelta(days=months * 30)
                status = "overdue" if next_due < date.today() else \
                         "due_for_review" if (next_due - date.today()).days <= 30 else "current"
                profile = get_profile()
                try:
                    sb.table("documents").insert({
                        "title":            title,
                        "doc_code":         doc_code or None,
                        "audit_type":       audit_t,
                        "doc_type":         doc_type,
                        "owner_id":         owner_opts[owner_label],
                        "review_frequency": review_freq,
                        "last_reviewed":    last_reviewed.isoformat(),
                        "next_review_due":  next_due.isoformat(),
                        "version":          version or None,
                        "location":         location or None,
                        "notes":            notes or None,
                        "status":           status,
                        "created_by":       profile["id"],
                    }).execute()
                    st.success(f"✅ Document '{title}' saved. Next review due: {format_date(next_due)}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")
