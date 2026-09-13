"""
Napco-styled matplotlib chart archetypes used ONLY for the PPTX export.
(The live Streamlit dashboard uses esko/charts.py, plotly, for interactivity -
these are separate, purpose-built layers: plotly for on-screen, matplotlib for
static rasterization into a fixed-size slide box.)

Each function returns a matplotlib Figure sized to napco_theme.CONTENT_BOX_FIGSIZE,
ready to be saved straight to PNG and dropped into the PPTX content box with no
cropping and no aspect-ratio drift.
"""
import textwrap
import numpy as np
from esko import napco_theme as theme


def ranked_bar(labels, values, title="", ylabel="Days", color=None, wrap=18, rotation=25):
    """Single-series ranked bar. Caller controls sort order (descending for
    'lead time by stage', process order for the flow overview)."""
    fig, ax = theme.new_content_figure()
    color = color or theme.GOLD
    wrapped = [textwrap.fill(str(l), wrap) for l in labels]
    bars = ax.bar(wrapped, values, width=0.6, color=color)
    ax.set_ylim(0, max(values) * 1.15 if len(values) else 1)
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontsize=14, color=theme.NAPCO_BLUE, fontweight="bold", loc="left")
    theme.clean_axes(ax)
    theme.add_value_labels(ax, bars, fmt="{:.1f}")
    fig.autofmt_xdate(rotation=rotation, ha="right")
    fig.tight_layout()
    return fig


def pareto_dual_axis(labels, values, cum_pct, title="Pareto - Top 10 Steps"):
    """Dual-axis bar + cumulative-% line, napco archetype 6."""
    fig, ax = theme.new_content_figure()
    wrapped = [textwrap.fill(str(l), 16) for l in labels]
    x = np.arange(len(labels))
    bars = ax.bar(x, values, width=0.6, color=theme.NAPCO_BLUE)
    ax.set_xticks(x)
    ax.set_xticklabels(wrapped, rotation=35, ha="right")
    ax.set_ylabel("Total days impact")
    ax.set_ylim(0, max(values) * 1.2 if len(values) else 1)
    theme.clean_axes(ax)
    theme.add_value_labels(ax, bars, fmt="{:.1f}")

    ax2 = ax.twinx()
    ax2.plot(x, np.array(cum_pct) * 100, color=theme.GOLD, lw=2.5, marker="o", ms=6, zorder=5)
    ax2.set_ylim(0, 105)
    ax2.set_ylabel("Cumulative %")
    ax2.spines["top"].set_visible(False)

    ax.set_title(title, fontsize=14, color=theme.NAPCO_BLUE, fontweight="bold", loc="left")
    fig.tight_layout()
    return fig


def stage_owner_grouped_bar(stage_labels, owner_series: dict, title="Lead Time by Stage x Completed By"):
    """4-series-style grouped bar (owners as series), napco archetype 3 simplified."""
    fig, ax = theme.new_content_figure()
    n_series = max(len(owner_series), 1)
    width = min(0.8 / n_series, 0.28)
    x = np.arange(len(stage_labels))
    for i, (owner, vals) in enumerate(owner_series.items()):
        offset = (i - (n_series - 1) / 2) * width
        ax.bar(x + offset, vals, width=width, label=owner,
               color=theme.PALETTE_15[i % len(theme.PALETTE_15)])
    ax.set_xticks(x)
    ax.set_xticklabels([textwrap.fill(s, 16) for s in stage_labels], rotation=35, ha="right")
    ax.set_ylabel("Total days")
    theme.clean_axes(ax)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=min(n_series, 4), frameon=False)
    ax.set_title(title, fontsize=14, color=theme.NAPCO_BLUE, fontweight="bold", loc="left")
    fig.subplots_adjust(bottom=0.35)
    return fig


def single_stacked_bar(segments: list, title="", ylabel=""):
    """One horizontal bar, stacked into 2+ colored segments - for showing a
    single pool split into parts (e.g. 511 aged open projects: how many are
    stuck on the customer vs stuck internally). segments: list of (label, value, color).
    Communicates 'this is one pool, mostly one color' far more viscerally than
    two separate side-by-side bars or KPI cards."""
    fig, ax = theme.new_content_figure()
    total = sum(v for _, v, _ in segments)
    left = 0
    for label, value, color in segments:
        ax.barh([0], [value], left=left, height=0.5, color=color,
                label=f"{label} ({value:,} - {value/total:.0%})")
        # value label centered in its own segment, white text if segment is wide enough
        if value / total > 0.06:
            ax.text(left + value / 2, 0, f"{value:,}", ha="center", va="center",
                    fontsize=13, color="white", fontweight="bold")
        left += value
    ax.set_xlim(0, total)
    ax.set_ylim(-1, 1)
    ax.set_yticks([])
    ax.set_xlabel(ylabel)
    theme.clean_axes(ax)
    ax.spines["left"].set_visible(False)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.25), ncol=len(segments), frameon=False, fontsize=11)
    ax.set_title(title, fontsize=14, color=theme.NAPCO_BLUE, fontweight="bold", loc="left")
    fig.tight_layout()
    return fig


def occurrence_histogram(counts, title="", xlabel="Occurrences per project", ylabel="Number of projects"):
    """Histogram of how many times a step recurs within a single project -
    e.g. Technical Approval happening 1x vs 10x on the same project. Shows the
    actual SHAPE of a rework signal (rare outlier vs. common pattern), which a
    single 'up to 10x' headline number can't convey."""
    fig, ax = theme.new_content_figure()
    max_val = int(max(counts)) if len(counts) else 1
    bins = np.arange(1, max_val + 2) - 0.5
    n, bin_edges, patches = ax.hist(counts, bins=bins, color=theme.NAPCO_BLUE, rwidth=0.7)
    ax.set_xticks(range(1, max_val + 1))
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    theme.clean_axes(ax)
    for count, edge in zip(n, bin_edges):
        if count > 0:
            ax.text(edge + 0.5, count + max(n) * 0.01, f"{int(count)}", ha="center", va="bottom", fontsize=10, color="#4D4D4D")
    ax.set_title(title, fontsize=14, color=theme.NAPCO_BLUE, fontweight="bold", loc="left")
    fig.tight_layout()
    return fig