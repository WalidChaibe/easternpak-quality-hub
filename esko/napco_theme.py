"""
Napco brand theme for the matplotlib charts embedded in the PPTX export.

Ported from the Quality Indicators presentation style, with the known bugs
fixed rather than replicated (see project handoff notes):
  - pad is now a FRACTION of the axis range, not a hardcoded data-unit value
    (the old 0.6 / 1 constants only worked for lead-time days / complaint counts).
  - Figures are sized to match the PPTX content box aspect ratio directly, and
    savefig is NOT cropped with bbox_inches="tight" - so the drawn box size and
    the actual image size stay in sync (no letterboxing, no crushed root-cause
    charts).
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

NAPCO_BLUE = "#0D68A3"
LIGHT_BLUE = "#D5E8F0"
DARK_TEXT = "#1F2937"
GRAY_BG = "#F5F5F5"
RED_ACCENT = "#DE201B"
BLUE_ACCENT = "#0C5595"
GOLD = "#C1A02E"
PREV_YEAR_BLUE = "#006394"
CURRENT_YEAR_GOLD = "#C1A02E"

CATEGORY_PALETTE = {
    "Service": "#C1A02E",
    "Quality": "#006394",
    "Invalid": "#D8C37D",
    "Commercial": "#2E8449",
}

PALETTE_15 = [
    "#0D68A3", "#C1A02E", "#2E8449", "#DE201B", "#006394",
    "#D8C37D", "#0C5595", "#8E5572", "#4C956C", "#F2A65A",
    "#457B9D", "#B5838D", "#6A994E", "#BC6C25", "#3A5A40",
]

# Content box on the slide is 880x408pt at 72pt/in -> 12.222in x 5.667in.
# Match figsize to this EXACTLY so savefig output can be dropped straight
# into the drawImage box with no letterboxing, no bbox_inches="tight" cropping.
CONTENT_BOX_FIGSIZE = (12.222, 5.667)


def apply_rcparams():
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 11,
        "figure.dpi": 160,
        "savefig.dpi": 320,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": False,
    })


def clean_axes(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(False)


def add_value_labels(ax, bars, fmt="{:.1f}", pad_frac=0.015):
    """
    Reconciled value-label helper: pad is a FRACTION of the axis y-range,
    so it scales correctly whether values are lead-time days, SAR costs, or
    percentages - unlike the old hardcoded 0.6 / 1 data-unit pads.
    """
    ymin, ymax = ax.get_ylim()
    pad = (ymax - ymin) * pad_frac
    for bar in bars:
        height = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2, height + pad,
            fmt.format(height), ha="center", va="bottom",
            fontsize=10, color="#4D4D4D",
        )


def new_content_figure():
    """Figure sized to exactly match the PPTX content box - no cropping needed."""
    apply_rcparams()
    fig, ax = plt.subplots(figsize=CONTENT_BOX_FIGSIZE)
    fig.patch.set_facecolor("white")
    return fig, ax
