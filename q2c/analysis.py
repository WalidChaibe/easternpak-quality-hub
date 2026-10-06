"""
Quote-to-close analysis - EXACTLY the Product Manager's method (his 'New quote to close' workbook).
Pure functions - no Streamlit calls.

Per Fact # quoted in the quote period (all order types, all salesmen - nothing filtered, as in his file):
  Area          = the customer's Area in the quote export (first row of that customer),
                  except the customers in PM_AREA_OVERRIDES, which he re-labels by hand
  Customer      = first quote line of that Fact # (file order)
  Count         = number of quote lines
  Quoted Qty    = SUM of MT of all quote lines
  AVG MT/Quote  = Quoted Qty / Count
  Sales Qty     = SUM of MT of all SC order lines for that Fact # with DELIVERY date in the sales window
  Last quote #, Date = the latest quote line
Closed = Sales Qty > 0, Lost = Sales Qty = 0.

Checked against his workbook (quotes 1 Jan - 2 Sep 2026, deliveries 1 Jan - 31 Aug 2026): Area, Customer,
Count, Quoted Qty, AVG, Last quote # and Date match on all 5,077 Fact #s; Sales Qty matches exactly on
4,737 (the SC export used here is newer than his - deliveries re-dated / orders added since).
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd

BUFFER_DAYS = 20          # default sales window = quote period + 20 days (90.0% of orders within 20 days)
STATUSES = ["Closed", "Lost"]

# The PM's own Area labels (customers he re-labels; everyone else keeps the export's Area).
PM_AREA_OVERRIDES = {
    'ARABIAN PETROCHEMICAL COMPANY PETROKEMYA': 'Petrochemical',
    'Al Jubail Petrochemical Company (Kemya)': 'Petrochemical',
    'BEAUFORT RAK LLC': 'Consumer Area',
    'CADIS -  CATERING DISPOSABLE PACKAGING CO': 'Eastern',
    'CARLTON AL MOAIBED HOTEL*': 'Eastern',
    'IBN SINA NATIONAL METHANOL COMPANY - SABIC': 'Petrochemical',
    'Multipak Core': 'Central Procurement',
    'NAPCO CONSUMER PRODUCT COMPANY': 'Consumer Area',
    'NAPCO PAPER PRODUCTS COMPANY': 'Consumer Area',
    'NAPCO TRADE DISTRIBUTION': 'Consumer Area',
    'NATIONAL PAPER CO. LTD. (NPCL)': 'Consumer Area',
    'Napco Composite Film Packaging Technology (Compact)': 'Central Procurement',
    'Napco Modern Plastic Products Company': 'Central Procurement',
    'Napco National C.J.S.C': 'Central Procurement',
    'Napco Packaging Systems Co. (Uniplast)': 'Central Procurement',
    'Napco Plastics Company': 'Central Procurement',
    'PINEHILL ARABIA FOOD LIMITED': 'Western',
    'Petro Rabigh Rabigh Refining Petrochemicals Co.': 'Petrochemical',
    'SABIC - SAMAC': 'Petrochemical',
    'SADARA CHEMICAL COMPANY': 'Petrochemical',
    'SAUDI KAYAN PETROCHEMICAL COMPANY': 'Petrochemical',
    'Saudi Acrylic Polymers Company': 'Petrochemical',
    'Saudi Ethylene Polyethylene Co.': 'Petrochemical',
    'Saudi Polymers Company': 'Petrochemical',
    'Saudi Polyolefins Company': 'Petrochemical',
    'UNITED PLASTIC PRODUCTS CO. (UPPC-TECH)': 'Central Procurement',
    'United Plastic Products Co. (Uppc-Hygiene)': 'Central Procurement',
}


@dataclass
class Params:
    quote_start: pd.Timestamp
    quote_end: pd.Timestamp
    sales_start: pd.Timestamp
    sales_end: pd.Timestamp
    export_day: pd.Timestamp       # last order entry date in the SC export ("today" of that file)

    @property
    def complete(self) -> bool:
        """The sales window has fully passed by the time the SC file was exported."""
        return self.sales_end <= self.export_day


def make_params(q_start, q_end, s_start, s_end, orders: pd.DataFrame) -> Params:
    ts = lambda d: pd.Timestamp(d).normalize()
    return Params(ts(q_start), ts(q_end), ts(s_start), ts(s_end), orders["order_day"].max().normalize())


def default_quote_period(quotes: pd.DataFrame, orders: pd.DataFrame, buffer_days: int = BUFFER_DAYS):
    """From the first quote in the file to 20 days before the SC export date."""
    start = quotes["quote_day"].min()
    end = min(quotes["quote_day"].max(), orders["order_day"].max().normalize() - pd.Timedelta(days=buffer_days))
    return start, max(end, start)


def default_sales_window(q_start, q_end, buffer_days: int = BUFFER_DAYS):
    """Deliveries from the quote period start to the quote period end + 20 days."""
    return pd.Timestamp(q_start), pd.Timestamp(q_end) + pd.Timedelta(days=buffer_days)


def build_table(quotes: pd.DataFrame, orders: pd.DataFrame, p: Params) -> pd.DataFrame:
    """The PM's 'Quote to close' sheet: one row per Fact #."""
    cols = ["area", "customer", "item", "count", "quoted_mt", "avg_mt", "sales_mt", "last_quote_no",
            "last_quote", "status", "salesman", "order_type", "delivery_lines"]
    q = quotes[(quotes["quote_day"] >= p.quote_start) & (quotes["quote_day"] <= p.quote_end)].copy()
    if q.empty:
        return pd.DataFrame(columns=cols)
    q["_row"] = np.arange(len(q))

    cust_area = q.groupby("customer", sort=False)["area"].first()            # customer's first Area
    first = q.groupby("item", sort=False)
    latest = q.sort_values(["quote_date", "_row"]).groupby("item", sort=False)
    t = pd.DataFrame({
        "customer": first["customer"].first(),
        "count": first.size(),
        "quoted_mt": first["mt"].sum(),
        "last_quote_no": latest["quote_no"].last(),
        "last_quote": latest["quote_day"].last(),
        "salesman": latest["salesman"].last(),
        "order_type": latest["order_type"].last(),
    })
    t = t.loc[q.drop_duplicates("item")["item"]]                       # keep first-appearance (file) order
    t["avg_mt"] = t["quoted_mt"] / t["count"]
    t["area"] = [PM_AREA_OVERRIDES.get(c, cust_area.get(c, "")) for c in t["customer"]]

    o = orders[orders["item"].isin(t.index) & (orders["delivery_day"] >= p.sales_start)
               & (orders["delivery_day"] <= p.sales_end)]
    agg = o.groupby("item").agg(sales_mt=("mt", "sum"), delivery_lines=("mt", "size"))
    t = t.join(agg)
    t["sales_mt"] = t["sales_mt"].fillna(0.0)
    t["delivery_lines"] = t["delivery_lines"].fillna(0).astype(int)
    t["status"] = np.where(t["sales_mt"] > 0, "Closed", "Lost")
    t.index.name = "item"
    return t.reset_index()[cols]


