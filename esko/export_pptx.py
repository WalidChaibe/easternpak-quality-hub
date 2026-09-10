"""
Build the Esko Lead-Time Analytics PPTX deck, styled to match the Quality
Indicators presentation: cover slide, section dividers, and content slides
with a title + two-tone accent rule + a chart dropped into a fixed content box.

Slide chrome (title bar, accent rule, cover, section dividers) is built with
native python-pptx shapes/text boxes - editable in PowerPoint, not rasterized.
Charts are matplotlib figures (napco_charts.py) rasterized to PNG and placed
in the content box at the box's native aspect ratio, so there's no letterboxing
and no cropped/crushed charts (see napco_theme.py for the fix).
"""
import io
import os
import tempfile
from datetime import date

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE

from esko import napco_theme as theme

# 960x540pt @ 72pt/in == 13.333in x 7.5in - matches the reference deck exactly.
SLIDE_W_IN = 13.333
SLIDE_H_IN = 7.5

# Content box: (40,40) pt origin, 880x408 pt size -> inches
BOX_X_IN = 40 / 72
BOX_Y_IN = 132 / 72          # below the title + accent rule
BOX_W_IN = 880 / 72
BOX_H_IN = 408 / 72

NAPCO_BLUE = RGBColor.from_string(theme.NAPCO_BLUE.lstrip("#"))
RED_ACCENT = RGBColor.from_string(theme.RED_ACCENT.lstrip("#"))
BLUE_ACCENT = RGBColor.from_string(theme.BLUE_ACCENT.lstrip("#"))
DARK_TEXT = RGBColor.from_string(theme.DARK_TEXT.lstrip("#"))
WHITE = RGBColor.from_string("FFFFFF")


def _blank_slide(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])  # blank layout


def _title_bar(slide, title_text):
    """Content title (Helvetica-Bold 22, napco blue) + two-tone accent rule."""
    tb = slide.shapes.add_textbox(Inches(40 / 72), Inches(20 / 72), Inches(8.5), Inches(0.5))
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = title_text
    run.font.size = Pt(22)
    run.font.bold = True
    run.font.color.rgb = NAPCO_BLUE
    run.font.name = "Arial"

    rule_y = Inches(64 / 72)
    red = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(40 / 72), rule_y, Inches(110 / 72), Pt(4))
    red.fill.solid(); red.fill.fore_color.rgb = RED_ACCENT; red.line.fill.background()
    blue = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(155 / 72), rule_y,
        Inches(SLIDE_W_IN - 195 / 72), Pt(4),
    )
    blue.fill.solid(); blue.fill.fore_color.rgb = BLUE_ACCENT; blue.line.fill.background()


def _save_fig_png(fig) -> str:
    path = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
    fig.savefig(path, facecolor="white")  # NO bbox_inches="tight" - keeps aspect ratio matched to the box
    return path


def add_content_slide(prs, title_text, fig):
    """A slide with the standard title bar + a chart dropped into the content box."""
    slide = _blank_slide(prs)
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = WHITE
    _title_bar(slide, title_text)

    png_path = _save_fig_png(fig)
    slide.shapes.add_picture(
        png_path, Inches(BOX_X_IN), Inches(BOX_Y_IN), width=Inches(BOX_W_IN), height=Inches(BOX_H_IN),
    )
    os.unlink(png_path)
    return slide


def add_cover_slide(prs, title, subtitle, date_str=None, logo_path=None):
    slide = _blank_slide(prs)
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = WHITE

    # red vertical bar, left edge
    red_bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Pt(4), Inches(SLIDE_H_IN))
    red_bar.fill.solid(); red_bar.fill.fore_color.rgb = RED_ACCENT; red_bar.line.fill.background()

    # blue bar near bottom of logo band
    bar_y = Inches(SLIDE_H_IN) - Inches(125 / 72)
    blue_bar = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(18 / 72), bar_y, Inches(SLIDE_W_IN) - Inches(18 / 72), Pt(3),
    )
    blue_bar.fill.solid(); blue_bar.fill.fore_color.rgb = BLUE_ACCENT; blue_bar.line.fill.background()

    if logo_path and os.path.exists(logo_path):
        slide.shapes.add_picture(logo_path, Inches(18 / 72), bar_y - Inches(110 / 72),
                                  width=Inches(280 / 72), height=Inches(110 / 72))

    title_box = slide.shapes.add_textbox(Inches(0.8), Inches(2.8), Inches(SLIDE_W_IN - 1.6), Inches(1.3))
    tf = title_box.text_frame; tf.word_wrap = True
    p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
    run = p.add_run(); run.text = title
    run.font.size = Pt(44); run.font.bold = True; run.font.color.rgb = NAPCO_BLUE; run.font.name = "Arial"

    rule = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(80 / 72), Inches(4.15),
                                   Inches(SLIDE_W_IN - 160 / 72), Pt(2))
    rule.fill.solid(); rule.fill.fore_color.rgb = RED_ACCENT; rule.line.fill.background()

    sub_box = slide.shapes.add_textbox(Inches(0.8), Inches(4.3), Inches(SLIDE_W_IN - 1.6), Inches(0.6))
    tf2 = sub_box.text_frame; tf2.word_wrap = True
    p2 = tf2.paragraphs[0]; p2.alignment = PP_ALIGN.CENTER
    r2 = p2.add_run(); r2.text = subtitle
    r2.font.size = Pt(20); r2.font.italic = True; r2.font.color.rgb = RGBColor.from_string("555555")
    r2.font.name = "Arial"

    if date_str:
        date_box = slide.shapes.add_textbox(Inches(SLIDE_W_IN - 2.5), Inches(0.4), Inches(2.1), Inches(0.4))
        tf3 = date_box.text_frame
        p3 = tf3.paragraphs[0]; p3.alignment = PP_ALIGN.RIGHT
        r3 = p3.add_run(); r3.text = date_str
        r3.font.size = Pt(14); r3.font.italic = True; r3.font.color.rgb = RGBColor.from_string("888888")
    return slide


