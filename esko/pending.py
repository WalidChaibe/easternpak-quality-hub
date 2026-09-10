"""
Open (not-completed) project analysis - spec section 7.
Project Completed is blank -> lead-time metrics are irrelevant. Only question:
where is each open project stuck (pending step/stage/owner) and how long has it sat.
"""
import numpy as np
import pandas as pd
from esko.metrics import CAL

AGE_BUCKETS = [
    (0, 3, "0-3 days"),
    (4, 7, "4-7 days"),
    (8, 14, "8-14 days"),
    (15, 30, "15-30 days"),
    (31, np.inf, "30+ days"),
]


def _busdays_to_today(start: pd.Series, today: pd.Timestamp) -> pd.Series:
    start_d = start.values.astype("datetime64[D]")
    end_d = np.full(len(start), np.datetime64(today.normalize(), "D"))
    return pd.Series(np.busday_count(start_d, end_d, busdaycal=CAL), index=start.index)


def find_pending_task(open_df: pd.DataFrame) -> pd.DataFrame:
    """
    For each open project, find the current/last task:
    row with blank Task Completed, else the row with max Task Started.
    Returns one row per project: pending_step, pending_stage, pending_owner,
    pending_task_started, project_created_date.
    """
    rows = []
    for project, g in open_df.groupby("project_name"):
        incomplete = g[g["task_completed"].isna()]
        current = incomplete if len(incomplete) else g.loc[[g["task_started"].idxmax()]]
        # if multiple incomplete tasks, take the one started most recently
        current_row = current.loc[current["task_started"].idxmax()]
        rows.append({
            "project_name": project,
            "pending_step": current_row["stage_name"],
            "pending_stage_label": current_row.get("stage_label", current_row["stage_name"]),
            "pending_stage_no": current_row.get("stage_no", np.nan),
            "pending_owner": current_row["assigned_to"],
            "pending_task_started": current_row["task_started"],
            "project_created_date": g["project_created_date"].min(),
        })
    return pd.DataFrame(rows)


def open_project_ages(pending: pd.DataFrame, today: pd.Timestamp = None) -> pd.DataFrame:
    today = today or pd.Timestamp.now()
    out = pending.copy()
    out["age_in_stage"] = _busdays_to_today(out["pending_task_started"], today)
    out["project_age"] = _busdays_to_today(out["project_created_date"], today)
    out["age_bucket"] = out["age_in_stage"].apply(_bucket)
    return out


def _bucket(days: float) -> str:
    for lo, hi, label in AGE_BUCKETS:
        if lo <= days <= hi:
            return label
    return AGE_BUCKETS[-1][2]


def count_by_pending_stage(aged: pd.DataFrame) -> pd.DataFrame:
    return (
        aged.groupby(["pending_stage_no", "pending_stage_label"])
        .size().rename("open_count").reset_index()
        .sort_values("open_count", ascending=False)
    )


def count_by_pending_owner(aged: pd.DataFrame) -> pd.DataFrame:
    return (
        aged.groupby("pending_owner").size().rename("open_count")
        .reset_index().sort_values("open_count", ascending=False)
    )


def ageing_buckets_by_stage(aged: pd.DataFrame) -> pd.DataFrame:
    bucket_order = [b[2] for b in AGE_BUCKETS]
    pivot = (
        aged.groupby(["age_bucket", "pending_stage_label"]).size()
        .rename("open_count").reset_index()
    )
    pivot["age_bucket"] = pd.Categorical(pivot["age_bucket"], categories=bucket_order, ordered=True)
    return pivot.sort_values("age_bucket")


def oldest_open_projects(aged: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    cols = ["project_name", "pending_owner", "pending_stage_label", "pending_step", "age_in_stage", "project_age"]
    return aged.sort_values("project_age", ascending=False)[cols].head(n)
