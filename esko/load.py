"""
Load and clean a raw Esko 'Search Results' export.

Implements spec sections 1 (input) and 2 (cleaning).
Pure function, no Streamlit calls.
"""
import re
import pandas as pd

# Raw column name -> normalized snake_case name
COLUMN_MAP = {
    "Task Name": "task_name",
    "Task Status": "task_status",
    "Task Started": "task_started",
    "Task Due": "task_due",
    "Task Completed": "task_completed",
    "Task Duration [days]": "task_duration_days",
    "Completed by": "completed_by",
    "Assigned to": "assigned_to",
    "Project Name": "project_name",
    "Project Creator": "project_creator",
    "Project Template": "project_template",
    "Project Status": "project_status",
    "Assignee's Location": "assignee_location",
    "Project Created Date": "project_created_date",
    "Project Completed": "project_completed_date",
    "Type": "task_type",
    "FT#": "ft_number",
    "SC#": "sc_number",
    "SR#": "sr_number",
    "Stage Name": "stage_name",
}

DATE_COLS = [
    "task_started",
    "task_due",
    "task_completed",
    "project_created_date",
    "project_completed_date",
]

_SUFFIX_RE = re.compile(r"_(\d+)\s*$")


def _strip_suffix(name: str) -> str:
    """Remove a trailing _N rework suffix, e.g. 'Artwork Preparation_2' -> 'Artwork Preparation'."""
    if not isinstance(name, str):
        return name
    return _SUFFIX_RE.sub("", name).strip()


def load_export(file) -> pd.DataFrame:
    """
    Read the raw Esko export (file-like object, e.g. from st.file_uploader) and
    return a cleaned, normalized DataFrame. One row = one task.

    Steps:
      - normalize headers
      - parse date columns
      - fill Stage Name per 2.1 (stage_name = stage_name if non-empty else task_name)
      - trim whitespace
      - add base_step = stage_name with trailing _N suffix stripped (roll-up only,
        the suffixed stage_name itself is preserved as the canonical step identity
        per 2.2 - do not strip it from stage_name)
    """
    df = pd.read_excel(file) if _is_excel(file) else pd.read_csv(file)

    missing = [c for c in COLUMN_MAP if c not in df.columns]
    if missing:
        raise ValueError(
            f"Export is missing expected column(s): {missing}. "
            f"Found columns: {list(df.columns)}"
        )

    df = df.rename(columns=COLUMN_MAP)

    for col in DATE_COLS:
        df[col] = pd.to_datetime(df[col], errors="coerce")

    for col in ["task_name", "stage_name", "project_name", "completed_by", "assigned_to"]:
        df[col] = df[col].astype(str).str.strip()
        df[col] = df[col].replace({"nan": "", "None": ""})

    # 2.1 - fill Stage Name
    blank_stage = df["stage_name"].eq("") | df["stage_name"].isna()
    df.loc[blank_stage, "stage_name"] = df.loc[blank_stage, "task_name"]
    df["stage_name"] = df["stage_name"].str.strip()

    # 2.2 - base_step for roll-up views only; stage_name (with suffix) stays canonical
    df["base_step"] = df["stage_name"].apply(_strip_suffix)

    return df


def _is_excel(file) -> bool:
    name = getattr(file, "name", "")
    if isinstance(name, str) and name:
        return name.lower().endswith((".xlsx", ".xls"))
    # fall back: try to sniff, default to excel since Esko exports are typically .xlsx
    return True
