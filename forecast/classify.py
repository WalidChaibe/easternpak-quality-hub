"""
Customer trend classification: selected window vs a comparison period.
Pure functions - no Streamlit calls.

Windows are DATE based, so a partial month is compared like-for-like.
  cutoff = 2026-10-01, window = 6 months -> W = 2026-04-02 .. 2026-10-01
  'yoy'      : P = 2025-04-02 .. 2025-10-01   (same dates one year earlier)
  'previous' : P = 2025-10-02 .. 2026-04-01   (the 6 months right before the window)
For a 12-month window both comparisons are the same period.
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd

COMPARISONS = {"yoy": "Same period last year", "previous": "Previous period"}

STATUS_ORDER = [
    "New", "Recent customer", "Returning", "Strong growth", "Organic growth", "Stable",
    "Organic loss", "Total loss", "No volume in either period", "Below threshold",
]

STATUS_RULES = {
    "Total loss": "No invoice at all in the last 12 months (always 12 months, whatever the window).",
    "New": "First invoice ever (in the uploaded files) is inside the selected window.",
    "Recent customer": "First invoice ever is within the last 12 months but before the window - "
                       "too new for a fair comparison. Only appears in the 6- and 3-month views.",
    "Returning": "Invoiced before, nothing in the comparison period, invoiced again in the window.",
    "No volume in either period": "Invoiced in the last 12 months, but not in the window or the comparison period.",
    "Below threshold": "Under the minimum MT in both the window and the comparison period.",
    "Strong growth": "Window volume up more than the strong-growth threshold vs the comparison period.",
    "Organic growth": "Window volume up more than the stable band, up to the strong-growth threshold.",
    "Stable": "Window volume within +/- the stable band.",
    "Organic loss": "Window volume down more than the stable band (still invoiced within the last 12 months).",
}
RULE_ORDER = ["Total loss", "New", "Recent customer", "Returning", "No volume in either period",
              "Below threshold", "Strong growth", "Organic growth", "Stable", "Organic loss"]


@dataclass
class Thresholds:
    stable_band: float = 0.10     # +/-10%
    strong_growth: float = 0.25   # >+25%
    min_mt: float = 5.0           # minimum MT in window or comparison period for a % status


def windows(cutoff: pd.Timestamp, months: int, comparison: str = "yoy"):
    """Return (w_start, w_end, p_start, p_end), all inclusive dates."""
    w_end = cutoff
    w_start = cutoff - pd.DateOffset(months=months) + pd.Timedelta(days=1)
    if comparison == "previous":
        p_end = w_start - pd.Timedelta(days=1)
        p_start = cutoff - pd.DateOffset(months=2 * months) + pd.Timedelta(days=1)
    else:
        p_end = cutoff - pd.DateOffset(years=1)
        p_start = p_end - pd.DateOffset(months=months) + pd.Timedelta(days=1)
    return w_start, w_end, p_start, p_end


def period_mt(df: pd.DataFrame, lo, hi) -> float:
    m = (df["inv_date"] >= lo) & (df["inv_date"] <= hi)
    return float(df.loc[m, "tons"].sum())


def seasonal_reference(df: pd.DataFrame, cutoff: pd.Timestamp, months: int, data_start: pd.Timestamp):
    """
    For 'previous period' comparisons: how the SAME two periods moved one year earlier
    (company total of df). Returns None if the uploaded data doesn't reach back far enough.
    """
    w_start, w_end, p_start, p_end = windows(cutoff, months, "previous")
    yr = pd.DateOffset(years=1)
    if p_start - yr < data_start:
        return None
    w_ly = period_mt(df, w_start - yr, w_end - yr)
    p_ly = period_mt(df, p_start - yr, p_end - yr)
    return (w_ly / p_ly - 1) if p_ly > 0 else None


def classify(df: pd.DataFrame, attrs: pd.DataFrame, cutoff: pd.Timestamp,
             months: int, th: Thresholds, comparison: str = "yoy") -> pd.DataFrame:
    w_start, w_end, p_start, p_end = windows(cutoff, months, comparison)
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
    out["change_pct"] = out["change_mt"] / out["prior_mt"].where(out["prior_mt"] > 0)

    pct = out["change_pct"]
    masks = {
        "Total loss": out["last12_mt"] <= 0,
        "New": out["first_invoice"] >= w_start,
        "Recent customer": out["first_invoice"] >= l12_start,
        "Returning": (out["prior_mt"] <= 0) & (out["window_mt"] > 0),
        "No volume in either period": (out["prior_mt"] <= 0) & (out["window_mt"] <= 0),
        "Below threshold": np.maximum(out["window_mt"], out["prior_mt"]) < th.min_mt,
        "Strong growth": pct > th.strong_growth,
        "Organic growth": pct > th.stable_band,
        "Stable": pct >= -th.stable_band,
        "Organic loss": pct < -th.stable_band,
    }
    status = pd.Series("", index=out.index, dtype=object)
    for label in RULE_ORDER:                      # first matching rule wins
        status = status.mask((status == "") & masks[label], label)
    out["status"] = pd.Categorical(status, categories=STATUS_ORDER, ordered=True)

    out.attrs.update(w_start=w_start, w_end=w_end, p_start=p_start, p_end=p_end,
                     l12_start=l12_start, comparison=comparison)
    return out.reset_index().rename(columns={"index": "customer"})


def status_summary(cls: pd.DataFrame) -> pd.DataFrame:
    return cls.groupby("status", observed=False).agg(
        customers=("customer", "count"),
        window_mt=("window_mt", "sum"),
        prior_mt=("prior_mt", "sum"),
        change_mt=("change_mt", "sum"),
    ).reset_index()
