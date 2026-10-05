"""
Customer name standardization and period (cutoff / partial month) detection.
Pure functions - no Streamlit calls.
"""
import re
from dataclasses import dataclass
import pandas as pd

_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)

# Salespeople whose invoice lines are excluded BEFORE any analysis, with the reason.
# Matched case-insensitively on the 'Salesman Name' column. Edit here if this changes.
EXCLUDED_SALESMEN = {
    "ZAINAB ALKHUNAIZI": "Runs the e-shop (not B2B demand)",
}


def exclude_salesmen(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split into (kept, excluded). Excluded lines are returned so the page can show them."""
    names = df["salesman"].str.upper().str.strip()
    mask = names.isin(EXCLUDED_SALESMEN.keys())
    excluded = df.loc[mask].copy()
    excluded["reason"] = names[mask].map(EXCLUDED_SALESMEN)
    return df.loc[~mask].copy(), excluded


def customer_key(name: str) -> str:
    """Uppercase, drop spaces and punctuation. Unicode-aware so Arabic names keep their letters."""
    key = _NON_WORD.sub("", str(name).upper())
    return key or str(name).strip().upper()


def standardize_customers(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Adds 'customer' (one display name per standardized key = the most recently
    invoiced spelling) and returns a table of every key that had 2+ spellings.
    """
    out = df.copy()
    out["customer_key"] = out["customer_raw"].map(customer_key)

    latest = (out.sort_values("inv_date")
                 .groupby("customer_key")["customer_raw"].last())
    out["customer"] = out["customer_key"].map(latest)

    spellings = out.groupby("customer_key").agg(
        display_name=("customer", "first"),
        spellings=("customer_raw", lambda s: sorted(s.unique())),
        n_spellings=("customer_raw", "nunique"),
        tons=("tons", "sum"),
    )
    merged = (spellings[spellings["n_spellings"] > 1]
              .sort_values("tons", ascending=False)
              .reset_index(drop=True))
    merged["spellings"] = merged["spellings"].map(" | ".join)
    return out, merged[["display_name", "spellings", "tons"]]


def customer_attributes(df: pd.DataFrame) -> pd.DataFrame:
    """Latest non-blank salesman / industry / area per customer."""
    d = df.sort_values("inv_date")
    def last_non_blank(s):
        s = s[s != ""]
        return s.iloc[-1] if len(s) else ""
    return d.groupby("customer").agg(
        salesman=("salesman", last_non_blank),
        industry=("industry", last_non_blank),
        area=("area", last_non_blank),
        first_invoice=("inv_date", "min"),
        last_invoice=("inv_date", "max"),
    )


@dataclass
class PeriodInfo:
    data_start: pd.Timestamp
    cutoff: pd.Timestamp                 # last invoice date in the data = "today" for the analysis
    is_partial: bool                     # True if the cutoff month is not complete
    cutoff_month: pd.Period
    last_complete_month: pd.Period       # last month used for model fitting
    first_complete_month: pd.Period


def period_info(df: pd.DataFrame) -> PeriodInfo:
    start = df["inv_date"].min()
    cutoff = df["inv_date"].max()
    cutoff_month = cutoff.to_period("M")
    is_partial = cutoff < cutoff_month.end_time.normalize()
    last_complete = cutoff_month - 1 if is_partial else cutoff_month
    start_month = start.to_period("M")
    first_complete = start_month if start == start_month.start_time else start_month + 1
    return PeriodInfo(start, cutoff, is_partial, cutoff_month, last_complete, first_complete)
