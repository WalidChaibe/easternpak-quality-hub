"""
Quote-to-close analysis - the COO's method, with Sales Qty corrected to a SUM of all order lines.
Pure functions - no Streamlit calls.

Per item (Fact #) quoted in the period:
  Count            = number of quote lines in the period
  AVG Qty/Quote    = average MT of those quote lines (quoted volume)
  First/Last quote = first / last quote date in the period
  Sales Qty        = SUM of order MT for that Fact #, orders issued from the item's first quote
                     date up to the order cutoff date (order entry date, not invoice date)
  Status           = Closed if Sales Qty > 0
                     Open   if Sales Qty = 0 and the last quote is less than BUFFER days before the cutoff
                     Lost   otherwise
Close rate = Closed / (Closed + Lost); Open items are shown separately, not counted as lost.

Waste sales quotes (Order Type 'Open Waste Order' - scrap cartons, plastic, steel) are not packaging
demand and are kept OUT of the KPIs, as in the COO's analysis. They are reported separately, never deleted.
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd

BUFFER_DAYS = 20          # 90.0% of orders arrive within 20 days of the quote (measured on 2026 data)
QUOTE_BUCKETS = ["1", "2", "3", "4", "5 or more"]
STATUSES = ["Closed", "Lost", "Open"]
TOP_LOST = 15
WASTE_ORDER_TYPES = {"Open Waste Order"}


def split_waste(quotes: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(packaging quote lines, waste-sales quote lines)."""
    is_waste = quotes["order_type"].isin(WASTE_ORDER_TYPES)
    return quotes[~is_waste].copy(), quotes[is_waste].copy()


@dataclass
class Params:
    period_start: pd.Timestamp
    period_end: pd.Timestamp
    order_cutoff: pd.Timestamp
    buffer_days: int = BUFFER_DAYS


def default_params(orders: pd.DataFrame, buffer_days: int = BUFFER_DAYS) -> Params:
    cutoff = orders["order_day"].max()
    end = cutoff - pd.Timedelta(days=buffer_days)
    start = end - pd.DateOffset(months=1)
    return Params(start.normalize(), end.normalize(), cutoff.normalize(), buffer_days)


def buffer_ok(p: Params) -> tuple[bool, int]:
    days = int((p.order_cutoff - p.period_end).days)
    return days >= p.buffer_days, days


def build_items(quotes: pd.DataFrame, orders: pd.DataFrame, p: Params) -> pd.DataFrame:
    q = quotes[(quotes["quote_day"] >= p.period_start) & (quotes["quote_day"] <= p.period_end)].copy()
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

    open_from = p.order_cutoff - pd.Timedelta(days=p.buffer_days)
    items["status"] = np.where(items["sales_mt"] > 0, "Closed",
                               np.where(items["last_quote"] > open_from, "Open", "Lost"))
    items["var_mt"] = items["sales_mt"] - items["avg_mt"]
    items["quote_bucket"] = pd.Categorical(
        np.where(items["count"] >= 5, "5 or more", items["count"].astype(str)), categories=QUOTE_BUCKETS, ordered=True)
    items["days_to_first_order"] = (items["first_order"] - items["first_quote"]).dt.days
    return items.reset_index()


def _rate(n, d):
    return n / d if d else np.nan


def summary(items: pd.DataFrame) -> dict:
    closed = items[items["status"] == "Closed"]
    lost = items[items["status"] == "Lost"]
    decided = len(closed) + len(lost)
    quoted = items["avg_mt"].sum()
    return {
        "items": len(items),
        "closed": len(closed), "lost": len(lost), "open": int((items["status"] == "Open").sum()),
        "close_rate": _rate(len(closed), decided),
        "quoted_mt": quoted, "sales_mt": items["sales_mt"].sum(),
        "fill_rate": _rate(items["sales_mt"].sum(), quoted),
        "lost_mt": lost["avg_mt"].sum(),
        "open_mt": items.loc[items["status"] == "Open", "avg_mt"].sum(),
        "avg_quotes_closed": closed["count"].mean() if len(closed) else np.nan,
        "avg_quotes_lost": lost["count"].mean() if len(lost) else np.nan,
        "lost_once": int((lost["count"] == 1).sum()),
        "lost_repeated": int((lost["count"] >= 2).sum()),
        "lost_repeated_share": _rate(int((lost["count"] >= 2).sum()), len(lost)),
        "median_days_to_order": closed["days_to_first_order"].median() if len(closed) else np.nan,
    }


