"""
Stage taxonomy (spec section 5).

config/stage_map.csv holds BASE step names (rework suffixes like _1/_2/_3 stripped)
mapped to one of the 7 process stages. Matching is done on base_step, because the
same base step recurs with different suffixes and listing every suffix explicitly
would be unmaintainable. The suffixed stage_name itself is preserved everywhere
else (metrics, counts) per spec 2.2 - only the STAGE LOOKUP is suffix-insensitive.

Any stage_name whose base_step has no entry in the map is UNMAPPED and must be
surfaced loudly in the UI, never silently dropped.
"""
import pandas as pd

REQUIRED_COLS = {"stage_no", "stage_name", "step_name"}


def load_stage_map(path: str = "config/stage_map.csv") -> pd.DataFrame:
    stage_map = pd.read_csv(path)
    missing = REQUIRED_COLS - set(stage_map.columns)
    if missing:
        raise ValueError(f"stage_map.csv missing column(s): {missing}")
    stage_map["step_name"] = stage_map["step_name"].str.strip()
    return stage_map


def apply_stage_taxonomy(df: pd.DataFrame, stage_map: pd.DataFrame):
    """
    Join df (must have base_step) against stage_map on base_step == step_name.

    Returns:
      mapped_df   - df with stage_no, stage_label columns added, unmapped rows dropped
      unmapped_df - the rows that had no match, for the UI to display loudly
    """
    lookup = stage_map.drop_duplicates("step_name").set_index("step_name")

    stage_no = df["base_step"].map(lookup["stage_no"])
    stage_label = df["base_step"].map(lookup["stage_name"])

    out = df.copy()
    out["stage_no"] = stage_no
    out["stage_label"] = stage_label

    is_unmapped = out["stage_no"].isna()
    unmapped_df = out.loc[is_unmapped].copy()
    mapped_df = out.loc[~is_unmapped].copy()
    mapped_df["stage_no"] = mapped_df["stage_no"].astype(int)

    return mapped_df, unmapped_df


STAGE_ORDER = list(range(1, 8))  # chart by stage_no, never sheet row order


def save_stage_map(stage_map: pd.DataFrame, path: str = "config/stage_map.csv") -> None:
    """Persist an edited taxonomy back to the CSV so it survives app reruns.

    NOTE on deployment: this writes to the app's local filesystem. On a normal
    server or your own machine that's durable. On a platform that redeploys
    from git on every push (e.g. Streamlit Community Cloud), a manual edit
    saved this way will persist while the app instance is running, but a
    fresh deploy pulls whatever is committed in the repo - so download the
    updated CSV from the "Manage Stages" panel and commit it back to git to
    make a change permanent.
    """
    missing = REQUIRED_COLS - set(stage_map.columns)
    if missing:
        raise ValueError(f"Cannot save stage_map, missing column(s): {missing}")
    stage_map = stage_map.sort_values(["stage_no", "stage_name", "step_name"]).reset_index(drop=True)
    stage_map.to_csv(path, index=False)


def existing_stages(stage_map: pd.DataFrame) -> pd.DataFrame:
    """One row per stage_no with its label, in stage order - for populating dropdowns."""
    return (
        stage_map[["stage_no", "stage_name"]]
        .drop_duplicates()
        .sort_values("stage_no")
        .reset_index(drop=True)
    )


def add_step_to_stage(
    stage_map: pd.DataFrame,
    step_name: str,
    stage_no: int,
    stage_label: str,
) -> pd.DataFrame:
    """
    Assign an unmapped step to a stage - either an existing one (pass its
    current stage_no and label) or a brand new one (pass a stage_no not yet
    in the map and its new label; every other step already on that stage_no
    keeps the same label since it's a 1:1 stage_no<->stage_name pairing).
    Returns the updated DataFrame; caller is responsible for save_stage_map.
    """
    step_name = step_name.strip()
    if step_name in stage_map["step_name"].values:
        raise ValueError(f"'{step_name}' is already mapped in stage_map.csv")

    new_row = pd.DataFrame([{
        "stage_no": int(stage_no),
        "stage_name": stage_label.strip(),
        "step_name": step_name,
    }])
    return pd.concat([stage_map, new_row], ignore_index=True)


def rename_stage(stage_map: pd.DataFrame, stage_no: int, new_label: str) -> pd.DataFrame:
    """Rename a stage - updates the label on every step currently in that stage."""
    out = stage_map.copy()
    out.loc[out["stage_no"] == stage_no, "stage_name"] = new_label.strip()
    return out


def next_available_stage_no(stage_map: pd.DataFrame) -> int:
    return int(stage_map["stage_no"].max()) + 1 if len(stage_map) else 1
