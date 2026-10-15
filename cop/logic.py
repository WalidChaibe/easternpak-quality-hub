"""
Customer COP strategy - all calculations. Pure functions - no Streamlit calls.

Mirrors the 'Customer Strategy' and 'Summary' sheets of the PM's COP workbook (checked customer by customer:
2025 vs 2026, Jan-Sep, with name merging switched off), with these agreed changes:
  1. Margin tiers set by the manager: High >= $230/t, Mid $65-230/t, Low $0-65/t, Loss-making < $0/t.
     Worth fighting for = High or Mid margin, OR a key account (one of the customers that together make
     80% of PY COP) - so a big customer is worth fighting for even at a low margin.
  2. Momentum compares the last N months of CY with the SAME months of PY (seasonality-safe).
  3. Customer names that differ only in case / spaces / punctuation, or by an added Arabic name, are one customer.
  4. Plain labels: the bridge is Volume / Price / Cost / New / Lost / Small customers.

Units: MT, SAR k for sales/COP totals, $/t for per-ton values ($/t = SAR k x 1000 / MT / FX).
Customer universe: every customer in the PY or CY file (any month), as in the workbook.
"""
import re
from dataclasses import dataclass, field
import numpy as np
import pandas as pd

from cop.load import MONTHS

STRATEGIES = {
    1: ("Grow", "Volume growing, COP per ton up or flat",
        "Keep the price. Sell them more products. Offer volume deals or longer contracts."),
    2: ("Hold & plan next increase", "Volume stable, COP per ton up",
        "Keep the price – it is sticking. Plan the next increase."),
    3: ("Raise price", "Volume not falling, but COP per ton falling (or flat with stable volume), or loss-making",
        "Raise price at next renewal. Show cost evidence. The customer is not leaving."),
    4: ("Defend", "Volume falling, worth fighting for (High/Mid margin or key account)",
        "Meet them quickly. Find the reason. Fix service or design before touching price."),
    5: ("Fix or exit", "Volume falling, Low or loss-making margin and not a key account",
        "Don't discount to keep them. Reprice to cover cost, or let the volume go."),
    6: ("Onboard", "New or regained customer",
        "Check the price covers cost and freight. Agree terms early."),
    7: ("Win back", "Lost customer that was worth fighting for (judged on PY)",
        "Call them. Find out why they left. Try to win them back."),
    8: ("Let go", "Lost customer with a Low or loss-making PY margin, not a key account",
        "Don't chase. Put the effort elsewhere."),
    9: ("Not classified", "Small customer: under the minimum MT in both years", "Too small. Review once a year."),
}

READINGS = {
    "Real margin gain": "Price went up and most of it stayed as COP",
    "Partly cost recovery": "Price went up; part covered higher cost/freight, part became COP",
    "Cost recovery only": "Price went up but COP per ton barely moved – the increase only covered cost",
    "Cost ran ahead of price": "Price went up but cost rose more – COP per ton fell; needs another increase",
    "Discount covered by cost savings": "Price went down but lower cost kept COP per ton – exposed if costs rise",
    "Discount cut margin": "Price went down and COP per ton fell",
    "Margin up without price": "Price flat; COP per ton rose because cost per ton fell",
    "Margin squeezed – no price increase": "Price flat; cost rose so COP per ton fell",
    "No change": "Price and COP per ton both roughly flat",
}
STATUSES = ["Continuing", "New / regained", "Lost / near-lost", "Small customer"]
TIERS = ["High", "Mid", "Low", "Loss-making"]


@dataclass
class Settings:                       # defaults = the workbook's Assumptions sheet
    month_to: int = 9                 # compare months 1..M
    fx: float = 3.75                  # SAR per USD
    min_mt: float = 10.0              # minimum MT in the period (the page sets it as MT per month x months compared)
    recent_months: int = 3            # momentum window
    vol_up: float = 0.05              # volume Growing if change >= +5%
    vol_down: float = -0.05           # volume Declining if change <= -5%
    copt_up: float = 10.0             # COP/t Up if change >= +$10/t
    copt_down: float = -10.0          # COP/t Down if change <= -$10/t
    min_price_change: float = 20.0    # capture only measured if |price change| >= $20/t
    capture_real: float = 0.75
    capture_partly: float = 0.25
    tier_mid: float = 65.0            # margin tier: Mid from $65/t ...
    tier_high: float = 230.0          # ... High from $230/t (Low below $65/t, Loss-making below $0/t)
    key_share: float = 0.80           # key accounts: the customers that together make 80% of PY COP
    merge_names: bool = True          # always on in the app; False only in tests, to reproduce the workbook exactly
    manual_keys: tuple = ()           # customers the PM marks as key accounts by hand
    cost_adjust: float = 0.0          # cost-risk scenario: PY cost/t x (1 + this)
    actions: dict = field(default_factory=lambda: {k: v[2] for k, v in STRATEGIES.items()})


