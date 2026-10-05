"""
Customer-level monthly forecasting.

Series modelled = MT per effective invoicing day (see calendar.py), complete months only.
Forecast MT = forecast rate x future month's effective days.

Model per customer (decided by its own history, trimmed to its first invoice month):
  no volume in last 12 complete months      -> zero
  >40% empty months in last 24              -> TSB (Croston variant), built for intermittent demand
  < 12 months of history                    -> average of last 6 months
  12-23 months                              -> Holt, damped trend
  24+ months                                -> Holt-Winters, damped trend, 12-month seasonality
                                               (additive or multiplicative, lower AICc)
Accuracy test: refit on all but the last 6 complete months, forecast those 6, compare
with the actuals. If a plain 6-month average beats the model in that test, the
average is used instead.
Size change (last 6 months >= 3x or <= 1/3 of the 12 months before) is a FLAG for review only:
tested on Apr-Sep 2026, forcing an average for these customers more than doubled their error
(3,592 vs 1,835 MT), because most kept moving in the same direction. Pure functions - no Streamlit calls.
"""
import warnings
import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing

TEST_MONTHS = 6
MIN_HOLT = 12
MIN_HW = 24
INTERMITTENT_ZERO_SHARE = 0.40
TSB_ALPHA = 0.10   # smoothing of order size
TSB_BETA = 0.10    # smoothing of order probability
AVG_MONTHS = 6
SIZE_CHANGE_RATIO = 3.0   # flag: last-6-month average vs the 12 months before, >= 3x or <= 1/3

MODEL_LABELS = {
    "zero": "No orders in 12 months",
    "croston": "TSB (intermittent)",
    "average": "6-month average",
    "holt": "Holt (damped trend)",
    "hw": "Holt-Winters (seasonal)",
}


def choose_kind(y: np.ndarray) -> str:
    n = len(y)
    if n == 0 or y[-12:].sum() <= 0:
        return "zero"
    if (y[-24:] == 0).mean() > INTERMITTENT_ZERO_SHARE:
        return "croston"
    if n < MIN_HOLT:
        return "average"
    if n < MIN_HW:
        return "holt"
    return "hw"


def size_change(y: np.ndarray) -> bool:
    """True if the last 6 months' level is >= 3x or <= 1/3 of the 12 months before them."""
    if len(y) < 18:
        return False
    last6, prior12 = y[-6:].mean(), y[-18:-6].mean()
    if prior12 <= 0:
        return False
    ratio = last6 / prior12
    return ratio >= SIZE_CHANGE_RATIO or ratio <= 1 / SIZE_CHANGE_RATIO


def _tsb(y: np.ndarray, h: int) -> np.ndarray:
    """
    TSB (Teunter-Syntetos-Babai) for intermittent demand. Unlike classic Croston/SBA,
    the order probability is updated EVERY month, so a customer who stops ordering
    decays towards zero instead of keeping its old forecast. Tested on this data:
    Croston/SBA forecast 3.3x the last 6 months' actuals for intermittent customers.
    """
    nz = np.flatnonzero(y > 0)
    if len(nz) == 0:
        return np.zeros(h)
    z = y[nz[0]]                     # demand size when ordering
    p = 1.0 / max(len(y) / len(nz), 1.0)   # starting order probability
    for v in y[nz[0] + 1:]:
        if v > 0:
            z = z + TSB_ALPHA * (v - z)
            p = p + TSB_BETA * (1 - p)
        else:
            p = p + TSB_BETA * (0 - p)
    return np.full(h, p * z)


