import streamlit as st
import pandas as pd
from datetime import date
import base64
import requests
import io
import math
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, HRFlowable, PageBreak, KeepTogether
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus.flowables import Flowable

from utils.auth import require_auth, can_write, get_profile
from utils.supabase_client import get_supabase

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────
LOGO_PATH  = "static/napco_logo.png"
NAPCO_BLUE = colors.HexColor("#0D68A3")
LIGHT_BLUE = colors.HexColor("#D5E8F0")
DARK_TEXT  = colors.HexColor("#1F2937")
GRAY_BG    = colors.HexColor("#F5F5F5")
PAGE_W, PAGE_H = A4
LEFT_M = RIGHT_M = 2*cm
TOP_M  = 3.2*cm   # space for header
BOT_M  = 2*cm

DEPT_MAP = {
    "SC — Supply Chain":         {"code": "SC",  "subs": ["CS","MH","AW","WH","XX","Other"]},
    "PL — Plant":                {"code": "PL",  "subs": ["MT","PD","XX","Other"]},
    "TQA — Technical & Quality": {"code": "TQA", "subs": ["QC","TQ","XX","Other"]},
    "PM — Product Management":   {"code": "PM",  "subs": ["XX","Other"]},
    "Other":                     {"code": None,  "subs": ["XX","Other"]},
}
DOC_TYPE_MAP = {"PD — Procedure": "PD", "PR — Process": "PR"}
SWIMLANES_DEFAULT = ["Step Owner","QC","Production","Maintenance","Management","HSE","Supply Chain"]

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def ordinal(n):
    s = {1:"st",2:"nd",3:"rd"}
    return f"{n}{s.get(n%10 if n%100 not in (11,12,13) else 0,'th')}"

def revision_label(rev):
    return "1st Issue" if rev == 0 else f"{ordinal(rev)} Revision"

def build_mermaid(steps, lanes):
    if not steps: return ""
    lines = ["flowchart TD"]
    lane_steps = {}
    for s in steps:
        lane = s.get("swimlane","General")
        lane_steps.setdefault(lane, [])
        lane_steps[lane].append(s)
    for lane, ls in lane_steps.items():
        safe = lane.replace(" ","_").replace("-","_").replace("/","_")
        lines.append(f'    subgraph {safe}["{lane}"]')
        for s in ls:
            sid   = s["id"].replace("-","")
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
        src  = steps[i]["id"].replace("-","")
        dst  = steps[i+1]["id"].replace("-","")
        conn = s.get("connection_label","")
        lines.append(f"    {src} -->|{conn}| {dst}" if conn else f"    {src} --> {dst}")
    return "\n".join(lines)

def check_or_add_master(sb, code, title, doc_type, is_internal, uid):
    if not code and not title: return
    key = code or title
    res = sb.table("master_documents").select("id").eq("doc_code", key).execute()
    if not res.data:
        try:
            sb.table("master_documents").insert({
                "doc_code": key, "title": title or key,
                "doc_type": doc_type, "is_internal": is_internal, "created_by": uid,
            }).execute()
        except Exception:
            pass


# ─────────────────────────────────────────────
# REPORTLAB SWIMLANE FLOWCHART FLOWABLE
# ─────────────────────────────────────────────

def _draw_filled_poly(canvas, points, fill_color, stroke_color):
    p = canvas.beginPath()
    p.moveTo(points[0], points[1])
    for i in range(2, len(points), 2):
        p.lineTo(points[i], points[i+1])
    p.close()
    canvas.setFillColor(fill_color)
    canvas.setStrokeColor(stroke_color)
    canvas.drawPath(p, fill=1, stroke=1)

def _draw_arrowhead(canvas, x, y, color):
    p = canvas.beginPath()
    p.moveTo(x, y)
    p.lineTo(x - 0.12*cm, y + 0.22*cm)
    p.lineTo(x + 0.12*cm, y + 0.22*cm)
    p.close()
    canvas.setFillColor(color)
    canvas.setStrokeColor(color)
    canvas.drawPath(p, fill=1, stroke=0)

