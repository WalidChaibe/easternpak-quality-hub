import streamlit as st
import pandas as pd
from datetime import date
from utils.auth import require_auth, can_write
from utils.supabase_client import get_supabase
from utils.helpers import users_options, status_badge, format_date


def show(audit_type: str):
    require_auth()
    label = "ISO 9001" if audit_type == "ISO9001" else "BRCGS"
    st.title(f"📋 NC/CAPA — {label}")
    st.caption("Previous audit findings — log, track and close non-conformities")

    sb = get_supabase()

    # ── Tabs: List | Add New ──────────────────────────────────
    tab_list, tab_add, tab_edit = st.tabs(["📄 All Findings", "➕ Log New Finding", "✏️ Update Finding"])

    # ──────────────────────────────────────────────────────────
    # TAB 1 — List
    # ──────────────────────────────────────────────────────────
    with tab_list:
        filters_col, _ = st.columns([2, 3])
        with filters_col:
            status_filter = st.selectbox(
                "Filter by status",
                ["All", "open", "in_progress", "closed", "overdue"],
                key=f"status_filter_{audit_type}"
            )

        query = sb.table("nc_findings").select(
            "*, profiles!nc_findings_action_owner_id_fkey(full_name)"
        ).eq("audit_type", audit_type).order("target_date")

        if status_filter != "All":
            query = query.eq("status", status_filter)

        res = query.execute()
        findings = res.data or []

        if not findings:
            st.info("No findings recorded yet.")
        else:
            rows = []
            for f in findings:
                owner = (f.get("profiles") or {}).get("full_name", "—")
                rows.append({
                    "ID":        f["id"][:8],
                    "Ref":       f.get("finding_ref","—"),
                    "Clause":    f.get("clause_ref","—"),
                    "Details":   (f.get("details","") or "")[:70] + "…",
                    "Owner":     owner,
                    "Target":    format_date(f.get("target_date")),
                    "Closed":    format_date(f.get("closing_date")),
                    "Status":    status_badge(f.get("status","open")),
                    "_id":       f["id"],
                })
            df = pd.DataFrame(rows)
            st.dataframe(df.drop(columns=["_id"]), use_container_width=True, hide_index=True)

            # Overdue count banner
            overdue = [f for f in findings if f.get("target_date") and
                       date.fromisoformat(f["target_date"]) < date.today() and
                       f.get("status") != "closed"]
            if overdue:
                st.warning(f"⚠️ {len(overdue)} finding(s) past their target date and not yet closed.")

    # ──────────────────────────────────────────────────────────
    # TAB 2 — Add New Finding
    # ──────────────────────────────────────────────────────────
    with tab_add:
        if not can_write():
            st.info("You have view-only access. Contact your Quality Manager to log findings.")
            return

        with st.form(f"new_finding_{audit_type}", clear_on_submit=True):
            st.markdown("#### New Non-Conformity")

            col1, col2 = st.columns(2)
            with col1:
                finding_ref  = st.text_input("Finding Reference *", placeholder="e.g. NC-001")
                clause_ref   = st.text_input("Clause / Requirement Ref", placeholder=f"e.g. {'8.5.2' if audit_type=='ISO9001' else 'Section 3.5'}")
                audit_ref    = st.text_input("Audit Name", placeholder=f"e.g. {label} Annual Audit 2025")
            with col2:
                owner_opts   = users_options()
                owner_label  = st.selectbox("Action Owner *", list(owner_opts.keys()), key=f"owner_{audit_type}")
                target_date  = st.date_input("Target Closure Date *", min_value=date.today())
                status       = st.selectbox("Initial Status", ["open","in_progress"], key=f"status_new_{audit_type}")

            details          = st.text_area("Details of Non-Conformity *", height=100)
            root_cause       = st.text_area("Root Cause Analysis", height=80)
            correction       = st.text_area("Immediate Correction Taken", height=80)
            preventive_action = st.text_area("Preventive Action Plan", height=80)
            evidence_notes   = st.text_area("Evidence / Notes", height=60)

            submitted = st.form_submit_button("💾 Save Finding", use_container_width=True)

        if submitted:
            if not finding_ref or not details or not target_date:
                st.error("Please fill in all required fields (*)")
            else:
                from utils.auth import get_profile
                profile = get_profile()
                payload = {
                    "audit_type":       audit_type,
                    "finding_ref":      finding_ref,
                    "clause_ref":       clause_ref or None,
                    "audit_ref":        audit_ref or None,
                    "details":          details,
                    "root_cause":       root_cause or None,
                    "correction":       correction or None,
                    "preventive_action": preventive_action or None,
                    "evidence_notes":   evidence_notes or None,
                    "action_owner_id":  owner_opts[owner_label],
                    "target_date":      target_date.isoformat(),
                    "status":           status,
                    "created_by":       profile["id"],
                }
                try:
                    sb.table("nc_findings").insert(payload).execute()
                    st.success(f"✅ Finding {finding_ref} saved successfully.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error saving: {e}")

    # ──────────────────────────────────────────────────────────
    # TAB 3 — Update / Close a Finding
    # ──────────────────────────────────────────────────────────
    with tab_edit:
        if not can_write():
            st.info("View-only access.")
            return

        res = sb.table("nc_findings").select("id, finding_ref, details, status, audit_type")\
            .eq("audit_type", audit_type).neq("status","closed").order("finding_ref").execute()
        open_findings = res.data or []

        if not open_findings:
            st.success("All findings are closed. ✓")
        else:
            opts = {f"{f['finding_ref']} — {(f['details'] or '')[:50]}": f["id"] for f in open_findings}
            selected_label = st.selectbox("Select finding to update", list(opts.keys()), key=f"edit_select_{audit_type}")
            selected_id = opts[selected_label]

            rec = sb.table("nc_findings").select("*").eq("id", selected_id).single().execute().data

            with st.form(f"edit_finding_{audit_type}_{selected_id}"):
                col1, col2 = st.columns(2)
                with col1:
                    new_status = st.selectbox("Status", ["open","in_progress","closed","overdue"],
                        index=["open","in_progress","closed","overdue"].index(rec.get("status","open")))
                with col2:
                    closing_date = st.date_input("Date of Closing",
                        value=date.fromisoformat(rec["closing_date"]) if rec.get("closing_date") else None)

                root_cause        = st.text_area("Root Cause Analysis",       value=rec.get("root_cause","") or "",   height=80)
                correction        = st.text_area("Correction Taken",          value=rec.get("correction","") or "",   height=80)
                preventive_action = st.text_area("Preventive Action Plan",    value=rec.get("preventive_action","") or "", height=80)
                evidence_notes    = st.text_area("Evidence / Notes",          value=rec.get("evidence_notes","") or "", height=60)

                owner_opts   = users_options(include_blank=False)
                current_owner_key = next(
                    (k for k, v in owner_opts.items() if v == rec.get("action_owner_id")),
                    list(owner_opts.keys())[0]
                )
                owner_label = st.selectbox("Action Owner", list(owner_opts.keys()),
                    index=list(owner_opts.keys()).index(current_owner_key))

                save = st.form_submit_button("💾 Save Updates", use_container_width=True)

            if save:
                update = {
                    "status":           new_status,
                    "root_cause":       root_cause or None,
                    "correction":       correction or None,
                    "preventive_action": preventive_action or None,
                    "evidence_notes":   evidence_notes or None,
                    "action_owner_id":  owner_opts[owner_label],
                }
                if new_status == "closed" and closing_date:
                    update["closing_date"] = closing_date.isoformat()
                try:
                    sb.table("nc_findings").update(update).eq("id", selected_id).execute()
                    st.success("✅ Finding updated.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")