def breakdown(items: pd.DataFrame, by: str) -> pd.DataFrame:
    """COO layout: Quotes, Closed, Lost, Open, Close rate, Qty quoted, Qty sold, Fill rate (+ TOTAL row)."""
    g = items.groupby(by, observed=False)
    t = pd.DataFrame({
        "quotes": g.size(),
        "closed": g["status"].apply(lambda s: (s == "Closed").sum()),
        "lost": g["status"].apply(lambda s: (s == "Lost").sum()),
        "open": g["status"].apply(lambda s: (s == "Open").sum()),
        "qty_quoted": g["avg_mt"].sum(),
        "qty_sold": g["sales_mt"].sum(),
    })
    if by != "quote_bucket":
        t = t.sort_values("qty_quoted", ascending=False)
    total = t.sum().to_frame().T
    total.index = ["TOTAL"]
    t = pd.concat([t, total])
    t["close_rate"] = (t["closed"] / (t["closed"] + t["lost"])).where((t["closed"] + t["lost"]) > 0)
    t["fill_rate"] = (t["qty_sold"] / t["qty_quoted"]).where(t["qty_quoted"] > 0)
    t.index.name = by
    return t.reset_index()[[by, "quotes", "closed", "lost", "open", "close_rate", "qty_quoted", "qty_sold", "fill_rate"]]


def top_lost(items: pd.DataFrame, n: int = TOP_LOST) -> pd.DataFrame:
    lost = items[items["status"] == "Lost"]
    t = lost.groupby("customer").agg(lost_quotes=("item", "size"), lost_mt=("avg_mt", "sum"))
    return t.sort_values("lost_mt", ascending=False).head(n).reset_index()


def repeated_no_order(items: pd.DataFrame) -> pd.DataFrame:
    r = items[(items["status"] == "Lost") & (items["count"] >= 2)]
    return r.sort_values(["count", "avg_mt"], ascending=False)[
        ["area", "customer", "item", "product_group", "count", "avg_mt", "salesman", "last_quote"]]


def reconcile(items: pd.DataFrame, earlier: pd.DataFrame, orders: pd.DataFrame) -> pd.DataFrame:
    """
    Compare with an earlier analysis (e.g. the COO's 'Raw Data' sheet: Item, Sales Qty).
    'Equals one order line' = the earlier Sales Qty is exactly the MT of a single order line of that item.
    """
    e = earlier.rename(columns={"Item": "item", "Sales Qty": "earlier_sales"})[["item", "earlier_sales"]].copy()
    e["item"] = e["item"].astype(str).str.strip()
    e["earlier_sales"] = pd.to_numeric(e["earlier_sales"], errors="coerce").fillna(0.0)
    m = e.merge(items[["item", "customer", "count", "avg_mt", "sales_mt", "status"]], on="item", how="left")
    lines = orders[orders["item"].isin(m["item"])].groupby("item")["mt"].apply(lambda s: set(np.round(s, 2)))
    m["equals_one_order_line"] = [
        bool(v > 0 and isinstance(lines.get(i), set) and round(v, 2) in lines.get(i))
        for i, v in zip(m["item"], m["earlier_sales"])]
    m["difference_mt"] = m["sales_mt"] - m["earlier_sales"]
    m["earlier_closed"] = m["earlier_sales"] > 0
    return m