class SwimlaneFlowchart(Flowable):
    """Draws a vertical swimlane flowchart using ReportLab primitives."""

    BOX_W      = 3.8*cm
    BOX_H      = 1.1*cm
    DIA_W      = 3.8*cm
    DIA_H      = 1.4*cm
    V_GAP      = 0.7*cm   # vertical gap between steps
    LANE_PAD   = 0.4*cm
    LANE_HDR_H = 0.7*cm
    FONT       = "Helvetica"
    FONT_B     = "Helvetica-Bold"
    FONT_SZ    = 7
    ARROW_LEN  = 0.5*cm

    def __init__(self, steps, available_width):
        super().__init__()
        self.steps = steps
        self.avail_w = available_width

        # Group steps by swimlane preserving order of first appearance
        self.lanes = []
        seen = {}
        for s in steps:
            lane = s.get("swimlane","General")
            if lane not in seen:
                seen[lane] = len(self.lanes)
                self.lanes.append(lane)

        self.n_lanes  = max(len(self.lanes), 1)
        self.lane_w   = self.avail_w / self.n_lanes

        # Calculate total height
        step_height = self.BOX_H + self.V_GAP
        self.total_h = (self.LANE_HDR_H + self.LANE_PAD +
                        len(steps) * step_height + self.LANE_PAD)
        self.width  = available_width
        self.height = self.total_h

    def _lane_x(self, lane_name):
        idx = self.lanes.index(lane_name) if lane_name in self.lanes else 0
        return idx * self.lane_w

    def _step_y(self, step_idx):
        """Y from bottom for step center."""
        content_h = self.total_h - self.LANE_HDR_H - self.LANE_PAD
        step_area = self.BOX_H + self.V_GAP
        # top-down: step 0 is near top
        y_from_top = (self.LANE_PAD + step_idx * step_area +
                      self.BOX_H / 2)
        return self.total_h - self.LANE_HDR_H - y_from_top

    def draw(self):
        c = self.canv

        # ── Lane backgrounds and headers ──
        for i, lane in enumerate(self.lanes):
            x = i * self.lane_w
            # Alternating background
            bg = colors.HexColor("#EBF3FA") if i % 2 == 0 else colors.white
            c.setFillColor(bg)
            c.rect(x, 0, self.lane_w,
                   self.total_h - self.LANE_HDR_H, fill=1, stroke=0)

            # Lane header
            c.setFillColor(NAPCO_BLUE)
            c.rect(x, self.total_h - self.LANE_HDR_H,
                   self.lane_w, self.LANE_HDR_H, fill=1, stroke=0)
            c.setFillColor(colors.white)
            c.setFont(self.FONT_B, self.FONT_SZ + 1)
            c.drawCentredString(x + self.lane_w/2,
                                self.total_h - self.LANE_HDR_H + 0.18*cm,
                                lane)

        # Border around whole chart
        c.setStrokeColor(colors.HexColor("#AAAAAA"))
        c.setLineWidth(0.5)
        c.rect(0, 0, self.width, self.total_h, fill=0, stroke=1)

        # Lane dividers
        for i in range(1, self.n_lanes):
            x = i * self.lane_w
            c.line(x, 0, x, self.total_h)

        # ── Draw steps and arrows ──
        for idx, step in enumerate(self.steps):
            lane  = step.get("swimlane","General")
            shape = step.get("shape","rect")
            title = step.get("title","")
            conn  = step.get("connection_label","")

            lx    = self._lane_x(lane)
            cy    = self._step_y(idx)
            cx    = lx + self.lane_w / 2

            # Box
            c.setFillColor(colors.white)
            c.setStrokeColor(NAPCO_BLUE)
            c.setLineWidth(1)

            if shape == "diamond":
                hw = self.DIA_W / 2
                hh = self.DIA_H / 2
                pts = [cx, cy+hh, cx+hw, cy, cx, cy-hh, cx-hw, cy]
                _draw_filled_poly(c, pts,
                    colors.HexColor("#FFF9C4"), NAPCO_BLUE)
                box_top    = cy + hh
                box_bottom = cy - hh
            elif shape == "rounded":
                bw = self.BOX_W
                bh = self.BOX_H
                c.setFillColor(colors.HexColor("#E8F5E9"))
                c.roundRect(cx - bw/2, cy - bh/2, bw, bh, radius=bh/2,
                             fill=1, stroke=1)
                box_top    = cy + bh/2
                box_bottom = cy - bh/2
            else:
                bw = self.BOX_W
                bh = self.BOX_H
                c.setFillColor(colors.white)
                c.rect(cx - bw/2, cy - bh/2, bw, bh, fill=1, stroke=1)
                box_top    = cy + bh/2
                box_bottom = cy - bh/2

            # Step text — wrap manually
            c.setFillColor(DARK_TEXT)
            c.setFont(self.FONT, self.FONT_SZ)
            words      = title.split()
            line1, line2 = [], []
            for w in words:
                test = " ".join(line1 + [w])
                if c.stringWidth(test, self.FONT, self.FONT_SZ) < self.BOX_W - 4:
                    line1.append(w)
                else:
                    line2.append(w)
            if line2:
                c.drawCentredString(cx, cy + 0.1*cm, " ".join(line1))
                c.drawCentredString(cx, cy - 0.22*cm, " ".join(line2))
            else:
                c.drawCentredString(cx, cy - 0.08*cm, " ".join(line1))

            # Arrow to next step
            if idx < len(self.steps) - 1:
                next_step  = self.steps[idx+1]
                next_lane  = next_step.get("swimlane","General")
                next_lx    = self._lane_x(next_lane)
                next_cy    = self._step_y(idx+1)
                next_cx    = next_lx + self.lane_w / 2
                next_shape = next_step.get("shape","rect")
                next_top   = next_cy + (self.DIA_H/2 if next_shape=="diamond" else self.BOX_H/2)

                c.setStrokeColor(DARK_TEXT)
                c.setFillColor(DARK_TEXT)
                c.setLineWidth(0.8)

                if lane == next_lane:
                    # Straight down arrow
                    y_start = box_bottom
                    y_end   = next_top + 0.05*cm
                    mid_y   = (y_start + y_end) / 2
                    c.line(cx, y_start, cx, y_end)
                    _draw_arrowhead(c, cx, y_end, DARK_TEXT)
                    # Connection label
                    if conn:
                        c.setFont(self.FONT, self.FONT_SZ - 1)
                        c.setFillColor(colors.HexColor("#555555"))
                        c.drawCentredString(cx + 0.3*cm, mid_y, conn)
                        c.setFillColor(DARK_TEXT)
                else:
                    # Cross-lane: go down then across then down
                    y_mid = box_bottom - self.V_GAP * 0.4
                    c.line(cx, box_bottom, cx, y_mid)
                    c.line(cx, y_mid, next_cx, y_mid)
                    c.line(next_cx, y_mid, next_cx, next_top + 0.05*cm)
                    _draw_arrowhead(c, next_cx, next_top + 0.05*cm, DARK_TEXT)
                    if conn:
                        c.setFont(self.FONT, self.FONT_SZ - 1)
                        c.setFillColor(colors.HexColor("#555555"))
                        mid_x = (cx + next_cx) / 2
                        c.drawCentredString(mid_x, y_mid + 0.08*cm, conn)
                        c.setFillColor(DARK_TEXT)


