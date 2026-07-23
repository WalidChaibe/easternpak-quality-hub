import streamlit as st
import pandas as pd
from datetime import date
import base64
import requests
import io
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, HRFlowable, PageBreak
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import KeepTogether

from utils.auth import require_auth, can_write, get_profile
from utils.supabase_client import get_supabase

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────
LOGO_PATH = "static/napco_logo.png"

DEPT_MAP = {
    "SC — Supply Chain":         {"code": "SC",  "subs": ["CS","MH","AW","WH","XX","Other"]},
    "PL — Plant":                {"code": "PL",  "subs": ["MT","PD","XX","Other"]},
    "TQA — Technical & Quality": {"code": "TQA", "subs": ["QC","TQ","XX","Other"]},
    "PM — Product Management":   {"code": "PM",  "subs": ["XX","Other"]},
    "Other":                     {"code": None,  "subs": ["XX","Other"]},
}

DOC_TYPE_MAP = {
    "PD — Procedure": "PD",
    "PR — Process":   "PR",
}

SWIMLANES_DEFAULT = ["Step Owner","QC","Production","Management","HSE","Supply Chain","Other"]

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def ordinal(n):
    suffixes = {1:"st",2:"nd",3:"rd"}
    return f"{n}{suffixes.get(n%10 if n%100 not in (11,12,13) else 0,'th')}"

def revision_label(rev):
    return "1st Issue" if rev == 0 else f"{ordinal(rev)} Revision"

def build_mermaid(steps, lanes):
    if not steps:
        return ""
    lines = ["flowchart TD"]
    lane_steps = {}
    for s in steps:
        lane = s.get("swimlane","General")
        lane_steps.setdefault(lane, [])
        lane_steps[lane].append(s)
    for lane, lane_s in lane_steps.items():
        safe = lane.replace(" ","_").replace("-","_").replace("/","_")
        lines.append(f'    subgraph {safe}["{lane}"]')
        for s in lane_s:
            sid = s["id"].replace("-","")
            label = s.get("title","Step").replace('"',"'")
            shape = s.get("shape","rect")
            if shape == "diamond":
                lines.append(f'        {sid}{{{{{label}}}}}')
            elif shape == "rounded":
                lines.append(f'        {sid}([{label}])')
            else:
                lines.append(f'        {sid}[{label}]')
        lines.append("    end")
    for i, s in enumerate(steps[:-1]):
        src = steps[i]["id"].replace("-","")
        dst = steps[i+1]["id"].replace("-","")
        conn = s.get("connection_label","")
        if conn:
            lines.append(f"    {src} -->|{conn}| {dst}")
        else:
            lines.append(f"    {src} --> {dst}")
    return "\n".join(lines)

def mermaid_to_image_bytes(mermaid_code):
    """Convert mermaid code to PNG bytes via mermaid.ink API."""
    try:
        encoded = base64.urlsafe_b64encode(mermaid_code.encode()).decode()
        url = f"https://mermaid.ink/img/{encoded}?bgColor=white"
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            return resp.content
    except Exception:
        pass
    return None

def check_or_add_master(sb, code, title, doc_type, is_internal, uid):
    if not code and not title:
        return
    key = code or title
    res = sb.table("master_documents").select("id").eq("doc_code", key).execute()
    if not res.data:
        try:
            sb.table("master_documents").insert({
                "doc_code":    key,
                "title":       title or key,
                "doc_type":    doc_type,
                "is_internal": is_internal,
                "created_by":  uid,
            }).execute()
        except Exception:
            pass

