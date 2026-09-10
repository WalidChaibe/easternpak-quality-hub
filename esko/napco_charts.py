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


def ageing_stacked_bar(bucket_labels, stage_series: dict, title="Ageing Buckets, Stacked by Stage"):
    fig, ax = theme.new_content_figure()
    x = np.arange(len(bucket_labels))
    bottom = np.zeros(len(bucket_labels))
    for i, (stage, vals) in enumerate(stage_series.items()):
        vals = np.array(vals, dtype=float)
        ax.bar(x, vals, bottom=bottom, width=0.6, label=stage,
               color=theme.PALETTE_15[i % len(theme.PALETTE_15)])
        bottom += vals
    ax.set_xticks(x)
    ax.set_xticklabels(bucket_labels)
    ax.set_ylabel("Open project count")
    theme.clean_axes(ax)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=4, frameon=False)
    ax.set_title(title, fontsize=14, color=theme.NAPCO_BLUE, fontweight="bold", loc="left")
    fig.tight_layout()
    return fig
