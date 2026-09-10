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



def add_kpi_page(c: canvas.Canvas, title, kpis: dict):
    c.setFillColor(WHITE)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    _title_bar(c, title)

    items = list(kpis.items())
    cols = 3
    card_w, card_h = 280, 108
    gap = 22
    start_x = 40
    start_y_from_top = 132

    for i, (label, value) in enumerate(items):
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



def add_chart_page(c: canvas.Canvas, title, fig):
    c.setFillColor(WHITE)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    _title_bar(c, title)

    png_path = _save_fig_png(fig)
    box_y = _y_from_top(BOX_Y_FROM_TOP + BOX_H)
    c.drawImage(png_path, BOX_X, box_y, width=BOX_W, height=BOX_H, preserveAspectRatio=False)
    os.unlink(png_path)



def add_table_page(c: canvas.Canvas, title, df, max_rows=18):
    """Native reportlab table - selectable/copyable text, not an image.
    max_rows defaults higher than the pptx version (10) since a PDF page has
    more usable vertical room without a slide's fixed aspect ratio."""
    c.setFillColor(WHITE)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    _title_bar(c, title)

    df = df.head(max_rows)
    data = [list(df.columns)] + [
        [f"{v:.2f}" if isinstance(v, float) else str(v) for v in row]
        for row in df.itertuples(index=False)
    ]

    col_w = BOX_W / len(df.columns)
    table = Table(data, colWidths=[col_w] * len(df.columns))
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAPCO_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, GRAY_BG]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    table_w, table_h = table.wrapOn(c, BOX_W, BOX_H)
    table.drawOn(c, BOX_X, _y_from_top(BOX_Y_FROM_TOP) - table_h)



def new_canvas(buf) -> canvas.Canvas:
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    return c


def finish(c: canvas.Canvas, buf) -> io.BytesIO:
    c.save()
    buf.seek(0)
    return buf