def per_ton(sar_k, mt, fx):
    """$/t = SAR k x 1000 / MT / FX; NaN where MT <= 0."""
    mt = np.asarray(mt, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(mt > 0, np.asarray(sar_k, dtype=float) * 1000 / np.where(mt > 0, mt, 1) / fx, np.nan)


_ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]+")
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def name_key(name: str) -> str:
    """
    Same customer if the names match after ignoring case, spaces and punctuation - and, for names written
    'English + Arabic', after dropping the Arabic part ('شركة الراشد للاغذية Rashed Bakery' = 'Rashed Bakery').
    Names written only in Arabic keep their Arabic letters.
    """
    latin = _NON_WORD.sub("", _ARABIC.sub("", str(name))).upper()
    return latin if latin else _NON_WORD.sub("", str(name)).upper()


def standardize_names(data: dict) -> tuple[dict, pd.DataFrame]:
    """
    Merge name variants into one customer, shown under the spelling with the most MT in the latest year it appears.
    Returns the renamed data and the list of merges (for review). Nothing is dropped.
    """
    rows = []
    for y, d in data.items():
        g = d.groupby("customer")["mt"].sum()
        rows += [{"customer": n, "year": y, "mt": v} for n, v in g.items()]
    seen = pd.DataFrame(rows)
    seen["key"] = seen["customer"].map(name_key)
    seen = seen.sort_values(["key", "year", "mt"], ascending=[True, False, False])
    display = seen.groupby("key")["customer"].first()
    mapping = {n: display[k] for n, k in zip(seen["customer"], seen["key"])}
    variants = seen.groupby("key")["customer"].agg(lambda x: sorted(set(x)))
    merged = variants[variants.map(len) > 1]
    merges = pd.DataFrame({"shown_as": [display[k] for k in merged.index],
                           "names_merged": [" | ".join(v) for v in merged]})
    out = {}
    for y, d in data.items():
        d = d.copy()
        d["customer"] = d["customer"].map(mapping)
        out[y] = d
    return out, merges.sort_values("shown_as").reset_index(drop=True)


def period(df: pd.DataFrame, lo: int, hi: int) -> pd.DataFrame:
    return df[(df["m"] >= lo) & (df["m"] <= hi)]


def default_month_to(cy_df: pd.DataFrame, cy: int, today) -> int:
    """Last COMPLETE month of CY: the last month in the file, minus one if it is the current calendar month."""
    last = int(cy_df["m"].max())
    if cy == today.year and last >= today.month:
        return max(last - 1, 1)
    return last


# ── Year KPIs ─────────────────────────────────────────────────────────────────────────────────────
def year_kpis(data: dict, s: Settings) -> pd.DataFrame:
    rows = []
    for y in sorted(data):
        d = period(data[y], 1, s.month_to)
        mt, sales, cop = d["mt"].sum(), d["sales"].sum(), d["cop"].sum()
        price = sales * 1000 / mt / s.fx if mt else np.nan
        copt = cop * 1000 / mt / s.fx if mt else np.nan
        active = int((d.groupby("customer")["mt"].sum() > 0).sum())
        rows.append({"year": y, "mt": mt, "sales": sales, "cop": cop, "price_t": price,
                     "cost_t": price - copt, "cop_t": copt, "active_customers": active})
    return pd.DataFrame(rows)


