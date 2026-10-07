"""
Read the RFQ (quotation) export and the SC (sales order entry) export.
Footer rows ('Grand Summaries', 'Total Count : ...') are rows without a valid date and are dropped;
the footer's printed line count is used to verify nothing else was lost.
Pure functions - no Streamlit calls.
"""
import io
import re
from dataclasses import dataclass
import pandas as pd

RFQ_COLUMNS = {
    "Order Type": "order_type", "Trans #": "quote_no", "Fact #": "item", "MFG Code": "mfg_code",
    "Issue Date": "quote_date", "Customer": "customer", "Product": "product", "MT": "mt",
    "Area": "area", "Salesman": "salesman", "Box Style Group": "product_group",
    "Number Of Orders Issued": "orders_issued_flag",
}
SC_COLUMNS = {
    "Order Type": "order_type", "SC #": "sc_no", "Order ID": "order_id", "Fact #": "item",
    "Issue Date": "order_date", "Del Date": "delivery_date", "Customer": "customer", "MT": "mt", "Satus": "status",
    "Salesman": "salesman",
}


@dataclass
class FileCheck:
    name: str
    lines: int
    footer_rows: int
    footer_count: int | None      # line count printed in the footer
    count_match: bool | None
    footer_mt: float | None       # MT total printed in the footer
    mt_match: bool | None
    date_min: pd.Timestamp
    date_max: pd.Timestamp
    mt: float


def _read(content: bytes, name: str, columns: dict, date_col: str, count_col: str, kind: str, mt_col: str = "MT"):
    engine = "xlrd" if name.lower().endswith(".xls") else "openpyxl"
    raw = pd.read_excel(io.BytesIO(content), engine=engine)
    missing = [c for c in columns if c not in raw.columns]
    if missing:
        raise ValueError(f"'{name}' does not look like the {kind} export - missing column(s): {missing}")

    dates = pd.to_datetime(raw[date_col], errors="coerce")
    footer = raw[dates.isna()]
    count, footer_mt = None, None
    for v in footer[count_col]:
        m = re.fullmatch(r"(?:Total Count\s*:\s*)?(\d[\d,]*)", str(v).strip())
        if m:
            count = int(m.group(1).replace(",", ""))
    mt_vals = pd.to_numeric(footer[mt_col].astype(str).str.replace(",", "", regex=False), errors="coerce").dropna()
    if len(mt_vals):
        footer_mt = float(mt_vals.iloc[-1])
    df = raw.loc[dates.notna(), list(columns)].rename(columns=columns).copy()
    df[columns[date_col]] = dates[dates.notna()]
    df["mt"] = pd.to_numeric(df["mt"].astype(str).str.replace(",", "", regex=False), errors="coerce").fillna(0.0)
    for c in df.columns:
        if c not in (columns[date_col], "mt", "delivery_date") and not pd.api.types.is_numeric_dtype(df[c]):
            df[c] = df[c].astype("string").str.strip().fillna("")      # trims e.g. 'Central ' -> 'Central'
    mt = float(df["mt"].sum())
    check = FileCheck(name, len(df), int(len(footer)), count,
                      None if count is None else count == len(df),
                      footer_mt, None if footer_mt is None else abs(footer_mt - mt) < 0.05,
                      df[columns[date_col]].min(), df[columns[date_col]].max(), mt)
    return df, check


def read_rfq(content: bytes, name: str):
    df, check = _read(content, name, RFQ_COLUMNS, "Issue Date", "Order Type", "RFQ (quotation)")
    df["quote_day"] = df["quote_date"].dt.normalize()
    df["orders_issued_flag"] = pd.to_numeric(df["orders_issued_flag"], errors="coerce").fillna(0)
    df["quote_no"] = df["quote_no"].astype(str).str.replace(r"\.0$", "", regex=True)
    return df, check


def read_sc(content: bytes, name: str):
    df, check = _read(content, name, SC_COLUMNS, "Issue Date", "SC #", "sales order (SC)")
    df["order_day"] = df["order_date"].dt.normalize()
    df["delivery_day"] = pd.to_datetime(df["delivery_date"], errors="coerce").dt.normalize()
    df["sc_no"] = df["sc_no"].astype(str).str.replace(r"\.0$", "", regex=True)
    return df, check


# ── Invoice Detail exports (one file per year) ──
INV_COLUMNS = {
    "Inv Date": "sale_date", "Fact Tic NB": "item", "Inv Gen ID": "inv_no", "Customer Name": "customer",
    "Shipped Tons": "mt", "Invoice Amount": "amount", "Product Item MFG Code": "mfg_code", "SC NB": "sc_no",
}


def read_invoices(content: bytes, name: str):
    """
    One Invoice Detail export. 'Fact Tic NB' is the Fact # (it matches the quote export's Fact #, including the
    old codes where Fact # and MFG Code differ). Footer rows are dropped and checked against the printed totals.
    """
    df, check = _read(content, name, INV_COLUMNS, "Inv Date", "Inv Date", "Invoice Detail", mt_col="Shipped Tons")
    df["sale_day"] = df["sale_date"].dt.normalize()
    df["amount"] = pd.to_numeric(df["amount"].astype(str).str.replace(",", "", regex=False), errors="coerce").fillna(0.0)
    df["sc_no"] = df["sc_no"].astype(str).str.replace(r"\.0$", "", regex=True)
    df["source_file"] = name
    return df, check


def overlapping_files(checks: list) -> list[tuple[str, str]]:
    """Pairs of invoice files whose date ranges overlap (the same period uploaded twice)."""
    ordered = sorted(checks, key=lambda c: c.date_min)
    return [(a.name, b.name) for a, b in zip(ordered, ordered[1:]) if b.date_min <= a.date_max]
