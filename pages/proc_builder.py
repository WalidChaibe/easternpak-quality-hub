import streamlit as st
import pandas as pd
from datetime import date
from utils.auth import require_auth, can_write, get_profile
from utils.supabase_client import get_supabase

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────
DEPT_MAP = {
    "SC — Supply Chain":        {"code": "SC",  "subs": ["CS","MH","AW","WH","XX","Other"]},
    "PL — Plant":               {"code": "PL",  "subs": ["MT","PD","XX","Other"]},
    "TQA — Technical & Quality":{"code": "TQA", "subs": ["CQ","TQ","XX","Other"]},
    "PM — Product Management":  {"code": "PM",  "subs": ["XX","Other"]},
    "Other":                    {"code": None,  "subs": ["XX","Other"]},
}

DOC_TYPE_MAP = {
    "PD — Procedure": "PD",
    "PR — Process":   "PR",
}

REVISION_STATUS = [
    "1st Issue","1st Revision","2nd Revision","3rd Revision",
    "4th Revision","5th Revision","6th Revision",
]

SWIMLANES_DEFAULT = ["Step Owner","QC","Production","Management","HSE","Supply Chain","Other"]

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def ordinal(n):
    suffixes = {1:"st",2:"nd",3:"rd"}
    return f"{n}{suffixes.get(n % 10 if n % 100 not in (11,12,13) else 0,'th')}"

def revision_label(rev):
    if rev == 0:
        return "1st Issue"
    return f"{ordinal(rev)} Revision"

def build_mermaid(steps, lanes):
    """Generate Mermaid flowchart code from step list."""
    if not steps:
        return ""
    lines = ["flowchart TD"]
    lane_steps = {}
    for s in steps:
        lane = s.get("swimlane","General")
        lane_steps.setdefault(lane, [])
        lane_steps[lane].append(s)

    for lane, lane_s in lane_steps.items():
        safe_lane = lane.replace(" ","_").replace("-","_")
        lines.append(f'    subgraph {safe_lane}["{lane}"]')
        for s in lane_s:
            sid = s["id"].replace("-","")
            shape = s.get("shape","rect")
            label = s.get("title","Step").replace('"',"'")
            if shape == "diamond":
                lines.append(f'        {sid}{{{{{label}}}}}')
            elif shape == "rounded":
                lines.append(f'        {sid}([{label}])')
            else:
                lines.append(f'        {sid}[{label}]')
        lines.append("    end")

    # connections
    for i, s in enumerate(steps[:-1]):
        src = s["id"].replace("-","")
        dst = steps[i+1]["id"].replace("-","")
        conn = s.get("connection_label","")
        if conn:
            lines.append(f"    {src} -->|{conn}| {dst}")
        else:
            lines.append(f"    {src} --> {dst}")

    return "\n".join(lines)


