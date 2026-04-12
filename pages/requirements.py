import streamlit as st
import pandas as pd
from datetime import date, timedelta
from utils.auth import require_auth, can_write, get_profile
from utils.supabase_client import get_supabase
from utils.helpers import users_options, format_date

STANDARDS = {
    "ISO 9001":  "ISO9001",
    "ISO 14001": "ISO14001",
    "ISO 45001": "ISO45001",
    "BRCGS":     "BRCGS",
}

DOC_TYPES = ["Form", "Work Instruction", "Process", "Procedure", "Policy", "Record", "Other"]

REVIEW_OPTIONS = ["None", "Monthly", "Quarterly", "Bi-Annual", "Annual"]


def show():
    require_auth()
    sb  = get_supabase()
    st.title("📘 Requirements Register")
    st.caption("ISO 9001 · ISO 14001 · ISO 45001 · BRCGS — document availability and review tracker")

    # ── FILTERS ───────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns([1.2, 1, 1, 1.5])
    with c1:
        std_label  = st.selectbox("Standard", list(STANDARDS.keys()), key="req_std")
        std_code   = STANDARDS[std_label]
    with c2:
        avail_filter = st.selectbox("Availability", ["All", "✅ Available", "❌ Missing"], key="req_avail")
    with c3:
        review_filter = st.selectbox("Review Due", ["All", "Overdue", "Due this month", "Up to date"], key="req_review")
    with c4:
        search = st.text_input("🔍 Search clause / keyword", key="req_search")

    # ── FETCH REQUIREMENTS ────────────────────────────────────
    res = sb.table("requirements").select(
        "*, profiles(full_name)"
    ).eq("standard", std_code).order("clause_number").execute()
    reqs = res.data or []

    # Fetch all documents for this standard in one call
    req_ids = [r["id"] for r in reqs]
    docs_map = {}
    if req_ids:
        docs_res = sb.table("requirement_documents").select("*").in_("requirement_id", req_ids).execute()
        for d in (docs_res.data or []):
            docs_map.setdefault(d["requirement_id"], []).append(d)

    # Apply filters
    today = date.today()
    filtered = []
    for r in reqs:
        has_docs = bool(docs_map.get(r["id"]))
        nrd = r.get("next_review_due")
        nrd_date = date.fromisoformat(nrd) if nrd else None

        if avail_filter == "✅ Available" and not has_docs:
            continue
        if avail_filter == "❌ Missing" and has_docs:
            continue

        if review_filter == "Overdue" and (not nrd_date or nrd_date >= today):
            continue
        if review_filter == "Due this month" and (not nrd_date or not (today <= nrd_date <= today + timedelta(days=30))):
            continue
        if review_filter == "Up to date" and nrd_date and nrd_date < today:
            continue

        if search:
            s = search.lower()
            if s not in (r.get("clause_number","") or "").lower() and \
               s not in (r.get("clause_title","") or "").lower() and \
               s not in (r.get("description","") or "").lower():
                continue

        filtered.append((r, has_docs, nrd_date))

    # ── SUMMARY METRICS ───────────────────────────────────────
    total     = len(filtered)
    available = sum(1 for _, h, _ in filtered if h)
    missing   = total - available
    overdue_r = sum(1 for _, _, nd in filtered if nd and nd < today)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Clauses", total)
    m2.metric("✅ Documents Available", available)
    m3.metric("❌ Documents Missing", missing)
    m4.metric("🔴 Reviews Overdue", overdue_r)

    # Review due soon banner
    due_soon = [(r, nd) for r, _, nd in filtered if nd and today <= nd <= today + timedelta(days=7)]
    if due_soon:
        clauses = ", ".join(r["clause_number"] for r, _ in due_soon)
        st.warning(f"⏰ {len(due_soon)} clause(s) due for review within 7 days: **{clauses}**")

    st.markdown("---")

    if not filtered:
        st.info("No clauses match your filters.")
        return

    # ── CLAUSE CARDS ─────────────────────────────────────────
    for r, has_docs, nrd_date in filtered:
        docs      = docs_map.get(r["id"], [])
        owner     = (r.get("profiles") or {}).get("full_name", "—")
        status_icon = "✅" if has_docs else "❌"
        review_str  = format_date(nrd_date) if nrd_date else "—"
        overdue_flag = " 🔴" if nrd_date and nrd_date < today else ""

        title = f"{status_icon} **{r['clause_number']}** — {r['clause_title']}  |  Owner: {owner}  |  Next Review: {review_str}{overdue_flag}"

        with st.expander(title, expanded=False):
            # Description
            st.markdown(f"_{r.get('description','—')}_")

            # Metadata row
            mc1, mc2, mc3 = st.columns(3)
            with mc1:
                st.markdown(f"**Standard:** {std_label}")
                st.markdown(f"**Review Frequency:** {r.get('review_frequency','—') or '—'}")
            with mc2:
                st.markdown(f"**Last Reviewed:** {format_date(r.get('last_reviewed'))}")
                st.markdown(f"**Next Review Due:** {review_str}{overdue_flag}")
            with mc3:
                st.markdown(f"**Owner:** {owner}")
                st.markdown(f"**Notes:** {r.get('notes','—') or '—'}")

            # Existing documents
            if docs:
                st.markdown("**📎 Documents:**")
                for d in docs:
                    col_a, col_b = st.columns([4, 1])
                    with col_a:
                        st.markdown(
                            f"[{d.get('doc_code','') or ''} {d.get('file_name','')}]({d.get('file_url','#')}) "
                            f"— *{d.get('doc_type','') or ''}* "
                            f"{'v' + d['version'] if d.get('version') else ''}"
                        )
                    with col_b:
                        if can_write():
                            if st.button("🗑️", key=f"del_doc_{d['id']}", help="Remove document"):
                                sb.table("requirement_documents").delete().eq("id", d["id"]).execute()
                                st.rerun()
            else:
                st.info("No documents uploaded yet for this clause.")

            # Upload + edit form
            if can_write():
                st.markdown("---")
                with st.form(f"req_form_{r['id']}"):
                    fc1, fc2 = st.columns(2)
                    with fc1:
                        owner_opts  = users_options()
                        current_key = next(
                            (k for k, v in owner_opts.items() if v == r.get("owner_id")),
                            list(owner_opts.keys())[0]
                        )
                        new_owner = st.selectbox(
                            "Assign Owner",
                            list(owner_opts.keys()),
                            index=list(owner_opts.keys()).index(current_key),
                            key=f"owner_{r['id']}"
                        )
                        review_freq = st.selectbox(
                            "Review Frequency",
                            REVIEW_OPTIONS,
                            index=REVIEW_OPTIONS.index(r.get("review_frequency","Annual") or "Annual"),
                            key=f"freq_{r['id']}"
                        )
                    with fc2:
                        last_reviewed = st.date_input(
                            "Last Reviewed Date",
                            value=date.fromisoformat(r["last_reviewed"]) if r.get("last_reviewed") else None,
                            key=f"lr_{r['id']}"
                        )
                        notes = st.text_input(
                            "Notes",
                            value=r.get("notes","") or "",
                            key=f"notes_{r['id']}"
                        )

                    # Document upload
                    st.markdown("**Upload Document**")
                    uc1, uc2, uc3 = st.columns(3)
                    with uc1:
                        doc_type = st.selectbox("Document Type", DOC_TYPES, key=f"dtype_{r['id']}")
                        doc_code = st.text_input("Document Code", placeholder="e.g. QP-001", key=f"dcode_{r['id']}")
                    with uc2:
                        doc_version = st.text_input("Version", placeholder="e.g. v1.0", key=f"dver_{r['id']}")
                    with uc3:
                        uploaded_file = st.file_uploader(
                            "Choose file",
                            type=["pdf","docx","xlsx","png","jpg","msg","eml","txt"],
                            key=f"ufile_{r['id']}"
                        )

                    save = st.form_submit_button("💾 Save", use_container_width=True)

                if save:
                    # Calculate next review due
                    freq_days = {"Monthly":30,"Quarterly":90,"Bi-Annual":180,"Annual":365,"None":None}
                    days = freq_days.get(review_freq)
                    next_due = (last_reviewed + timedelta(days=days)) if (last_reviewed and days) else None

                    update = {
                        "owner_id":         owner_opts[new_owner],
                        "review_frequency": review_freq,
                        "last_reviewed":    last_reviewed.isoformat() if last_reviewed else None,
                        "next_review_due":  next_due.isoformat() if next_due else None,
                        "notes":            notes or None,
                    }
                    try:
                        sb.table("requirements").update(update).eq("id", r["id"]).execute()

                        if uploaded_file:
                            profile      = get_profile()
                            file_bytes   = uploaded_file.read()
                            storage_path = f"{std_code}/{r['clause_number']}/{uploaded_file.name}"
                            sb.storage.from_("requirements").upload(
                                storage_path, file_bytes,
                                {"upsert": "true"}
                            )
                            file_url = sb.storage.from_("requirements").get_public_url(storage_path)
                            sb.table("requirement_documents").insert({
                                "requirement_id": r["id"],
                                "file_name":      uploaded_file.name,
                                "file_url":       file_url,
                                "doc_type":       doc_type,
                                "doc_code":       doc_code or None,
                                "version":        doc_version or None,
                                "uploaded_by":    profile["id"],
                            }).execute()

                        st.success("✅ Saved.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")