def _r(n, d):
    return n / d if d else np.nan


def summary(t: pd.DataFrame) -> dict:
    """The PM's Summary sheet, row by row."""
    closed = t["status"] == "Closed"
    quoted, sales = float(t["quoted_mt"].sum()), float(t["sales_mt"].sum())
    q_closed, q_lost = float(t.loc[closed, "quoted_mt"].sum()), float(t.loc[~closed, "quoted_mt"].sum())
    avg = float(t["avg_mt"].sum())
    a_closed, a_lost = float(t.loc[closed, "avg_mt"].sum()), float(t.loc[~closed, "avg_mt"].sum())
    return {
        "total": len(t), "closed": int(closed.sum()), "lost": int((~closed).sum()),
        "ratio": _r(int(closed.sum()), len(t)),
        "quoted": quoted,
        "q_closed": q_closed, "q_closed_pct": _r(q_closed, quoted),
        "q_lost": q_lost, "q_lost_pct": _r(q_lost, quoted),
        "sales": sales,
        "fill_quoted": _r(sales, quoted), "fill_closed": _r(sales, q_closed),
        "avg": avg,
        "a_closed": a_closed, "a_closed_pct": _r(a_closed, avg),
        "a_lost": a_lost, "a_lost_pct": _r(a_lost, avg),
    }


