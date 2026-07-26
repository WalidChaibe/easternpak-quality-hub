import streamlit as st
import pandas as pd
from datetime import date, timedelta
import base64
import requests
import io
import math
import re
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
from reportlab.pdfbase.pdfmetrics import stringWidth

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

def _clean_approval_label(text):
    """Some legacy documents embed '(Prepared by)' / '(Reviewed by)' /
    '(Approved by)' directly inside the function name. The approvals table
    already has a 'Prepared by / Reviewed by / Approved by' style role for
    each column via its position, so this suffix is redundant — strip it."""
    if not text:
        return text
    return re.sub(r"\s*\((?:prepared|reviewed|approved)\s+by\)\s*", "",
                   text, flags=re.IGNORECASE).strip()

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

def _draw_arrowhead_right(canvas, x, y, color):
    p = canvas.beginPath()
    p.moveTo(x, y)
    p.lineTo(x - 0.22*cm, y + 0.12*cm)
    p.lineTo(x - 0.22*cm, y - 0.12*cm)
    p.close()
    canvas.setFillColor(color)
    canvas.setStrokeColor(color)
    canvas.drawPath(p, fill=1, stroke=0)

def _wrap_words(text, font, size, max_width):
    """Greedy word-wrap: returns a list of lines that each fit within max_width."""
    words = (text or "").split()
    if not words:
        return [""]
    lines, cur = [], []
    for w in words:
        test = " ".join(cur + [w])
        if stringWidth(test, font, size) <= max_width or not cur:
            cur.append(w)
        else:
            lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    return lines

