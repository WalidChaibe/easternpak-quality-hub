"""
Customer trend classification: selected window vs the same dates one year earlier.
Pure functions - no Streamlit calls.

Windows are DATE based, so a partial month is compared like-for-like:
  cutoff = 2026-10-01, window = 3 months
  window W = 2026-07-02 .. 2026-10-01   vs   prior P = 2025-07-02 .. 2025-10-01
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd

STATUS_ORDER = [
    "New", "Returning", "Strong growth", "Organic growth", "Stable",
    "Organic loss", "Total loss", "No volume in either period", "Below threshold",
]

STATUS_RULES = {
    "New": "First invoice ever is within the last 12 months.",
    "Returning": "Invoiced before, but nothing in the same period last year; invoiced in the window.",
    "Strong growth": "Window volume up more than the strong-growth threshold vs same period last year.",
    "Organic growth": "Window volume up more than the stable band, up to the strong-growth threshold.",
    "Stable": "Window volume within +/- the stable band.",
    "Organic loss": "Window volume down more than the stable band (still invoiced within the last 12 months).",
    "Total loss": "No invoice at all in the last 12 months (fixed 12 months, whatever the window).",
    "No volume in either period": "Invoiced in the last 12 months, but not in the window or the comparison period.",
    "Below threshold": "Under the minimum MT in both the window and the comparison period.",
}


@dataclass
class Thresholds:
    stable_band: float = 0.10     # +/-10%
    strong_growth: float = 0.25   # >+25%
    min_mt: float = 5.0           # minimum MT in window or prior period for a % status


def windows(cutoff: pd.Timestamp, months: int):
    """Return (w_start, w_end, p_start, p_end), all inclusive dates."""
    w_end = cutoff
    w_start = cutoff - pd.DateOffset(months=months) + pd.Timedelta(days=1)
    p_end = cutoff - pd.DateOffset(years=1)
    p_start = p_end - pd.DateOffset(months=months) + pd.Timedelta(days=1)
    return w_start, w_end, p_start, p_end


def classify(df: pd.DataFrame, attrs: pd.DataFrame, cutoff: pd.Timestamp,
             months: int, th: Thresholds) -> pd.DataFrame:
    w_start, w_end, p_start, p_end = windows(cutoff, months)
    l12_start = cutoff - pd.DateOffset(years=1) + pd.Timedelta(days=1)

    d = df[["customer", "inv_date", "tons"]]
    def vol(lo, hi):
        m = (d["inv_date"] >= lo) & (d["inv_date"] <= hi)
        return d.loc[m].groupby("customer")["tons"].sum()

    out = attrs[["salesman", "industry", "area", "first_invoice", "last_invoice"]].copy()
    out["window_mt"] = vol(w_start, w_end)
    out["prior_mt"] = vol(p_start, p_end)
    out["last12_mt"] = vol(l12_start, cutoff)
    out[["window_mt", "prior_mt", "last12_mt"]] = out[["window_mt", "prior_mt", "last12_mt"]].fillna(0.0)
    out["change_mt"] = out["window_mt"] - out["prior_mt"]
    out["change_pct"] = np.where(out["prior_mt"] > 0, out["change_mt"] / out["prior_mt"].where(out["prior_mt"] > 0), np.nan)

    status = pd.Series("", index=out.index, dtype=object)
    pct = out["change_pct"]

    rules = [
        (out["last12_mt"] <= 0, "Total loss"),
        (out["first_invoice"] >= l12_start, "New"),
        ((out["prior_mt"] <= 0) & (out["window_mt"] > 0), "Returning"),
        ((out["prior_mt"] <= 0) & (out["window_mt"] <= 0), "No volume in either period"),
        (np.maximum(out["window_mt"], out["prior_mt"]) < th.min_mt, "Below threshold"),
        (pct > th.strong_growth, "Strong growth"),
        (pct > th.stable_band, "Organic growth"),
        (pct >= -th.stable_band, "Stable"),
        (pct < -th.stable_band, "Organic loss"),
    ]
    for mask, label in rules:                 # first matching rule wins
        status = status.mask((status == "") & mask, label)
    out["status"] = pd.Categorical(status, categories=STATUS_ORDER, ordered=True)

    out.attrs.update(w_start=w_start, w_end=w_end, p_start=p_start, p_end=p_end, l12_start=l12_start)
    return out.reset_index().rename(columns={"index": "customer"})


def status_summary(cls: pd.DataFrame) -> pd.DataFrame:
    s = cls.groupby("status", observed=False).agg(
        customers=("customer", "count"),
        window_mt=("window_mt", "sum"),
        prior_mt=("prior_mt", "sum"),
        change_mt=("change_mt", "sum"),
    ).reset_index()
    return s
