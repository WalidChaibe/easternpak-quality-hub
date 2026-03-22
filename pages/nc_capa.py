import streamlit as st
import pandas as pd
from datetime import date
from utils.auth import require_auth, can_write, get_profile
from utils.supabase_client import get_supabase
from utils.helpers import users_options, status_badge, format_date


def show(audit_type: str):
    require_auth()
    label = "ISO 9001" if audit_type == "ISO9001" else "BRCGS"
    st.title(f"📋 NC/CAPA — {label}")
    st.caption("Previous audit findings — log, track and close non-conformities")

    sb = get_supabase()

    tab_list, tab_add, tab_edit = st.tabs(["📄 All Findings", "➕ Log New Finding", "✏️ Update Finding"])

    # ──────────────────────────────────────────────────────────
    # TAB 1 — List
    # ──────────────────────────────────────────────────────────
    with tab_list:
        col1, col2 = st.columns([1, 3])
        with col1:
            status_filter = st.selectbox(
                "Filter by status",
                ["All", "open", "in_progress", "closed", "overdue"],
                key=f"status_filter_{audit_type}"
            )

        query = sb.table("nc_findings").select(
            "*, profiles!nc_findings_action_owner_id_fkey(full_name)"
        ).eq("audit_type", audit_type).order("created_at")

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
                    "Ref":        f.get("finding_ref", "—"),
                    "Clause":     f.get("clause_ref", "—"),
                    "Details":    f.get("details", "") or "",
                    "Owner":      owner,
                    "Target":     format_date(f.get("target_date")),
                    "Closed":     format_date(f.get("closing_date")),
                    "Status":     status_badge(f.get("status", "open")),
                    "_id":        f["id"],
                })
            df = pd.DataFrame(rows)
            st.dataframe(df.drop(columns=["_id"]), use_container_width=True, hide_index=True, column_config={"Details": st.column_config.TextColumn("Details", width="large")})

            overdue = [f for f in findings if f.get("target_date") and
                       date.fromisoformat(f["target_date"]) < date.today() and
                       f.get("status") != "closed"]
            if overdue:
                st.warning(f"⚠️ {len(overdue)} finding(s) past their target date and not yet closed.")

        # ── Expanded view ─────────────────────────────────────
        if findings:
            st.markdown("---")
            st.markdown("#### View Full Finding Details")
            opts = {f"{f.get('finding_ref','—')} — {f.get('details','') or ''}": f for f in findings}
            sel_label = st.selectbox("Select finding", list(opts.keys()), key=f"view_sel_{audit_type}")
            sel = opts[sel_label]

            with st.container():
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown(f"**Clause/Ref:** {sel.get('clause_ref') or '—'}")
                    st.markdown(f"**Audit:** {sel.get('audit_ref') or '—'}")
                    st.markdown(f"**Status:** {status_badge(sel.get('status','open'))}")
                    st.markdown(f"**Target Date:** {format_date(sel.get('target_date'))}")
                    st.markdown(f"**Closing Date:** {format_date(sel.get('closing_date'))}")
                with c2:
                    owner = (sel.get("profiles") or {}).get("full_name", "—")
                    st.markdown(f"**Action Owner:** {owner}")
                    st.markdown(f"**Evidence Notes:** {sel.get('evidence_notes') or '—'}")

                st.markdown(f"**Details of Non-Conformity:**")
                st.info(sel.get("details", "—"))
                st.markdown(f"**Root Cause Analysis:**")
                st.info(sel.get("root_cause") or "—")
                st.markdown(f"**Correction:**")
                st.info(sel.get("correction") or "—")
                st.markdown(f"**Preventive Action Plan:**")
                st.info(sel.get("preventive_action") or "—")

                # Evidence files
                evidence = sb.table("nc_evidence").select("*").eq("finding_id", sel["id"]).execute()
                if evidence.data:
                    st.markdown("**Attached Evidence:**")
                    for ev in evidence.data:
                        st.markdown(f"📎 [{ev.get('file_name','File')}]({ev.get('file_url','#')})")

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
                clause_ref  = st.text_input("Clause / Requirement Ref",
                    placeholder=f"e.g. {'8.5.2' if audit_type=='ISO9001' else 'Section 3.5'}")
                audit_ref   = st.text_input("Audit Name",
                    placeholder=f"e.g. {label} Annual Audit 2025")
            with col2:
                owner_opts  = users_options()
                owner_label = st.selectbox("Action Owner *", list(owner_opts.keys()),
                    key=f"owner_{audit_type}")
                target_date = st.date_input("Target Closure Date", value=None,
                    key=f"target_{audit_type}")
                status      = st.selectbox("Status", ["open", "in_progress"],
                    key=f"status_new_{audit_type}")

            details           = st.text_area("Details of Non-Conformity *", height=100)
            root_cause        = st.text_area("Root Cause Analysis", height=80)
            correction        = st.text_area("Immediate Correction Taken", height=80)
            preventive_action = st.text_area("Preventive Action Plan", height=80)
            evidence_notes    = st.text_area("Evidence / Notes", height=60)

            # File upload
            uploaded_file = st.file_uploader(
                "Attach Evidence (optional)",
                type=["pdf","png","jpg","jpeg","docx","xlsx"],
                key=f"upload_{audit_type}"
            )

            submitted = st.form_submit_button("💾 Save Finding", use_container_width=True)

        if submitted:
            if not details:
                st.error("Details of Non-Conformity is required.")
            else:
                profile = get_profile()

                # Auto-generate finding ref
                count_res = sb.table("nc_findings").select("id", count="exact").eq("audit_type", audit_type).execute()
                count = (count_res.count or 0) + 1
                prefix = "ISO" if audit_type == "ISO9001" else "BRC"
                finding_ref = f"{prefix}-{str(count).zfill(3)}"

                payload = {
                    "audit_type":        audit_type,
                    "finding_ref":       finding_ref,
                    "clause_ref":        clause_ref or None,
                    "audit_ref":         audit_ref or None,
                    "details":           details,
                    "root_cause":        root_cause or None,
                    "correction":        correction or None,
                    "preventive_action": preventive_action or None,
                    "evidence_notes":    evidence_notes or None,
                    "action_owner_id":   owner_opts[owner_label],
                    "target_date":       target_date.isoformat() if target_date else None,
                    "status":            status,
                    "created_by":        profile["id"],
                }
                try:
                    insert_res = sb.table("nc_findings").insert(payload).execute()
                    new_id = insert_res.data[0]["id"]

                    # Handle file upload to Supabase Storage
                    if uploaded_file:
                        file_bytes = uploaded_file.read()
                        file_name  = uploaded_file.name
                        storage_path = f"evidence/{new_id}/{file_name}"
                        try:
                            sb.storage.from_("evidence").upload(storage_path, file_bytes)
                            file_url = sb.storage.from_("evidence").get_public_url(storage_path)
                            sb.table("nc_evidence").insert({
                                "finding_id": new_id,
                                "file_name":  file_name,
                                "file_url":   file_url,
                                "uploaded_by": profile["id"],
                            }).execute()
                        except Exception as fe:
                            st.warning(f"Finding saved but file upload failed: {fe}")

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

        res = sb.table("nc_findings").select(
            "id, finding_ref, details, status, audit_type, clause_ref, root_cause, correction, preventive_action, evidence_notes, target_date, closing_date, action_owner_id"
        ).eq("audit_type", audit_type).neq("status", "closed").order("finding_ref").execute()
        open_findings = res.data or []

        if not open_findings:
            st.success("All findings are closed. ✓")
        else:
            opts = {f"{f['finding_ref']} — {(f['details'] or '')[:50]}": f["id"] for f in open_findings}
            selected_label = st.selectbox("Select finding to update", list(opts.keys()),
                key=f"edit_select_{audit_type}")
            selected_id = opts[selected_label]
            rec = next(f for f in open_findings if f["id"] == selected_id)

            with st.form(f"edit_finding_{audit_type}_{selected_id}"):
                col1, col2 = st.columns(2)
                with col1:
                    new_status = st.selectbox("Status",
                        ["open", "in_progress", "closed", "overdue"],
                        index=["open", "in_progress", "closed", "overdue"].index(rec.get("status", "open")))
                with col2:
                    closing_date = st.date_input("Date of Closing",
                        value=date.fromisoformat(rec["closing_date"]) if rec.get("closing_date") else None)

                owner_opts = users_options(include_blank=False)
                current_owner_key = next(
                    (k for k, v in owner_opts.items() if v == rec.get("action_owner_id")),
                    list(owner_opts.keys())[0]
                )
                owner_label = st.selectbox("Action Owner", list(owner_opts.keys()),
                    index=list(owner_opts.keys()).index(current_owner_key))

                target_date       = st.date_input("Target Closure Date",
                    value=date.fromisoformat(rec["target_date"]) if rec.get("target_date") else None)
                root_cause        = st.text_area("Root Cause Analysis",
                    value=rec.get("root_cause", "") or "", height=80)
                correction        = st.text_area("Correction Taken",
                    value=rec.get("correction", "") or "", height=80)
                preventive_action = st.text_area("Preventive Action Plan",
                    value=rec.get("preventive_action", "") or "", height=80)
                evidence_notes    = st.text_area("Evidence / Notes",
                    value=rec.get("evidence_notes", "") or "", height=60)

                uploaded_file = st.file_uploader(
                    "Attach Additional Evidence",
                    type=["pdf","png","jpg","jpeg","docx","xlsx"],
                    key=f"edit_upload_{audit_type}_{selected_id}"
                )

                save = st.form_submit_button("💾 Save Updates", use_container_width=True)

            if save:
                update = {
                    "status":            new_status,
                    "root_cause":        root_cause or None,
                    "correction":        correction or None,
                    "preventive_action": preventive_action or None,
                    "evidence_notes":    evidence_notes or None,
                    "action_owner_id":   owner_opts[owner_label],
                    "target_date":       target_date.isoformat() if target_date else None,
                }
                if new_status == "closed" and closing_date:
                    update["closing_date"] = closing_date.isoformat()

                try:
                    sb.table("nc_findings").update(update).eq("id", selected_id).execute()

                    # Handle file upload
                    if uploaded_file:
                        profile = get_profile()
                        file_bytes   = uploaded_file.read()
                        file_name    = uploaded_file.name
                        storage_path = f"evidence/{selected_id}/{file_name}"
                        try:
                            sb.storage.from_("evidence").upload(storage_path, file_bytes)
                            file_url = sb.storage.from_("evidence").get_public_url(storage_path)
                            sb.table("nc_evidence").insert({
                                "finding_id":  selected_id,
                                "file_name":   file_name,
                                "file_url":    file_url,
                                "uploaded_by": profile["id"],
                            }).execute()
                        except Exception as fe:
                            st.warning(f"Finding updated but file upload failed: {fe}")

                    st.success("✅ Finding updated.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")
