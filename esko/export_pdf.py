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
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Table, TableStyle

from esko import napco_theme as theme
import io as _io


def _save_fig_png_bytes(fig, dpi=300):
    """PDF-specific chart rasterization, matching the reference template's
    fig_to_png_bytes exactly: bbox_inches='tight' crops to content (so a
    chart with a short title/no legend doesn't carry dead whitespace), and
    the drawImage call below uses preserveAspectRatio=True to center whatever
    size comes out without distorting it.

    NOT shared with export_pptx.py's _save_fig_png, which deliberately avoids
    bbox_inches='tight' for a different reason documented there (the PPTX
    content box needs the saved image's aspect ratio to exactly match the
    box's own, since it doesn't center/pad - cropping there would drift the
    two apart again, re-introducing the letterboxing bug already fixed for
    that pathway). Two different embedding strategies, so two functions."""
    buf = _io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor="white")
    buf.seek(0)
    return buf

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
TITLE_BLUE = colors.HexColor("#0E5E86")   # matches the reference PDF template's heading color exactly
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
    c.setFillColor(TITLE_BLUE)
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
    """Exact port of the reference draw_cover_slide(): logo + blue bar band
    sits near the TOP of the page (not the bottom), the vertical red bar only
    spans that top band's height, and title/rule/subtitle are centered as a
    group in the remaining space between the band and the date line."""
    W, H = PAGE_W, PAGE_H

    c.setFillColorRGB(1, 1, 1)
    c.rect(0, 0, W, H, fill=1, stroke=0)

    if logo_path and os.path.exists(logo_path):
        c.drawImage(logo_path, 18, H - 115, width=280, height=110,
                    preserveAspectRatio=True, mask="auto")

    # Blue horizontal bar - from the logo's left edge to the right edge of the page
    line_y = H - 125
    logo_left = 18
    c.setFillColor(BLUE_ACCENT)
    c.rect(logo_left, line_y, W - logo_left, 4, fill=1, stroke=0)

    # Thin vertical red bar - flush with the left edge, top down to the blue line only
    c.setFillColor(RED_ACCENT)
    c.rect(0, line_y, 4, H - line_y, fill=1, stroke=0)

    # Title block centered in the remaining space (below the blue line, above the date)
    remaining_center = (H - 125 + 50) / 2
    title_font_size = 44
    subtitle_font_size = 20
    gap = 28

    title_y = remaining_center + gap + 10
    c.setFillColor(TITLE_BLUE)
    c.setFont("Helvetica-Bold", title_font_size)
    title_w = c.stringWidth(title, "Helvetica-Bold", title_font_size)
    c.drawString((W - title_w) / 2, title_y, title)

    rule_y = title_y - gap
    c.setFillColor(RED_ACCENT)
    c.rect(80, rule_y, W - 160, 2, fill=1, stroke=0)

    sub_y = rule_y - gap - 4
    c.setFillColor(colors.HexColor("#555555"))
    c.setFont("Helvetica-Oblique", subtitle_font_size)
    sub_w = c.stringWidth(subtitle, "Helvetica-Oblique", subtitle_font_size)
    c.drawString((W - sub_w) / 2, sub_y, subtitle)

    if date_str:
        c.setFont("Helvetica-Oblique", 14)
        c.setFillColor(colors.HexColor("#888888"))
        date_w = c.stringWidth(date_str, "Helvetica-Oblique", 14)
        c.drawString(W - date_w - 40, 28, date_str)



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



