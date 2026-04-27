import streamlit as st
import pandas as pd
from datetime import date, timedelta
from utils.auth import require_auth, can_write, get_profile, get_role
from utils.supabase_client import get_supabase
from utils.helpers import users_options, format_date

STANDARDS = {
    "All Standards": None,
    "ISO 9001":       "ISO9001",
    "ISO 14001":      "ISO14001",
    "ISO 45001":      "ISO45001",
    "BRCGS":          "BRCGS",
}

DOC_TYPES      = ["Form", "Work Instruction", "Process", "Procedure", "Policy", "Record", "Other"]
REVIEW_OPTIONS = ["None", "Monthly", "Quarterly", "Bi-Annual", "Annual"]


def show():
    require_auth()
    sb   = get_supabase()
    role = get_role()

    st.title("📘 Requirements Register")
    st.caption("ISO 9001 · ISO 14001 · ISO 45001 · BRCGS — document availability and review tracker")

    # ── TABS declared first ───────────────────────────────────
    tab_main, tab_add, tab_manage = st.tabs([
        "📋 Requirements",
        "➕ Add Requirement",
        "⚙️ Manage / Edit / Delete",
    ])

    # ── Load departments once ─────────────────────────────────
    depts_res = sb.table("departments").select("id, name").order("name").execute()
    depts     = depts_res.data or []
    dept_map  = {d["name"]: d["id"] for d in depts}   # name → id
    dept_id_map = {d["id"]: d["name"] for d in depts} # id   → name
    dept_options = ["All Departments"] + [d["name"] for d in depts]

    # ══════════════════════════════════════════════════════════
    # TAB 1 — REQUIREMENTS LIST
    # ══════════════════════════════════════════════════════════
    with tab_main:
        # ── Filters ──────────────────────────────────────────
        fc1, fc2, fc3, fc4, fc5 = st.columns([1.2, 1.2, 1, 1, 1.5])
        with fc1:
            std_label = st.selectbox("Standard", list(STANDARDS.keys()), key="req_std")
            std_code  = STANDARDS[std_label]
        with fc2:
            dept_label = st.selectbox("Department", dept_options, key="req_dept")
        with fc3:
            avail_filter = st.selectbox("Availability", ["All", "✅ Available", "❌ Missing"], key="req_avail")
        with fc4:
            review_filter = st.selectbox("Review Due", ["All", "Overdue", "Due this month", "Up to date"], key="req_review")
        with fc5:
            search = st.text_input("🔍 Search clause / keyword", key="req_search")

        # ── Fetch requirements ────────────────────────────────
        query = sb.table("requirements").select("*")
        if std_code:
            query = query.eq("standard", std_code)
        reqs = query.order("clause_number").execute().data or []

        # Fetch owner names separately
        owner_ids  = list({r["owner_id"] for r in reqs if r.get("owner_id")})
        owners_map = {}
        if owner_ids:
            owners_res = sb.table("profiles").select("id, full_name").in_("id", owner_ids).execute()
            owners_map = {p["id"]: p["full_name"] for p in (owners_res.data or [])}

        # ── Fetch department links ────────────────────────────
        req_ids  = [r["id"] for r in reqs]
        dept_links_map = {}  # requirement_id → [dept_ids]
        if req_ids:
            links_res = sb.table("requirement_departments").select("requirement_id, department_id").in_("requirement_id", req_ids).execute()
            for lnk in (links_res.data or []):
                dept_links_map.setdefault(lnk["requirement_id"], []).append(lnk["department_id"])

        # ── Fetch attached documents ──────────────────────────
        docs_map = {}
        if req_ids:
            docs_res = sb.table("requirement_documents").select("*").in_("requirement_id", req_ids).execute()
            for d in (docs_res.data or []):
                docs_map.setdefault(d["requirement_id"], []).append(d)

        # ── Apply filters ─────────────────────────────────────
        today    = date.today()
        filtered = []
        for r in reqs:
            has_docs  = bool(docs_map.get(r["id"]))
            nrd       = r.get("next_review_due")
            nrd_date  = date.fromisoformat(nrd) if nrd else None
            req_depts = dept_links_map.get(r["id"], [])

            # Auto-reset marked_done if next_review_due has passed
            marked_done = r.get("marked_done", False)
            if marked_done and nrd_date and nrd_date < today:
                marked_done = False
                sb.table("requirements").update({
                    "marked_done":    False,
                    "marked_done_at": None,
                    "marked_done_by": None,
                }).eq("id", r["id"]).execute()

            # A requirement is "available" if it has docs OR is marked done
            is_available = has_docs or marked_done

            # Department filter
            if dept_label != "All Departments":
                dept_id = dept_map.get(dept_label)
                if dept_id not in req_depts:
                    continue

            if avail_filter == "✅ Available" and not is_available: continue
            if avail_filter == "❌ Missing"   and is_available:     continue
            if review_filter == "Overdue"        and (not nrd_date or nrd_date >= today): continue
            if review_filter == "Due this month" and (not nrd_date or not (today <= nrd_date <= today + timedelta(days=30))): continue
            if review_filter == "Up to date"     and nrd_date and nrd_date < today: continue

            if search:
                s = search.lower()
                if s not in (r.get("clause_number","") or "").lower() and \
                   s not in (r.get("clause_title","")  or "").lower() and \
                   s not in (r.get("description","")   or "").lower():
                    continue

            filtered.append((r, has_docs, nrd_date, req_depts, marked_done, is_available))

        # ── Metrics ───────────────────────────────────────────
        total     = len(filtered)
        available = sum(1 for _, _, _, _, _, ia in filtered if ia)
        missing   = total - available
        overdue_r = sum(1 for _, _, nd, _, _, _ in filtered if nd and nd < today)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Clauses",      total)
        m2.metric("✅ Available",        available)
        m3.metric("❌ Missing",          missing)
        m4.metric("🔴 Overdue Reviews",  overdue_r)

        due_soon = [(r, nd) for r, _, nd, _, _, _ in filtered if nd and today <= nd <= today + timedelta(days=7)]
        if due_soon:
            clauses = ", ".join(r["clause_number"] for r, _ in due_soon)
            st.warning(f"⏰ {len(due_soon)} clause(s) due for review within 7 days: **{clauses}**")

        st.markdown("---")

        if not filtered:
            st.info("No clauses match your filters.")
        else:
            for r, has_docs, nrd_date, req_depts, marked_done, is_available in filtered:
                docs         = docs_map.get(r["id"], [])
                owner        = owners_map.get(r.get("owner_id"), "—")
                if marked_done and not has_docs:
                    status_icon = "✅"
                elif is_available:
                    status_icon = "✅"
                else:
                    status_icon = "❌"
                review_str   = format_date(nrd_date) if nrd_date else "—"
                overdue_flag = " 🔴" if nrd_date and nrd_date < today else ""
                dept_names   = " · ".join(dept_id_map.get(did, "?") for did in req_depts) or "—"

                title = (
                    f"{status_icon} **{r['clause_number']}** — {r['clause_title']}  "
                    f"|  {dept_names}  |  Owner: {owner}  |  Next Review: {review_str}{overdue_flag}"
                )

                with st.expander(title, expanded=False):
                    st.markdown(f"_{r.get('description','—')}_")

                    mc1, mc2, mc3 = st.columns(3)
                    with mc1:
                        st.markdown(f"**Standard:** {r.get('standard','—')}")
                        st.markdown(f"**Departments:** {dept_names}")
                        st.markdown(f"**Review Frequency:** {r.get('review_frequency','—') or '—'}")
                    with mc2:
                        st.markdown(f"**Last Reviewed:** {format_date(r.get('last_reviewed'))}")
                        st.markdown(f"**Next Review Due:** {review_str}{overdue_flag}")
                    with mc3:
                        st.markdown(f"**Owner:** {owner}")
                        st.markdown(f"**Notes:** {r.get('notes','—') or '—'}")

                    # ── Mark as Done button (no doc needed) ───────
                    if can_write() and not has_docs:
                        st.markdown("---")
                        if marked_done:
                            done_by_res = sb.table("profiles").select("full_name").eq("id", r.get("marked_done_by","")).execute()
                            done_by     = (done_by_res.data or [{}])[0].get("full_name","—") if r.get("marked_done_by") else "—"
                            done_at     = format_date(r.get("marked_done_at"))
                            st.success(f"✅ Marked as done by **{done_by}** on {done_at}. Will reset on next review date.")
                            if st.button("↩️ Unmark", key=f"unmark_{r['id']}"):
                                sb.table("requirements").update({
                                    "marked_done":    False,
                                    "marked_done_at": None,
                                    "marked_done_by": None,
                                }).eq("id", r["id"]).execute()
                                st.rerun()
                        else:
                            if st.button("✅ Mark as Done", key=f"done_{r['id']}", help="No document needed — mark this requirement as fulfilled"):
                                profile = get_profile()
                                sb.table("requirements").update({
                                    "marked_done":    True,
                                    "marked_done_at": today.isoformat(),
                                    "marked_done_by": profile["id"],
                                }).eq("id", r["id"]).execute()
                                st.rerun()

                    # Attached documents
                    if docs:
                        st.markdown("**📎 Attached Documents:**")
                        for d in docs:
                            col_a, col_b = st.columns([5, 1])
                            with col_a:
                                label = f"[{d.get('doc_code','') or ''} {d.get('file_name','')}]({d.get('file_url','#')}) — *{d.get('doc_type','') or ''}*"
                                if d.get("version"):
                                    label += f" v{d['version']}"
                                st.markdown(label)
                            with col_b:
                                if can_write():
                                    if st.button("🗑️", key=f"del_{d['id']}", help="Remove document"):
                                        sb.table("requirement_documents").delete().eq("id", d["id"]).execute()
                                        st.rerun()
                    else:
                        st.info("No documents uploaded yet.")

                    # Edit + upload form
                    if can_write():
                        st.markdown("---")
                        with st.form(f"req_{r['id']}"):
                            fc1, fc2 = st.columns(2)
                            with fc1:
                                owner_opts  = users_options()
                                current_key = next((k for k, v in owner_opts.items() if v == r.get("owner_id")), list(owner_opts.keys())[0])
                                new_owner   = st.selectbox("Assign Owner", list(owner_opts.keys()),
                                    index=list(owner_opts.keys()).index(current_key), key=f"own_{r['id']}")
                                review_freq = st.selectbox("Review Frequency", REVIEW_OPTIONS,
                                    index=REVIEW_OPTIONS.index(r.get("review_frequency","Annual") or "Annual"),
                                    key=f"freq_{r['id']}")
                            with fc2:
                                last_reviewed = st.date_input("Last Reviewed",
                                    value=date.fromisoformat(r["last_reviewed"]) if r.get("last_reviewed") else None,
                                    key=f"lr_{r['id']}")
                                notes = st.text_input("Notes", value=r.get("notes","") or "", key=f"notes_{r['id']}")

                            st.markdown("**Upload Document**")
                            uc1, uc2, uc3 = st.columns(3)
                            with uc1:
                                doc_type = st.selectbox("Type", DOC_TYPES, key=f"dt_{r['id']}")
                                doc_code = st.text_input("Code", placeholder="e.g. QP-001", key=f"dc_{r['id']}")
                            with uc2:
                                doc_ver = st.text_input("Version", placeholder="v1.0", key=f"dv_{r['id']}")
                            with uc3:
                                ufile = st.file_uploader("Choose any file", key=f"uf_{r['id']}")

                            save = st.form_submit_button("💾 Save", use_container_width=True)

                        if save:
                            freq_days = {"Monthly": 30, "Quarterly": 90, "Bi-Annual": 180, "Annual": 365, "None": None}
                            days         = freq_days.get(review_freq)
                            next_due     = (last_reviewed + timedelta(days=days)) if (last_reviewed and days) else None
                            new_owner_id = owner_opts.get(new_owner)
                            update_payload = {
                                "review_frequency": review_freq,
                                "last_reviewed":    last_reviewed.isoformat() if last_reviewed else None,
                                "next_review_due":  next_due.isoformat() if next_due else None,
                                "notes":            notes or None,
                            }
                            if new_owner_id:
                                update_payload["owner_id"] = new_owner_id
                            try:
                                sb.table("requirements").update(update_payload).eq("id", r["id"]).execute()
                                if ufile:
                                    profile         = get_profile()
                                    file_bytes      = ufile.read()
                                    std_code_upload = r.get("standard", "general")
                                    storage_path    = f"{std_code_upload}/{r['clause_number']}/{ufile.name}"
                                    import mimetypes
                                    mime_type, _ = mimetypes.guess_type(ufile.name)
                                    mime_type = mime_type or "application/octet-stream"
                                    sb.storage.from_("requirements").upload(storage_path, file_bytes, {"content-type": mime_type, "upsert": "true"})
                                    file_url = sb.storage.from_("requirements").get_public_url(storage_path)
                                    sb.table("requirement_documents").insert({
                                        "requirement_id": r["id"],
                                        "file_name":      ufile.name,
                                        "file_url":       file_url,
                                        "doc_type":       doc_type,
                                        "doc_code":       doc_code or None,
                                        "version":        doc_ver or None,
                                        "uploaded_by":    profile["id"] if profile else None,
                                    }).execute()
                                st.success("✅ Saved.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error saving: {e}")

    # ══════════════════════════════════════════════════════════
    # TAB 2 — ADD NEW REQUIREMENT
    # ══════════════════════════════════════════════════════════
    with tab_add:
        if not can_write():
            st.info("View-only access.")
        else:
            st.markdown("#### Add Custom Requirement")
            with st.form("add_req", clear_on_submit=True):
                c1, c2 = st.columns(2)
                with c1:
                    new_std    = st.selectbox("Standard", list(STANDARDS.keys()), key="add_std")
                    new_clause = st.text_input("Clause Number *", placeholder="e.g. PR-10")
                    new_title  = st.text_input("Clause Title *", placeholder="e.g. My Process")
                with c2:
                    new_freq  = st.selectbox("Review Frequency", REVIEW_OPTIONS)
                    owner_opts = users_options()
                    new_owner  = st.selectbox("Owner", list(owner_opts.keys()))
                    new_depts  = st.multiselect("Departments", [d["name"] for d in depts])

                new_desc  = st.text_area("Description", height=100)
                new_notes = st.text_input("Notes")

                submitted = st.form_submit_button("➕ Add Requirement")

            if submitted:
                if not new_clause or not new_title:
                    st.error("Clause number and title are required.")
                else:
                    try:
                        res = sb.table("requirements").insert({
                            "standard":         STANDARDS.get(new_std) or "ISO9001",
                            "clause_number":    new_clause,
                            "clause_title":     new_title,
                            "description":      new_desc or None,
                            "review_frequency": new_freq,
                            "owner_id":         owner_opts[new_owner],
                            "notes":            new_notes or None,
                        }).execute()
                        new_id = res.data[0]["id"]
                        for dn in new_depts:
                            did = dept_map.get(dn)
                            if did:
                                sb.table("requirement_departments").insert({
                                    "requirement_id": new_id,
                                    "department_id":  did,
                                }).execute()
                        st.success(f"✅ Requirement {new_clause} added.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

    # ══════════════════════════════════════════════════════════
    # TAB 3 — MANAGE / EDIT / DELETE
    # ══════════════════════════════════════════════════════════
    with tab_manage:
        if not can_write():
            st.info("View-only access.")
        else:
            st.markdown("#### Edit or Delete Requirements")

            col_std, col_dept = st.columns(2)
            with col_std:
                std_m  = st.selectbox("Standard", list(STANDARDS.keys()), key="mgmt_std")
                code_m = STANDARDS[std_m]
            with col_dept:
                dept_m = st.selectbox("Department", dept_options, key="mgmt_dept")

            query_m = sb.table("requirements").select("*")
            if code_m:
                query_m = query_m.eq("standard", code_m)
            reqs_m = query_m.order("clause_number").execute().data or []

            # Filter by department if selected
            if dept_m != "All Departments" and reqs_m:
                did_m     = dept_map.get(dept_m)
                rids_m    = [r["id"] for r in reqs_m]
                links_m   = sb.table("requirement_departments").select("requirement_id").eq("department_id", did_m).in_("requirement_id", rids_m).execute().data or []
                linked_ids = {l["requirement_id"] for l in links_m}
                reqs_m    = [r for r in reqs_m if r["id"] in linked_ids]

            if not reqs_m:
                st.info("No requirements found.")
            else:
                opts_m    = {f"{r['clause_number']} — {r['clause_title']}": r for r in reqs_m}
                sel_label = st.selectbox("Select requirement to edit / delete", list(opts_m.keys()))
                sel_r     = opts_m[sel_label]

                # Current departments for this requirement
                cur_links = sb.table("requirement_departments").select("department_id").eq("requirement_id", sel_r["id"]).execute().data or []
                cur_dept_ids   = [l["department_id"] for l in cur_links]
                cur_dept_names = [dept_id_map.get(did,"") for did in cur_dept_ids]

                with st.form("edit_req"):
                    ec1, ec2 = st.columns(2)
                    with ec1:
                        e_clause = st.text_input("Clause Number", value=sel_r["clause_number"])
                        e_title  = st.text_input("Clause Title",  value=sel_r["clause_title"])
                        e_freq   = st.selectbox("Review Frequency", REVIEW_OPTIONS,
                            index=REVIEW_OPTIONS.index(sel_r.get("review_frequency","Annual") or "Annual"))
                        e_depts  = st.multiselect("Departments", [d["name"] for d in depts], default=cur_dept_names)
                    with ec2:
                        e_desc  = st.text_area("Description", value=sel_r.get("description","") or "", height=100)
                        e_notes = st.text_input("Notes", value=sel_r.get("notes","") or "")

                    col_save, col_del = st.columns(2)
                    with col_save:
                        save_edit  = st.form_submit_button("💾 Save Changes",       use_container_width=True)
                    with col_del:
                        delete_req = st.form_submit_button("🗑️ Delete Requirement",  use_container_width=True)

                if save_edit:
                    try:
                        sb.table("requirements").update({
                            "clause_number":    e_clause,
                            "clause_title":     e_title,
                            "description":      e_desc or None,
                            "review_frequency": e_freq,
                            "notes":            e_notes or None,
                        }).eq("id", sel_r["id"]).execute()

                        # Update department links — delete all then reinsert
                        sb.table("requirement_departments").delete().eq("requirement_id", sel_r["id"]).execute()
                        for dn in e_depts:
                            did = dept_map.get(dn)
                            if did:
                                sb.table("requirement_departments").insert({
                                    "requirement_id": sel_r["id"],
                                    "department_id":  did,
                                }).execute()

                        st.success("✅ Requirement updated.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

                if delete_req:
                    if role not in ("admin", "quality_manager"):
                        st.error("Only admins and quality managers can delete requirements.")
                    else:
                        try:
                            sb.table("requirement_departments").delete().eq("requirement_id", sel_r["id"]).execute()
                            sb.table("requirement_documents").delete().eq("requirement_id",  sel_r["id"]).execute()
                            sb.table("requirements").delete().eq("id", sel_r["id"]).execute()
                            st.success("✅ Requirement deleted.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error: {e}")