class SwimlaneFlowchart(Flowable):
    """Draws a top-down process flowchart with support for:
      - a horizontal row of parallel "trigger" boxes that fan into one point
        (step type "input_row"), matching multiple inputs feeding one step
      - a horizontal side-branch off any step (e.g. a parallel procedure
        that gets triggered), drawn to the right with its own arrow

    Everything else is a single centered step column, same as before.
    Box/row heights are computed up front from the actual wrapped text, so
    nothing overflows regardless of title length.
    """

    BOX_W        = 7.6*cm
    MIN_BOX_H    = 1.0*cm
    DIA_W        = 6.2*cm
    MIN_DIA_H    = 1.7*cm
    V_GAP        = 1.05*cm     # vertical gap between steps (room for arrow + label)
    ROLE_LBL_H   = 0.55*cm     # space reserved above each box for the role chip
    LANE_PAD     = 0.5*cm      # top/bottom padding of the whole chart
    LANE_HDR_H   = 0           # no header row in the single-column layout
    FONT         = "Helvetica"
    FONT_B       = "Helvetica-Bold"
    FONT_SZ      = 8
    ROLE_FONT_SZ = 6.3
    LINE_H       = FONT_SZ + 2.4

    BOX_FILL     = colors.HexColor("#EAF3FB")
    BOX_BORDER   = NAPCO_BLUE
    DIA_FILL     = colors.HexColor("#FFF6D8")
    DIA_BORDER   = colors.HexColor("#C9A227")
    BRANCH_FILL  = colors.HexColor("#FCEAEA")
    BRANCH_BORDER= colors.HexColor("#B03A3A")
    SHADOW_COLOR = colors.HexColor("#DADADA")
    ARROW_COLOR  = colors.HexColor("#4A4A4A")

    def __init__(self, steps, available_width, all_lanes=None):
        # all_lanes accepted for backward compatibility; unused.
        super().__init__()
        self.steps   = steps
        self.avail_w = available_width
        self.width   = available_width

        # If any step has a side-branch, shift the main column left so
        # there's room on the right for the branch box.
        self._has_branch = any(s.get("side_branch") for s in steps
                                if s.get("type", "step") == "step")
        self.cx = self.width * 0.36 if self._has_branch else self.width / 2

        self._prepared = []
        for step in steps:
            stype = step.get("type", "step")

            if stype == "input_row":
                items = step.get("items", [])
                n = max(1, len(items))
                gap = 0.22*cm
                item_w = min(3.4*cm, (self.width - gap*(n-1) - 2*self.LANE_PAD) / n)
                prepared_items, max_lines = [], 1
                for it in items:
                    lines = _wrap_words(it.get("title",""), self.FONT,
                                         self.ROLE_FONT_SZ + 0.7, item_w - 0.3*cm)
                    prepared_items.append(lines)
                    max_lines = max(max_lines, len(lines))
                row_h = max(1.0*cm, max_lines * (self.ROLE_FONT_SZ + 2.6) + 0.25*cm)
                bus_h = 0.6*cm  # room for the converging arrows below the row
                self._prepared.append({
                    "type": "input_row", "items": prepared_items, "item_w": item_w,
                    "row_h": row_h, "box_h": row_h + bus_h, "bus_h": bus_h,
                })
                continue

            shape = step.get("shape", "rect")
            title = step.get("title", "")
            max_w = (self.DIA_W - 1.3*cm) if shape == "diamond" else (self.BOX_W - 0.7*cm)
            lines  = _wrap_words(title, self.FONT, self.FONT_SZ, max_w)
            text_h = len(lines) * self.LINE_H
            if shape == "diamond":
                box_h = max(self.MIN_DIA_H, text_h + 0.7*cm)
            else:
                box_h = max(self.MIN_BOX_H, text_h + 0.35*cm)

            side_prepared = None
            side = step.get("side_branch")
            if side:
                side_w = self.width - (self.cx + self.BOX_W/2) - 1.1*cm
                side_w = max(2.6*cm, min(side_w, 5.6*cm))
                side_lines = _wrap_words(side.get("title",""), self.FONT,
                                          self.FONT_SZ - 0.5, side_w - 0.5*cm)
                side_h = max(0.95*cm, len(side_lines) * self.LINE_H + 0.3*cm)
                side_prepared = {"lines": side_lines, "w": side_w, "box_h": side_h}

            self._prepared.append({
                "type": "step", "shape": shape, "lines": lines, "box_h": box_h,
                "side_branch": side_prepared,
            })

        self._step_heights = []
        for p in self._prepared:
            lbl_h = 0 if p["type"] == "input_row" else self.ROLE_LBL_H
            self._step_heights.append(lbl_h + p["box_h"] + self.V_GAP)

        self.total_h = self.LANE_PAD + sum(self._step_heights) + self.LANE_PAD
        self.height  = self.total_h

    def _step_geom(self, idx):
        """Returns (center_y, box_h) for step idx, from the bottom of the flowable."""
        y_from_top = self.LANE_PAD + sum(self._step_heights[:idx])
        p = self._prepared[idx]
        lbl_h = 0 if p["type"] == "input_row" else self.ROLE_LBL_H
        y_from_top += lbl_h + p["box_h"] / 2
        return self.total_h - y_from_top, p["box_h"]

    def _draw_box(self, c, cx, cy, bw, bh, fill, border, radius=8):
        c.setFillColor(self.SHADOW_COLOR)
        c.roundRect(cx - bw/2 + 0.08*cm, cy - bh/2 - 0.08*cm, bw, bh, radius=radius, fill=1, stroke=0)
        c.setFillColor(fill)
        c.setStrokeColor(border)
        c.setLineWidth(1.2)
        c.roundRect(cx - bw/2, cy - bh/2, bw, bh, radius=radius, fill=1, stroke=1)

    def _draw_role_chip(self, c, cx, top_y, role):
        role_txt = role.upper()
        c.setFont(self.FONT_B, self.ROLE_FONT_SZ)
        chip_w = stringWidth(role_txt, self.FONT_B, self.ROLE_FONT_SZ) + 0.34*cm
        chip_h = 0.36*cm
        chip_y = top_y + 0.09*cm
        c.setFillColor(NAPCO_BLUE)
        c.roundRect(cx - chip_w/2, chip_y, chip_w, chip_h, radius=chip_h/2, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.drawCentredString(cx, chip_y + 0.10*cm, role_txt)

    def _draw_centered_lines(self, c, cx, cy, lines, font, size, color=None):
        c.setFillColor(color or DARK_TEXT)
        c.setFont(font, size)
        line_h = size + 2.4
        start_y = cy + (len(lines) - 1) * line_h / 2
        for li, ln in enumerate(lines):
            c.drawCentredString(cx, start_y - li * line_h, ln)

    def draw(self):
        c  = self.canv
        cx = self.cx
        c.setLineCap(1)
        c.setLineJoin(1)

        c.setStrokeColor(colors.HexColor("#DDDDDD"))
        c.setLineWidth(0.6)
        c.roundRect(1, 1, self.width - 2, self.total_h - 2, radius=6, fill=0, stroke=1)

        for idx, step in enumerate(self.steps):
            p  = self._prepared[idx]
            cy, bh = self._step_geom(idx)

            # ── Fan-in input row ──────────────────────────────
            if p["type"] == "input_row":
                item_w, row_h, bus_h = p["item_w"], p["row_h"], p["bus_h"]
                n = len(p["items"])
                gap = 0.22*cm
                total_w = n * item_w + (n - 1) * gap
                start_x = (self.width - total_w) / 2
                row_cy  = cy + bus_h / 2

                centers = []
                for i, lines in enumerate(p["items"]):
                    bx = start_x + i * (item_w + gap)
                    bx_c = bx + item_w / 2
                    centers.append(bx_c)
                    self._draw_box(c, bx_c, row_cy, item_w, row_h,
                                    self.BOX_FILL, self.BOX_BORDER, radius=6)
                    self._draw_centered_lines(c, bx_c, row_cy, lines,
                                               self.FONT, self.ROLE_FONT_SZ + 0.7)

                # Converging "fan-in" arrows: stub down from each box to a
                # shared bus line, then one arrow down into the next step.
                bus_y = row_cy - row_h / 2 - bus_h * 0.5
                c.setStrokeColor(self.ARROW_COLOR)
                c.setLineWidth(1.0)
                for bx_c in centers:
                    c.line(bx_c, row_cy - row_h / 2, bx_c, bus_y)
                c.line(min(centers), bus_y, max(centers), bus_y)

                if idx < len(self.steps) - 1:
                    next_cy, next_bh = self._step_geom(idx + 1)
                    next_top = next_cy + next_bh / 2
                    y_end = next_top + 0.06*cm
                    c.setFillColor(self.ARROW_COLOR)
                    c.line(cx, bus_y, cx, y_end)
                    _draw_arrowhead(c, cx, y_end, self.ARROW_COLOR)
                continue

            # ── Regular step (rect / diamond) ─────────────────
            shape = p["shape"]
            lines = p["lines"]
            role  = step.get("swimlane", "")
            conn  = step.get("connection_label", "")

            if shape == "diamond":
                hw, hh = self.DIA_W / 2, bh / 2
                off = 0.08*cm
                shadow_pts = [cx+off, cy+hh-off, cx+hw+off, cy-off,
                              cx+off, cy-hh-off, cx-hw+off, cy-off]
                _draw_filled_poly(c, shadow_pts, self.SHADOW_COLOR, self.SHADOW_COLOR)
                pts = [cx, cy+hh, cx+hw, cy, cx, cy-hh, cx-hw, cy]
                c.setLineWidth(1.2)
                _draw_filled_poly(c, pts, self.DIA_FILL, self.DIA_BORDER)
                box_top, box_bottom = cy + hh, cy - hh
            else:
                self._draw_box(c, cx, cy, self.BOX_W, bh, self.BOX_FILL, self.BOX_BORDER)
                box_top, box_bottom = cy + bh/2, cy - bh/2

            if role:
                self._draw_role_chip(c, cx, box_top, role)

            self._draw_centered_lines(c, cx, cy, lines, self.FONT, self.FONT_SZ)

            # ── Side branch (e.g. a parallel procedure this step triggers) ──
            if p.get("side_branch"):
                sb = p["side_branch"]
                sb_w, sb_h = sb["w"], sb["box_h"]
                sb_x  = cx + self.BOX_W/2 + 1.0*cm
                sb_cx = sb_x + sb_w/2

                self._draw_box(c, sb_cx, cy, sb_w, sb_h,
                                self.BRANCH_FILL, self.BRANCH_BORDER, radius=6)
                self._draw_centered_lines(c, sb_cx, cy, sb["lines"], self.FONT, self.FONT_SZ - 0.5)

                c.setStrokeColor(self.ARROW_COLOR)
                c.setFillColor(self.ARROW_COLOR)
                c.setLineWidth(1.1)
                x_start = cx + self.BOX_W/2
                x_end   = sb_x - 0.05*cm
                c.line(x_start, cy, x_end, cy)
                _draw_arrowhead_right(c, x_end, cy, self.ARROW_COLOR)

            # ── Arrow down to next step ────────────────────────
            if idx < len(self.steps) - 1:
                next_cy, next_bh = self._step_geom(idx + 1)
                next_top = next_cy + next_bh / 2

                c.setStrokeColor(self.ARROW_COLOR)
                c.setFillColor(self.ARROW_COLOR)
                c.setLineWidth(1.1)
                y_end = next_top + 0.06*cm
                c.line(cx, box_bottom, cx, y_end)
                _draw_arrowhead(c, cx, y_end, self.ARROW_COLOR)

                if conn:
                    mid_y = (box_bottom + y_end) / 2
                    c.setFont(self.FONT_B, self.ROLE_FONT_SZ)
                    label_w = stringWidth(conn, self.FONT_B, self.ROLE_FONT_SZ) + 0.3*cm
                    label_h = 0.34*cm
                    lx = cx + 0.16*cm
                    c.setFillColor(colors.white)
                    c.setStrokeColor(self.ARROW_COLOR)
                    c.setLineWidth(0.6)
                    c.roundRect(lx, mid_y - label_h/2, label_w, label_h,
                                radius=label_h/2, fill=1, stroke=1)
                    c.setFillColor(self.ARROW_COLOR)
                    c.drawCentredString(lx + label_w/2, mid_y - 0.09*cm, conn)

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
        # Header table moved down from the very top of the page so the logo
        # (drawn above it) has room and doesn't get clipped by the page edge.
        hdr_top = h - 1.7*cm
        row1_h  = 0.65*cm
        row2_h  = 0.55*cm
        hdr_h   = row1_h + row2_h

        # Logo — sits above header table, top-left
        try:
            canvas.drawImage(LOGO_PATH,
                             hdr_x, hdr_top + 0.15*cm,
                             width=2.6*cm, height=1.0*cm,
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

        # Footer — disclaimer text is word-wrapped so it always stays inside the box
        disclaimer = ("THE INFORMATION CONTAINED HEREIN IS PROPRIETARY TO NAPCO NATIONAL AND IT SHALL "
                      "NOT BE USED, REPRODUCED OR DISCLOSED TO OTHERS EXCEPT AS SPECIFICALLY PERMITTED "
                      "IN WRITING BY THE PROPRIETOR.")
        disc_font, disc_sz = "Helvetica-Oblique", 6.5
        disc_max_w  = hdr_w - 0.6*cm
        disc_lines  = _wrap_words(disclaimer, disc_font, disc_sz, disc_max_w)
        line_gap    = 0.30*cm
        footer_h    = 0.25*cm + (len(disc_lines) + 1) * line_gap  # +1 for the UNCONTROLLED line

        fy = 0.5*cm
        canvas.setFillColor(GRAY_BG)
        canvas.rect(LEFT_M, fy, hdr_w, footer_h, fill=1, stroke=1)

        ty = fy + footer_h - 0.28*cm
        canvas.setFont(disc_font, disc_sz)
        canvas.setFillColor(colors.HexColor("#555555"))
        for ln in disc_lines:
            canvas.drawCentredString(w/2, ty, ln)
            ty -= line_gap

        canvas.setFont("Helvetica-BoldOblique", 7)
        canvas.setFillColor(colors.black)
        canvas.drawCentredString(w/2, ty, '"UNCONTROLLED IF PRINTED"')
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
            [Paragraph(_clean_approval_label(a.get("function","")), centered) for a in approvals],
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

    toc_num_main = ParagraphStyle("TNM", fontName="Helvetica-Bold", fontSize=10, leading=14)
    toc_num_sub  = ParagraphStyle("TNS", fontName="Helvetica",      fontSize=9,  leading=13, leftIndent=10)
    toc_lbl_main = ParagraphStyle("TLM", fontName="Helvetica-Bold", fontSize=10, leading=14)
    toc_lbl_sub  = ParagraphStyle("TLS", fontName="Helvetica",      fontSize=9,  leading=13)
    toc_dots_sty = ParagraphStyle("TD",  fontName="Helvetica",      fontSize=9,  leading=13,
                                   textColor=colors.HexColor("#BBBBBB"))

    toc_rows = []
    for num, lbl, is_sub in toc_entries:
        num_sty = toc_num_sub if is_sub else toc_num_main
        lbl_sty = toc_lbl_sub if is_sub else toc_lbl_main
        toc_rows.append([
            Paragraph(num, num_sty),
            Paragraph(lbl, lbl_sty),
            Paragraph("." * 55, toc_dots_sty),
        ])

    # Number column widened to comfortably fit "1.4" / "4.3" etc. in bold
    # 10pt without being forced to wrap character-by-character.
    toc_t = Table(toc_rows, colWidths=[1.8*cm, 9*cm, None])
    toc_t.setStyle(TableStyle([
        ("VALIGN",        (0,0), (-1,-1), "BOTTOM"),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING",    (0,0), (-1,-1), 0),
        ("LEFTPADDING",   (0,0), (-1,-1), 0),
        ("RIGHTPADDING",  (0,0), (-1,-1), 4),
    ]))
    story.append(toc_t)

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
    step_num = 0
    for step in steps:
        if step.get("type", "step") == "input_row":
            items = step.get("items", [])
            titles = "; ".join(it.get("title", "") for it in items)
            story.append(Paragraph("Trigger Sources", h2s))
            story.append(Paragraph(
                "A Corrective and Preventive Action Request (CPAR) may be triggered by any "
                f"of the following: {titles}.", numbered))
            story.append(Spacer(1, 0.15*cm))
            continue

        step_num += 1
        story.append(Paragraph(f"{step_num}. {step.get('title','')}", h2s))
        if step.get("text"):
            story.append(Paragraph(step["text"], numbered))
        if step.get("side_branch"):
            sb_title = step["side_branch"].get("title", "")
            story.append(Paragraph(f"→ In parallel: {sb_title}", numbered))
        story.append(Spacer(1, 0.15*cm))

    # Single-column process flowchart — paginated so it never exceeds one frame's height.
    # Box heights are now dynamic (based on wrapped title length), so chunks are built
    # greedily by actually measuring each candidate chart's computed height.
    if steps:
        story.append(Spacer(1, 0.4*cm))
        story.append(Paragraph("Process Flowchart", h2s))
        story.append(Spacer(1, 0.2*cm))

        avail_h = (PAGE_H - TOP_M - BOT_M) - 1*cm  # safety margin
        chunks, current = [], []
        for step in steps:
            trial = current + [step]
            if SwimlaneFlowchart(trial, avail_w).total_h > avail_h and current:
                chunks.append(current)
                current = [step]
            else:
                current = trial
        if current:
            chunks.append(current)

        for i, chunk in enumerate(chunks):
            chart = SwimlaneFlowchart(chunk, avail_w)
            story.append(chart)
            if i < len(chunks) - 1:
                story.append(PageBreak())
                story.append(Paragraph("Process Flowchart (continued)", h2s))
                story.append(Spacer(1, 0.2*cm))
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

        # ── Upload an already-existing document (form/policy/work instruction) ──
        with st.expander("📤 Upload an Existing Document"):
            st.caption(
                "For forms, policies, and work instructions that are already finished and "
                "approved outside this app — upload the file and log its control details here, "
                "rather than rebuilding it through the procedure builder."
            )
            with st.form("upload_existing_doc", clear_on_submit=True):
                uc1, uc2 = st.columns(2)
                with uc1:
                    up_code  = st.text_input("Document Code*", placeholder="e.g. FM:03.10 or NFP-EP-...")
                    up_title = st.text_input("Title*", placeholder="e.g. Corrective & Preventive Action Request")
                    up_cat   = st.selectbox("Category*",
                                             ["Procedure","Policy","Work Instruction","Form","External Standard","Other"])
                    up_internal = st.checkbox("Internal document", value=True)
                with uc2:
                    up_issue_date = st.date_input("Issue Date", value=None)
                    up_rev_label  = st.text_input("Revision (as printed on the document)", placeholder="e.g. Rev 5 or 00")
                    up_rev_date   = st.date_input("Revision Date", value=None)
                    up_approved   = st.text_input("Approved By", placeholder="e.g. Executive Director / ED")

                up_reviewed_date = st.date_input("Reviewed Date (for the yearly review cycle)", value=None)
                up_status  = st.selectbox("Status", ["Active","Draft","Obsolete","Superseded"])
                up_file    = st.file_uploader("File", type=["pdf","docx","doc","xlsx","xls","png","jpg","jpeg"])

                submitted = st.form_submit_button("Upload & Save")
                if submitted:
                    if not up_code or not up_title:
                        st.error("Document Code and Title are required.")
                    else:
                        try:
                            file_url, file_name = None, None
                            if up_file is not None:
                                file_bytes = up_file.getvalue()
                                storage_path = f"{up_code.replace('/', '-')}/{up_file.name}"
                                sb.storage.from_("documents").upload(
                                    storage_path, file_bytes,
                                    {"content-type": up_file.type or "application/octet-stream",
                                     "upsert": "true"})
                                file_url  = sb.storage.from_("documents").get_public_url(storage_path)
                                file_name = up_file.name

                            payload = {
                                "doc_code":      up_code,
                                "title":         up_title,
                                "doc_type":      up_cat,
                                "category":      up_cat,
                                "is_internal":   up_internal,
                                "status":        up_status,
                                "approved_by":   up_approved or None,
                                "issue_date":    up_issue_date.isoformat() if up_issue_date else None,
                                "revision_label":up_rev_label or None,
                                "revision_date": up_rev_date.isoformat() if up_rev_date else None,
                                "reviewed_date": up_reviewed_date.isoformat() if up_reviewed_date else None,
                                "created_by":    uid,
                            }
                            if file_url:
                                payload["file_url"]  = file_url
                                payload["file_name"] = file_name

                            existing = sb.table("master_documents").select("id").eq("doc_code", up_code).execute()
                            if existing.data:
                                sb.table("master_documents").update(payload).eq("doc_code", up_code).execute()
                                st.success(f"Updated existing master list entry for {up_code}.")
                            else:
                                sb.table("master_documents").insert(payload).execute()
                                st.success(f"Added {up_code} to the master list.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error uploading document: {e}")

        res     = sb.table("master_documents").select("*").order("doc_code").execute()
        masters = res.data or []

        today = date.today()

        def _next_review(m):
            """Next review = reviewed_date + 1 year. Returns None if never reviewed."""
            rd_raw = m.get("reviewed_date")
            if not rd_raw:
                return None
            try:
                rd = date.fromisoformat(str(rd_raw)[:10])
                return rd.replace(year=rd.year + 1)
            except Exception:
                return None

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            int_f = st.selectbox("Source", ["All","Internal","External"])
        with c2:
            msearch = st.text_input("Search", placeholder="code or title")
        with c3:
            review_f = st.selectbox("Review status",
                                     ["All","Never reviewed","Due soon (30 days)","Overdue","Obsolete"])
        with c4:
            cat_options = ["All","Procedure","Policy","Work Instruction","Form","External Standard","Other"]
            cat_f = st.selectbox("Category", cat_options)

        if int_f == "Internal":
            masters = [m for m in masters if m.get("is_internal")]
        elif int_f == "External":
            masters = [m for m in masters if not m.get("is_internal")]
        if msearch:
            s       = msearch.lower()
            masters = [m for m in masters
                       if s in (m.get("doc_code","") or "").lower()
                       or s in (m.get("title","") or "").lower()]
        if cat_f != "All":
            masters = [m for m in masters
                       if (m.get("category") or m.get("doc_type") or "") == cat_f]
        if review_f != "All":
            def _matches(m):
                is_obsolete = (m.get("status") == "Obsolete")
                if review_f == "Obsolete":
                    return is_obsolete
                if is_obsolete:
                    # Obsolete docs are out of the active review cycle —
                    # they don't count as never-reviewed/due/overdue.
                    return False
                nr = _next_review(m)
                if review_f == "Never reviewed":
                    return nr is None
                if nr is None:
                    return False
                if review_f == "Overdue":
                    return nr <= today
                if review_f == "Due soon (30 days)":
                    return today < nr <= today + timedelta(days=30)
                return True
            masters = [m for m in masters if _matches(m)]

        if not masters:
            st.info("No documents in master list yet.")
        else:
            st.caption(f"{len(masters)} document(s) in master list")

            hdr_cols = st.columns([1.5, 2.6, 1.1, 1, 1.3, 1.3, 0.6, 0.6, 0.6])
            for col, label in zip(hdr_cols,
                                   ["Code","Title","Category","Source","Reviewed Date","Next Review","","",""]):
                col.markdown(f"**{label}**")
            st.markdown("<hr style='margin:2px 0'>", unsafe_allow_html=True)

            for m in masters:
                mid = m["id"]
                is_obsolete = (m.get("status") == "Obsolete")
                row = st.columns([1.5, 2.6, 1.1, 1, 1.3, 1.3, 0.6, 0.6, 0.6])

                code_txt  = m.get("doc_code","—")
                title_txt = m.get("title","")
                if is_obsolete:
                    strike = "color:#999;text-decoration:line-through"
                    row[0].markdown(f"<span style='{strike}'>{code_txt}</span>", unsafe_allow_html=True)
                    row[1].markdown(f"<span style='{strike}'>{title_txt}</span>", unsafe_allow_html=True)
                else:
                    row[0].markdown(code_txt)
                    row[1].markdown(title_txt)
                row[2].markdown(m.get("category") or m.get("doc_type","—"))
                row[3].markdown("Internal" if m.get("is_internal") else "External")

                rd_raw = m.get("reviewed_date")
                try:
                    rd_val = date.fromisoformat(str(rd_raw)[:10]) if rd_raw else today
                except Exception:
                    rd_val = today
                new_reviewed = row[4].date_input("Reviewed", value=rd_val,
                                                  key=f"revdate_{mid}", label_visibility="collapsed")

                if is_obsolete:
                    row[5].markdown("⬛ *Obsolete — excluded from reviews*")
                else:
                    next_review = _next_review(m)
                    if next_review is None:
                        row[5].markdown("⚪ Never reviewed")
                    elif next_review <= today:
                        row[5].markdown(f"🔴 {next_review.isoformat()} (overdue)")
                    elif (next_review - today).days <= 30:
                        row[5].markdown(f"🟡 {next_review.isoformat()}")
                    else:
                        row[5].markdown(f"🟢 {next_review.isoformat()}")

                if row[6].button("💾", key=f"save_{mid}", help="Save reviewed date"):
                    try:
                        sb.table("master_documents").update(
                            {"reviewed_date": new_reviewed.isoformat()}
                        ).eq("id", mid).execute()
                        st.success(f"Reviewed date updated for {m.get('doc_code')}.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error saving: {e}")

                if is_obsolete:
                    if row[7].button("♻️", key=f"reactivate_{mid}", help="Reactivate this document"):
                        try:
                            sb.table("master_documents").update({"status": "Active"}).eq("id", mid).execute()
                            st.success(f"{code_txt} reactivated.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error reactivating: {e}")
                else:
                    if row[7].button("🚫", key=f"obsolete_{mid}", help="Mark as obsolete (kept in the list, excluded from reviews)"):
                        try:
                            sb.table("master_documents").update({"status": "Obsolete"}).eq("id", mid).execute()
                            st.success(f"{code_txt} marked obsolete.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error: {e}")

                if row[8].button("🗑️", key=f"del_{mid}", help="Permanently delete from master list"):
                    st.session_state[f"confirm_del_{mid}"] = True

                # Compact detail line: revision, approver, status, and a file link if uploaded
                detail_bits = []
                if m.get("revision_label"):
                    detail_bits.append(f"Rev {m['revision_label']}")
                if m.get("revision_date"):
                    detail_bits.append(f"revised {m['revision_date']}")
                if m.get("issue_date"):
                    detail_bits.append(f"issued {m['issue_date']}")
                if m.get("approved_by"):
                    detail_bits.append(f"approved by {m['approved_by']}")
                if m.get("status") and m.get("status") != "Active":
                    detail_bits.append(f"status: {m['status']}")
                detail_line = " • ".join(detail_bits)
                if detail_line or m.get("file_url"):
                    dcol1, dcol2 = st.columns([5,1])
                    if detail_line:
                        dcol1.caption(detail_line)
                    if m.get("file_url"):
                        dcol2.markdown(f"[📄 View file]({m['file_url']})")

                if st.session_state.get(f"confirm_del_{mid}"):
                    st.warning(
                        f"Delete **{m.get('doc_code')} — {m.get('title')}** from the master list? "
                        "This cannot be undone. Any document that references this code will keep "
                        "the reference, but it will no longer resolve to a master entry."
                    )
                    cc1, cc2 = st.columns([1,1])
                    with cc1:
                        if st.button("Yes, delete", key=f"confirm_yes_{mid}"):
                            try:
                                sb.table("master_documents").delete().eq("id", mid).execute()
                                st.session_state.pop(f"confirm_del_{mid}", None)
                                st.success("Deleted.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error deleting: {e}")
                    with cc2:
                        if st.button("Cancel", key=f"confirm_no_{mid}"):
                            st.session_state.pop(f"confirm_del_{mid}", None)
                            st.rerun()
                st.markdown("<hr style='margin:4px 0;opacity:0.3'>", unsafe_allow_html=True)


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
                st.markdown(f"*{_clean_approval_label(ap.get('function',''))}*")
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
    step_num = 0
    for step in steps:
        if step.get("type", "step") == "input_row":
            items = step.get("items", [])
            titles = "; ".join(it.get("title", "") for it in items)
            st.markdown("**Trigger Sources**")
            st.markdown(f"A CPAR may be triggered by any of the following: {titles}.")
            continue
        step_num += 1
        st.markdown(f"**{step_num}. {step.get('title','')}**")
        if step.get("text"):
            st.markdown(step["text"])
        if step.get("side_branch"):
            st.markdown(f"→ *In parallel:* {step['side_branch'].get('title','')}")


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