def add_overview_page(c: canvas.Canvas, title, stats: list, fig, stats_panel_width_frac: float = 0.32):
    """Single stat panel on the left (one bordered rectangle, stats stacked
    inside it top to bottom, each with an optional percentage), a larger
    chart taking up the rest of the width on the right. No dividing line, no
    sub-headings on either side - just one title bar and two zones."""
    c.setFillColor(WHITE)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    _title_bar(c, title)

    panel_w = (PAGE_W - 2 * BOX_X) * stats_panel_width_frac
    panel_x = BOX_X
    panel_y = _y_from_top(BOX_Y_FROM_TOP + BOX_H)
    panel_h = BOX_H

    c.setFillColor(LIGHT_BLUE)
    c.setStrokeColor(NAPCO_BLUE)
    c.roundRect(panel_x, panel_y, panel_w, panel_h, 10, stroke=1, fill=1)

    row_h = panel_h / len(stats)
    for i, (value, label, pct) in enumerate(stats):
        row_top_y = panel_y + panel_h - i * row_h
        row_center_y = row_top_y - row_h / 2
        c.setFont("Helvetica-Bold", 26)
        c.setFillColor(NAPCO_BLUE)
        value_text = f"{value}" + (f"  ({pct})" if pct else "")
        c.drawCentredString(panel_x + panel_w / 2, row_center_y + 8, value_text)
        c.setFont("Helvetica", 12)
        c.setFillColor(DARK_TEXT)
        c.drawCentredString(panel_x + panel_w / 2, row_center_y - 14, label)
        if i > 0:
            c.setStrokeColor(colors.HexColor("#BFD9E8"))
            c.setLineWidth(0.75)
            c.line(panel_x + 16, row_top_y, panel_x + panel_w - 16, row_top_y)

    chart_x = panel_x + panel_w + 30
    chart_w = PAGE_W - BOX_X - chart_x
    png_buf = _save_fig_png_bytes(fig)
    c.drawImage(ImageReader(png_buf), chart_x, panel_y, width=chart_w, height=panel_h,
                preserveAspectRatio=True, anchor="c", mask="auto")


def add_split_page(c: canvas.Canvas, title, left_title, left_stats: list, right_title, right_fig, right_subtitle: str = None):
    """Two halves side by side, sharing one title bar: left is a small stack of
    big-number stat cards (value, label), right is a chart image with an
    optional one-line subtitle (e.g. a total count the chart itself doesn't
    show, since the chart's bars only show the breakdown, not the sum). Used
    for 'closed vs. open at a glance' - the two populations are genuinely
    different kinds of data (a rate for one, a distribution for the other),
    so putting them on one page as a comparison reads better than two
    separate pages."""
    c.setFillColor(WHITE)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    _title_bar(c, title)

    mid_x = PAGE_W / 2
    col_pad = 24

    # Left half: sub-heading + stacked stat cards
    c.setFont("Helvetica-Bold", 16)
    c.setFillColor(TITLE_BLUE)
    c.drawString(BOX_X, _y_from_top(BOX_Y_FROM_TOP), left_title)

    card_w = mid_x - BOX_X - col_pad
    card_h = 110
    gap = 20
    for i, (value, label) in enumerate(left_stats):
        y_top = BOX_Y_FROM_TOP + 40 + i * (card_h + gap)
        y = _y_from_top(y_top + card_h)
        c.setFillColor(LIGHT_BLUE)
        c.setStrokeColor(NAPCO_BLUE)
        c.roundRect(BOX_X, y, card_w, card_h, 8, stroke=1, fill=1)
        c.setFont("Helvetica-Bold", 30)
        c.setFillColor(NAPCO_BLUE)
        c.drawCentredString(BOX_X + card_w / 2, y + card_h - 48, str(value))
        c.setFont("Helvetica", 13)
        c.setFillColor(DARK_TEXT)
        c.drawCentredString(BOX_X + card_w / 2, y + card_h - 75, label)

    # Divider between halves
    c.setStrokeColor(colors.HexColor("#DDDDDD"))
    c.setLineWidth(1)
    c.line(mid_x, _y_from_top(BOX_Y_FROM_TOP + BOX_H), mid_x, _y_from_top(BOX_Y_FROM_TOP))

    # Right half: sub-heading (+ optional total-count subtitle) + chart
    c.setFont("Helvetica-Bold", 16)
    c.setFillColor(TITLE_BLUE)
    c.drawString(mid_x + col_pad, _y_from_top(BOX_Y_FROM_TOP), right_title)

    chart_top_offset = BOX_Y_FROM_TOP + 30
    if right_subtitle:
        c.setFont("Helvetica-Bold", 13)
        c.setFillColor(DARK_TEXT)
        c.drawString(mid_x + col_pad, _y_from_top(chart_top_offset), right_subtitle)
        chart_top_offset += 26

    png_buf = _save_fig_png_bytes(right_fig)
    right_box_w = mid_x - col_pad - 20
    right_box_h = BOX_H - (chart_top_offset - BOX_Y_FROM_TOP)
    box_y = _y_from_top(chart_top_offset + right_box_h)
    c.drawImage(ImageReader(png_buf), mid_x + col_pad, box_y, width=right_box_w, height=right_box_h,
                preserveAspectRatio=True, anchor="c", mask="auto")


