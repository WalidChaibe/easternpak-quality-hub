"""
Build the Esko Lead-Time Analytics report as a PDF, using the exact same
"Quality Indicators" visual language as the PPTX export (esko/export_pptx.py,
esko/napco_theme.py) - Napco blue/red/gray palette, the title-bar + two-tone
accent-rule pattern, the KPI card grid, the cover-slide layout with the red
vertical bar and logo band.

Charts are matplotlib figures (esko/napco_charts.py) embedded as images - this
is the normal, expected way to include charts in a PDF (unlike PPTX, there is
no "native editable chart" concept a PDF reader offers anyway), so it does not
carry the same drawback the PPTX had.

Built with reportlab (already a project dependency) rather than introducing a
new library.
"""
import io
import os

from reportlab.lib.pagesizes import landscape
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

from esko import napco_theme as theme
from esko.export_pptx import _save_fig_png

# Match the PPTX slide dimensions exactly (13.333in x 7.5in, 16:9) so every
# chart figure (sized to CONTENT_BOX_FIGSIZE) drops in with the same
# proportions and no redesign is needed.
PAGE_W = 13.333 * inch
PAGE_H = 7.5 * inch

BOX_X = 40
BOX_Y_FROM_TOP = 132   # matches export_pptx.py's BOX_Y_IN * 72
BOX_W = 880
BOX_H = 408

NAPCO_BLUE = colors.HexColor(theme.NAPCO_BLUE)
LIGHT_BLUE = colors.HexColor(theme.LIGHT_BLUE)
RED_ACCENT = colors.HexColor(theme.RED_ACCENT)
BLUE_ACCENT = colors.HexColor(theme.BLUE_ACCENT)
DARK_TEXT = colors.HexColor(theme.DARK_TEXT)
GRAY_BG = colors.HexColor(theme.GRAY_BG)
WHITE = colors.white


def _y_from_top(pt_from_top):
    """reportlab's origin is bottom-left; the pptx layout constants are
    measured from the top - convert once, here, so every function below can
    keep using the same top-down numbers as export_pptx.py."""
    return PAGE_H - pt_from_top


def _wrap_text(text, font_name, font_size, max_width, c):
    """Greedy word-wrap using reportlab's own string-width metric, so lines
    actually fit the page instead of running off the edge."""
    words = text.split()
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if c.stringWidth(candidate, font_name, font_size) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _title_bar(c: canvas.Canvas, title_text: str):
    c.setFont("Helvetica-Bold", 22)
    c.setFillColor(NAPCO_BLUE)
    c.drawString(40, _y_from_top(20 + 22), title_text)

    rule_y = _y_from_top(64)
    c.setFillColor(RED_ACCENT)
    c.rect(40, rule_y, 110, 4, stroke=0, fill=1)
    c.setFillColor(BLUE_ACCENT)
    c.rect(155, rule_y, (PAGE_W - 195), 4, stroke=0, fill=1)


def _new_page(c: canvas.Canvas):
    c.showPage()
    c.setPageSize((PAGE_W, PAGE_H))


def add_cover_page(c: canvas.Canvas, title, subtitle, date_str=None, logo_path=None):
    c.setFillColor(WHITE)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

    # red vertical bar, left edge
    c.setFillColor(RED_ACCENT)
    c.rect(0, 0, 4, PAGE_H, stroke=0, fill=1)

    # blue bar near bottom of logo band
    bar_y = 125
    c.setFillColor(BLUE_ACCENT)
    c.rect(18, bar_y, PAGE_W - 18, 3, stroke=0, fill=1)

    if logo_path and os.path.exists(logo_path):
        c.drawImage(logo_path, 18, bar_y + 5, width=280, height=110,
                     preserveAspectRatio=True, mask="auto")

    c.setFont("Helvetica-Bold", 44)
    c.setFillColor(NAPCO_BLUE)
    c.drawCentredString(PAGE_W / 2, PAGE_H - 3.2 * inch, title)

    c.setFillColor(RED_ACCENT)
    c.rect(80, PAGE_H - 4.25 * inch, PAGE_W - 160, 2, stroke=0, fill=1)

    c.setFont("Helvetica-Oblique", 20)
    c.setFillColor(colors.HexColor("#555555"))
    c.drawCentredString(PAGE_W / 2, PAGE_H - 4.55 * inch, subtitle)

    if date_str:
        c.setFont("Helvetica-Oblique", 14)
        c.setFillColor(colors.HexColor("#888888"))
        c.drawRightString(PAGE_W - 40, PAGE_H - 0.55 * inch, date_str)



def add_section_page(c: canvas.Canvas, title):
    c.setFillColor(GRAY_BG)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)

    y = PAGE_H * 0.58
    c.setFillColor(RED_ACCENT)
    c.rect(72, y, PAGE_W - 144, 2, stroke=0, fill=1)

    c.setFont("Helvetica-Bold", 40)
    c.setFillColor(NAPCO_BLUE)
    c.drawCentredString(PAGE_W / 2, y + 30, title)



