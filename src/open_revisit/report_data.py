"""Pure deterministic data preparation for M4 report artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import geopandas as gpd
import numpy as np
import pandas as pd

from open_revisit.config import AppConfig
from open_revisit.metric_pipeline import METRIC_TABLE_SORTS
from open_revisit.metrics import service_level_success


@dataclass(frozen=True, slots=True)
class ReportTables:
    """Config-isolated tables required to render the M4 report."""

    aois: gpd.GeoDataFrame
    observations: pd.DataFrame
    scenes: pd.DataFrame
    scene_aoi: pd.DataFrame
    wait_daily: pd.DataFrame
    survival: pd.DataFrame
    monthly: pd.DataFrame
    summary: pd.DataFrame
    catalog_filter: pd.DataFrame


def _read_required(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"required report table not found: {path}")
    return pd.read_parquet(path)


def _as_float(value: object) -> float:
    if isinstance(value, int | float | np.integer | np.floating):
        return float(value)
    raise TypeError(f"expected numeric value, got {type(value).__name__}")


def _as_int(value: object) -> int:
    if isinstance(value, int | np.integer):
        return int(value)
    raise TypeError(f"expected integer value, got {type(value).__name__}")


def _current(frame: pd.DataFrame, config: AppConfig, *, table: str) -> pd.DataFrame:
    if "config_hash" not in frame:
        raise ValueError(f"{table} has no config_hash column")
    selected = frame.loc[
        (frame["config_hash"] == config.config_hash())
        & frame["aoi_id"].isin((*config.aoi_ids, "ALL"))
    ].copy()
    if selected.empty:
        raise ValueError(f"{table} has no rows for config_hash {config.config_hash()}")
    return selected


def load_report_tables(config: AppConfig) -> ReportTables:
    """Load and isolate all report inputs from existing Parquet tables."""
    data_dir = config.data_dir
    metric = {
        name: _current(_read_required(data_dir / f"{name}.parquet"), config, table=name)
        for name in METRIC_TABLE_SORTS
    }
    observations = _current(
        _read_required(data_dir / "observations.parquet"),
        config,
        table="observations",
    )
    scenes = _read_required(data_dir / "scenes.parquet")
    scene_aoi = _read_required(data_dir / "scene_aoi.parquet")
    aois = gpd.read_parquet(data_dir / "aois.parquet")
    aois = aois.loc[aois["aoi_id"].isin(config.aoi_ids)].copy()
    if set(aois["aoi_id"].astype(str)) != set(config.aoi_ids):
        raise ValueError("report AOI table does not contain every configured AOI")
    return ReportTables(
        aois=aois.sort_values("aoi_id", kind="stable").reset_index(drop=True),
        observations=observations.sort_values(
            ["aoi_id", "observed_at", "datatake_id"], kind="stable"
        ).reset_index(drop=True),
        scenes=scenes.sort_values("scene_id", kind="stable").reset_index(drop=True),
        scene_aoi=scene_aoi.sort_values(
            ["aoi_id", "scene_id"], kind="stable"
        ).reset_index(drop=True),
        wait_daily=metric["aoi_wait_daily"],
        survival=metric["aoi_survival"],
        monthly=metric["aoi_monthly"],
        summary=metric["aoi_summary"],
        catalog_filter=metric["catalog_filter_eval"],
    )


def select_survival_aois(summary: pd.DataFrame, *, count: int = 6) -> tuple[str, ...]:
    """Select best, worst, and evenly spaced quantile-nearest AOIs."""
    if count < 2:
        raise ValueError("survival selection count must be at least two")
    ordered = summary.sort_values(
        ["effective_median_gap_days", "aoi_id"], kind="stable"
    ).reset_index(drop=True)
    if ordered.empty:
        raise ValueError("cannot select survival AOIs from an empty summary")
    if len(ordered) <= count:
        return tuple(ordered["aoi_id"].astype(str))
    values = ordered["effective_median_gap_days"].to_numpy(dtype=float)
    identifiers = ordered["aoi_id"].astype(str).tolist()
    chosen: list[int] = [0]
    for quantile in np.linspace(0.0, 1.0, count)[1:-1]:
        target = float(np.quantile(values, quantile))
        candidates = [index for index in range(len(values)) if index not in chosen]
        chosen.append(
            min(candidates, key=lambda index: (abs(values[index] - target), index))
        )
    chosen.append(len(values) - 1)
    return tuple(identifiers[index] for index in chosen)


def service_level_frame(wait_daily: pd.DataFrame) -> pd.DataFrame:
    """Prepare per-AOI and median SLA rates for W=1..30 via metric functions."""
    rows: list[dict[str, object]] = []
    aoi_ids = sorted(str(value) for value in wait_daily["aoi_id"].unique())
    for aoi_id in aoi_ids:
        waits = wait_daily.loc[wait_daily["aoi_id"] == aoi_id]
        for window_days in range(1, 31):
            rows.append(
                {
                    "aoi_id": aoi_id,
                    "window_days": window_days,
                    "success_rate": service_level_success(waits, window_days),
                }
            )
    frame = pd.DataFrame(rows)
    median = (
        frame.groupby("window_days", as_index=False)["success_rate"]
        .median()
        .assign(aoi_id="MEDIAN")
        .loc[:, ["aoi_id", "window_days", "success_rate"]]
    )
    result = cast(pd.DataFrame, pd.concat([frame, median], ignore_index=True))
    return result.sort_values(["aoi_id", "window_days"], kind="stable")


def select_rgb_examples(observations: pd.DataFrame) -> pd.DataFrame:
    """Select deterministic extreme metadata/pixel disagreement examples."""
    complete = observations.loc[observations["complete"].astype(bool)].copy()
    cloud = pd.to_numeric(complete["catalog_cloud_cover"], errors="coerce")
    clear_fraction = pd.to_numeric(complete["clear_fraction"], errors="coerce")
    cloud_fraction = pd.to_numeric(complete["cloud_fraction"], errors="coerce")
    false_clear = complete.loc[cloud.le(20) & ~complete["usable"].astype(bool)].copy()
    false_cloudy = complete.loc[cloud.gt(20) & complete["usable"].astype(bool)].copy()
    if false_clear.empty or false_cloudy.empty:
        raise ValueError("both catalog disagreement classes need at least one example")
    false_clear = false_clear.assign(
        _clear=clear_fraction.loc[false_clear.index],
        _cloud=cloud_fraction.loc[false_clear.index],
    )
    false_cloudy = false_cloudy.assign(_clear=clear_fraction.loc[false_cloudy.index])
    selected_clear = false_clear.sort_values(
        ["_cloud", "_clear", "catalog_cloud_cover", "aoi_id", "datatake_id"],
        ascending=[False, True, True, True, True],
        kind="stable",
    ).iloc[0]
    selected_cloudy = false_cloudy.sort_values(
        ["catalog_cloud_cover", "_clear", "aoi_id", "datatake_id"],
        ascending=[False, False, True, True],
        kind="stable",
    ).iloc[0]
    result = pd.DataFrame([selected_clear, selected_cloudy]).drop(
        columns=["_clear", "_cloud"], errors="ignore"
    )
    result.insert(0, "case", ["catalog-clear_aoi-cloudy", "catalog-cloudy_aoi-clear"])
    return result.reset_index(drop=True)


def render_summary_table(summary: pd.DataFrame, names: dict[str, str]) -> str:
    """Render the deterministic AOI summary Markdown table at display precision."""
    columns = [
        "AOI",
        "Observations",
        "Usable",
        "Usable rate",
        "Nominal median (d)",
        "Effective median (d)",
        "P(within 7d)",
        "Longest outage (d)",
    ]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| "
        + " | ".join(["---", "---:", "---:", "---:", "---:", "---:", "---:", "---:"])
        + " |",
    ]
    ordered = summary.sort_values("aoi_id", kind="stable")
    for row in ordered.itertuples(index=False):
        lines.append(
            "| "
            + " | ".join(
                [
                    names.get(str(row.aoi_id), str(row.aoi_id)),
                    f"{_as_int(row.n_observations):,}",
                    f"{_as_int(row.n_usable):,}",
                    f"{_as_float(row.usable_rate):.1%}",
                    f"{_as_float(row.nominal_median_gap_days):.2f}",
                    f"{_as_float(row.effective_median_gap_days):.2f}",
                    f"{_as_float(row.p_within_7d):.1%}",
                    f"{_as_float(row.longest_outage_days):.2f}",
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"
