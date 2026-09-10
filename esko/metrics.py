"""
Metric definitions (spec section 4). Every function is pure: DataFrame in, DataFrame out.

Business-day calendar: Sunday-Thursday work week (Fri/Sat weekend), per spec header.
"""
import numpy as np
import pandas as pd

CAL = np.busdaycalendar(weekmask="Sun Mon Tue Wed Thu")

WEIGHT_BASIS_DEFAULT = "global"  # 'global' | 'stage' - config flag per spec 4.3


def _busday_span(start: pd.Series, end: pd.Series) -> np.ndarray:
    """Vectorized Sun-Thu business-day count between two datetime Series."""
    start_d = start.values.astype("datetime64[D]")
    end_d = end.values.astype("datetime64[D]")
    return np.busday_count(start_d, end_d, busdaycal=CAL)


def per_project_metrics(completed: pd.DataFrame) -> pd.DataFrame:
    """
    Spec 4.1. One row per project.
    net_lead_time  = calendar days, project_end - project_start
    calc_lead_time = real Sun-Thu business-day span (NOT the workbook's span-(span/7)*2 approx)
    sys_lead_time  = sum of Task Duration [days] for the project
    variance       = calc_lead_time - sys_lead_time  (idle / queue time)
    """
    g = completed.groupby("project_name")

    project_start = g["project_created_date"].min()
    project_end = g["project_completed_date"].max()
    sys_lead_time = g["task_duration_days"].sum()
    task_count = g.size()

    net_lead_time = (project_end - project_start).dt.total_seconds() / 86400.0
    calc_lead_time = _busday_span(project_start, project_end)
    # workbook-continuity approximation, reported but not charted
    span_days = net_lead_time
    calc_lead_time_workbook_approx = span_days - (span_days / 7) * 2

    out = pd.DataFrame({
        "project_name": project_start.index,
        "project_start": project_start.values,
        "project_end": project_end.values,
        "net_lead_time": net_lead_time.values,
        "calc_lead_time": calc_lead_time,
        "calc_lead_time_workbook_approx": calc_lead_time_workbook_approx.values,
        "sys_lead_time": sys_lead_time.values,
        "task_count": task_count.values,
    }).reset_index(drop=True)
    out["variance"] = out["calc_lead_time"] - out["sys_lead_time"]

    return out.sort_values("variance", ascending=False).reset_index(drop=True)


def per_step_metrics(completed: pd.DataFrame) -> pd.DataFrame:
    """
    Spec 4.2. One row per distinct Stage Name (i.e. per step, suffixes kept distinct).
    Ranked descending by total_days_impact, with cumulative_pct for a Pareto view.
    """
    g = completed.groupby("stage_name")["task_duration_days"]
    out = g.agg(count="count", avg_days="mean").reset_index()
    out["total_days_impact"] = out["count"] * out["avg_days"]

    grand_total = out["total_days_impact"].sum()
    out["pct_of_total"] = out["total_days_impact"] / grand_total if grand_total else 0.0

    out = out.sort_values("total_days_impact", ascending=False).reset_index(drop=True)
    out["cumulative_pct"] = out["pct_of_total"].cumsum()

    return out


def weighted_lead_time(
    step_metrics: pd.DataFrame,
    stage_map_applied: pd.DataFrame,
    basis: str = WEIGHT_BASIS_DEFAULT,
) -> pd.DataFrame:
    """
    Spec 4.3. Weight each step by how often it occurs, so rare steps don't overstate.

    basis='global' (default, DECIDED): denominator = max(count) across ALL steps.
    basis='stage': denominator = max(count) within the step's own stage
                   (old workbook behaviour, kept only for reconciliation - do NOT
                   apply a second stage-level re-weighting on top of this, spec 4.3).

    Returns step-level weighted_lead_time, plus stage_lead_time and
    total_system_lead_time attached as columns for convenience.
    """
    # attach stage_no/stage_label to each step via the (already-applied) stage map
    step_stage = (
        stage_map_applied[["stage_name", "stage_no", "stage_label"]]
        .drop_duplicates("stage_name")
    )
    steps = step_metrics.merge(step_stage, on="stage_name", how="left")

    if steps["stage_no"].isna().any():
        missing = steps.loc[steps["stage_no"].isna(), "stage_name"].tolist()
        raise ValueError(
            f"weighted_lead_time called with unmapped stage_name(s): {missing}. "
            "Run apply_stage_taxonomy / drop unmapped rows first."
        )

    if basis == "global":
        denom = steps["count"].max()
        steps["weight_step"] = steps["count"] / denom
    elif basis == "stage":
        max_by_stage = steps.groupby("stage_no")["count"].transform("max")
        steps["weight_step"] = steps["count"] / max_by_stage
    else:
        raise ValueError("basis must be 'global' or 'stage'")

    steps["weighted_lead_time"] = steps["weight_step"] * steps["avg_days"]

    stage_lead_time = steps.groupby(["stage_no", "stage_label"])["weighted_lead_time"].sum()
    stage_lead_time = stage_lead_time.rename("stage_lead_time").reset_index()
    steps = steps.merge(stage_lead_time, on=["stage_no", "stage_label"], how="left")

    steps.attrs["total_system_lead_time"] = float(stage_lead_time["stage_lead_time"].sum())
    steps.attrs["stage_lead_time"] = stage_lead_time.sort_values("stage_no")

    return steps.sort_values(["stage_no", "total_days_impact"], ascending=[True, False]).reset_index(drop=True)


def sanity_check_weighting(steps_with_weights: pd.DataFrame, completed_project_count: int, tolerance: float = 0.15):
    """
    Spec 4.3 sanity check: after F2, max(count over all steps) should be roughly
    equal to the completed project count. If it noticeably exceeds it, F2 didn't
    do its job.
    Returns (ok: bool, max_count: int, message: str)
    """
    max_count = int(steps_with_weights["count"].max())
    upper_bound = completed_project_count * (1 + tolerance)
    ok = max_count <= upper_bound
    msg = (
        f"max step count = {max_count}, completed projects analysed = {completed_project_count}. "
        + ("OK - within tolerance." if ok else
           "WARNING: max count noticeably exceeds project count - F2 truncation "
           "filter may not be working as expected. Investigate before trusting "
           "weighted numbers.")
    )
    return ok, max_count, msg
