import streamlit as st
import pandas as pd
from datetime import date
from io import BytesIO
from utils.auth import require_auth, can_write, get_profile
from utils.supabase_client import get_supabase
from utils.helpers import users_options, status_badge, format_date
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

AUDIT_TYPES = ["ISO 9001", "ISO 14001", "ISO 45001", "BRCGS", "QMS Internal Audit"]
AUDIT_TYPE_MAP = {
    "ISO 9001":           "ISO9001",
    "ISO 14001":          "ISO14001",
    "ISO 45001":          "ISO45001",
    "BRCGS":              "BRCGS",
    "QMS Internal Audit": "QMS",
}
AUDIT_TYPE_REVERSE = {v: k for k, v in AUDIT_TYPE_MAP.items()}
STATUSES = ["open", "in_progress", "closed", "overdue"]

STATUS_COLOR = {
    "open":        "🟡",
    "in_progress": "🔵",
    "closed":      "🟢",
    "overdue":     "🔴",
}



def generate_pdf(findings: list, filters_desc: str) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        rightMargin=1*cm, leftMargin=1*cm,
        topMargin=1.5*cm, bottomMargin=1*cm
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", fontSize=14, fontName="Helvetica-Bold", spaceAfter=4)
    sub_style   = ParagraphStyle("sub",   fontSize=9,  fontName="Helvetica",      spaceAfter=10, textColor=colors.grey)
    cell_style  = ParagraphStyle("cell",  fontSize=7.5,fontName="Helvetica",      leading=10)
    head_style  = ParagraphStyle("head",  fontSize=8,  fontName="Helvetica-Bold", textColor=colors.white)

    STATUS_COLORS = {
        "open":        colors.HexColor("#FFC107"),
        "in_progress": colors.HexColor("#2196F3"),
        "closed":      colors.HexColor("#4CAF50"),
        "overdue":     colors.HexColor("#F44336"),
    }

    AUDIT_REVERSE = {
        "ISO9001": "ISO 9001", "ISO14001": "ISO 14001",
        "ISO45001": "ISO 45001", "BRCGS": "BRCGS", "QMS": "QMS Internal Audit"
    }

    elements = []
    elements.append(Paragraph("Easternpak Quality Hub — NC/CAPA Findings Report", title_style))
    elements.append(Paragraph(f"Generated: {date.today().strftime('%d %b %Y')} | Filters: {filters_desc}", sub_style))

    # Table header
    headers = ["Ref", "Audit Type", "Clause", "Non-Conformity Details", "Root Cause",
               "Correction", "Preventive Action", "Owner", "Target Date", "Status", "Remarks"]
    col_widths = [1.5*cm, 2.2*cm, 1.8*cm, 6*cm, 4.5*cm, 4*cm, 4*cm, 2.5*cm, 2*cm, 2*cm, 3*cm]

    data = [[Paragraph(h, head_style) for h in headers]]

    row_colors = []
    for i, f in enumerate(findings):
        status = f.get("status","open")
        owner  = (f.get("profiles") or {}).get("full_name","—")
        row = [
            Paragraph(f.get("finding_ref","—") or "—", cell_style),
            Paragraph(AUDIT_REVERSE.get(f.get("audit_type",""),"—"), cell_style),
            Paragraph(f.get("clause_ref","—") or "—", cell_style),
            Paragraph(f.get("details","—") or "—", cell_style),
            Paragraph(f.get("root_cause","—") or "—", cell_style),
            Paragraph(f.get("correction","—") or "—", cell_style),
            Paragraph(f.get("preventive_action","—") or "—", cell_style),
            Paragraph(owner, cell_style),
            Paragraph(format_date(f.get("target_date")), cell_style),
            Paragraph(status.replace("_"," ").title(), cell_style),
            Paragraph(f.get("evidence_notes","—") or "—", cell_style),
        ]
        data.append(row)
        row_colors.append(STATUS_COLORS.get(status, colors.white))

    table = Table(data, colWidths=col_widths, repeatRows=1)

    style_cmds = [
        ("BACKGROUND",    (0,0), (-1,0),  colors.HexColor("#0f1c2e")),
        ("TEXTCOLOR",     (0,0), (-1,0),  colors.white),
        ("FONTNAME",      (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,-1), 7.5),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [colors.HexColor("#F9F9F9"), colors.white]),
        ("GRID",          (0,0), (-1,-1), 0.4, colors.HexColor("#DDDDDD")),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING",   (0,0), (-1,-1), 4),
        ("RIGHTPADDING",  (0,0), (-1,-1), 4),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]

    # Color status column per row
    for i, color in enumerate(row_colors):
        style_cmds.append(("BACKGROUND", (9, i+1), (9, i+1), color))
        style_cmds.append(("TEXTCOLOR",  (9, i+1), (9, i+1), colors.white))

    table.setStyle(TableStyle(style_cmds))
    elements.append(table)

    doc.build(elements)
    buf.seek(0)
    return buf.getvalue()