# ─────────────────────────────────────────────
# PDF GENERATION
# ─────────────────────────────────────────────
def generate_pdf(doc):
    buffer = io.BytesIO()
    page_w, page_h = A4

    styles = getSampleStyleSheet()
    normal   = ParagraphStyle("normal",   fontName="Helvetica",       fontSize=9,  leading=13)
    bold     = ParagraphStyle("bold",     fontName="Helvetica-Bold",  fontSize=9,  leading=13)
    h1s      = ParagraphStyle("h1s",      fontName="Helvetica-Bold",  fontSize=13, leading=18, spaceAfter=6)
    h2s      = ParagraphStyle("h2s",      fontName="Helvetica-Bold",  fontSize=10, leading=14, spaceAfter=4)
    center   = ParagraphStyle("center",   fontName="Helvetica",       fontSize=9,  leading=13, alignment=TA_CENTER)
    center_b = ParagraphStyle("center_b", fontName="Helvetica-Bold",  fontSize=9,  leading=13, alignment=TA_CENTER)
    title_s  = ParagraphStyle("title_s",  fontName="Helvetica-Bold",  fontSize=18, leading=24, alignment=TA_CENTER)
    dept_s   = ParagraphStyle("dept_s",   fontName="Helvetica-Bold",  fontSize=13, leading=18, alignment=TA_CENTER)
    small    = ParagraphStyle("small",    fontName="Helvetica-Oblique",fontSize=7, leading=9,  alignment=TA_CENTER, textColor=colors.grey)
    numbered = ParagraphStyle("numbered", fontName="Helvetica",        fontSize=9,  leading=13, leftIndent=20)

    doc_code    = doc.get("doc_code","—")
    title       = doc.get("title","")
    dept_label  = doc.get("dept_label","") or doc.get("dept","")
    adoption    = doc.get("date_of_adoption","—")
    approvals   = doc.get("approvals") or []
    revisions   = doc.get("_revisions") or []

    NAPCO_BLUE = colors.HexColor("#0D68A3")
    LIGHT_BLUE = colors.HexColor("#D5E8F0")

    def header_footer(canvas, doc_obj):
        canvas.saveState()
        w, h = A4
        # Header
        canvas.setFillColor(colors.white)
        canvas.rect(1*cm, h-2*cm, w-2*cm, 1.4*cm, fill=1, stroke=0)
        canvas.setStrokeColor(colors.HexColor("#999999"))
        canvas.setLineWidth(0.5)
        # Header table lines
        canvas.rect(1*cm, h-2*cm, w-2*cm, 1.4*cm, fill=0, stroke=1)
        # Logo
        try:
            canvas.drawImage(LOGO_PATH, 1.1*cm, h-1.9*cm, width=2.5*cm, height=1.1*cm, preserveAspectRatio=True)
        except Exception:
            pass
        # Title cell
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(colors.black)
        mid_x = 1*cm + (w-2*cm)*0.25
        canvas.drawCentredString(mid_x + (w-2*cm)*0.25, h-1.3*cm, title.upper())
        # Control nbr label
        canvas.setFont("Helvetica-Bold", 7)
        canvas.drawString(3.8*cm, h-1.7*cm, "CONTROL NBR.")
        canvas.setFont("Helvetica", 7)
        canvas.drawString(5.5*cm, h-1.7*cm, doc_code)
        # Page
        canvas.setFont("Helvetica-Bold", 7)
        canvas.drawString(w-4*cm, h-1.3*cm, "PAGE")
        canvas.setFont("Helvetica", 7)
        canvas.drawString(w-3*cm, h-1.3*cm, f"{doc_obj.page}")

        # Footer
        canvas.setFillColor(colors.HexColor("#f5f5f5"))
        canvas.rect(1*cm, 0.5*cm, w-2*cm, 0.8*cm, fill=1, stroke=1)
        canvas.setFont("Helvetica-Oblique", 6.5)
        canvas.setFillColor(colors.grey)
        canvas.drawCentredString(w/2, 0.9*cm,
            "THE INFORMATION CONTAINED HEREIN IS PROPRIETARY TO NAPCO NATIONAL AND IT SHALL NOT BE USED, "
            "REPRODUCED OR DISCLOSED TO OTHERS EXCEPT AS SPECIFICALLY PERMITTED IN WRITING BY THE PROPRIETOR.")
        canvas.drawCentredString(w/2, 0.65*cm, '"UNCONTROLLED IF PRINTED"')
        canvas.restoreState()

    pdf = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2.5*cm, bottomMargin=2*cm,
        onFirstPage=header_footer, onLaterPages=header_footer,
    )

    story = []

    # ── COVER PAGE ──
    story.append(Spacer(1, 0.5*cm))

    # Logo large on cover
    try:
        story.append(Image(LOGO_PATH, width=4*cm, height=1.8*cm))
    except Exception:
        pass

    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph(dept_label.upper(), dept_s))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(title.upper(), title_s))
    story.append(Spacer(1, 0.8*cm))

    # Approvals table
    if approvals:
        ap_header = [Paragraph("APPROVED BY", bold)] + [Paragraph(a.get("department",""), center_b) for a in approvals]
        ap_dept   = [Paragraph("Department", bold)] + [Paragraph(a.get("department",""), center) for a in approvals]
        ap_func   = [Paragraph("Function", bold)]   + [Paragraph(a.get("function",""), center) for a in approvals]
        ap_sign   = [Paragraph("Signature", bold)]  + [Paragraph("", center) for _ in approvals]
        ap_date   = [Paragraph("Date", bold)]        + [Paragraph("", center) for _ in approvals]

        col_w = [(page_w - 4*cm) / (len(approvals) + 1)] * (len(approvals) + 1)
        ap_table = Table([ap_dept, ap_func, ap_sign, ap_date], colWidths=col_w, rowHeights=[0.7*cm, 0.7*cm, 1.5*cm, 0.7*cm])
        ap_table.setStyle(TableStyle([
            ("GRID",        (0,0), (-1,-1), 0.5, colors.grey),
            ("BACKGROUND",  (0,0), (0,-1),  LIGHT_BLUE),
            ("FONTNAME",    (0,0), (0,-1),  "Helvetica-Bold"),
            ("FONTSIZE",    (0,0), (-1,-1), 8),
            ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
            ("ALIGN",       (1,0), (-1,-1), "CENTER"),
        ]))
        story.append(ap_table)
        story.append(Spacer(1, 0.5*cm))

    # Date of adoption
    adopt_t = Table([[Paragraph("Date of Adoption", bold), Paragraph(str(adoption), center)]],
                    colWidths=[5*cm, 5*cm])
    adopt_t.setStyle(TableStyle([
        ("GRID",       (0,0),(-1,-1), 0.5, colors.grey),
        ("BACKGROUND", (0,0),(0,0),   LIGHT_BLUE),
        ("ALIGN",      (0,0),(-1,-1), "CENTER"),
        ("FONTSIZE",   (0,0),(-1,-1), 8),
        ("VALIGN",     (0,0),(-1,-1), "MIDDLE"),
    ]))
    story.append(adopt_t)
    story.append(Spacer(1, 0.6*cm))

    # Revision history on cover
    story.append(Paragraph("REVISION HISTORY", h1s))
    if revisions:
        rev_data = [[
            Paragraph("Revision", center_b),
            Paragraph("Date", center_b),
            Paragraph("Status", center_b),
            Paragraph("Description", center_b),
        ]]
        for r in revisions:
            rev_data.append([
                Paragraph(str(r.get("revision","")).zfill(2), center),
                Paragraph(str(r.get("revised_date","")), center),
                Paragraph(r.get("status",""), center),
                Paragraph(r.get("description",""), normal),
            ])
        rev_t = Table(rev_data, colWidths=[2*cm, 3*cm, 3.5*cm, None])
        rev_t.setStyle(TableStyle([
            ("GRID",       (0,0),(-1,-1), 0.5, colors.grey),
            ("BACKGROUND", (0,0),(-1,0),  NAPCO_BLUE),
            ("TEXTCOLOR",  (0,0),(-1,0),  colors.white),
            ("FONTSIZE",   (0,0),(-1,-1), 8),
            ("VALIGN",     (0,0),(-1,-1), "MIDDLE"),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, LIGHT_BLUE]),
        ]))
        story.append(rev_t)

    story.append(PageBreak())

    # ── TABLE OF CONTENTS ──
    story.append(Paragraph("Table of Contents", h1s))
    story.append(Spacer(1, 0.3*cm))
    toc_items = [
        ("1.0", "Introduction"),
        ("1.1", "Purpose"),
        ("1.2", "Policy"),
        ("1.3", "Scope of Application"),
        ("1.4", "Authorities & Responsibilities"),
        ("2.0", "Abbreviations and Definitions"),
        ("3.0", "Procedure"),
        ("4.0", "Associated Documentation"),
        ("4.1", "Related Documents"),
        ("4.2", "Resulting Records"),
        ("4.3", "Internal / External References"),
    ]
    toc_data = []
    for num, lbl in toc_items:
        indent = 20 if num.count(".") > 0 and not num.endswith(".0") else 0
        style = ParagraphStyle("toc", fontName="Helvetica", fontSize=9, leftIndent=indent)
        toc_data.append([Paragraph(num, style), Paragraph(lbl, style), Paragraph("", style)])
    toc_t = Table(toc_data, colWidths=[1.5*cm, 12*cm, 1.5*cm])
    toc_t.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("FONTSIZE",(0,0),(-1,-1),9)]))
    story.append(toc_t)
    story.append(PageBreak())

    # ── 1.0 INTRODUCTION ──
    story.append(Paragraph("1.0 Introduction", h1s))

    purpose = doc.get("purpose","")
    if purpose:
        story.append(Paragraph("1.1 Purpose", h2s))
        story.append(Paragraph(f"1.1.1 &nbsp;&nbsp; {purpose}", numbered))
        story.append(Spacer(1, 0.3*cm))

    policy = doc.get("policy") or []
    if policy:
        story.append(Paragraph("1.2 Policy", h2s))
        for i, p in enumerate(policy, 1):
            story.append(Paragraph(f"1.2.{i} &nbsp;&nbsp; {p}", numbered))
        story.append(Spacer(1, 0.3*cm))

    scope = doc.get("scope") or []
    if scope:
        story.append(Paragraph("1.3 Scope of Application", h2s))
        for i, s in enumerate(scope, 1):
            story.append(Paragraph(f"1.3.{i} &nbsp;&nbsp; {s}", numbered))
        story.append(Spacer(1, 0.3*cm))

    resp = doc.get("responsibilities") or []
    if resp:
        story.append(Paragraph("1.4 Authorities & Responsibilities", h2s))
        for i, r in enumerate(resp, 1):
            story.append(Paragraph(f"1.4.{i} &nbsp;&nbsp; {r}", numbered))
        story.append(Spacer(1, 0.3*cm))

    # ── 2.0 ABBREVIATIONS ──
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    story.append(Paragraph("2.0 Abbreviations and Definitions", h1s))
    abbrevs = doc.get("abbreviations") or []
    if abbrevs:
        ab_data = [[Paragraph("Term / Abbreviation", center_b), Paragraph("Definition", center_b)]]
        for i, ab in enumerate(abbrevs):
            bg = LIGHT_BLUE if i % 2 == 0 else colors.white
            ab_data.append([Paragraph(ab.get("term",""), bold), Paragraph(ab.get("definition",""), normal)])
        ab_t = Table(ab_data, colWidths=[5*cm, None])
        ab_t.setStyle(TableStyle([
            ("GRID",       (0,0),(-1,-1), 0.5, colors.grey),
            ("BACKGROUND", (0,0),(-1,0),  NAPCO_BLUE),
            ("TEXTCOLOR",  (0,0),(-1,0),  colors.white),
            ("FONTSIZE",   (0,0),(-1,-1), 8),
            ("VALIGN",     (0,0),(-1,-1), "MIDDLE"),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, LIGHT_BLUE]),
        ]))
        story.append(ab_t)
    story.append(Spacer(1, 0.4*cm))

    # ── 3.0 PROCEDURE ──
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    story.append(Paragraph("3.0 Procedure", h1s))

    steps = doc.get("procedure_steps") or []
    for i, step in enumerate(steps, 1):
        story.append(Paragraph(f"{i}. {step.get('title','')}", h2s))
        if step.get("text"):
            story.append(Paragraph(step["text"], numbered))
        story.append(Spacer(1, 0.2*cm))

    # Flowchart
    mermaid = doc.get("flowchart_mermaid","")
    if mermaid:
        story.append(Spacer(1, 0.3*cm))
        story.append(Paragraph("Process Flowchart", h2s))
        img_bytes = mermaid_to_image_bytes(mermaid)
        if img_bytes:
            img_buf = io.BytesIO(img_bytes)
            img = Image(img_buf, width=15*cm, height=12*cm, kind="proportional")
            story.append(img)
        else:
            story.append(Paragraph("[Flowchart could not be rendered — check internet connection]", small))
    story.append(Spacer(1, 0.4*cm))

    # ── 4.0 ASSOCIATED DOCUMENTATION ──
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    story.append(Paragraph("4.0 Associated Documentation", h1s))

    def ref_table(items, label):
        if not items:
            return
        story.append(Paragraph(label, h2s))
        rd = [[Paragraph("Code", center_b), Paragraph("Title", center_b)]]
        for i, r in enumerate(items):
            rd.append([Paragraph(r.get("code","—"), normal), Paragraph(r.get("title","—"), normal)])
        rt = Table(rd, colWidths=[5*cm, None])
        rt.setStyle(TableStyle([
            ("GRID",      (0,0),(-1,-1), 0.5, colors.grey),
            ("BACKGROUND",(0,0),(-1,0),  NAPCO_BLUE),
            ("TEXTCOLOR", (0,0),(-1,0),  colors.white),
            ("FONTSIZE",  (0,0),(-1,-1), 8),
            ("VALIGN",    (0,0),(-1,-1), "MIDDLE"),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, LIGHT_BLUE]),
        ]))
        story.append(rt)
        story.append(Spacer(1, 0.3*cm))

    ref_table(doc.get("related_docs") or [],      "4.1 Related Documents")
    ref_table(doc.get("resulting_records") or [],  "4.2 Resulting Records")
    ref_table(doc.get("ext_references") or [],     "4.3 Internal / External References")

    pdf.build(story)
    buffer.seek(0)
    return buffer.read()


