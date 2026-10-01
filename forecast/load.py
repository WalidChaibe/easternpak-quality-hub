"""
Read raw 'Invoice Detail' exports (one file per year) and combine them.
Pure functions - no Streamlit calls.
"""
import io
from dataclasses import dataclass
import pandas as pd

# Raw column name -> normalized name. Only these columns are read.
COLUMN_MAP = {
    "Inv Date": "inv_date",
    "Customer Name": "customer_raw",
    "Product Name": "item",
    "Shipped Tons": "tons",
    "Invoice Amount": "amount",
    "Contribution Amount": "contribution",
    "Salesman Name": "salesman",
    "Industry Group Name": "industry",
    "Area Name": "area",
}


@dataclass
class FileInfo:
    name: str
    rows_read: int
    rows_kept: int
    footer_rows_dropped: int
    date_min: pd.Timestamp
    date_max: pd.Timestamp
    tons: float
    footer_tons: float | None      # 'Total' figure printed in the export's footer, if found
    footer_match: bool | None      # True if the kept rows sum to the footer total


def _to_number(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(",", "", regex=False).str.strip(), errors="coerce")


def read_invoice_file(content: bytes, name: str) -> tuple[pd.DataFrame, FileInfo]:
    """
    Read one export. Footer rows ('Invoice Detail - Summary', 'Total Count : ...')
    are identified as rows whose Inv Date is not a date, and dropped. The footer's
    printed tonnage total is used to verify nothing else was lost.
    """
    engine = "xlrd" if name.lower().endswith(".xls") else "openpyxl"
    raw = pd.read_excel(io.BytesIO(content), engine=engine, usecols=lambda c: c in COLUMN_MAP)

    missing = [c for c in COLUMN_MAP if c not in raw.columns]
    if missing:
        raise ValueError(f"'{name}' is missing expected column(s): {missing}")

    df = raw.rename(columns=COLUMN_MAP)
    df["inv_date"] = pd.to_datetime(df["inv_date"], errors="coerce")

    is_footer = df["inv_date"].isna()
    footer = df.loc[is_footer]
    footer_tons = _to_number(footer["tons"]).dropna()
    footer_total = float(footer_tons.iloc[-1]) if len(footer_tons) else None

    df = df.loc[~is_footer].copy()
    df["inv_date"] = df["inv_date"].dt.normalize()
    for col in ["tons", "amount", "contribution"]:
        df[col] = _to_number(df[col]).fillna(0.0)
    for col in ["customer_raw", "item", "salesman", "industry", "area"]:
        df[col] = df[col].astype("string").str.strip().fillna("")
    df = df[df["customer_raw"] != ""]

    tons = float(df["tons"].sum())
    match = None if footer_total is None else abs(tons - footer_total) < 0.05
    info = FileInfo(
        name=name,
        rows_read=len(raw),
        rows_kept=len(df),
        footer_rows_dropped=int(is_footer.sum()),
        date_min=df["inv_date"].min(),
        date_max=df["inv_date"].max(),
        tons=tons,
        footer_tons=footer_total,
        footer_match=match,
    )
    df["source_file"] = name
    return df, info


def find_overlaps(infos: list[FileInfo]) -> list[tuple[str, str]]:
    """Pairs of files whose invoice date ranges overlap (same period uploaded twice)."""
    ordered = sorted(infos, key=lambda i: i.date_min)
    overlaps = []
    for a, b in zip(ordered, ordered[1:]):
        if b.date_min <= a.date_max:
            overlaps.append((a.name, b.name))
    return overlaps


def combine(frames: list[pd.DataFrame]) -> pd.DataFrame:
    return pd.concat(frames, ignore_index=True).sort_values("inv_date").reset_index(drop=True)