def _fit(y: np.ndarray, h: int, kind: str) -> tuple[np.ndarray, str]:
    """Returns (forecast, kind actually used). Falls back to a simpler model if a fit fails."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if kind == "zero":
            return np.zeros(h), "zero"
        if kind == "croston":
            return _tsb(y, h), "croston"
        if kind == "average":
            return np.full(h, y[-AVG_MONTHS:].mean()), "average"
        if kind == "holt":
            try:
                m = ExponentialSmoothing(y, trend="add", damped_trend=True,
                                         initialization_method="estimated").fit()
                f = m.forecast(h)
                if np.all(np.isfinite(f)):
                    return np.clip(f, 0, None), "holt"
            except Exception:
                pass
            return _fit(y, h, "average")
        if kind == "hw":
            best, best_aicc = None, np.inf
            for seasonal in (["add", "mul"] if np.all(y > 0) else ["add"]):
                try:
                    m = ExponentialSmoothing(y, trend="add", damped_trend=True, seasonal=seasonal,
                                             seasonal_periods=12, initialization_method="estimated").fit()
                    f = m.forecast(h)
                    if np.all(np.isfinite(f)) and m.aicc < best_aicc:
                        best, best_aicc = f, m.aicc
                except Exception:
                    continue
            if best is not None:
                return np.clip(best, 0, None), "hw"
            return _fit(y, h, "holt")
    raise ValueError(kind)


def forecast_series(y: np.ndarray, h: int, eff_hist: np.ndarray) -> dict:
    """
    y: rate history (MT per effective day), complete months, trimmed to first invoice.
    eff_hist: effective days for the same months (to score the test in MT).
    """
    kind = choose_kind(y)
    sc = kind in ("holt", "hw") and size_change(y)     # flag only - does not change the model
    result = {"kind_selected": kind, "test_abs_err": np.nan, "test_actual": np.nan,
              "test_forecast": np.nan, "test_err_model": np.nan, "test_err_avg": np.nan,
              "replaced_by_average": False, "size_change": sc}

    if len(y) >= MIN_HOLT + TEST_MONTHS and kind != "zero":
        train, test = y[:-TEST_MONTHS], y[-TEST_MONTHS:]
        eff_test = eff_hist[-TEST_MONTHS:]
        f_model, _ = _fit(train, TEST_MONTHS, choose_kind(train))
        f_avg = np.full(TEST_MONTHS, train[-AVG_MONTHS:].mean())
        actual_mt = test * eff_test
        model_mt, avg_mt = f_model * eff_test, f_avg * eff_test
        tot = actual_mt.sum()
        if tot > 0:
            err_model = np.abs(actual_mt - model_mt).sum()
            err_avg = np.abs(actual_mt - avg_mt).sum()
            beat = err_avg < err_model and kind != "average"
            chosen_mt = avg_mt if beat else model_mt
            result.update(
                test_err_model=err_model / tot,
                test_err_avg=err_avg / tot,
                replaced_by_average=bool(beat),
                test_abs_err=np.abs(actual_mt - chosen_mt).sum(),
                test_actual=tot,
                test_forecast=chosen_mt.sum(),
            )
            if beat:
                kind = "average"

    f, used = _fit(y, h, kind)
    result["kind_used"] = used
    result["forecast_rate"] = f
    return result


def build_matrix(df: pd.DataFrame, months: pd.PeriodIndex) -> pd.DataFrame:
    """Customer x month MT for the given complete months (zeros filled)."""
    d = df[df["inv_date"].dt.to_period("M").isin(months)]
    m = d.groupby(["customer", d["inv_date"].dt.to_period("M")])["tons"].sum().unstack(fill_value=0.0)
    return m.reindex(columns=months, fill_value=0.0)


def run_forecasts(mt: pd.DataFrame, eff_hist: pd.Series, eff_future: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    mt: customer x complete-month MT. eff_hist: effective days per complete month.
    eff_future: effective days per forecast month (for a partial month: REMAINING days only).
    Returns (forecast MT customer x future month, per-customer model table).
    """
    h = len(eff_future)
    rates = mt.div(eff_hist.values, axis=1)
    fc_rows, info_rows = {}, []
    for cust, row in rates.iterrows():
        vals = row.values
        nz = np.flatnonzero(vals > 0)
        if len(nz) == 0:
            continue
        y = vals[nz[0]:]
        e = eff_hist.values[nz[0]:]
        r = forecast_series(y, h, e)
        fc_rows[cust] = r["forecast_rate"] * eff_future.values
        label = MODEL_LABELS[r["kind_used"]]
        info_rows.append({
            "customer": cust, "history_months": len(y),
            "model": label,
            "replaced_by_average": r["replaced_by_average"],
            "size_change": r["size_change"],
            "test_error_model": r["test_err_model"], "test_error_average": r["test_err_avg"],
            "_abs_err": r["test_abs_err"], "_actual": r["test_actual"], "_fc": r["test_forecast"],
        })
    fc = pd.DataFrame.from_dict(fc_rows, orient="index", columns=eff_future.index)
    return fc, pd.DataFrame(info_rows).set_index("customer")


def forecast_total(total_mt: pd.Series, eff_hist: pd.Series, eff_future: pd.Series) -> tuple[np.ndarray, str]:
    """Company-level model on the total series - a cross-check against the sum of customers."""
    y = (total_mt / eff_hist).values
    r = forecast_series(y, len(eff_future), eff_hist.values)
    return r["forecast_rate"] * eff_future.values, MODEL_LABELS[r["kind_used"]]


def accuracy_summary(info: pd.DataFrame) -> dict:
    t = info.dropna(subset=["_actual"])
    if t.empty or t["_actual"].sum() <= 0:
        return {"wape": np.nan, "bias": np.nan, "customers_tested": 0}
    actual = t["_actual"].sum()
    return {
        "wape": t["_abs_err"].sum() / actual,              # customer-level error, volume weighted
        "bias": (t["_fc"].sum() - actual) / actual,         # total over/under-forecast
        "customers_tested": len(t),
    }


def ft_mix(df: pd.DataFrame, cutoff: pd.Timestamp, months: int = 12) -> pd.DataFrame:
    """
    Every customer's FT mix: each FT's MT share of the customer's invoices in the last
    `months` months up to the analysis date (a full year, so event FTs carry their annual
    weight). Adds the latest product name, months invoiced and last invoice date per FT.
    Nothing is excluded: every FT invoiced in the period gets its share.
    """
    start = cutoff - pd.DateOffset(months=months) + pd.Timedelta(days=1)
    d = df[(df["inv_date"] >= start) & (df["inv_date"] <= cutoff)].sort_values("inv_date")
    g = d.groupby(["customer", "ft"])
    mix = g.agg(
        mt=("tons", "sum"),
        product_name=("item", "last"),
        months_invoiced=("inv_date", lambda s: s.dt.to_period("M").nunique()),
        last_invoiced=("inv_date", "max"),
    ).reset_index()
    total = mix.groupby("customer")["mt"].transform("sum")
    mix["share"] = np.where(total > 0, mix["mt"] / total.where(total > 0), 0.0)
    return mix.sort_values(["customer", "share"], ascending=[True, False]).reset_index(drop=True)


def ft_split(mix: pd.DataFrame, customer: str, cust_fc: pd.Series) -> pd.DataFrame:
    """One customer's forecast split across its FTs (share x customer forecast)."""
    m = mix[mix["customer"] == customer]
    if m.empty:
        return pd.DataFrame()
    vals = pd.DataFrame(np.outer(m["share"].values, cust_fc.values), columns=cust_fc.index, index=m.index)
    return pd.concat([m[["ft", "product_name", "share", "months_invoiced", "last_invoiced"]], vals], axis=1)