# ─────────────────────────────────────────────
# MAIN PAGE
# ─────────────────────────────────────────────
def show():
    require_auth()
    sb      = get_supabase()
    profile = get_profile()
    uid     = profile["id"]

    st.title("📋 Process & Procedure Builder")
    st.caption("Create, view and manage Easternpak procedures and processes in Napco NFP format")

    tab_list, tab_new, tab_master = st.tabs([
        "📂 Document Library",
        "➕ New Document",
        "📚 Master Document List",
    ])

    # ══════════════════════════════════════════
    # TAB 1 — DOCUMENT LIBRARY
    # ══════════════════════════════════════════
    with tab_list:
        col1, col2, col3 = st.columns(3)
        with col1:
            dept_f = st.selectbox("Department", ["All","SC","PL","TQA","PM"])
        with col2:
            type_f = st.selectbox("Type", ["All","PD","PR"])
        with col3:
            search = st.text_input("🔍 Search title or code")

        q = sb.table("proc_documents").select("*").order("doc_code")
        if dept_f != "All":
            q = q.eq("dept", dept_f)
        if type_f != "All":
            q = q.eq("doc_type", type_f)
        res = q.execute()
        docs = res.data or []

        if search:
            s = search.lower()
            docs = [d for d in docs if s in (d.get("title","") or "").lower()
                    or s in (d.get("doc_code","") or "").lower()]

        if not docs:
            st.info("No documents found. Create your first one using the ➕ New Document tab.")
        else:
            for doc in docs:
                rev_label = revision_label(doc.get("revision", 0))
                with st.expander(
                    f"**{doc.get('doc_code','—')}** — {doc.get('title','')}  "
                    f"| Rev {doc.get('revision',0):02d} | {rev_label}"
                ):
                    # Fetch revisions
                    rev_res = sb.table("proc_revisions").select("*")\
                        .eq("doc_id", doc["id"]).order("revision").execute()
                    doc["_revisions"] = rev_res.data or []

                    # Download button
                    col_dl, col_sp = st.columns([2,8])
                    with col_dl:
                        if st.button("⬇️ Generate PDF", key=f"gen_{doc['id']}"):
                            with st.spinner("Generating PDF…"):
                                try:
                                    pdf_bytes = generate_pdf(doc)
                                    st.download_button(
                                        label="📥 Click to Download",
                                        data=pdf_bytes,
                                        file_name=f"{doc.get('doc_code','document')}.pdf",
                                        mime="application/pdf",
                                        key=f"dl2_{doc['id']}",
                                    )
                                except Exception as e:
                                    st.error(f"PDF error: {e}")

                    _render_document(sb, doc, uid)

    # ══════════════════════════════════════════
    # TAB 2 — NEW DOCUMENT
    # ══════════════════════════════════════════
    with tab_new:
        if not can_write():
            st.info("View-only access. Contact your administrator.")
            return

        st.markdown("### Document Identity")
        c1, c2, c3 = st.columns(3)
        with c1:
            dept_sel  = st.selectbox("Department *", list(DEPT_MAP.keys()), key="nd_dept")
            dept_info = DEPT_MAP[dept_sel]
            dept_code = dept_info["code"]
            if dept_code is None:
                dept_code = st.text_input("Department code", key="nd_dept_other").upper()
        with c2:
            sub_opts = dept_info["subs"]
            subdept_sel = st.selectbox("Sub-department *", sub_opts, key="nd_subdept")
            if subdept_sel == "Other":
                subdept_sel = st.text_input("Sub-dept code", key="nd_subdept_other").upper()
        with c3:
            type_sel = st.selectbox("Document Type *", list(DOC_TYPE_MAP.keys()), key="nd_type")
            doc_type = DOC_TYPE_MAP[type_sel]

        if dept_code and subdept_sel and doc_type:
            seq_res = sb.table("proc_documents")\
                .select("seq_number")\
                .eq("dept", dept_code)\
                .eq("subdept", subdept_sel)\
                .eq("doc_type", doc_type)\
                .order("seq_number", desc=True).limit(1).execute()
            next_seq = (seq_res.data[0]["seq_number"] + 1) if seq_res.data else 1
            preview_code = f"NFP-EP-{dept_code}-{subdept_sel}-{doc_type}-{next_seq:02d}-00"
            st.info(f"📄 Auto-generated code: **{preview_code}**")

        st.markdown("---")
        st.markdown("### Cover Page")
        c1, c2 = st.columns(2)
        with c1:
            title      = st.text_input("Document Title *", placeholder="e.g. Customer Complaint Handling Process")
            dept_label = st.text_input("Department Name (for cover)", placeholder="e.g. Supply Chain Department")
        with c2:
            adoption_dt = st.date_input("Date of Adoption", value=date.today())

        st.markdown("**Approvals (cover page)**")
        st.caption("Fill in department and function for each approver. Signature boxes are included automatically.")
        if "approvals" not in st.session_state:
            st.session_state.approvals = [
                {"department": "", "function": ""},
                {"department": "", "function": ""},
                {"department": "Top Management", "function": "Operations Manager"},
            ]
        approval_cols = st.columns(len(st.session_state.approvals))
        for i, ap in enumerate(st.session_state.approvals):
            with approval_cols[i]:
                st.session_state.approvals[i]["department"] = st.text_input(
                    f"Dept {i+1}", value=ap["department"], key=f"ap_dept_{i}")
                st.session_state.approvals[i]["function"] = st.text_input(
                    f"Function {i+1}", value=ap["function"], key=f"ap_func_{i}")
        if st.button("➕ Add approver"):
            st.session_state.approvals.append({"department":"","function":""})
            st.rerun()

        st.markdown("---")
        st.markdown("### 1.0 Introduction")
        purpose = st.text_area("1.1 Purpose", height=100,
                               placeholder="The purpose of this procedure is to…")

        st.markdown("**1.2 Policy**")
        if "policy_points" not in st.session_state:
            st.session_state.policy_points = [""]
        _edit_list("policy_points", "Policy point")

        st.markdown("**1.3 Scope of Application**")
        if "scope_points" not in st.session_state:
            st.session_state.scope_points = [""]
        _edit_list("scope_points", "Scope point")

        st.markdown("**1.4 Authorities & Responsibilities**")
        if "resp_points" not in st.session_state:
            st.session_state.resp_points = [""]
        _edit_list("resp_points", "Responsibility point")

        st.markdown("---")
        st.markdown("### 2.0 Abbreviations & Definitions")
        if "abbrevs" not in st.session_state:
            st.session_state.abbrevs = [{"term":"","definition":""}]
        for i, ab in enumerate(st.session_state.abbrevs):
            c1, c2, c3 = st.columns([2,4,1])
            with c1:
                st.session_state.abbrevs[i]["term"] = st.text_input(
                    "Term", value=ab["term"], key=f"ab_term_{i}",
                    label_visibility="collapsed", placeholder="Term")
            with c2:
                st.session_state.abbrevs[i]["definition"] = st.text_input(
                    "Def", value=ab["definition"], key=f"ab_def_{i}",
                    label_visibility="collapsed", placeholder="Definition")
            with c3:
                if st.button("🗑️", key=f"ab_del_{i}") and len(st.session_state.abbrevs) > 1:
                    st.session_state.abbrevs.pop(i)
                    st.rerun()
        if st.button("➕ Add abbreviation"):
            st.session_state.abbrevs.append({"term":"","definition":""})
            st.rerun()

        st.markdown("---")
        st.markdown("### 3.0 Procedure Steps & Flowchart")
        with st.expander("⚙️ Configure swimlanes"):
            lanes_raw = st.text_area("Swimlanes (one per line)",
                                     value="\n".join(SWIMLANES_DEFAULT), height=150, key="nd_lanes")
            lanes = [l.strip() for l in lanes_raw.split("\n") if l.strip()]

        if "proc_steps" not in st.session_state:
            st.session_state.proc_steps = []

        for i, step in enumerate(st.session_state.proc_steps):
            with st.expander(f"Step {i+1}: {step.get('title','Untitled')}", expanded=False):
                c1, c2, c3 = st.columns([3,2,1])
                with c1:
                    st.session_state.proc_steps[i]["title"] = st.text_input(
                        "Step title", value=step.get("title",""), key=f"ps_title_{i}")
                with c2:
                    idx = lanes.index(step.get("swimlane", lanes[0])) if step.get("swimlane") in lanes else 0
                    st.session_state.proc_steps[i]["swimlane"] = st.selectbox(
                        "Swimlane", lanes, key=f"ps_lane_{i}", index=idx)
                with c3:
                    shapes = ["rect","diamond","rounded"]
                    sidx = shapes.index(step.get("shape","rect")) if step.get("shape") in shapes else 0
                    st.session_state.proc_steps[i]["shape"] = st.selectbox(
                        "Shape", shapes, key=f"ps_shape_{i}", index=sidx)
                st.session_state.proc_steps[i]["text"] = st.text_area(
                    "Description", value=step.get("text",""), key=f"ps_text_{i}", height=80)
                st.session_state.proc_steps[i]["connection_label"] = st.text_input(
                    "Arrow label to next step", value=step.get("connection_label",""),
                    key=f"ps_conn_{i}", placeholder="e.g. Yes / No / Approved")
                if st.button("🗑️ Remove step", key=f"ps_del_{i}"):
                    st.session_state.proc_steps.pop(i)
                    st.rerun()

        col_add, col_prev = st.columns(2)
        with col_add:
            if st.button("➕ Add step"):
                import uuid
                st.session_state.proc_steps.append({
                    "id": str(uuid.uuid4())[:8],
                    "title":"", "text":"",
                    "swimlane": lanes[0] if lanes else "General",
                    "shape":"rect", "connection_label":"",
                })
                st.rerun()
        with col_prev:
            if st.button("👁️ Preview flowchart") and st.session_state.proc_steps:
                mermaid_code = build_mermaid(st.session_state.proc_steps, lanes)
                img_bytes = mermaid_to_image_bytes(mermaid_code)
                if img_bytes:
                    st.image(img_bytes, caption="Flowchart Preview")
                else:
                    st.warning("Could not render preview — check connection.")

        st.markdown("---")
        st.markdown("### 4.0 Associated Documentation")
        st.markdown("**4.1 Related Documents**")
        if "rel_docs" not in st.session_state:
            st.session_state.rel_docs = [{"code":"","title":""}]
        _edit_refs("rel_docs", sb, uid)

        st.markdown("**4.2 Resulting Records**")
        if "rec_docs" not in st.session_state:
            st.session_state.rec_docs = [{"code":"","title":""}]
        _edit_refs("rec_docs", sb, uid)

        st.markdown("**4.3 Internal / External References**")
        if "ext_refs" not in st.session_state:
            st.session_state.ext_refs = [{"code":"","title":""}]
        _edit_refs("ext_refs", sb, uid)

        st.markdown("---")
        if st.button("💾 Save Document", type="primary"):
            if not title or not dept_code or not subdept_sel or not doc_type:
                st.error("Title, department, sub-department and type are required.")
            else:
                mermaid_code = build_mermaid(st.session_state.proc_steps, lanes)
                try:
                    res = sb.table("proc_documents").insert({
                        "dept":             dept_code,
                        "subdept":          subdept_sel,
                        "doc_type":         doc_type,
                        "seq_number":       0,
                        "revision":         0,
                        "title":            title,
                        "dept_label":       dept_label or None,
                        "date_of_adoption": adoption_dt.isoformat(),
                        "purpose":          purpose or None,
                        "policy":           [p for p in st.session_state.policy_points if p],
                        "scope":            [s for s in st.session_state.scope_points if s],
                        "responsibilities": [r for r in st.session_state.resp_points if r],
                        "abbreviations":    [a for a in st.session_state.abbrevs if a["term"]],
                        "procedure_steps":  st.session_state.proc_steps,
                        "flowchart_mermaid": mermaid_code or None,
                        "related_docs":     [r for r in st.session_state.rel_docs if r["code"] or r["title"]],
                        "resulting_records":[r for r in st.session_state.rec_docs if r["code"] or r["title"]],
                        "ext_references":   [r for r in st.session_state.ext_refs if r["code"] or r["title"]],
                        "approvals":        st.session_state.approvals,
                        "created_by":       uid,
                        "updated_by":       uid,
                    }).execute()

                    new_doc = res.data[0]
                    new_id  = new_doc["id"]
                    doc_code_final = new_doc.get("doc_code", preview_code)

                    # Log all references to master list
                    all_refs = (
                        [(r,"related_doc")       for r in st.session_state.rel_docs] +
                        [(r,"resulting_record")  for r in st.session_state.rec_docs] +
                        [(r,"reference")         for r in st.session_state.ext_refs]
                    )
                    for ref, rtype in all_refs:
                        code  = ref.get("code","").strip()
                        rtitle = ref.get("title","").strip()
                        if not code and not rtitle:
                            continue
                        check_or_add_master(sb, code or rtitle, rtitle or code, "Unknown", False, uid)
                        master = sb.table("master_documents").select("id")\
                            .eq("doc_code", code or rtitle).execute()
                        mid = master.data[0]["id"] if master.data else None
                        try:
                            sb.table("doc_references").insert({
                                "source_doc_id": new_id,
                                "ref_type":      rtype,
                                "master_doc_id": mid,
                                "raw_code":      code or None,
                                "raw_title":     rtitle or None,
                            }).execute()
                        except Exception:
                            pass

                    # Add this doc to master list
                    check_or_add_master(sb, doc_code_final, title, doc_type, True, uid)

                    st.success(f"✅ Document **{doc_code_final}** saved successfully!")
                    _clear_form()
                    st.rerun()
                except Exception as e:
                    st.error(f"Error saving: {e}")

    # ══════════════════════════════════════════
    # TAB 3 — MASTER DOCUMENT LIST
    # ══════════════════════════════════════════
    with tab_master:
        st.markdown("### 📚 Master Document List")
        st.caption("All documents referenced anywhere in the system — internal and external.")

        res = sb.table("master_documents").select("*").order("doc_code").execute()
        masters = res.data or []

        c1, c2 = st.columns(2)
        with c1:
            int_f = st.selectbox("Source", ["All","Internal","External"])
        with c2:
            msearch = st.text_input("Search", placeholder="code or title")

        if int_f == "Internal":
            masters = [m for m in masters if m.get("is_internal")]
        elif int_f == "External":
            masters = [m for m in masters if not m.get("is_internal")]
        if msearch:
            s = msearch.lower()
            masters = [m for m in masters if s in (m.get("doc_code","") or "").lower()
                       or s in (m.get("title","") or "").lower()]

        if not masters:
            st.info("No documents in master list yet.")
        else:
            rows = []
            for m in masters:
                rows.append({
                    "Code":     m.get("doc_code","—"),
                    "Title":    m.get("title",""),
                    "Type":     m.get("doc_type","—"),
                    "Source":   "Internal" if m.get("is_internal") else "External",
                    "Location": m.get("location","—") or "—",
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(f"{len(masters)} document(s) in master list")


# ─────────────────────────────────────────────
# DOCUMENT RENDERER (in-app view)
# ─────────────────────────────────────────────
def _render_document(sb, doc, uid):
    doc_code  = doc.get("doc_code","—")
    title     = doc.get("title","")
    rev       = doc.get("revision", 0)
    revisions = doc.get("_revisions") or []

    # Header bar
    st.markdown(f"""
<div style="border:1px solid #999;font-size:11px;margin-bottom:12px">
<table width="100%" style="border-collapse:collapse">
<tr>
  <td style="border:1px solid #999;padding:3px 6px;width:12%;font-weight:bold;background:#f0f0f0">TITLE</td>
  <td style="border:1px solid #999;padding:3px 6px;text-align:center">{title.upper()}</td>
  <td style="border:1px solid #999;padding:3px 6px;width:14%;font-weight:bold;background:#f0f0f0">NBR. OF PAGES</td>
  <td style="border:1px solid #999;padding:3px 6px;width:8%;text-align:center">—</td>
</tr>
<tr>
  <td style="border:1px solid #999;padding:3px 6px;font-weight:bold;background:#f0f0f0">CONTROL NBR.</td>
  <td style="border:1px solid #999;padding:3px 6px;text-align:center">{doc_code}</td>
  <td style="border:1px solid #999;padding:3px 6px;font-weight:bold;background:#f0f0f0">REVISION DATE</td>
  <td style="border:1px solid #999;padding:3px 6px;text-align:center">{revisions[-1]["revised_date"] if revisions else "—"}</td>
</tr>
</table>
</div>
""", unsafe_allow_html=True)

    # Logo + cover
    try:
        st.image(LOGO_PATH, width=120)
    except Exception:
        pass

    dept_label = doc.get("dept_label") or doc.get("dept","")
    st.markdown(f"<h3 style='text-align:center;margin-top:8px'>{dept_label.upper()}</h3>", unsafe_allow_html=True)
    st.markdown(f"<h2 style='text-align:center'>{title.upper()}</h2>", unsafe_allow_html=True)

    # Approvals
    approvals = doc.get("approvals") or []
    if approvals:
        cols = st.columns(len(approvals) + 1)
        with cols[0]:
            st.markdown("**Department**")
            st.markdown("**Function**")
            st.markdown("**Signature**")
            st.markdown("**Date**")
        for i, ap in enumerate(approvals):
            with cols[i+1]:
                st.markdown(ap.get("department",""))
                st.markdown(f"*{ap.get('function','')}*")
                st.markdown("&nbsp;")
                st.markdown("&nbsp;")

    adoption = doc.get("date_of_adoption","—")
    st.markdown(f"<p style='text-align:center;margin-top:8px'><b>Date of Adoption:</b> {adoption}</p>",
                unsafe_allow_html=True)

    # Revision history
    st.markdown("#### REVISION HISTORY")
    if revisions:
        rh = [{"Rev": f"{r['revision']:02d}", "Date": r.get("revised_date",""),
               "Status": r.get("status",""), "Description": r.get("description","")}
              for r in revisions]
        st.dataframe(pd.DataFrame(rh), hide_index=True, use_container_width=True)

    st.markdown("---")
    st.markdown("#### Table of Contents")
    st.markdown("""
**1.0** &nbsp; Introduction &nbsp;&nbsp;&nbsp; **1.1** Purpose &nbsp;&nbsp;&nbsp; **1.2** Policy &nbsp;&nbsp;&nbsp;
**1.3** Scope &nbsp;&nbsp;&nbsp; **1.4** Authorities & Responsibilities  
**2.0** &nbsp; Abbreviations and Definitions  
**3.0** &nbsp; Procedure  
**4.0** &nbsp; Associated Documentation &nbsp;&nbsp;&nbsp; **4.1** Related Documents &nbsp;&nbsp;&nbsp;
**4.2** Resulting Records &nbsp;&nbsp;&nbsp; **4.3** References
""", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("### 1.0 Introduction")
    purpose = doc.get("purpose")
    if purpose:
        st.markdown("**1.1 Purpose**")
        st.markdown(f"1.1.1 &nbsp; {purpose}", unsafe_allow_html=True)

    policy = doc.get("policy") or []
    if policy:
        st.markdown("**1.2 Policy**")
        for i, p in enumerate(policy, 1):
            st.markdown(f"1.2.{i} &nbsp; {p}", unsafe_allow_html=True)

    scope = doc.get("scope") or []
    if scope:
        st.markdown("**1.3 Scope of Application**")
        for i, s in enumerate(scope, 1):
            st.markdown(f"1.3.{i} &nbsp; {s}", unsafe_allow_html=True)

    resp = doc.get("responsibilities") or []
    if resp:
        st.markdown("**1.4 Authorities & Responsibilities**")
        for i, r in enumerate(resp, 1):
            st.markdown(f"1.4.{i} &nbsp; {r}", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("### 2.0 Abbreviations and Definitions")
    abbrevs = doc.get("abbreviations") or []
    if abbrevs:
        st.dataframe(
            pd.DataFrame([{"Term": a.get("term",""), "Definition": a.get("definition","")} for a in abbrevs]),
            hide_index=True, use_container_width=True)

    st.markdown("---")
    st.markdown("### 3.0 Procedure")
    steps = doc.get("procedure_steps") or []
    for i, step in enumerate(steps, 1):
        st.markdown(f"**{i}. {step.get('title','')}**")
        if step.get("text"):
            st.markdown(step["text"])

    mermaid = doc.get("flowchart_mermaid","")
    if mermaid:
        st.markdown("**Process Flowchart:**")
        img_bytes = mermaid_to_image_bytes(mermaid)
        if img_bytes:
            st.image(img_bytes, caption="Process Flowchart", use_column_width=True)
        else:
            st.warning("Flowchart could not be rendered.")

    st.markdown("---")
    st.markdown("### 4.0 Associated Documentation")

    def _ref_table(refs, label):
        if refs:
            st.markdown(f"**{label}**")
            st.dataframe(
                pd.DataFrame([{"Code": r.get("code","—"), "Title": r.get("title","—")} for r in refs]),
                hide_index=True, use_container_width=True)

    _ref_table(doc.get("related_docs") or [],      "4.1 Related Documents")
    _ref_table(doc.get("resulting_records") or [],  "4.2 Resulting Records")
    _ref_table(doc.get("ext_references") or [],     "4.3 Internal / External References")

    st.markdown(
        "<p style='font-size:10px;color:#aaa;text-align:center;margin-top:16px'>"
        "THE INFORMATION CONTAINED HEREIN IS PROPRIETARY TO NAPCO NATIONAL — "
        "<b>UNCONTROLLED IF PRINTED</b></p>", unsafe_allow_html=True)

    # Revision action
    if can_write():
        st.markdown("---")
        with st.expander("🔄 Create New Revision"):
            with st.form(f"revise_{doc['id']}", clear_on_submit=True):
                rev_desc = st.text_area("What changed in this revision?", height=80)
                submitted = st.form_submit_button("Create Revision")
            if submitted:
                new_rev = rev + 1
                try:
                    snapshot = {k: v for k, v in doc.items() if k != "_revisions"}
                    sb.table("proc_revisions").insert({
                        "doc_id":       doc["id"],
                        "revision":     new_rev,
                        "revised_date": date.today().isoformat(),
                        "status":       revision_label(new_rev),
                        "description":  rev_desc,
                        "revised_by":   uid,
                        "snapshot":     snapshot,
                    }).execute()
                    sb.table("proc_documents").update({
                        "revision":   new_rev,
                        "updated_by": uid,
                    }).eq("id", doc["id"]).execute()
                    st.success(f"✅ Revision {new_rev:02d} created.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")


# ─────────────────────────────────────────────
# UI HELPERS
# ─────────────────────────────────────────────
def _edit_list(key, placeholder):
    items = st.session_state[key]
    for i, item in enumerate(items):
        c1, c2 = st.columns([8,1])
        with c1:
            st.session_state[key][i] = st.text_input(
                f"{placeholder} {i+1}", value=item, key=f"{key}_{i}",
                label_visibility="collapsed", placeholder=placeholder)
        with c2:
            if st.button("🗑️", key=f"{key}_del_{i}") and len(items) > 1:
                items.pop(i)
                st.rerun()
    if st.button(f"➕ Add {placeholder.lower()}", key=f"{key}_add"):
        st.session_state[key].append("")
        st.rerun()


def _edit_refs(key, sb, uid):
    refs = st.session_state[key]
    for i, ref in enumerate(refs):
        c1, c2, c3 = st.columns([2,4,1])
        with c1:
            st.session_state[key][i]["code"] = st.text_input(
                "Code", value=ref.get("code",""), key=f"{key}_code_{i}",
                label_visibility="collapsed", placeholder="Doc code")
        with c2:
            st.session_state[key][i]["title"] = st.text_input(
                "Title", value=ref.get("title",""), key=f"{key}_title_{i}",
                label_visibility="collapsed", placeholder="Document title")
        with c3:
            if st.button("🗑️", key=f"{key}_del_{i}") and len(refs) > 1:
                refs.pop(i)
                st.rerun()
        code = ref.get("code","").strip()
        if code:
            exists = sb.table("master_documents").select("id").eq("doc_code", code).execute()
            if not exists.data:
                st.warning(f"⚠️ `{code}` not in master list — will be added on save.")
            else:
                st.success(f"✅ `{code}` found in master list.")
    if st.button("➕ Add reference", key=f"{key}_add"):
        st.session_state[key].append({"code":"","title":""})
        st.rerun()


def _clear_form():
    for key in ["approvals","policy_points","scope_points","resp_points",
                "abbrevs","proc_steps","rel_docs","rec_docs","ext_refs"]:
        if key in st.session_state:
            del st.session_state[key]