def show():
    require_auth()
    sb = get_supabase()

    st.title("📋 NC / CAPA — Findings Tracker")
    st.caption("All audit findings in one place — ISO 9001 · ISO 14001 · ISO 45001 · BRCGS · QMS Internal Audit")

    # ── Update schema to support new audit types ──────────────
    # (handled via check constraint update in SQL — see below)

    # ── TOP FILTERS ───────────────────────────────────────────
    with st.container():
        c1, c2, c3, c4 = st.columns([1.2, 1.2, 1, 1])
        with c1:
            audit_filter = st.multiselect(
                "Audit Type",
                options=list(AUDIT_TYPE_MAP.keys()),
                default=list(AUDIT_TYPE_MAP.keys()),
                key="nc_audit_filter"
            )
        with c2:
            status_filter = st.multiselect(
                "Status",
                options=STATUSES,
                default=["open", "in_progress", "overdue"],
                key="nc_status_filter"
            )
        with c3:
            search = st.text_input("🔍 Search", placeholder="keyword in details...", key="nc_search")
        with c4:
            show_closed = st.toggle("Include Closed", value=False, key="nc_show_closed")

    # ── FETCH ALL FINDINGS ────────────────────────────────────
    query = sb.table("nc_findings").select(
        "*, profiles!nc_findings_action_owner_id_fkey(full_name)"
    ).order("target_date")

    audit_codes = [AUDIT_TYPE_MAP[a] for a in audit_filter]
    if audit_codes:
        query = query.in_("audit_type", audit_codes)

    if not show_closed:
        status_filter_active = [s for s in status_filter if s != "closed"]
    else:
        status_filter_active = status_filter

    if status_filter_active:
        query = query.in_("status", status_filter_active)

    res = query.execute()
    findings = res.data or []

    # Search filter
    if search:
        s = search.lower()
        findings = [f for f in findings if
                    s in (f.get("details","") or "").lower() or
                    s in (f.get("finding_ref","") or "").lower() or
                    s in (f.get("clause_ref","") or "").lower() or
                    s in (f.get("root_cause","") or "").lower()]

    # ── SUMMARY ROW ───────────────────────────────────────────
    total    = len(findings)
    overdue  = sum(1 for f in findings if f.get("target_date") and
                   date.fromisoformat(f["target_date"]) < date.today() and
                   f.get("status") != "closed")
    open_c   = sum(1 for f in findings if f.get("status") == "open")
    inprog   = sum(1 for f in findings if f.get("status") == "in_progress")
    closed_c = sum(1 for f in findings if f.get("status") == "closed")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total Shown", total)
    m2.metric("🟡 Open", open_c)
    m3.metric("🔵 In Progress", inprog)
    m4.metric("🟢 Closed", closed_c)
    m5.metric("🔴 Overdue", overdue)

    # ── EXPORT BUTTONS ───────────────────────────────────────
    ex1, ex2, _ = st.columns([1, 1, 4])
    with ex1:
        if findings:
            pdf_bytes = generate_pdf(findings,
                f"Audit: {', '.join(audit_filter)} | Status: {', '.join(status_filter_active)}")
            st.download_button(
                "📄 Export PDF",
                data=pdf_bytes,
                file_name=f"NC_CAPA_Report_{date.today()}.pdf",
                mime="application/pdf",
                key="export_pdf"
            )
    with ex2:
        if findings:
            rows = []
            for f in findings:
                owner = (f.get("profiles") or {}).get("full_name","—")
                rows.append({
                    "Ref":               f.get("finding_ref","—"),
                    "Audit Type":        f.get("audit_type",""),
                    "Clause":            f.get("clause_ref","—"),
                    "Details":           f.get("details",""),
                    "Root Cause":        f.get("root_cause",""),
                    "Correction":        f.get("correction",""),
                    "Preventive Action": f.get("preventive_action",""),
                    "Owner":             owner,
                    "Target Date":       format_date(f.get("target_date")),
                    "Closing Date":      format_date(f.get("closing_date")),
                    "Status":            f.get("status",""),
                    "Remarks":           f.get("evidence_notes",""),
                })
            from io import BytesIO as _BytesIO
            import openpyxl as _xl
            buf = _BytesIO()
            df_exp = pd.DataFrame(rows)
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                df_exp.to_excel(writer, index=False, sheet_name="NC CAPA")
            buf.seek(0)
            st.download_button(
                "📊 Export Excel",
                data=buf,
                file_name=f"NC_CAPA_{date.today()}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="export_excel"
            )

    st.markdown("---")

    # ── ADD NEW FINDING (collapsible) ─────────────────────────
    with st.expander("➕ Log New Finding", expanded=False):
        if not can_write():
            st.info("View-only access.")
        else:
            with st.form("new_finding", clear_on_submit=True):
                c1, c2, c3 = st.columns(3)
                with c1:
                    audit_type_label = st.selectbox("Audit Type *", AUDIT_TYPES)
                    audit_ref        = st.text_input("Audit Name", placeholder="e.g. BRCGS Annual Audit 2025")
                    clause_ref       = st.text_input("Clause / Requirement Ref", placeholder="e.g. 3.5.2")
                with c2:
                    owner_opts  = users_options()
                    owner_label = st.selectbox("Action Owner *", list(owner_opts.keys()))
                    target_date = st.date_input("Target Closure Date", value=None)
                    status      = st.selectbox("Status", ["open", "in_progress"])
                with c3:
                    evidence_notes = st.text_area("Remarks / Notes", height=122)

                details           = st.text_area("Details of Non-Conformity *", height=80)
                root_cause        = st.text_area("Root Cause Analysis", height=70)
                correction        = st.text_area("Immediate Correction", height=70)
                preventive_action = st.text_area("Preventive Action Plan", height=70)

                uploaded_file = st.file_uploader(
                    "Attach Evidence",
                    type=["pdf","png","jpg","jpeg","docx","xlsx"]
                )

                submitted = st.form_submit_button("💾 Save Finding", use_container_width=True)

            if submitted:
                if not details:
                    st.error("Details of Non-Conformity is required.")
                else:
                    profile    = get_profile()
                    audit_code = AUDIT_TYPE_MAP[audit_type_label]
                    count_res  = sb.table("nc_findings").select("id", count="exact").eq("audit_type", audit_code).execute()
                    count      = (count_res.count or 0) + 1
                    prefix_map = {"ISO9001":"ISO","ISO14001":"ISO14","ISO45001":"ISO45","BRCGS":"BRC","QMS":"QMS"}
                    prefix     = prefix_map.get(audit_code, "NC")
                    finding_ref = f"{prefix}-{str(count).zfill(3)}"

                    payload = {
                        "audit_type":        audit_code,
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
                        new_id     = insert_res.data[0]["id"]

                        if uploaded_file:
                            file_bytes   = uploaded_file.read()
                            storage_path = f"evidence/{new_id}/{uploaded_file.name}"
                            try:
                                sb.storage.from_("evidence").upload(storage_path, file_bytes)
                                file_url = sb.storage.from_("evidence").get_public_url(storage_path)
                                sb.table("nc_evidence").insert({
                                    "finding_id":  new_id,
                                    "file_name":   uploaded_file.name,
                                    "file_url":    file_url,
                                    "uploaded_by": profile["id"],
                                }).execute()
                            except Exception as fe:
                                st.warning(f"Finding saved but file upload failed: {fe}")

                        st.success(f"✅ Finding {finding_ref} saved.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

    # ── FINDINGS LIST ─────────────────────────────────────────
    if not findings:
        st.info("No findings match your filters.")
    else:
        for f in findings:
            owner      = (f.get("profiles") or {}).get("full_name", "—")
            target     = f.get("target_date")
            is_overdue = target and date.fromisoformat(target) < date.today() and f.get("status") != "closed"
            status_val = f.get("status","open")
            emoji      = STATUS_COLOR.get(status_val,"⚪")
            audit_label = AUDIT_TYPE_REVERSE.get(f.get("audit_type",""), f.get("audit_type",""))

            # Expander title — compact but informative
            expander_title = (
                f"{emoji} **{f.get('finding_ref','—')}** · {audit_label} · "
                f"{f.get('clause_ref','') or ''} · "
                f"Owner: {owner} · "
                f"Due: {format_date(target)}"
                + (" 🔴 OVERDUE" if is_overdue else "")
            )

            with st.expander(expander_title, expanded=False):
                # Details (read-only)
                st.markdown(f"**Audit:** {f.get('audit_ref','—')}")
                st.info(f.get("details","—"))

                col_l, col_r = st.columns(2)
                with col_l:
                    st.markdown(f"**Root Cause:**")
                    st.markdown(f.get("root_cause","—") or "—")
                    st.markdown(f"**Correction:**")
                    st.markdown(f.get("correction","—") or "—")
                with col_r:
                    st.markdown(f"**Preventive Action:**")
                    st.markdown(f.get("preventive_action","—") or "—")
                    st.markdown(f"**Remarks:**")
                    st.markdown(f.get("evidence_notes","—") or "—")

                # Evidence files
                try:
                    evidence = sb.table("nc_evidence").select("*").eq("finding_id", f["id"]).execute()
                    if evidence.data:
                        st.markdown("**📎 Attached Evidence:**")
                        for ev in evidence.data:
                            st.markdown(f"[{ev.get('file_name','File')}]({ev.get('file_url','#')})")
                except:
                    pass

                # Inline update form (only for non-closed or write access)
                if can_write():
                    st.markdown("---")
                    with st.form(f"update_{f['id']}"):
                        uc1, uc2, uc3 = st.columns(3)
                        with uc1:
                            new_status = st.selectbox(
                                "Status",
                                STATUSES,
                                index=STATUSES.index(status_val),
                                key=f"status_{f['id']}"
                            )
                        with uc2:
                            owner_opts   = users_options(include_blank=False)
                            current_key  = next(
                                (k for k, v in owner_opts.items() if v == f.get("action_owner_id")),
                                list(owner_opts.keys())[0]
                            )
                            new_owner = st.selectbox(
                                "Action Owner",
                                list(owner_opts.keys()),
                                index=list(owner_opts.keys()).index(current_key),
                                key=f"owner_{f['id']}"
                            )
                        with uc3:
                            new_target = st.date_input(
                                "Target Date",
                                value=date.fromisoformat(target) if target else None,
                                key=f"target_{f['id']}"
                            )
                            closing_date = st.date_input(
                                "Closing Date",
                                value=date.fromisoformat(f["closing_date"]) if f.get("closing_date") else None,
                                key=f"closing_{f['id']}"
                            )

                        new_rc  = st.text_area("Root Cause", value=f.get("root_cause","") or "", height=60, key=f"rc_{f['id']}")
                        new_cor = st.text_area("Correction", value=f.get("correction","") or "", height=60, key=f"cor_{f['id']}")
                        new_pa  = st.text_area("Preventive Action", value=f.get("preventive_action","") or "", height=60, key=f"pa_{f['id']}")
                        new_ev  = st.text_area("Remarks / Notes", value=f.get("evidence_notes","") or "", height=50, key=f"ev_{f['id']}")

                        new_file = st.file_uploader(
                            "Attach Evidence",
                            type=["pdf","png","jpg","jpeg","docx","xlsx"],
                            key=f"file_{f['id']}"
                        )

                        save = st.form_submit_button("💾 Save Updates", use_container_width=True)

                    if save:
                        update = {
                            "status":            new_status,
                            "action_owner_id":   owner_opts[new_owner],
                            "target_date":       new_target.isoformat() if new_target else None,
                            "root_cause":        new_rc or None,
                            "correction":        new_cor or None,
                            "preventive_action": new_pa or None,
                            "evidence_notes":    new_ev or None,
                        }
                        if new_status == "closed" and closing_date:
                            update["closing_date"] = closing_date.isoformat()

                        try:
                            sb.table("nc_findings").update(update).eq("id", f["id"]).execute()

                            if new_file:
                                profile      = get_profile()
                                file_bytes   = new_file.read()
                                storage_path = f"evidence/{f['id']}/{new_file.name}"
                                try:
                                    sb.storage.from_("evidence").upload(storage_path, file_bytes)
                                    file_url = sb.storage.from_("evidence").get_public_url(storage_path)
                                    sb.table("nc_evidence").insert({
                                        "finding_id":  f["id"],
                                        "file_name":   new_file.name,
                                        "file_url":    file_url,
                                        "uploaded_by": profile["id"],
                                    }).execute()
                                except Exception as fe:
                                    st.warning(f"Updated but file upload failed: {fe}")

                            st.success("✅ Updated.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error: {e}")