def add_section_slide(prs, title):
    slide = _blank_slide(prs)
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = RGBColor.from_string(theme.GRAY_BG.lstrip("#"))

    y = Inches(SLIDE_H_IN * 0.58)
    rule = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(1.0), y, Inches(SLIDE_W_IN - 2.0), Pt(2))
    rule.fill.solid(); rule.fill.fore_color.rgb = RED_ACCENT; rule.line.fill.background()

    title_box = slide.shapes.add_textbox(Inches(0.8), y - Inches(70 / 72), Inches(SLIDE_W_IN - 1.6), Inches(1.0))
    tf = title_box.text_frame; tf.word_wrap = True
    p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
    run = p.add_run(); run.text = title
    run.font.size = Pt(40); run.font.bold = True; run.font.color.rgb = NAPCO_BLUE; run.font.name = "Arial"
    return slide


def add_kpi_slide(prs, title, kpis: dict):
    """KPI cards laid out as a simple grid of text boxes (spec section 8 KPI cards)."""
    slide = _blank_slide(prs)
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = WHITE
    _title_bar(slide, title)

    items = list(kpis.items())
    cols = 3
    card_w, card_h = Inches(3.9), Inches(1.5)
    gap = Inches(0.3)
    start_x, start_y = Inches(BOX_X_IN), Inches(BOX_Y_IN)

    for i, (label, value) in enumerate(items):
        row, col = divmod(i, cols)
        x = start_x + col * (card_w + gap)
        y = start_y + row * (card_h + gap)
        card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, card_w, card_h)
        card.fill.solid(); card.fill.fore_color.rgb = RGBColor.from_string(theme.LIGHT_BLUE.lstrip("#"))
        card.line.color.rgb = NAPCO_BLUE; card.line.width = Pt(0.75)
        tf = card.text_frame; tf.word_wrap = True
        tf.margin_left = tf.margin_right = Pt(10)
        p1 = tf.paragraphs[0]; p1.alignment = PP_ALIGN.CENTER
        r1 = p1.add_run(); r1.text = str(value)
        r1.font.size = Pt(26); r1.font.bold = True; r1.font.color.rgb = NAPCO_BLUE
        p2 = tf.add_paragraph(); p2.alignment = PP_ALIGN.CENTER
        r2 = p2.add_run(); r2.text = label
        r2.font.size = Pt(12); r2.font.color.rgb = DARK_TEXT
    return slide


def add_table_slide(prs, title, df, max_rows=10):
    """Simple native pptx table for the per-project variance list.

    max_rows defaults to 10 (not the full population): past that, rows get too
    thin to stay legible within the available slide height. Use multiple table
    slides (paginate df before calling) if more rows need to be shown.
    """
    slide = _blank_slide(prs)
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = WHITE
    _title_bar(slide, title)

    df = df.head(max_rows)
    n_rows, cols = len(df) + 1, len(df.columns)
    # The standard content box runs flush to the slide's bottom edge (BOX_Y_IN
    # + BOX_H_IN == SLIDE_H_IN), which is fine for a chart image but leaves a
    # table's last row with zero bottom margin. Reserve 0.5in below the table.
    table_h_in = SLIDE_H_IN - BOX_Y_IN - 0.5
    table_shape = slide.shapes.add_table(
        n_rows, cols, Inches(BOX_X_IN), Inches(BOX_Y_IN), Inches(BOX_W_IN), Inches(table_h_in),
    )
    table = table_shape.table

    # Pin every row to an equal share of the available height - python-pptx
    # otherwise lets rows grow to fit content, and 6+ rows at legible font
    # sizes exceeds the box.
    row_h = Emu(int(Inches(table_h_in) / n_rows))
    for r in range(n_rows):
        table.rows[r].height = row_h

    header_font_pt = 10
    data_font_pt = 9
    cell_margin_pt = 1

    for c, col_name in enumerate(df.columns):
        cell = table.cell(0, c)
        cell.text = str(col_name)
        cell.text_frame.word_wrap = True
        run = cell.text_frame.paragraphs[0].runs[0]
        run.font.bold = True
        run.font.size = Pt(header_font_pt)
        cell.fill.solid(); cell.fill.fore_color.rgb = NAPCO_BLUE
        run.font.color.rgb = WHITE
        cell.margin_top = cell.margin_bottom = Pt(cell_margin_pt)

    for r, (_, row) in enumerate(df.iterrows(), start=1):
        for c, val in enumerate(row):
            cell = table.cell(r, c)
            cell.text_frame.word_wrap = True
            if isinstance(val, float):
                cell.text = f"{val:.2f}"
            else:
                cell.text = str(val)
            run = cell.text_frame.paragraphs[0].runs[0]
            run.font.size = Pt(data_font_pt)
            cell.margin_top = cell.margin_bottom = Pt(cell_margin_pt)
    return slide


def new_presentation():
    prs = Presentation()
    prs.slide_width = Emu(int(SLIDE_W_IN * 914400))
    prs.slide_height = Emu(int(SLIDE_H_IN * 914400))
    return prs


def save_to_bytes(prs) -> io.BytesIO:
    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf
