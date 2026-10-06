"""
Quote-to-close analysis - the COO's method, with Sales Qty corrected to a SUM of all order lines.
Pure functions - no Streamlit calls.

One rule everywhere:
  Quotes   : issued between period start and period end
  Orders   : entered from each item's first quote date up to (period end + N days), N = 20 by default
             (90.0% of 2026 orders are entered within 20 days of the quote)
  A period is COMPLETE only if the SC export reaches period end + N days; otherwise it is flagged
  incomplete (some items may still order) and kept out of trend lines.

Per item (Fact #) quoted in the period:
  Count          = number of quote lines in the period
  AVG Qty/Quote  = average MT of those quote lines (quoted volume)
  Sales Qty      = SUM of order MT for that Fact # inside its order window
  Status         = Closed if Sales Qty > 0, otherwise Lost   (as in the COO's analysis)
Close rate = Closed / items quoted.

Waste sales quotes (Order Type 'Open Waste Order' - scrap cartons, plastic, steel) are not packaging
demand and are kept OUT of the KPIs, as in the COO's analysis. They are reported separately, never deleted.
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd

BUFFER_DAYS = 20
QUOTE_BUCKETS = ["1", "2", "3", "4", "5 or more"]
STATUSES = ["Closed", "Lost"]
TOP_LOST = 15
WASTE_ORDER_TYPES = {"Open Waste Order"}


@dataclass
class Params:
    period_start: pd.Timestamp
    period_end: pd.Timestamp
    buffer_days: int
    last_order_day: pd.Timestamp          # last order date in the SC export

    @property
    def order_cutoff(self) -> pd.Timestamp:
        return min(self.period_end + pd.Timedelta(days=self.buffer_days), self.last_order_day)

    @property
    def complete(self) -> bool:
        return self.period_end + pd.Timedelta(days=self.buffer_days) <= self.last_order_day

    @property
    def days_after_period(self) -> int:
        return int((self.order_cutoff - self.period_end).days)


def make_params(start, end, buffer_days, orders: pd.DataFrame) -> Params:
    return Params(pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize(), int(buffer_days),
                  orders["order_day"].max().normalize())


def default_period(orders: pd.DataFrame, buffer_days: int = BUFFER_DAYS):
    """The latest complete month-long period: ends N days before the last order date."""
    end = orders["order_day"].max().normalize() - pd.Timedelta(days=buffer_days)
    return (end - pd.DateOffset(months=1)).normalize(), end


def split_waste(quotes: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(packaging quote lines, waste-sales quote lines)."""
    is_waste = quotes["order_type"].isin(WASTE_ORDER_TYPES)
    return quotes[~is_waste].copy(), quotes[is_waste].copy()


def build_items(quotes: pd.DataFrame, orders: pd.DataFrame, p: Params) -> pd.DataFrame:
    q = quotes[(quotes["quote_day"] >= p.period_start) & (quotes["quote_day"] <= p.period_end)].copy()
    cols = ["item", "customer", "area", "product", "product_group", "salesman", "latest_order_type", "count",
            "avg_mt", "sum_mt", "first_quote", "last_quote", "last_quote_no", "sales_mt", "order_lines", "orders",
            "first_order", "status", "var_mt", "quote_bucket", "days_to_first_order"]
    if q.empty:
        return pd.DataFrame(columns=cols)
    q["_row"] = np.arange(len(q))                        # file order, for 'first row' attributes
    first = q.sort_values("_row").groupby("item", sort=False)
    latest = q.sort_values(["quote_date", "_row"]).groupby("item", sort=False)

    items = pd.DataFrame({
        "customer": first["customer"].first(),
        "area": first["area"].first(),
        "product": first["product"].first(),
        "product_group": first["product_group"].first(),
        "salesman": latest["salesman"].last(),
        "latest_order_type": latest["order_type"].last(),
        "count": first.size(),
        "avg_mt": first["mt"].mean(),
        "sum_mt": first["mt"].sum(),
        "first_quote": first["quote_day"].min(),
        "last_quote": first["quote_day"].max(),
        "last_quote_no": latest["quote_no"].last(),
    })
    items.index.name = "item"

    o = orders[orders["item"].isin(items.index) & (orders["order_day"] <= p.order_cutoff)]
    o = o.merge(items[["first_quote"]], left_on="item", right_index=True)
    o = o[o["order_day"] >= o["first_quote"]]
    agg = o.groupby("item").agg(sales_mt=("mt", "sum"), order_lines=("mt", "size"),
                                orders=("sc_no", "nunique"), first_order=("order_day", "min"))
    items = items.join(agg)
    items[["sales_mt", "order_lines", "orders"]] = items[["sales_mt", "order_lines", "orders"]].fillna(0)
    items[["order_lines", "orders"]] = items[["order_lines", "orders"]].astype(int)

    items["status"] = np.where(items["sales_mt"] > 0, "Closed", "Lost")
    items["var_mt"] = items["sales_mt"] - items["avg_mt"]
    items["quote_bucket"] = pd.Categorical(
        np.where(items["count"] >= 5, "5 or more", items["count"].astype(str)), categories=QUOTE_BUCKETS, ordered=True)
    items["days_to_first_order"] = (items["first_order"] - items["first_quote"]).dt.days
    return items.reset_index()[cols]


def _rate(n, d):
    return n / d if d else np.nan