# ─────────────────────────────────────────────
# PDF GENERATION
# ─────────────────────────────────────────────
def generate_pdf(doc):
    buffer    = io.BytesIO()
    avail_w   = PAGE_W - LEFT_M - RIGHT_M

    # Styles
    normal   = ParagraphStyle("N",  fontName="Helvetica",      fontSize=9,  leading=13)
    bold_s   = ParagraphStyle("B",  fontName="Helvetica-Bold", fontSize=9,  leading=13)
    h1s      = ParagraphStyle("H1", fontName="Helvetica-Bold", fontSize=12, leading=16,
                               spaceBefore=10, spaceAfter=4, textColor=NAPCO_BLUE)
    h2s      = ParagraphStyle("H2", fontName="Helvetica-Bold", fontSize=10, leading=13,
                               spaceBefore=6, spaceAfter=3)
    centered = ParagraphStyle("C",  fontName="Helvetica",      fontSize=9,  leading=13,
                               alignment=TA_CENTER)
    centered_b=ParagraphStyle("CB", fontName="Helvetica-Bold", fontSize=9,  leading=13,
                               alignment=TA_CENTER)
    title_s  = ParagraphStyle("T",  fontName="Helvetica-Bold", fontSize=20, leading=26,
                               alignment=TA_CENTER, spaceAfter=8)
    dept_s   = ParagraphStyle("D",  fontName="Helvetica-Bold", fontSize=13, leading=18,
                               alignment=TA_CENTER, spaceAfter=4)
    numbered = ParagraphStyle("NM", fontName="Helvetica",      fontSize=9,  leading=13,
                               leftIndent=24, spaceAfter=3)
    small_i  = ParagraphStyle("SI", fontName="Helvetica-Oblique", fontSize=7, leading=9,
                               alignment=TA_CENTER, textColor=colors.grey)
    toc_main = ParagraphStyle("TM", fontName="Helvetica-Bold", fontSize=10, leading=14)
    toc_sub  = ParagraphStyle("TS", fontName="Helvetica",      fontSize=9,  leading=13,
                               leftIndent=16)

    doc_code   = doc.get("doc_code","—")
    title_txt  = doc.get("title","")
    dept_label = doc.get("dept_label","") or doc.get("dept","")
    adoption   = str(doc.get("date_of_adoption","—"))
    approvals  = doc.get("approvals") or []
    revisions  = doc.get("_revisions") or []
    issue_date = revisions[0]["revised_date"] if revisions else adoption
    rev_date   = revisions[-1]["revised_date"] if revisions else adoption

    # ── Header/Footer callback ──────────────────────────────
    def header_footer(canvas, doc_obj):
        canvas.saveState()
        w, h = A4
        hdr_x   = LEFT_M
        hdr_w   = w - LEFT_M - RIGHT_M
        hdr_top = h - 0.8*cm
        row1_h  = 0.65*cm
        row2_h  = 0.55*cm
        hdr_h   = row1_h + row2_h

        # Logo — sits above header table, top-left
        try:
            canvas.drawImage(LOGO_PATH,
                             hdr_x, hdr_top + 0.1*cm,
                             width=2.8*cm, height=1.1*cm,
                             preserveAspectRatio=True, mask='auto')
        except Exception:
            pass

        # Header table background rows
        # Row 1 background
        canvas.setFillColor(GRAY_BG)
        canvas.rect(hdr_x, hdr_top - row1_h, hdr_w, row1_h, fill=1, stroke=0)
        # Row 2 background
        canvas.setFillColor(colors.white)
        canvas.rect(hdr_x, hdr_top - row1_h - row2_h, hdr_w, row2_h, fill=1, stroke=0)

        # Outer border
        canvas.setStrokeColor(colors.HexColor("#999999"))
        canvas.setLineWidth(0.5)
        canvas.rect(hdr_x, hdr_top - hdr_h, hdr_w, hdr_h, fill=0, stroke=1)

        # Column dividers — row 1: TITLE | (title spans) | NBR OF PAGES | page
        col1_w = 2.2*cm
        col3_w = 2.8*cm
        col4_w = 2.2*cm
        col2_w = hdr_w - col1_w - col3_w - col4_w

        # Vertical lines row 1
        canvas.line(hdr_x + col1_w,
                    hdr_top - row1_h, hdr_x + col1_w, hdr_top)
        canvas.line(hdr_x + col1_w + col2_w,
                    hdr_top - row1_h, hdr_x + col1_w + col2_w, hdr_top)
        canvas.line(hdr_x + col1_w + col2_w + col3_w,
                    hdr_top - row1_h, hdr_x + col1_w + col2_w + col3_w, hdr_top)

        # Horizontal divider between rows
        canvas.line(hdr_x, hdr_top - row1_h,
                    hdr_x + hdr_w, hdr_top - row1_h)

        # Row 1 text
        y1 = hdr_top - row1_h + 0.18*cm
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.setFillColor(colors.black)
        canvas.drawCentredString(hdr_x + col1_w/2, y1, "TITLE")
        canvas.setFont("Helvetica-Bold", 8.5)
        canvas.drawCentredString(hdr_x + col1_w + col2_w/2, y1, title_txt.upper())
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.drawCentredString(hdr_x + col1_w + col2_w + col3_w/2, y1, "NBR. OF PAGES")
        canvas.setFont("Helvetica", 7.5)
        canvas.drawCentredString(hdr_x + col1_w + col2_w + col3_w + col4_w/2,
                                 y1, f"PAGE {doc_obj.page}")

        # Row 2 columns: CONTROL NBR | doc_code | 1ST ISSUE DATE | date | REVISION DATE | date
        r2_cols = [2.2*cm, 3.5*cm, 2.5*cm, 2.2*cm, 2.5*cm]
        r2_last = hdr_w - sum(r2_cols)
        r2_cols.append(r2_last)
        r2_labels = ["CONTROL NBR.", doc_code,
                     "1ST ISSUE DATE", str(issue_date),
                     "REVISION DATE", str(rev_date)]
        r2_bold   = [True, False, True, False, True, False]
        x_cur = hdr_x
        y2 = hdr_top - hdr_h + 0.15*cm
        for i, (cw, lbl, is_b) in enumerate(zip(r2_cols, r2_labels, r2_bold)):
            if i > 0:
                canvas.setStrokeColor(colors.HexColor("#999999"))
                canvas.setLineWidth(0.5)
                canvas.line(x_cur, hdr_top - row1_h - row2_h,
                            x_cur, hdr_top - row1_h)
            canvas.setFont("Helvetica-Bold" if is_b else "Helvetica", 7)
            canvas.setFillColor(colors.black)
            canvas.drawCentredString(x_cur + cw/2, y2, lbl)
            x_cur += cw

        # Footer
        fy = 0.5*cm
        canvas.setFillColor(GRAY_BG)
        canvas.rect(LEFT_M, fy, hdr_w, 0.85*cm, fill=1, stroke=1)
        canvas.setFont("Helvetica-Oblique", 6.5)
        canvas.setFillColor(colors.HexColor("#555555"))
        canvas.drawCentredString(
            w/2, fy + 0.48*cm,
            "THE INFORMATION CONTAINED HEREIN IS PROPRIETARY TO NAPCO NATIONAL AND IT SHALL NOT BE "
            "USED, REPRODUCED OR DISCLOSED TO OTHERS EXCEPT AS SPECIFICALLY PERMITTED IN WRITING BY THE PROPRIETOR.")
        canvas.setFont("Helvetica-BoldOblique", 7)
        canvas.drawCentredString(w/2, fy + 0.18*cm, '"UNCONTROLLED IF PRINTED"')
        canvas.restoreState()

    # ── Build document ──────────────────────────────────────
    pdf = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=LEFT_M, rightMargin=RIGHT_M,
        topMargin=TOP_M, bottomMargin=BOT_M,
    )
    pdf.onFirstPage  = header_footer
    pdf.onLaterPages = header_footer

    story = []

    # ── COVER PAGE ──────────────────────────────────────────
    story.append(Spacer(1, 0.8*cm))

    # Large logo on cover
    try:
        story.append(Image(LOGO_PATH, width=5*cm, height=2*cm))
    except Exception:
        pass

    story.append(Spacer(1, 0.4*cm))
    story.append(Paragraph(dept_label.upper(), dept_s))
    story.append(Paragraph(title_txt.upper(), title_s))
    story.append(Spacer(1, 0.5*cm))

    # Approvals table
    if approvals:
        n = len(approvals)
        cw = avail_w / (n + 1)
        ap_data = [
            [Paragraph("APPROVED BY", bold_s)] + [""] * n,
            [Paragraph("Department", bold_s)] +
            [Paragraph(a.get("department",""), centered) for a in approvals],
            [Paragraph("Function", bold_s)] +
            [Paragraph(a.get("function",""), centered) for a in approvals],
            [Paragraph("Signature", bold_s)] + [""] * n,
            [Paragraph("Date", bold_s)] + [""] * n,
        ]
        ap_t = Table(ap_data, colWidths=[cw]*(n+1),
                     rowHeights=[0.6*cm, 0.7*cm, 0.7*cm, 1.4*cm, 0.7*cm])
        ap_t.setStyle(TableStyle([
            ("GRID",        (0,0), (-1,-1), 0.5, colors.HexColor("#999999")),
            ("BACKGROUND",  (0,0), (-1,0),  NAPCO_BLUE),
            ("TEXTCOLOR",   (0,0), (-1,0),  colors.white),
            ("BACKGROUND",  (0,1), (0,-1),  LIGHT_BLUE),
            ("FONTNAME",    (0,0), (0,-1),  "Helvetica-Bold"),
            ("FONTSIZE",    (0,0), (-1,-1), 8),
            ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
            ("ALIGN",       (1,0), (-1,-1), "CENTER"),
            ("SPAN",        (0,0), (-1,0)),
        ]))
        story.append(ap_t)
        story.append(Spacer(1, 0.4*cm))

    # Date of adoption
    adopt_t = Table(
        [[Paragraph("Date of Adoption", bold_s), Paragraph(adoption, centered)]],
        colWidths=[5*cm, 5*cm])
    adopt_t.setStyle(TableStyle([
        ("GRID",       (0,0),(-1,-1), 0.5, colors.HexColor("#999999")),
        ("BACKGROUND", (0,0),(0,0),   LIGHT_BLUE),
        ("FONTSIZE",   (0,0),(-1,-1), 8),
        ("VALIGN",     (0,0),(-1,-1), "MIDDLE"),
        ("ALIGN",      (1,0),(-1,-1), "CENTER"),
    ]))
    story.append(adopt_t)
    story.append(Spacer(1, 0.6*cm))

    # Revision history
    story.append(Paragraph("REVISION HISTORY", h1s))
    if revisions:
        rh_data = [[
            Paragraph("Revision", centered_b),
            Paragraph("Date", centered_b),
            Paragraph("Status", centered_b),
            Paragraph("Description", centered_b),
        ]]
        for r in revisions:
            rh_data.append([
                Paragraph(str(r.get("revision","")).zfill(2), centered),
                Paragraph(str(r.get("revised_date","")), centered),
                Paragraph(r.get("status",""), centered),
                Paragraph(r.get("description",""), normal),
            ])
        rh_t = Table(rh_data, colWidths=[2*cm, 3*cm, 3.5*cm, None])
        rh_t.setStyle(TableStyle([
            ("GRID",            (0,0),(-1,-1), 0.5, colors.HexColor("#999999")),
            ("BACKGROUND",      (0,0),(-1,0),  NAPCO_BLUE),
            ("TEXTCOLOR",       (0,0),(-1,0),  colors.white),
            ("FONTSIZE",        (0,0),(-1,-1), 8),
            ("VALIGN",          (0,0),(-1,-1), "MIDDLE"),
            ("ROWBACKGROUNDS",  (0,1),(-1,-1), [colors.white, LIGHT_BLUE]),
        ]))
        story.append(rh_t)

    story.append(PageBreak())

    # ── TABLE OF CONTENTS ───────────────────────────────────
    story.append(Paragraph("Table of Contents", h1s))
    story.append(Spacer(1, 0.3*cm))

    toc_entries = [
        ("1.0", "Introduction", False),
        ("1.1", "Purpose", True),
        ("1.2", "Policy", True),
        ("1.3", "Scope of Application", True),
        ("1.4", "Authorities & Responsibilities", True),
        ("2.0", "Abbreviations and Definitions", False),
        ("3.0", "Procedure (Narrative or Flowchart)", False),
        ("4.0", "Associated Documentation", False),
        ("4.1", "Related Documents", True),
        ("4.2", "Resulting Records", True),
        ("4.3", "Internal / External References", True),
    ]
    for num, lbl, is_sub in toc_entries:
        sty = toc_sub if is_sub else toc_main
        dots = "." * 80
        row = Table(
            [[Paragraph(f"{num}", sty),
              Paragraph(lbl, sty),
              Paragraph(dots, ParagraphStyle("dots", fontName="Helvetica",
                                              fontSize=8, textColor=colors.HexColor("#BBBBBB"))),
              Paragraph("", sty)]],
            colWidths=[1.2*cm, 8*cm, None, 1*cm]
        )
        row.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"BOTTOM"),
                                  ("BOTTOMPADDING",(0,0),(-1,-1),1)]))
        story.append(row)

    story.append(PageBreak())

    # ── 1.0 INTRODUCTION ────────────────────────────────────
    story.append(Paragraph("1.0 Introduction", h1s))
    story.append(HRFlowable(width="100%", thickness=0.5, color=NAPCO_BLUE))
    story.append(Spacer(1, 0.2*cm))

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

    # ── 2.0 ABBREVIATIONS ───────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=NAPCO_BLUE))
    story.append(Paragraph("2.0 Abbreviations and Definitions", h1s))
    abbrevs = doc.get("abbreviations") or []
    if abbrevs:
        ab_data = [[Paragraph("Terms & Abbreviations", centered_b),
                    Paragraph("Definition", centered_b)]]
        for i, ab in enumerate(abbrevs):
            ab_data.append([
                Paragraph(ab.get("term",""), bold_s),
                Paragraph(ab.get("definition",""), normal),
            ])
        ab_t = Table(ab_data, colWidths=[5*cm, None])
        ab_t.setStyle(TableStyle([
            ("GRID",           (0,0),(-1,-1), 0.5, colors.HexColor("#999999")),
            ("BACKGROUND",     (0,0),(-1,0),  NAPCO_BLUE),
            ("TEXTCOLOR",      (0,0),(-1,0),  colors.white),
            ("FONTSIZE",       (0,0),(-1,-1), 8),
            ("VALIGN",         (0,0),(-1,-1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0,1),(-1,-1), [colors.white, LIGHT_BLUE]),
        ]))
        story.append(ab_t)
    story.append(Spacer(1, 0.4*cm))

    # ── 3.0 PROCEDURE ───────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=NAPCO_BLUE))
    story.append(Paragraph("3.0 Procedure (Narrative or Flowchart)", h1s))

    steps = doc.get("procedure_steps") or []
    for i, step in enumerate(steps, 1):
        story.append(Paragraph(f"{i}. {step.get('title','')}", h2s))
        if step.get("text"):
            story.append(Paragraph(step["text"], numbered))
        story.append(Spacer(1, 0.15*cm))

    # ReportLab swimlane flowchart
    if steps:
        story.append(Spacer(1, 0.4*cm))
        story.append(Paragraph("Process Flowchart", h2s))
        story.append(Spacer(1, 0.2*cm))
        chart = SwimlaneFlowchart(steps, avail_w)
        story.append(chart)
        story.append(Spacer(1, 0.4*cm))

    # ── 4.0 ASSOCIATED DOCUMENTATION ────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=NAPCO_BLUE))
    story.append(Paragraph("4.0 Associated Documentation", h1s))

    def ref_table(items, label):
        if not items: return
        story.append(Paragraph(label, h2s))
        rd = [[Paragraph("Code", centered_b), Paragraph("Title", centered_b)]]
        for r in items:
            rd.append([Paragraph(r.get("code","—"), normal),
                       Paragraph(r.get("title","—"), normal)])
        rt = Table(rd, colWidths=[5*cm, None])
        rt.setStyle(TableStyle([
            ("GRID",           (0,0),(-1,-1), 0.5, colors.HexColor("#999999")),
            ("BACKGROUND",     (0,0),(-1,0),  NAPCO_BLUE),
            ("TEXTCOLOR",      (0,0),(-1,0),  colors.white),
            ("FONTSIZE",       (0,0),(-1,-1), 8),
            ("VALIGN",         (0,0),(-1,-1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0,1),(-1,-1), [colors.white, LIGHT_BLUE]),
        ]))
        story.append(rt)
        story.append(Spacer(1, 0.3*cm))

    ref_table(doc.get("related_docs") or [],     "4.1 Related Documents")
    ref_table(doc.get("resulting_records") or [], "4.2 Resulting Records")
    ref_table(doc.get("ext_references") or [],    "4.3 Internal / External References")

    pdf.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
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
                rl = revision_label(doc.get("revision", 0))
                with st.expander(
                    f"**{doc.get('doc_code','—')}** — {doc.get('title','')}  "
                    f"| Rev {doc.get('revision',0):02d} | {rl}"
                ):
                    rev_res = sb.table("proc_revisions").select("*")\
                        .eq("doc_id", doc["id"]).order("revision").execute()
                    doc["_revisions"] = rev_res.data or []

                    col_dl, _ = st.columns([2,8])
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
                                        key=f"dl_{doc['id']}",
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
            sub_opts    = dept_info["subs"]
            subdept_sel = st.selectbox("Sub-department *", sub_opts, key="nd_subdept")
            if subdept_sel == "Other":
                subdept_sel = st.text_input("Sub-dept code", key="nd_subdept_other").upper()
        with c3:
            type_sel = st.selectbox("Document Type *", list(DOC_TYPE_MAP.keys()), key="nd_type")
            doc_type = DOC_TYPE_MAP[type_sel]

        if dept_code and subdept_sel and doc_type:
            seq_res = sb.table("proc_documents")\
                .select("seq_number")\
                .eq("dept", dept_code).eq("subdept", subdept_sel)\
                .eq("doc_type", doc_type)\
                .order("seq_number", desc=True).limit(1).execute()
            next_seq     = (seq_res.data[0]["seq_number"] + 1) if seq_res.data else 1
            preview_code = f"NFP-EP-{dept_code}-{subdept_sel}-{doc_type}-{next_seq:02d}-00"
            st.info(f"📄 Auto-generated code: **{preview_code}**")

        st.markdown("---")
        st.markdown("### Cover Page")
        c1, c2 = st.columns(2)
        with c1:
            title      = st.text_input("Document Title *",
                                        placeholder="e.g. Customer Complaint Handling Process")
            dept_label = st.text_input("Department Name (for cover)",
                                        placeholder="e.g. Supply Chain Department")
        with c2:
            adoption_dt = st.date_input("Date of Adoption", value=date.today())

        st.markdown("**Approvals**")
        if "approvals" not in st.session_state:
            st.session_state.approvals = [
                {"department": "", "function": ""},
                {"department": "", "function": ""},
                {"department": "Top Management", "function": "Operations Manager"},
            ]
        ap_cols = st.columns(len(st.session_state.approvals))
        for i, ap in enumerate(st.session_state.approvals):
            with ap_cols[i]:
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
                                     value="\n".join(SWIMLANES_DEFAULT),
                                     height=150, key="nd_lanes")
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
                    idx = lanes.index(step.get("swimlane", lanes[0])) \
                          if step.get("swimlane") in lanes else 0
                    st.session_state.proc_steps[i]["swimlane"] = st.selectbox(
                        "Swimlane", lanes, key=f"ps_lane_{i}", index=idx)
                with c3:
                    shapes = ["rect","diamond","rounded"]
                    sidx   = shapes.index(step.get("shape","rect")) \
                             if step.get("shape") in shapes else 0
                    st.session_state.proc_steps[i]["shape"] = st.selectbox(
                        "Shape", shapes, key=f"ps_shape_{i}", index=sidx)
                st.session_state.proc_steps[i]["text"] = st.text_area(
                    "Description", value=step.get("text",""),
                    key=f"ps_text_{i}", height=80)
                st.session_state.proc_steps[i]["connection_label"] = st.text_input(
                    "Arrow label to next step",
                    value=step.get("connection_label",""),
                    key=f"ps_conn_{i}", placeholder="e.g. Yes / No")
                if st.button("🗑️ Remove step", key=f"ps_del_{i}"):
                    st.session_state.proc_steps.pop(i)
                    st.rerun()

        if st.button("➕ Add step"):
            import uuid
            st.session_state.proc_steps.append({
                "id": str(uuid.uuid4())[:8],
                "title":"", "text":"",
                "swimlane": lanes[0] if lanes else "General",
                "shape":"rect", "connection_label":"",
            })
            st.rerun()

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
                        "dept":              dept_code,
                        "subdept":           subdept_sel,
                        "doc_type":          doc_type,
                        "seq_number":        0,
                        "revision":          0,
                        "title":             title,
                        "dept_label":        dept_label or None,
                        "date_of_adoption":  adoption_dt.isoformat(),
                        "purpose":           purpose or None,
                        "policy":            [p for p in st.session_state.policy_points if p],
                        "scope":             [s for s in st.session_state.scope_points if s],
                        "responsibilities":  [r for r in st.session_state.resp_points if r],
                        "abbreviations":     [a for a in st.session_state.abbrevs if a["term"]],
                        "procedure_steps":   st.session_state.proc_steps,
                        "flowchart_mermaid": mermaid_code or None,
                        "related_docs":      [r for r in st.session_state.rel_docs
                                              if r["code"] or r["title"]],
                        "resulting_records": [r for r in st.session_state.rec_docs
                                              if r["code"] or r["title"]],
                        "ext_references":    [r for r in st.session_state.ext_refs
                                              if r["code"] or r["title"]],
                        "approvals":         st.session_state.approvals,
                        "created_by":        uid,
                        "updated_by":        uid,
                    }).execute()

                    new_doc        = res.data[0]
                    new_id         = new_doc["id"]
                    doc_code_final = new_doc.get("doc_code", preview_code)

                    all_refs = (
                        [(r,"related_doc")      for r in st.session_state.rel_docs] +
                        [(r,"resulting_record") for r in st.session_state.rec_docs] +
                        [(r,"reference")        for r in st.session_state.ext_refs]
                    )
                    for ref, rtype in all_refs:
                        code   = ref.get("code","").strip()
                        rtitle = ref.get("title","").strip()
                        if not code and not rtitle: continue
                        check_or_add_master(sb, code or rtitle, rtitle or code,
                                            "Unknown", False, uid)
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

        res     = sb.table("master_documents").select("*").order("doc_code").execute()
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
            s       = msearch.lower()
            masters = [m for m in masters
                       if s in (m.get("doc_code","") or "").lower()
                       or s in (m.get("title","") or "").lower()]

        if not masters:
            st.info("No documents in master list yet.")
        else:
            rows = [{"Code": m.get("doc_code","—"), "Title": m.get("title",""),
                     "Type": m.get("doc_type","—"),
                     "Source": "Internal" if m.get("is_internal") else "External",
                     "Location": m.get("location","—") or "—"} for m in masters]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(f"{len(masters)} document(s) in master list")


