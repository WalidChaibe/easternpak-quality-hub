"""
Build the analysis population BEFORE any maths (spec section 3).
Pure functions - no Streamlit calls. app.py surfaces the exclusion_log in an expander.
"""
from dataclasses import dataclass, field
import pandas as pd


@dataclass
class ExclusionLog:
    reasons: dict = field(default_factory=dict)  # reason -> DataFrame of dropped rows/projects

    def add(self, reason: str, df: pd.DataFrame):
        self.reasons[reason] = df

    def summary(self) -> pd.DataFrame:
        return pd.DataFrame(
            [{"reason": r, "count": len(d)} for r, d in self.reasons.items()]
        )


def split_completed_open(df: pd.DataFrame):
    """F1 - split (never drop) into completed vs open project populations."""
    project_completed = df.groupby("project_name")["project_completed_date"].max()
    completed_projects = project_completed[project_completed.notna()].index
    open_projects = project_completed[project_completed.isna()].index

    completed = df[df["project_name"].isin(completed_projects)].copy()
    open_df = df[df["project_name"].isin(open_projects)].copy()
    return completed, open_df


def filter_truncated(completed: pd.DataFrame, log: ExclusionLog) -> pd.DataFrame:
    """
    F2 - drop projects whose earliest task did not start the same calendar day
    the project was created (tol_days = 0, exact same-day match).
    Logs every dropped project with both dates for audit.
    """
    first_task_start = completed.groupby("project_name")["task_started"].min()
    project_created = completed.groupby("project_name")["project_created_date"].min()

    cmp = pd.DataFrame({
        "first_task_start": first_task_start,
        "project_created_date": project_created,
    })
    cmp["truncated"] = (
        cmp["first_task_start"].dt.normalize() != cmp["project_created_date"].dt.normalize()
    )

    truncated_projects = cmp.index[cmp["truncated"]]
    log.add("F2_truncated_project", cmp.loc[truncated_projects].reset_index())

    return completed[~completed["project_name"].isin(truncated_projects)].copy()


def apply_sidebar_filters(
    df: pd.DataFrame,
    project_template=None,
    completed_by=None,
    date_range=None,
    exclude_cliche: bool = False,
):
    """
    F3 - optional slicers, always applied via explicit params (never hardcoded).
    date_range: (start, end) tuple/timestamps, filters on project_created_date.
    exclude_cliche: drop rows whose base_step == 'Cliche Production' (Stage 7).
    """
    out = df.copy()

    if project_template:
        out = out[out["project_template"].isin(project_template)]
    if completed_by:
        out = out[out["completed_by"].isin(completed_by)]
    if date_range:
        start, end = date_range
        out = out[
            (out["project_created_date"] >= pd.Timestamp(start))
            & (out["project_created_date"] <= pd.Timestamp(end))
        ]
    if exclude_cliche:
        out = out[out["base_step"] != "Cliche Production"]

    return out
