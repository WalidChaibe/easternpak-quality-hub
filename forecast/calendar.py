"""
Eid holiday calendar and 'effective invoicing days'.

Why: the invoice data shows invoicing drops to 0-90 MT/day (vs ~224 normal) for a few
days at each Eid, then recovers. That is a working-days effect, not a demand change.
Models are fitted on MT per effective day, then multiplied back by each future
month's effective days - so moving Eid dates are handled exactly.

Dates up to 2026 are the actual Saudi dates. 2027 onward are TENTATIVE (moon sighting):
update them here once officially announced. Pure functions - no Streamlit calls.
"""
import pandas as pd

EID_AL_FITR = {
    2023: "2023-04-21",
    2024: "2024-04-10",
    2025: "2025-03-30",
    2026: "2026-03-20",
    2027: "2027-03-10",   # tentative
    2028: "2028-02-27",   # tentative
    2029: "2029-02-15",   # tentative
}
EID_AL_ADHA = {
    2023: "2023-06-28",
    2024: "2024-06-16",
    2025: "2025-06-06",
    2026: "2026-05-27",
    2027: "2027-05-16",   # tentative
    2028: "2028-05-05",   # tentative
    2029: "2029-04-24",   # tentative
}
TENTATIVE_FROM_YEAR = 2027

# Days relative to Eid day that showed reduced invoicing in 2024-2026 data.
FITR_WINDOW = (-1, 3)    # eve of Eid + Eid + 3 days
ADHA_WINDOW = (0, 2)     # Eid + 2 days

DEFAULT_HOLIDAY_WEIGHT = 0.3


def holiday_dates(start, end) -> pd.DatetimeIndex:
    days = []
    for table, (lo, hi) in ((EID_AL_FITR, FITR_WINDOW), (EID_AL_ADHA, ADHA_WINDOW)):
        for d in table.values():
            eid = pd.Timestamp(d)
            days.extend(pd.date_range(eid + pd.Timedelta(days=lo), eid + pd.Timedelta(days=hi)))
    idx = pd.DatetimeIndex(sorted(set(days)))
    return idx[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))]


def missing_years(start, end) -> list[int]:
    years = range(pd.Timestamp(start).year, pd.Timestamp(end).year + 1)
    return [y for y in years if y not in EID_AL_FITR or y not in EID_AL_ADHA]


def estimate_holiday_weight(daily_tons: pd.Series) -> float:
    """
    Holiday-day invoicing as a fraction of a normal day, measured from the data:
    mean tons on Eid holiday days / mean tons on the other days within +/-21 days.
    daily_tons: indexed by every calendar day (zeros filled).
    """
    hol = holiday_dates(daily_tons.index.min(), daily_tons.index.max())
    if len(hol) == 0:
        return DEFAULT_HOLIDAY_WEIGHT
    hol_set = set(hol)
    ratios = []
    for eid_block in _blocks(hol):
        around = daily_tons[(daily_tons.index >= eid_block[0] - pd.Timedelta(days=21))
                            & (daily_tons.index <= eid_block[-1] + pd.Timedelta(days=21))]
        base = around[[d not in hol_set for d in around.index]]
        if len(base) and base.mean() > 0:
            ratios.append(daily_tons.reindex(eid_block).mean() / base.mean())
    if not ratios:
        return DEFAULT_HOLIDAY_WEIGHT
    return float(min(max(sum(ratios) / len(ratios), 0.0), 1.0))


def _blocks(idx: pd.DatetimeIndex) -> list[pd.DatetimeIndex]:
    blocks, cur = [], [idx[0]]
    for d in idx[1:]:
        if (d - cur[-1]).days == 1:
            cur.append(d)
        else:
            blocks.append(pd.DatetimeIndex(cur)); cur = [d]
    blocks.append(pd.DatetimeIndex(cur))
    return blocks


def day_weights(start, end, holiday_weight: float) -> pd.Series:
    days = pd.date_range(pd.Timestamp(start), pd.Timestamp(end))
    w = pd.Series(1.0, index=days)
    w.loc[w.index.isin(holiday_dates(start, end))] = holiday_weight
    return w


def effective_days(start, end, holiday_weight: float) -> pd.Series:
    """Effective invoicing days per month (PeriodIndex) between start and end inclusive."""
    w = day_weights(start, end, holiday_weight)
    return w.groupby(w.index.to_period("M")).sum()