def add_headline_comparison_page(c: canvas.Canvas, title, cards: list, note: str = None):
    """A small number (2-3) of large, prominent stat cards side by side, for a
    single comparison the whole page exists to make (e.g. two different ways
    of computing 'total system lead time', and why they differ)."""
    c.setFillColor(WHITE)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    _title_bar(c, title)

    n = len(cards)
    gap = 40
    card_w = (BOX_W - gap * (n - 1)) / n
    card_h = 200
    start_y_top = BOX_Y_FROM_TOP + 60

    for i, (value, label, sublabel) in enumerate(cards):
        x = BOX_X + i * (card_w + gap)
        y = _y_from_top(start_y_top + card_h)
        c.setFillColor(LIGHT_BLUE)
        c.setStrokeColor(NAPCO_BLUE)
        c.roundRect(x, y, card_w, card_h, 10, stroke=1, fill=1)
        c.setFont("Helvetica-Bold", 42)
        c.setFillColor(NAPCO_BLUE)
        c.drawCentredString(x + card_w / 2, y + card_h - 75, str(value))
        c.setFont("Helvetica-Bold", 15)
        c.setFillColor(DARK_TEXT)
        c.drawCentredString(x + card_w / 2, y + card_h - 110, label)
        if sublabel:
            c.setFont("Helvetica-Oblique", 11)
            c.setFillColor(colors.HexColor("#6B7280"))
            c.drawCentredString(x + card_w / 2, y + card_h - 130, sublabel)

    if note:
        c.setFont("Helvetica-Oblique", 11)
        c.setFillColor(colors.HexColor("#6B7280"))
        max_width = PAGE_W - 2 * BOX_X
        note_y_top = start_y_top + card_h + 40
        for i, line in enumerate(_wrap_text(note, "Helvetica-Oblique", 11, max_width, c)):
            c.drawString(BOX_X, _y_from_top(note_y_top + i * 15), line)


def add_bullets_page(c: canvas.Canvas, title, bullets: list):
    """Simple bulleted text page, for a closing 'what this suggests' summary."""
    c.setFillColor(WHITE)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    _title_bar(c, title)

    y_top = BOX_Y_FROM_TOP + 30
    max_width = BOX_W - 30
    for bullet in bullets:
        c.setFillColor(RED_ACCENT)
        c.circle(BOX_X + 4, _y_from_top(y_top + 6), 3, fill=1, stroke=0)
        c.setFont("Helvetica", 14)
        c.setFillColor(DARK_TEXT)
        lines = _wrap_text(bullet, "Helvetica", 14, max_width, c)
        for i, line in enumerate(lines):
            c.drawString(BOX_X + 18, _y_from_top(y_top + i * 20), line)
        y_top += len(lines) * 20 + 26


def add_chart_page(c: canvas.Canvas, title, fig):
    c.setFillColor(WHITE)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    _title_bar(c, title)

    png_buf = _save_fig_png_bytes(fig)
    box_y = _y_from_top(BOX_Y_FROM_TOP + BOX_H)
    c.drawImage(ImageReader(png_buf), BOX_X, box_y, width=BOX_W, height=BOX_H,
                preserveAspectRatio=True, anchor="c", mask="auto")



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