SUMMARY_ROWS = [   # (label as in the PM's Summary sheet, value key, % key, kind)
    ("Total Quotations (line items)", "total", None, "int"),
    ("Quotations Closed (>= 1 order)", "closed", None, "int"),
    ("Quotations Lost (zero sales)", "lost", None, "int"),
    ("Quote-to-Close Ratio (by count)", "ratio", None, "pct"),
    ("Total Quoted Volume", "quoted", None, "mt"),
    ("Quotation closed (by volume)", "q_closed", "q_closed_pct", "mt"),
    ("Quotation lost (by volume)", "q_lost", "q_lost_pct", "mt"),
    ("Total Sales Volume Realised", "sales", None, "mt"),
    ("Volume Fill Rate (Sales / Quoted volume)", "fill_quoted", None, "pct"),
    ("Volume Fill Rate (Sales / Quotation closed)", "fill_closed", None, "pct"),
    ("Sum of AVG Quoted volume", "avg", None, "mt"),
    ("Quotation closed (by AVG volume)", "a_closed", "a_closed_pct", "mt"),
    ("Quotation lost (by AVG volume)", "a_lost", "a_lost_pct", "mt"),
]


def pivot(t: pd.DataFrame, by: str = "customer") -> pd.DataFrame:
    """
    The PM's Pivot: Count, Quoted Qty, AVG MT/Quote, Sales Qty, Fill rate (Sales / Quoted), highest first.
    Like an Excel pivot, names that differ only in upper/lower case are one row, shown with the first spelling.
    """
    t = t.copy()
    key = t[by].astype(str).str.lower()
    t[by] = key.map(t.groupby(key)[by].first())
    g = t.groupby(by)
    pv = pd.DataFrame({"count": g["count"].sum(), "quoted_mt": g["quoted_mt"].sum(), "avg_mt": g["avg_mt"].sum(),
                       "sales_mt": g["sales_mt"].sum(), "items": g.size(),
                       "closed": g["status"].apply(lambda s: (s == "Closed").sum())})
    pv["fill_rate"] = (pv["sales_mt"] / pv["quoted_mt"]).where(pv["quoted_mt"] > 0)
    pv = pv.sort_values("fill_rate", ascending=False, na_position="last")
    total = pv.drop(columns="fill_rate").sum().to_frame().T
    total.index = ["Grand Total"]
    total["fill_rate"] = total["sales_mt"] / total["quoted_mt"] if total["quoted_mt"].iat[0] else np.nan
    out = pd.concat([pv, total])
    out.index.name = by
    return out.reset_index()


# ── Period comparison (same method, each period with its own sales window) ──
def calendar_periods(quotes: pd.DataFrame, freq: str):
    lo, hi = quotes["quote_day"].min(), quotes["quote_day"].max()
    out = []
    for per in pd.period_range(lo, hi, freq=freq):
        label = per.strftime("%b %Y") if freq == "M" else f"Q{per.quarter} {per.year}"
        out.append((label, per.start_time.normalize(), per.end_time.normalize()))
    return out


def period_summary(quotes, orders, q_start, q_end, buffer_days: int) -> dict:
    s_start, s_end = default_sales_window(q_start, q_end, buffer_days)
    p = make_params(q_start, q_end, s_start, s_end, orders)
    m = summary(build_table(quotes, orders, p))
    m.update(quote_start=p.quote_start, quote_end=p.quote_end, sales_start=p.sales_start,
             sales_end=p.sales_end, complete=p.complete)
    return m


def trend(quotes, orders, freq: str, buffer_days: int) -> pd.DataFrame:
    rows = []
    for label, a, b in calendar_periods(quotes, freq):
        m = period_summary(quotes, orders, a, b, buffer_days)
        m["period"] = label
        rows.append(m)
    return pd.DataFrame(rows)