def add_kpi_page(c: canvas.Canvas, title, kpis: dict, footnote: str = None):
    """kpis values can be a plain string/number, or a (value, subtitle) tuple
    where subtitle is a short clarifying line shown in smaller gray text under
    the label - use this for any metric whose meaning isn't self-evident from
    its name alone (e.g. distinguishing 'weighted active task time' from
    'actual elapsed business days')."""
    c.setFillColor(WHITE)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    _title_bar(c, title)

    items = list(kpis.items())
    cols = 3
    card_w, card_h = 280, 124
    gap = 22
    start_x = 40
    start_y_from_top = 140

    for i, (label, entry) in enumerate(items):
        if isinstance(entry, tuple):
            value, subtitle = entry
        else:
            value, subtitle = entry, None

        row, col = divmod(i, cols)
        x = start_x + col * (card_w + gap)
        y_top = start_y_from_top + row * (card_h + gap)
        y = _y_from_top(y_top + card_h)

        c.setFillColor(LIGHT_BLUE)
        c.setStrokeColor(NAPCO_BLUE)
        c.roundRect(x, y, card_w, card_h, 8, stroke=1, fill=1)

        c.setFont("Helvetica-Bold", 26)
        c.setFillColor(NAPCO_BLUE)
        c.drawCentredString(x + card_w / 2, y + card_h - 42, str(value))

        c.setFont("Helvetica", 12)
        c.setFillColor(DARK_TEXT)
        c.drawCentredString(x + card_w / 2, y + card_h - 68, label)

        if subtitle:
            c.setFont("Helvetica-Oblique", 9)
            c.setFillColor(colors.HexColor("#6B7280"))
            c.drawCentredString(x + card_w / 2, y + card_h - 88, subtitle)

    if footnote:
        c.setFont("Helvetica-Oblique", 10)
        c.setFillColor(colors.HexColor("#6B7280"))
        rows_used = (len(items) - 1) // cols + 1
        note_y_top = start_y_from_top + rows_used * (card_h + gap) + 20
        max_width = PAGE_W - 2 * start_x
        for i, line in enumerate(_wrap_text(footnote, "Helvetica-Oblique", 10, max_width, c)):
            c.drawString(start_x, _y_from_top(note_y_top + i * 14), line)



def add_chart_page(c: canvas.Canvas, title, fig):
    c.setFillColor(WHITE)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    _title_bar(c, title)

    png_path = _save_fig_png(fig)
    box_y = _y_from_top(BOX_Y_FROM_TOP + BOX_H)
    c.drawImage(png_path, BOX_X, box_y, width=BOX_W, height=BOX_H, preserveAspectRatio=False)
    os.unlink(png_path)



def add_table_page(c: canvas.Canvas, title, df, max_rows=None):
    """Native reportlab table(s) - selectable/copyable text, not an image.
    Auto-paginates: if df has more rows than fit within the content box at
    the fixed font/padding sizes below, it renders multiple pages (each with
    the same title + '(continued)' on the 2nd+), calling showPage() between
    them itself. The LAST page does NOT call showPage() - same convention as
    every other add_* function here, leaving that to the caller.
    max_rows, if given, still caps the total rows shown before pagination."""
    if max_rows is not None:
        df = df.head(max_rows)

    header_font_pt, data_font_pt, pad_pt = 10, 9, 4
    row_h_estimate = data_font_pt + 2 * pad_pt + 3   # matches the actual TOPPADDING/BOTTOMPADDING below
    header_h_estimate = header_font_pt + 2 * pad_pt + 3
    rows_per_page = max(1, int((BOX_H - header_h_estimate) // row_h_estimate))

    chunks = [df.iloc[i:i + rows_per_page] for i in range(0, len(df), rows_per_page)] or [df]

    for chunk_i, chunk in enumerate(chunks):
        page_title = title if chunk_i == 0 else f"{title} (continued)"

        c.setFillColor(WHITE)
        c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
        _title_bar(c, page_title)

        data = [list(chunk.columns)] + [
            [f"{v:.2f}" if isinstance(v, float) else str(v) for v in row]
            for row in chunk.itertuples(index=False)
        ]

        col_w = BOX_W / len(chunk.columns)
        table = Table(data, colWidths=[col_w] * len(chunk.columns))
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAPCO_BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), data_font_pt),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, GRAY_BG]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
            ("TOPPADDING", (0, 0), (-1, -1), pad_pt),
            ("BOTTOMPADDING", (0, 0), (-1, -1), pad_pt),
        ]))

        table_w, table_h = table.wrapOn(c, BOX_W, BOX_H)
        table.drawOn(c, BOX_X, _y_from_top(BOX_Y_FROM_TOP) - table_h)

        if chunk_i < len(chunks) - 1:
            c.showPage()



def new_canvas(buf) -> canvas.Canvas:
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    return c


def finish(c: canvas.Canvas, buf) -> io.BytesIO:
    c.save()
    buf.seek(0)
    return buf