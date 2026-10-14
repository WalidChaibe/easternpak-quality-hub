"""
Read one year of COP data (.xlsx sheet or .csv). Columns are read BY POSITION (same layout every year):
  A Month (Jan..Dec) · B Customer · C Item · D MT · E Gross Sales (SAR k) · F COP (SAR k) · G, H ignored
The file holds no year - the year comes from the upload screen. Pure functions - no Streamlit calls.
"""
import io
from dataclasses import dataclass, field
import pandas as pd

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
BLANK_CUSTOMER = "(no customer name)"


@dataclass
class YearCheck:
    rows: int
    months: list
    blank_customers: int
    bad_months: int
    bad_numbers: int
    mt: float
    sales: float
    cop: float
    issues: list = field(default_factory=list)


def list_sheets(content: bytes, name: str) -> list[str]:
    if name.lower().endswith(".csv"):
        return []
    return pd.ExcelFile(io.BytesIO(content)).sheet_names


def _month_no(v) -> int | None:
    s = str(v).strip()[:3].title()
    return MONTHS.index(s) + 1 if s in MONTHS else None


def read_year(content: bytes, name: str, sheet: str | None = None) -> tuple[pd.DataFrame, YearCheck]:
    if name.lower().endswith(".csv"):
        raw = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig")
    else:
        raw = pd.read_excel(io.BytesIO(content), sheet_name=sheet or 0)
    if raw.shape[1] < 6:
        raise ValueError(f"needs at least 6 columns (Month, Customer, Item, MT, Gross Sales, COP) - found {raw.shape[1]}")
    df = raw.iloc[:, :6].copy()
    df.columns = ["month", "customer", "item", "mt", "sales", "cop"]
    df = df[~df.isna().all(axis=1)]                                    # fully empty rows only

    df["m"] = df["month"].map(_month_no)
    bad_months = int(df["m"].isna().sum())
    num_bad = 0
    for c in ("mt", "sales", "cop"):
        v = pd.to_numeric(df[c], errors="coerce")
        num_bad += int(v.isna().sum() - df[c].isna().sum())
        df[c] = v.fillna(0.0)
    cust = df["customer"].astype("string").str.strip()
    blank = cust.isna() | (cust == "")
    df["customer"] = cust.where(~blank, BLANK_CUSTOMER)
    df["item"] = df["item"].astype("string").str.strip().fillna("")

    issues = []
    if bad_months:
        issues.append(f"{bad_months} rows have a month that is not Jan–Dec; they can't be placed in a month and are "
                      "left out of the analysis")
    if num_bad:
        issues.append(f"{num_bad} MT / Sales / COP cells are not numbers; counted as 0")
    if blank.any():
        issues.append(f"{int(blank.sum())} rows have no customer name; kept as '{BLANK_CUSTOMER}'")
    ok = df[df["m"].notna()].copy()
    ok["m"] = ok["m"].astype(int)
    check = YearCheck(rows=len(df), months=sorted(ok["m"].unique().tolist()), blank_customers=int(blank.sum()),
                      bad_months=bad_months, bad_numbers=num_bad, mt=float(ok["mt"].sum()),
                      sales=float(ok["sales"].sum()), cop=float(ok["cop"].sum()), issues=issues)
    return ok[["m", "customer", "item", "mt", "sales", "cop"]], check