def summary(items: pd.DataFrame) -> dict:
    closed = items[items["status"] == "Closed"]
    lost = items[items["status"] == "Lost"]
    quoted = float(items["avg_mt"].sum())
    sold = float(items["sales_mt"].sum())
    quoted_closed = float(closed["avg_mt"].sum())
    return {
        "items": len(items),
        "closed": len(closed), "lost": len(lost),
        "close_rate": _rate(len(closed), len(items)),
        "quoted_mt": quoted, "quoted_closed_mt": quoted_closed, "lost_mt": float(lost["avg_mt"].sum()),
        "sales_mt": sold,
        "fill_rate": _rate(sold, quoted),
        "closed_sold_ratio": _rate(sold, quoted_closed),
        "avg_quotes_closed": closed["count"].mean() if len(closed) else np.nan,
        "avg_quotes_lost": lost["count"].mean() if len(lost) else np.nan,
        "lost_once": int((lost["count"] == 1).sum()),
        "lost_repeated": int((lost["count"] >= 2).sum()),
        "lost_repeated_share": _rate(int((lost["count"] >= 2).sum()), len(lost)),
        "median_days_to_order": closed["days_to_first_order"].median() if len(closed) else np.nan,
    }


def breakdown(items: pd.DataFrame, by: str) -> pd.DataFrame:
    """COO layout: Quotes, Closed, Lost, Close rate, Qty quoted, Qty sold, Fill rate (+ TOTAL row)."""
    g = items.groupby(by, observed=False)
    t = pd.DataFrame({
        "quotes": g.size(),
        "closed": g["status"].apply(lambda s: (s == "Closed").sum()),
        "lost": g["status"].apply(lambda s: (s == "Lost").sum()),
        "qty_quoted": g["avg_mt"].sum(),
        "qty_sold": g["sales_mt"].sum(),
    })
    if by != "quote_bucket":
        t = t.sort_values("qty_quoted", ascending=False)
    total = t.sum().to_frame().T
    total.index = ["TOTAL"]
    t = pd.concat([t, total])
    t["close_rate"] = (t["closed"] / t["quotes"]).where(t["quotes"] > 0)
    t["fill_rate"] = (t["qty_sold"] / t["qty_quoted"]).where(t["qty_quoted"] > 0)
    t.index.name = by
    return t.reset_index()[[by, "quotes", "closed", "lost", "close_rate", "qty_quoted", "qty_sold", "fill_rate"]]


def top_lost(items: pd.DataFrame, n: int = TOP_LOST) -> pd.DataFrame:
    lost = items[items["status"] == "Lost"]
    t = lost.groupby("customer").agg(lost_quotes=("item", "size"), lost_mt=("avg_mt", "sum"))
    return t.sort_values("lost_mt", ascending=False).head(n).reset_index()


def repeated_no_order(items: pd.DataFrame) -> pd.DataFrame:
    r = items[(items["status"] == "Lost") & (items["count"] >= 2)]
    return r.sort_values(["count", "avg_mt"], ascending=False)[
        ["area", "customer", "item", "product_group", "count", "avg_mt", "salesman", "last_quote"]]


# ── Period comparison ──────────────────────────────────────────────────────
COMPARE_ROWS = [
    ("items", "FTs quoted", "int"), ("closed", "Closed", "int"), ("lost", "Lost", "int"),
    ("close_rate", "Quote-to-close ratio", "pct"),
    ("quoted_mt", "Quoted volume (MT)", "mt"), ("quoted_closed_mt", "  of which closed FTs (MT)", "mt"),
    ("lost_mt", "  of which lost FTs (MT)", "mt"), ("sales_mt", "Sales volume (MT)", "mt"),
    ("fill_rate", "Volume fill rate", "pct"), ("closed_sold_ratio", "Sold ÷ quoted, closed FTs", "pct"),
    ("avg_quotes_closed", "Avg quotes sent, closed", "dec"), ("avg_quotes_lost", "Avg quotes sent, lost", "dec"),
    ("lost_repeated", "Lost, quoted 2+ times", "int"),
]


def period_metrics(quotes: pd.DataFrame, orders: pd.DataFrame, start, end, buffer_days: int) -> dict:
    p = make_params(start, end, buffer_days, orders)
    m = summary(build_items(quotes, orders, p))
    m.update(period_start=p.period_start, period_end=p.period_end, order_cutoff=p.order_cutoff,
             complete=p.complete)
    return m


def calendar_periods(quotes: pd.DataFrame, freq: str) -> list[tuple[str, pd.Timestamp, pd.Timestamp]]:
    """Every month ('M') or quarter ('Q') covered by the quote dates: (label, start, end)."""
    lo, hi = quotes["quote_day"].min(), quotes["quote_day"].max()
    out = []
    for per in pd.period_range(lo, hi, freq=freq):
        label = per.strftime("%b %Y") if freq == "M" else f"Q{per.quarter} {per.year}"
        out.append((label, per.start_time.normalize(), per.end_time.normalize()))
    return out


def trend(quotes: pd.DataFrame, orders: pd.DataFrame, freq: str, buffer_days: int) -> pd.DataFrame:
    rows = []
    for label, start, end in calendar_periods(quotes, freq):
        m = period_metrics(quotes, orders, start, end, buffer_days)
        m["period"] = label
        rows.append(m)
    return pd.DataFrame(rows)
