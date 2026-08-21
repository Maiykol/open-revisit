"""M3 metric-table orchestration and existing-table SLA queries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from open_revisit.config import AppConfig
from open_revisit.metrics import (
    catalog_filter_evaluation,
    gap_table,
    monthly_reliability,
    service_level_success,
    summary_metrics,
    survival_curve,
    wait_daily,
)
from open_revisit.store import refresh_duckdb_views, write_parquet

METRIC_TABLE_SORTS: dict[str, list[str]] = {
    "aoi_wait_daily": ["aoi_id", "config_hash", "t0"],
    "aoi_survival": ["aoi_id", "config_hash", "n_days"],
    "aoi_monthly": ["aoi_id", "config_hash", "month"],
    "aoi_gaps": ["aoi_id", "config_hash", "kind", "gap_start", "gap_end"],
    "aoi_summary": ["aoi_id", "config_hash"],
    "catalog_filter_eval": ["aoi_id", "config_hash", "threshold"],
}


@dataclass(frozen=True, slots=True)
class MetricRunSummary:
    """Row counts written by one metrics invocation."""

    config_hash: str
    table_rows: dict[str, int]


@dataclass(frozen=True, slots=True)
class SlaResult:
    """One SLA query result from the persisted daily-wait table."""

    aoi_id: str
    config_hash: str
    every_days: int
    month: int | None
    success_rate: float
    n_days: int


def _tag(frame: pd.DataFrame, *, aoi_id: str, config_hash: str) -> pd.DataFrame:
    tagged = frame.copy()
    tagged.insert(0, "config_hash", config_hash)
    tagged.insert(0, "aoi_id", aoi_id)
    return tagged


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        raise ValueError("at least one configured AOI is required")
    return pd.concat(frames, ignore_index=True)


def build_metric_tables(
    observations: pd.DataFrame,
    config: AppConfig,
) -> dict[str, pd.DataFrame]:
    """Build all six M3 tables in memory from one configuration's observations."""
    config_hash = config.config_hash()
    required = {
        "aoi_id",
        "datatake_id",
        "config_hash",
        "observed_at",
        "catalog_cloud_cover",
        "usable",
        "complete",
    }
    missing = required - set(observations.columns)
    if missing:
        raise ValueError(f"observations missing columns: {sorted(missing)}")
    current = observations.loc[
        (observations["config_hash"] == config_hash)
        & observations["aoi_id"].isin(config.aoi_ids)
    ].copy()
    if current.empty:
        raise ValueError(f"no observations found for config_hash {config_hash}")
    if current.duplicated(["aoi_id", "datatake_id", "config_hash"]).any():
        raise ValueError("observations contain duplicate primary keys")
    present_aois = set(str(value) for value in current["aoi_id"].unique())
    missing_aois = set(config.aoi_ids) - present_aois
    if missing_aois:
        raise ValueError(
            f"configured AOIs have no observations: {sorted(missing_aois)}"
        )

    catalog = catalog_filter_evaluation(current)
    catalog.insert(1, "config_hash", config_hash)
    wait_frames: list[pd.DataFrame] = []
    survival_frames: list[pd.DataFrame] = []
    monthly_frames: list[pd.DataFrame] = []
    gap_frames: list[pd.DataFrame] = []
    summary_rows: list[dict[str, object]] = []

    for aoi_id in config.aoi_ids:
        aoi_observations = current.loc[current["aoi_id"] == aoi_id].copy()
        complete = aoi_observations.loc[aoi_observations["complete"].astype(bool)]
        nominal = pd.Series(complete["observed_at"])
        effective = pd.Series(
            complete.loc[complete["usable"].astype(bool), "observed_at"]
        )
        waits = wait_daily(
            effective,
            start=pd.Timestamp(config.start),
            end=pd.Timestamp(config.end),
            horizon_days=config.horizon_days,
        )
        survival = survival_curve(waits, horizon_days=config.horizon_days)
        monthly = monthly_reliability(waits)
        gaps = pd.concat(
            [
                gap_table(nominal, kind="nominal"),
                gap_table(effective, kind="effective"),
            ],
            ignore_index=True,
        )
        aoi_catalog = catalog.loc[catalog["aoi_id"] == aoi_id]
        summary_rows.append(
            {
                "aoi_id": aoi_id,
                "config_hash": config_hash,
                **summary_metrics(
                    aoi_observations,
                    gaps,
                    survival,
                    monthly,
                    aoi_catalog,
                ),
            }
        )
        wait_frames.append(_tag(waits, aoi_id=aoi_id, config_hash=config_hash))
        survival_frames.append(_tag(survival, aoi_id=aoi_id, config_hash=config_hash))
        monthly_frames.append(_tag(monthly, aoi_id=aoi_id, config_hash=config_hash))
        gap_frames.append(_tag(gaps, aoi_id=aoi_id, config_hash=config_hash))

    return {
        "aoi_wait_daily": _concat(wait_frames),
        "aoi_survival": _concat(survival_frames),
        "aoi_monthly": _concat(monthly_frames),
        "aoi_gaps": _concat(gap_frames),
        "aoi_summary": pd.DataFrame(summary_rows),
        "catalog_filter_eval": catalog,
    }


def run_metrics(config: AppConfig) -> MetricRunSummary:
    """Recompute and persist all M3 tables for exactly the current config hash."""
    observations_path = config.data_dir / "observations.parquet"
    if not observations_path.exists():
        raise FileNotFoundError(
            f"observation table not found: {observations_path}; run process first"
        )
    observations = pd.read_parquet(observations_path)
    tables = build_metric_tables(observations, config)
    for table_name, frame in tables.items():
        write_parquet(
            frame,
            config.data_dir / f"{table_name}.parquet",
            sort_by=METRIC_TABLE_SORTS[table_name],
        )
    refresh_duckdb_views(config.data_dir, list(METRIC_TABLE_SORTS))
    return MetricRunSummary(
        config_hash=config.config_hash(),
        table_rows={name: len(frame) for name, frame in tables.items()},
    )


def query_sla(
    data_dir: Path,
    *,
    aoi_id: str,
    config_hash: str,
    every_days: int,
    month: int | None = None,
) -> SlaResult:
    """Read only persisted waits and return SLA success per selected start day."""
    wait_path = data_dir / "aoi_wait_daily.parquet"
    if not wait_path.exists():
        raise FileNotFoundError(
            f"metric table not found: {wait_path}; run metrics first"
        )
    waits = pd.read_parquet(
        wait_path,
        columns=["aoi_id", "config_hash", "t0", "wait_days", "censored"],
    )
    selected = waits.loc[
        (waits["aoi_id"] == aoi_id) & (waits["config_hash"] == config_hash)
    ].copy()
    if selected.empty:
        raise ValueError(
            f"no metric rows for aoi_id={aoi_id!r}, config_hash={config_hash}"
        )
    if month is not None:
        t0 = pd.to_datetime(selected["t0"], utc=True)
        n_days = int(t0.dt.month.eq(month).sum())
    else:
        n_days = len(selected)
    return SlaResult(
        aoi_id=aoi_id,
        config_hash=config_hash,
        every_days=every_days,
        month=month,
        success_rate=service_level_success(selected, every_days, month=month),
        n_days=n_days,
    )