# ─────────────────────────────────────────────
# IN-APP DOCUMENT RENDERER
# ─────────────────────────────────────────────
def _render_document(sb, doc, uid):
    doc_code  = doc.get("doc_code","—")
    title     = doc.get("title","")
    rev       = doc.get("revision", 0)
    revisions = doc.get("_revisions") or []

    st.markdown(f"""
<div style="border:1px solid #ccc;font-size:11px;margin-bottom:12px;border-radius:4px">
<table width="100%" style="border-collapse:collapse">
<tr>
  <td style="border:1px solid #ccc;padding:4px 8px;width:12%;font-weight:bold;background:#f5f5f5">TITLE</td>
  <td style="border:1px solid #ccc;padding:4px 8px;text-align:center;font-weight:bold">{title.upper()}</td>
  <td style="border:1px solid #ccc;padding:4px 8px;width:15%;font-weight:bold;background:#f5f5f5">NBR. OF PAGES</td>
  <td style="border:1px solid #ccc;padding:4px 8px;width:8%;text-align:center">—</td>
</tr>
<tr>
  <td style="border:1px solid #ccc;padding:4px 8px;font-weight:bold;background:#f5f5f5">CONTROL NBR.</td>
  <td style="border:1px solid #ccc;padding:4px 8px;text-align:center">{doc_code}</td>
  <td style="border:1px solid #ccc;padding:4px 8px;font-weight:bold;background:#f5f5f5">REVISION DATE</td>
  <td style="border:1px solid #ccc;padding:4px 8px;text-align:center">{revisions[-1]["revised_date"] if revisions else "—"}</td>
</tr>
</table>
</div>""", unsafe_allow_html=True)

    try:
        st.image(LOGO_PATH, width=120)
    except Exception:
        pass

    dept_label = doc.get("dept_label") or doc.get("dept","")
    st.markdown(f"<h3 style='text-align:center'>{dept_label.upper()}</h3>", unsafe_allow_html=True)
    st.markdown(f"<h2 style='text-align:center'>{title.upper()}</h2>", unsafe_allow_html=True)

    approvals = doc.get("approvals") or []
    if approvals:
        cols = st.columns(len(approvals)+1)
        labels = ["Department","Function","Signature","Date"]
        with cols[0]:
            for l in labels:
                st.markdown(f"**{l}**")
        for i, ap in enumerate(approvals):
            with cols[i+1]:
                st.markdown(ap.get("department",""))
                st.markdown(f"*{ap.get('function','')}*")
                st.markdown("&nbsp;")
                st.markdown("&nbsp;")

    adoption = doc.get("date_of_adoption","—")
    st.markdown(f"<p style='text-align:center'><b>Date of Adoption:</b> {adoption}</p>",
                unsafe_allow_html=True)

    st.markdown("#### REVISION HISTORY")
    if revisions:
        st.dataframe(pd.DataFrame([{
            "Rev": f"{r['revision']:02d}", "Date": r.get("revised_date",""),
            "Status": r.get("status",""), "Description": r.get("description","")
        } for r in revisions]), hide_index=True, use_container_width=True)

    st.markdown("---")
    st.markdown("### 1.0 Introduction")
    if doc.get("purpose"):
        st.markdown("**1.1 Purpose**")
        st.markdown(f"1.1.1 &nbsp; {doc['purpose']}", unsafe_allow_html=True)
    for i, p in enumerate(doc.get("policy") or [], 1):
        if i == 1: st.markdown("**1.2 Policy**")
        st.markdown(f"1.2.{i} &nbsp; {p}", unsafe_allow_html=True)
    for i, s in enumerate(doc.get("scope") or [], 1):
        if i == 1: st.markdown("**1.3 Scope of Application**")
        st.markdown(f"1.3.{i} &nbsp; {s}", unsafe_allow_html=True)
    for i, r in enumerate(doc.get("responsibilities") or [], 1):
        if i == 1: st.markdown("**1.4 Authorities & Responsibilities**")
        st.markdown(f"1.4.{i} &nbsp; {r}", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("### 2.0 Abbreviations and Definitions")
    abbrevs = doc.get("abbreviations") or []
    if abbrevs:
        st.dataframe(pd.DataFrame([{"Term": a.get("term",""),
                                    "Definition": a.get("definition","")}
                                   for a in abbrevs]),
                     hide_index=True, use_container_width=True)

    st.markdown("---")
    st.markdown("### 3.0 Procedure")
    steps = doc.get("procedure_steps") or []
    for i, step in enumerate(steps, 1):
        st.markdown(f"**{i}. {step.get('title','')}**")
        if step.get("text"):
            st.markdown(step["text"])

    st.markdown("---")
    st.markdown("### 4.0 Associated Documentation")
    for refs, label in [
        (doc.get("related_docs") or [],      "4.1 Related Documents"),
        (doc.get("resulting_records") or [],  "4.2 Resulting Records"),
        (doc.get("ext_references") or [],     "4.3 Internal / External References"),
    ]:
        if refs:
            st.markdown(f"**{label}**")
            st.dataframe(pd.DataFrame([{"Code": r.get("code","—"),
                                        "Title": r.get("title","—")}
                                       for r in refs]),
                         hide_index=True, use_container_width=True)

    st.markdown(
        "<p style='font-size:10px;color:#aaa;text-align:center;margin-top:16px'>"
        "PROPRIETARY TO NAPCO NATIONAL — <b>UNCONTROLLED IF PRINTED</b></p>",
        unsafe_allow_html=True)

    if can_write():
        st.markdown("---")
        with st.expander("🔄 Create New Revision"):
            with st.form(f"revise_{doc['id']}", clear_on_submit=True):
                rev_desc  = st.text_area("What changed in this revision?", height=80)
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
                        "revision": new_rev, "updated_by": uid,
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
                items.pop(i); st.rerun()
    if st.button(f"➕ Add {placeholder.lower()}", key=f"{key}_add"):
        st.session_state[key].append(""); st.rerun()


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
                refs.pop(i); st.rerun()
        code = ref.get("code","").strip()
        if code:
            exists = sb.table("master_documents").select("id").eq("doc_code", code).execute()
            if not exists.data:
                st.warning(f"⚠️ `{code}` not in master list — will be added on save.")
            else:
                st.success(f"✅ `{code}` found in master list.")
    if st.button("➕ Add reference", key=f"{key}_add"):
        st.session_state[key].append({"code":"","title":""}); st.rerun()


def _clear_form():
    for key in ["approvals","policy_points","scope_points","resp_points",
                "abbrevs","proc_steps","rel_docs","rec_docs","ext_refs"]:
        if key in st.session_state:
            del st.session_state[key]