def check_or_add_master(sb, code, title, doc_type, is_internal, created_by):
    """Check if a doc exists in master_documents; if not, insert it."""
    res = sb.table("master_documents").select("id").eq("doc_code", code).execute()
    if not res.data:
        sb.table("master_documents").insert({
            "doc_code":   code,
            "title":      title,
            "doc_type":   doc_type,
            "is_internal":is_internal,
            "created_by": created_by,
        }).execute()


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
            dept_f = st.selectbox("Department", ["All"] + [v["code"] for v in DEPT_MAP.values() if v["code"]])
        with col2:
            type_f = st.selectbox("Type", ["All","PD","PR"])
        with col3:
            search = st.text_input("🔍 Search title or code")

        q = sb.table("v_proc_documents").select("*").order("doc_code")
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
                dept_code = st.text_input("Department code (e.g. HR)", key="nd_dept_other").upper()
        with c2:
            sub_opts = dept_info["subs"]
            subdept_sel = st.selectbox("Sub-department *", sub_opts, key="nd_subdept")
            if subdept_sel == "Other":
                subdept_sel = st.text_input("Sub-dept code (e.g. QA)", key="nd_subdept_other").upper()
        with c3:
            type_sel  = st.selectbox("Document Type *", list(DOC_TYPE_MAP.keys()), key="nd_type")
            doc_type  = DOC_TYPE_MAP[type_sel]

        # Preview auto-generated code
        if dept_code and subdept_sel and doc_type:
            # Get next seq
            seq_res = sb.table("proc_documents")\
                .select("seq_number")\
                .eq("dept", dept_code)\
                .eq("subdept", subdept_sel)\
                .eq("doc_type", doc_type)\
                .order("seq_number", desc=True)\
                .limit(1).execute()
            next_seq = (seq_res.data[0]["seq_number"] + 1) if seq_res.data else 1
            preview_code = f"NFP-EP-{dept_code}-{subdept_sel}-{doc_type}-{next_seq:02d}-00"
            st.info(f"📄 Auto-generated code: **{preview_code}**")

        st.markdown("---")
        st.markdown("### Cover Page")
        c1, c2 = st.columns(2)
        with c1:
            title       = st.text_input("Document Title *", placeholder="e.g. Customer Complaint Handling Process")
            dept_label  = st.text_input("Department Name (for cover)", placeholder="e.g. Supply Chain Department")
        with c2:
            adoption_dt = st.date_input("Date of Adoption", value=date.today())

        # Approvals
        st.markdown("**Approvals (cover page)**")
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

        cola, colb = st.columns([1,5])
        with cola:
            if st.button("➕ Add approver"):
                st.session_state.approvals.append({"department":"","function":""})
                st.rerun()

        st.markdown("---")
        st.markdown("### 1.0 Introduction")

        purpose = st.text_area("1.1 Purpose", height=100,
                               placeholder="The purpose of this procedure is to…")

        st.markdown("**1.2 Policy** — add one point per line")
        if "policy_points" not in st.session_state:
            st.session_state.policy_points = [""]
        _edit_list("policy_points", "Policy point")

        st.markdown("**1.3 Scope of Application** — add one point per line")
        if "scope_points" not in st.session_state:
            st.session_state.scope_points = [""]
        _edit_list("scope_points", "Scope point")

        st.markdown("**1.4 Authorities & Responsibilities** — add one point per line")
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
                    "Term", value=ab["term"], key=f"ab_term_{i}", label_visibility="collapsed",
                    placeholder="Term / Abbreviation")
            with c2:
                st.session_state.abbrevs[i]["definition"] = st.text_input(
                    "Def", value=ab["definition"], key=f"ab_def_{i}", label_visibility="collapsed",
                    placeholder="Definition")
            with c3:
                if st.button("🗑️", key=f"ab_del_{i}") and len(st.session_state.abbrevs) > 1:
                    st.session_state.abbrevs.pop(i)
                    st.rerun()

        if st.button("➕ Add abbreviation"):
            st.session_state.abbrevs.append({"term":"","definition":""})
            st.rerun()

        st.markdown("---")
        st.markdown("### 3.0 Procedure Steps & Flowchart")
        st.caption("Add steps below. Assign each to a swimlane. The flowchart is generated automatically.")

        # Swimlane config
        with st.expander("⚙️ Configure swimlanes"):
            lanes_raw = st.text_area(
                "Swimlanes (one per line)",
                value="\n".join(SWIMLANES_DEFAULT),
                height=150,
                key="nd_lanes"
            )
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
                    st.session_state.proc_steps[i]["swimlane"] = st.selectbox(
                        "Swimlane", lanes, key=f"ps_lane_{i}",
                        index=lanes.index(step.get("swimlane", lanes[0])) if step.get("swimlane") in lanes else 0)
                with c3:
                    st.session_state.proc_steps[i]["shape"] = st.selectbox(
                        "Shape", ["rect","diamond","rounded"],
                        key=f"ps_shape_{i}",
                        index=["rect","diamond","rounded"].index(step.get("shape","rect")))

                st.session_state.proc_steps[i]["text"] = st.text_area(
                    "Step description", value=step.get("text",""), key=f"ps_text_{i}", height=80)
                st.session_state.proc_steps[i]["connection_label"] = st.text_input(
                    "Arrow label to next step (optional)", value=step.get("connection_label",""),
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
                    "title": "", "text": "",
                    "swimlane": lanes[0] if lanes else "General",
                    "shape": "rect",
                    "connection_label": "",
                })
                st.rerun()
        with col_prev:
            if st.button("👁️ Preview flowchart") and st.session_state.proc_steps:
                mermaid_code = build_mermaid(st.session_state.proc_steps, lanes)
                st.code(mermaid_code, language="")
                st.markdown("**Rendered flowchart:**")
                st.markdown(f"```mermaid\n{mermaid_code}\n```")

        st.markdown("---")
        st.markdown("### 4.0 Associated Documentation")
        st.caption("Any code you enter here will be checked against the master document list and added if missing.")

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

        # SAVE
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
                        "seq_number":       0,  # trigger assigns
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
                        "references":       [r for r in st.session_state.ext_refs if r["code"] or r["title"]],
                        "approvals":        st.session_state.approvals,
                        "created_by":       uid,
                        "updated_by":       uid,
                    }).execute()

                    new_doc = res.data[0]
                    new_id  = new_doc["id"]

                    # Log all references to master list
                    all_refs = (
                        [(r, "related_doc")    for r in st.session_state.rel_docs] +
                        [(r, "resulting_record") for r in st.session_state.rec_docs] +
                        [(r, "reference")      for r in st.session_state.ext_refs]
                    )
                    for ref, rtype in all_refs:
                        code  = ref.get("code","").strip()
                        rtitle = ref.get("title","").strip()
                        if not code and not rtitle:
                            continue
                        check_or_add_master(sb, code or rtitle, rtitle or code,
                                           "Unknown", False, uid)
                        master = sb.table("master_documents").select("id")\
                            .eq("doc_code", code or rtitle).execute()
                        mid = master.data[0]["id"] if master.data else None
                        sb.table("doc_references").insert({
                            "source_doc_id": new_id,
                            "ref_type":      rtype,
                            "master_doc_id": mid,
                            "raw_code":      code or None,
                            "raw_title":     rtitle or None,
                        }).execute()

                    # Also add THIS document to master list
                    doc_code_final = new_doc.get("doc_code") or preview_code
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

        col1, col2 = st.columns(2)
        with col1:
            int_f = st.selectbox("Source", ["All","Internal","External"])
        with col2:
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
# DOCUMENT RENDERER
# ─────────────────────────────────────────────
def _render_document(sb, doc, uid):
    """Render a full document in Napco style."""
    doc_code = doc.get("doc_code","—")
    title    = doc.get("title","")
    rev      = doc.get("revision", 0)

    # Fetch revision history
    rev_res = sb.table("proc_revisions").select("*")\
        .eq("doc_id", doc["id"]).order("revision").execute()
    revisions = rev_res.data or []

    # ── HEADER BAR ──
    st.markdown(f"""
<div style="border:1px solid #999;font-size:12px;margin-bottom:8px">
<table width="100%" style="border-collapse:collapse">
<tr>
  <td style="border:1px solid #999;padding:4px;width:12%;font-weight:bold;text-align:center">TITLE</td>
  <td style="border:1px solid #999;padding:4px;text-align:center">{title.upper()}</td>
  <td style="border:1px solid #999;padding:4px;width:14%;font-weight:bold;text-align:center">NBR. OF PAGES</td>
  <td style="border:1px solid #999;padding:4px;width:10%;text-align:center">—</td>
</tr>
<tr>
  <td style="border:1px solid #999;padding:4px;font-weight:bold;text-align:center">CONTROL NBR.</td>
  <td style="border:1px solid #999;padding:4px;text-align:center">{doc_code}</td>
  <td style="border:1px solid #999;padding:4px;font-weight:bold;text-align:center">REVISION DATE</td>
  <td style="border:1px solid #999;padding:4px;text-align:center">{revisions[-1]["revised_date"] if revisions else "—"}</td>
</tr>
</table>
</div>
""", unsafe_allow_html=True)

    # ── COVER ──
    dept_label = doc.get("dept_label") or doc.get("dept","")
    st.markdown(f"<h2 style='text-align:center;margin-top:16px'>{dept_label.upper()}</h2>", unsafe_allow_html=True)
    st.markdown(f"<h3 style='text-align:center'>{title.upper()}</h3>", unsafe_allow_html=True)

    # Approvals table
    approvals = doc.get("approvals") or []
    if approvals:
        ap_html = """
<div style='margin:16px 0'>
<table style='border-collapse:collapse;width:100%;font-size:12px'>
<tr><td colspan='{n}' style='border:1px solid #999;padding:4px;font-weight:bold'>APPROVED BY</td></tr>
<tr>
  <td style='border:1px solid #999;padding:4px;font-weight:bold'>Department</td>
  {depts}
</tr>
<tr>
  <td style='border:1px solid #999;padding:4px;font-weight:bold'>Function</td>
  {funcs}
</tr>
</table>
</div>
""".format(
            n=len(approvals)+1,
            depts="".join(f"<td style='border:1px solid #999;padding:4px;text-align:center'>{a.get('department','')}</td>" for a in approvals),
            funcs="".join(f"<td style='border:1px solid #999;padding:4px;text-align:center'>{a.get('function','')}</td>" for a in approvals),
        )
        st.markdown(ap_html, unsafe_allow_html=True)

    adoption = doc.get("date_of_adoption","—")
    st.markdown(f"<p style='text-align:center'><b>Date of Adoption:</b> {adoption}</p>", unsafe_allow_html=True)

    # Revision history
    st.markdown("#### REVISION HISTORY")
    if revisions:
        rh_rows = []
        for r in revisions:
            rh_rows.append({
                "Revision": f"{r['revision']:02d}",
                "Date":     r.get("revised_date",""),
                "Status":   r.get("status",""),
                "Description": r.get("description",""),
            })
        st.dataframe(pd.DataFrame(rh_rows), hide_index=True, use_container_width=True)

    st.markdown("---")
    st.markdown("#### Table of Contents")
    st.markdown("""
**1.0** Introduction  
&nbsp;&nbsp;&nbsp;**1.1** Purpose  
&nbsp;&nbsp;&nbsp;**1.2** Policy  
&nbsp;&nbsp;&nbsp;**1.3** Scope of Application  
&nbsp;&nbsp;&nbsp;**1.4** Authorities & Responsibilities  
**2.0** Abbreviations and Definitions  
**3.0** Procedure  
**4.0** Associated Documentation  
&nbsp;&nbsp;&nbsp;**4.1** Related Documents  
&nbsp;&nbsp;&nbsp;**4.2** Resulting Records  
&nbsp;&nbsp;&nbsp;**4.3** Internal / External References  
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
        ab_rows = [{"Term / Abbreviation": a.get("term",""), "Definition": a.get("definition","")} for a in abbrevs]
        st.dataframe(pd.DataFrame(ab_rows), hide_index=True, use_container_width=True)

    st.markdown("---")
    st.markdown("### 3.0 Procedure")
    steps = doc.get("procedure_steps") or []
    if steps:
        for i, step in enumerate(steps, 1):
            st.markdown(f"**{i}. {step.get('title','')}**")
            if step.get("text"):
                st.markdown(step["text"])

        mermaid = doc.get("flowchart_mermaid")
        if mermaid:
            st.markdown("**Process Flowchart:**")
            st.markdown(f"```mermaid\n{mermaid}\n```")

    st.markdown("---")
    st.markdown("### 4.0 Associated Documentation")

    def _ref_table(refs, label):
        if refs:
            st.markdown(f"**{label}**")
            rows = [{"Code": r.get("code","—"), "Title": r.get("title","—")} for r in refs]
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    _ref_table(doc.get("related_docs") or [],      "4.1 Related Documents")
    _ref_table(doc.get("resulting_records") or [],  "4.2 Resulting Records")
    _ref_table(doc.get("references") or [],         "4.3 Internal / External References")

    st.markdown("---")
    st.markdown(
        "<p style='font-size:11px;color:#888;text-align:center'>"
        "THE INFORMATION CONTAINED HEREIN IS PROPRIETARY TO NAPCO NATIONAL AND IT SHALL NOT BE USED, "
        "REPRODUCED OR DISCLOSED TO OTHERS EXCEPT AS SPECIFICALLY PERMITTED IN WRITING BY THE PROPRIETOR.<br>"
        "<b>\"UNCONTROLLED IF PRINTED\"</b></p>",
        unsafe_allow_html=True
    )

    # ── REVISION ACTION (managers only) ──
    if can_write():
        st.markdown("---")
        with st.expander("🔄 Create New Revision"):
            with st.form(f"revise_{doc['id']}", clear_on_submit=True):
                rev_desc = st.text_area("Describe what changed in this revision", height=80)
                submitted = st.form_submit_button("Create Revision")
            if submitted:
                new_rev = rev + 1
                try:
                    # Snapshot current doc
                    snapshot = {k: v for k, v in doc.items()}
                    sb.table("proc_revisions").insert({
                        "doc_id":      doc["id"],
                        "revision":    new_rev,
                        "revised_date": date.today().isoformat(),
                        "status":      revision_label(new_rev),
                        "description": rev_desc,
                        "revised_by":  uid,
                        "snapshot":    snapshot,
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
    """Editable list of text points."""
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
    """Editable list of document references with master list check."""
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

        # Check master list
        code = ref.get("code","").strip()
        if code:
            exists = sb.table("master_documents").select("id").eq("doc_code", code).execute()
            if not exists.data:
                st.warning(f"⚠️ `{code}` not in master list — will be added on save.")
            else:
                st.success(f"✅ `{code}` found in master list.")

    if st.button(f"➕ Add reference", key=f"{key}_add"):
        st.session_state[key].append({"code":"","title":""})
        st.rerun()


def _clear_form():
    """Reset all session state after save."""
    for key in ["approvals","policy_points","scope_points","resp_points",
                "abbrevs","proc_steps","rel_docs","rec_docs","ext_refs"]:
        if key in st.session_state:
            del st.session_state[key]