# ── Customer table ────────────────────────────────────────────────────────────────────────────────
def customer_table(data: dict, cy: int, py: int, s: Settings) -> pd.DataFrame:
    years = sorted(data)
    names = pd.Index(pd.unique(pd.concat([data[py]["customer"], data[cy]["customer"]])), name="customer")
    t = pd.DataFrame(index=names)
    for y in years:
        a = period(data[y], 1, s.month_to).groupby("customer")[["mt", "sales", "cop"]].sum().reindex(names).fillna(0.0)
        t[f"mt_{y}"], t[f"sales_{y}"], t[f"cop_{y}"] = a["mt"], a["sales"], a["cop"]
        t[f"price_{y}"] = per_ton(t[f"sales_{y}"], t[f"mt_{y}"], s.fx)
        t[f"copt_{y}"] = per_ton(t[f"cop_{y}"], t[f"mt_{y}"], s.fx)
        t[f"cost_{y}"] = t[f"price_{y}"] - t[f"copt_{y}"]

    mp, mc = t[f"mt_{py}"], t[f"mt_{cy}"]
    t["status"] = np.select([(mp < s.min_mt) & (mc < s.min_mt), mp < s.min_mt, mc < s.min_mt],
                            ["Small customer", "New / regained", "Lost / near-lost"], "Continuing")
    cont = t["status"] == "Continuing"

    t["vol_pct"] = np.where((mp > 0) & (mc > 0), mc / mp.where(mp > 0) - 1, np.nan)
    t["dprice"] = t[f"price_{cy}"] - t[f"price_{py}"]
    t["dprice_pct"] = t[f"price_{cy}"] / t[f"price_{py}"] - 1
    t["dcost"] = t[f"cost_{cy}"] - t[f"cost_{py}"]
    t["dcopt"] = t[f"copt_{cy}"] - t[f"copt_{py}"]
    t["dcop"] = t[f"cop_{cy}"] - t[f"cop_{py}"]
    t["dcop_pct"] = np.where(t[f"cop_{py}"] > 0, t[f"cop_{cy}"] / t[f"cop_{py}"].where(t[f"cop_{py}"] > 0) - 1, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        t["cop_pct_sales"] = np.where(t[f"sales_{cy}"] != 0, t[f"cop_{cy}"] / t[f"sales_{cy}"], np.nan)

    # Capture and the Price -> COP reading (continuing customers only)
    measurable = cont & (t["dprice"].abs() >= s.min_price_change)
    t["capture"] = np.where(measurable, t["dcopt"] / t["dprice"], np.nan)
    t["reading"] = [_reading(st, i, u, w, s) for st, i, u, w in zip(t["status"], t["dprice"], t["dcopt"], t["capture"])]

    # COP bridge
    t["vol_eff"] = np.where(cont, (mc - mp) * t[f"cop_{py}"] / mp.where(mp > 0), np.nan)
    t["price_eff"] = np.where(cont, t["dprice"] * mc * s.fx / 1000, np.nan)
    t["cost_eff"] = np.where(cont, t["dcop"] - t["vol_eff"] - t["price_eff"], np.nan)

    # Moves
    t["vol_move"] = np.where(cont, np.where(t["vol_pct"] >= s.vol_up, "Growing",
                                            np.where(t["vol_pct"] <= s.vol_down, "Declining", "Stable")), "")
    t["copt_move"] = np.where(cont, np.where(t["dcopt"] >= s.copt_up, "Up",
                                             np.where(t["dcopt"] <= s.copt_down, "Down", "Flat")), "")

    # Worth fighting for = High or Mid margin tier, OR a key account
    t["tier_cy"] = t[f"copt_{cy}"].map(lambda x: margin_tier(x, s))
    t["tier_py"] = t[f"copt_{py}"].map(lambda x: margin_tier(x, s))
    t["margin_tier"] = np.where(t["status"] == "Lost / near-lost", t["tier_py"], t["tier_cy"])
    top = top_share(t[f"cop_{py}"], s.key_share)
    manual = t.index.isin(list(s.manual_keys))
    t["key_account"] = top | manual
    t["key_reason"] = np.where(top, f"Top {s.key_share:.0%} of {py} COP", np.where(manual, "Marked by PM", ""))
    t["worth_cy"] = t["tier_cy"].isin(["High", "Mid"]) | t["key_account"]
    t["worth_py"] = t["tier_py"].isin(["High", "Mid"]) | t["key_account"]
    t["worth"] = np.where(t["status"] == "Lost / near-lost", t["worth_py"],
                          np.where(t["status"] == "Continuing", t["worth_cy"], False))

    t["strategy_no"] = [strategy(st, v, c, x, wc, wp) for st, v, c, x, wc, wp in
                        zip(t["status"], t["vol_move"], t["copt_move"], t[f"copt_{cy}"], t["worth_cy"], t["worth_py"])]
    t["strategy"] = t["strategy_no"].map(lambda k: STRATEGIES[k][0])
    t["action"] = t["strategy_no"].map(lambda k: s.actions.get(k, STRATEGIES[k][2]))
    # COP growth driver (Grow customers only). Price bar > 0 <=> price per ton went up;
    # cost bar > 0 <=> cost per ton went down - so the labels say exactly what happened.
    up_p, down_c = t["price_eff"] > 0, t["cost_eff"] > 0
    t["grow_tag"] = np.where(t["strategy_no"] == 1,
                             np.select([up_p & down_c, up_p, down_c],
                                       ["Price up, cost down", "Price up, cost up", "Price down, cost down"],
                                       "Volume only"), "")

    t["trend"] = multi_year_trend(t, years, s.min_mt)
    t = t.join(momentum(data, cy, py, names, s))
    t["share_cy_cop"] = t[f"cop_{cy}"] / t[f"cop_{cy}"].sum()
    t["at_stake"] = at_stake(t, cy, py, s.fx)
    return t.sort_values(f"cop_{cy}", ascending=False, kind="stable").reset_index()


def _reading(st, i, u, w, s: Settings) -> str:
    if st != "Continuing" or pd.isna(i):
        return ""
    if i >= s.min_price_change:
        if w >= s.capture_real:
            return "Real margin gain"
        if w >= s.capture_partly:
            return "Partly cost recovery"
        return "Cost recovery only" if w >= 0 else "Cost ran ahead of price"
    if i <= -s.min_price_change:
        return "Discount covered by cost savings" if u >= 0 else "Discount cut margin"
    if u >= s.copt_up:
        return "Margin up without price"
    if u <= s.copt_down:
        return "Margin squeezed – no price increase"
    return "No change"


def margin_tier(copt, s: Settings) -> str:
    """High >= $230/t · Mid $65-230/t · Low $0-65/t · Loss-making < $0/t (thresholds editable)."""
    if pd.isna(copt):
        return ""
    if copt < 0:
        return "Loss-making"
    if copt >= s.tier_high:
        return "High"
    return "Mid" if copt >= s.tier_mid else "Low"


def top_share(cop_py: pd.Series, share: float) -> pd.Series:
    """True for customers inside the top `share` of PY COP: ranked by PY COP, cumulative share before them < share."""
    pos = cop_py.clip(lower=0)
    total = pos.sum()
    if total <= 0:
        return pd.Series(False, index=cop_py.index)
    order = pos.sort_values(ascending=False, kind="stable")
    before = (order.cumsum() - order) / total
    inside = (before < share) & (order > 0)
    return inside.reindex(cop_py.index).fillna(False)


def strategy(st, v, c, copt_cy, worth_cy, worth_py) -> int:
    """Exactly one strategy per customer - same precedence as the workbook's Strategy # formula."""
    if st == "Small customer":
        return 9
    if st == "New / regained":
        return 6
    if st == "Lost / near-lost":
        return 7 if worth_py else 8
    if v == "Declining":
        return 4 if worth_cy else 5
    if c == "Down" or (pd.notna(copt_cy) and copt_cy < 0) or (v == "Stable" and c == "Flat"):
        return 3
    return 1 if v == "Growing" else 2


def strategy_excel_tier(t: pd.DataFrame, cy: int, py: int, high_pct: float = 0.67, low_pct: float = 0.33) -> pd.Series:
    """
    The workbook's ORIGINAL margin-tier rule (High/Mid = worth fighting for). Not used by the app - kept so the
    tests can prove the rule tree reproduces the workbook's strategy counts exactly.
    """
    cont = t["status"] == "Continuing"
    vals = t.loc[cont, f"copt_{cy}"].dropna().to_numpy()
    hi, lo = np.percentile(vals, high_pct * 100), np.percentile(vals, low_pct * 100)

    def tier(x):
        if pd.isna(x):
            return ""
        return "Loss-making" if x < 0 else "High" if x >= hi else "Low" if x <= lo else "Mid"

    out = []
    for _, r in t.iterrows():
        x = r[f"copt_{py}"] if r["status"] == "Lost / near-lost" else (np.nan if r["status"] == "Small customer" else r[f"copt_{cy}"])
        hm = tier(x) in ("High", "Mid")
        out.append(strategy(r["status"], r["vol_move"], r["copt_move"], r[f"copt_{cy}"], hm, hm))
    return pd.Series(out, index=t.index)


def multi_year_trend(t: pd.DataFrame, years: list, min_mt: float) -> pd.Series:
    """
    Customers with MT >= min in EVERY uploaded year, labelled by the direction of COP over the last two changes:
    up, up = Growing · up, down = Falling back · down, up = Recovering · down, down = Declining (flat counts as up).
    With only 2 years: Growing / Declining.
    """
    active = np.logical_and.reduce([t[f"mt_{y}"] >= min_mt for y in years])
    out = pd.Series(f"Not active all {len(years)} yrs", index=t.index, dtype=object)
    if len(years) >= 3:
        a, b, c = (t[f"cop_{y}"] for y in years[-3:])
        lab = np.where(b >= a, np.where(c >= b, "Growing", "Falling back"), np.where(c >= b, "Recovering", "Declining"))
    else:
        a, b = (t[f"cop_{y}"] for y in years[-2:])
        lab = np.where(b >= a, "Growing", "Declining")
    out[active] = lab[active]
    return out


def momentum(data: dict, cy: int, py: int, names: pd.Index, s: Settings) -> pd.DataFrame:
    """COP/t in the last N months of CY vs the SAME months of PY (seasonality-safe)."""
    lo, hi = max(1, s.month_to - s.recent_months + 1), s.month_to
    def window(y):
        a = period(data[y], lo, hi).groupby("customer")[["mt", "cop"]].sum().reindex(names).fillna(0.0)
        return pd.Series(per_ton(a["cop"], a["mt"], s.fx), index=names), a["mt"]
    c_cy, m_cy = window(cy)
    c_py, m_py = window(py)
    diff = c_cy - c_py
    lab = np.select([m_cy <= 0, m_py <= 0, diff >= s.copt_up, diff <= s.copt_down],
                    [f"No orders in last {s.recent_months} months", "No orders same months last year",
                     "Improving", "Slipping"], "Steady")
    return pd.DataFrame({"recent_copt_cy": c_cy, "recent_copt_py": c_py, "momentum_raw": lab}, index=names)


def at_stake(t: pd.DataFrame, cy: int, py: int, fx: float) -> pd.Series:
    """
    SAR k at stake, for the focus list:
      Defend      -volume effect
      Win back    PY COP
      Raise price MT_CY x max(0, COP/t PY - COP/t CY), converted from $ to SAR k (x FX / 1000)
      Grow        positive ΔCOP
    """
    k = t["strategy_no"]
    gap = (t[f"copt_{py}"] - t[f"copt_{cy}"]).clip(lower=0).fillna(0)
    return pd.Series(np.select(
        [k == 4, k == 7, k == 3, k == 1],
        [-t["vol_eff"], t[f"cop_{py}"], t[f"mt_{cy}"] * gap * fx / 1000, t["dcop"].clip(lower=0)],
        np.nan), index=t.index)


def finalize(t: pd.DataFrame) -> pd.DataFrame:
    """Momentum label only for continuing customers."""
    t = t.copy()
    t["momentum"] = np.where(t["status"] == "Continuing", t["momentum_raw"], "")
    return t.drop(columns="momentum_raw")


# ── Summaries ─────────────────────────────────────────────────────────────────────────────────────
def bridge(t: pd.DataFrame, cy: int, py: int) -> dict:
    b = {
        "cop_py": t[f"cop_{py}"].sum(),
        "volume": t["vol_eff"].sum(), "price": t["price_eff"].sum(), "cost": t["cost_eff"].sum(),
        "new": t.loc[t["status"] == "New / regained", "dcop"].sum(),
        "lost": t.loc[t["status"] == "Lost / near-lost", "dcop"].sum(),
        "small": t.loc[t["status"] == "Small customer", "dcop"].sum(),
        "cop_cy": t[f"cop_{cy}"].sum(),
    }
    b["check"] = b["cop_py"] + b["volume"] + b["price"] + b["cost"] + b["new"] + b["lost"] + b["small"] - b["cop_cy"]
    return b


def strategy_summary(t: pd.DataFrame, cy: int, py: int, s: Settings) -> pd.DataFrame:
    rows = []
    for k, (name, who, _) in STRATEGIES.items():
        x = t[t["strategy_no"] == k]
        rows.append({"#": k, "strategy": name, "who": who, "customers": len(x), f"mt_{cy}": x[f"mt_{cy}"].sum(),
                     f"cop_{py}": x[f"cop_{py}"].sum(), f"cop_{cy}": x[f"cop_{cy}"].sum(), "dcop": x["dcop"].sum(),
                     "volume": x["vol_eff"].sum(), "price": x["price_eff"].sum(), "cost": x["cost_eff"].sum(),
                     "action": s.actions.get(k, STRATEGIES[k][2])})
    out = pd.DataFrame(rows)
    tot = out.drop(columns=["#", "strategy", "who", "action"]).sum()
    out.loc[len(out)] = {"#": 0, "strategy": "Total", "who": "", "action": "", **tot.to_dict()}
    out["#"] = out["#"].astype(int).astype(str).replace("0", "")
    out["customers"] = out["customers"].astype(int)
    return out


def reading_summary(t: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r, meaning in READINGS.items():
        x = t[t["reading"] == r]
        rows.append({"reading": r, "customers": len(x),
                     "avg_dprice": x["dprice"].mean() if len(x) else 0.0,
                     "avg_dcopt": x["dcopt"].mean() if len(x) else 0.0,
                     "dcop": x["dcop"].sum(), "price": x["price_eff"].sum(), "cost": x["cost_eff"].sum(),
                     "meaning": meaning})
    return pd.DataFrame(rows)


def focus_list(t: pd.DataFrame, n: int = 30) -> pd.DataFrame:
    f = t[t["at_stake"].notna() & (t["at_stake"] > 0)].sort_values("at_stake", ascending=False).head(n)
    return f


def cost_risk(t: pd.DataFrame, cy: int, py: int, s: Settings) -> pd.DataFrame:
    """CY COP if cost/t returned to PY level x (1 + adjust). Customers without PY volume keep their CY COP."""
    r = t[["customer", "status", "strategy", f"mt_{cy}", f"cop_{cy}", f"cost_{py}", f"cost_{cy}"]].copy()
    has_py = r[f"cost_{py}"].notna() & (r[f"mt_{cy}"] > 0)
    scen_cost = r[f"cost_{py}"] * (1 + s.cost_adjust)
    r["cost_scenario_t"] = np.where(has_py, scen_cost, np.nan)
    r["cop_scenario"] = np.where(has_py, r[f"cop_{cy}"] - r[f"mt_{cy}"] * (scen_cost - r[f"cost_{cy}"]) * s.fx / 1000,
                                 r[f"cop_{cy}"])
    r["at_risk"] = r[f"cop_{cy}"] - r["cop_scenario"]
    r["scenario_basis"] = np.where(has_py, "PY cost/t", "no PY volume – CY COP kept")
    return r.sort_values("at_risk", ascending=False)


def monthly_copt(data: dict, customer: str, fx: float) -> pd.DataFrame:
    rows = []
    for y, d in sorted(data.items()):
        a = d[d["customer"] == customer].groupby("m")[["mt", "cop"]].sum()
        for m, r in a.iterrows():
            rows.append({"year": str(y), "month": MONTHS[m - 1], "m": m,
                         "cop_t": r["cop"] * 1000 / r["mt"] / fx if r["mt"] > 0 else np.nan})
    return pd.DataFrame(rows)


def run(data: dict, s: Settings):
    """Everything the app needs, in one call (cache this)."""
    years = sorted(data)
    cy, py = years[-1], years[-2]
    merges = pd.DataFrame(columns=["shown_as", "names_merged"])
    if s.merge_names:
        data, merges = standardize_names(data)
    t = finalize(customer_table(data, cy, py, s))
    return {"cy": cy, "py": py, "years": years, "table": t, "kpis": year_kpis(data, s),
            "bridge": bridge(t, cy, py), "strategies": strategy_summary(t, cy, py, s),
            "readings": reading_summary(t), "focus": focus_list(t), "risk": cost_risk(t, cy, py, s),
            "merges": merges, "data": data}
