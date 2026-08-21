"""Read-only observation loading and metric preparation for the M6 app."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from open_revisit.metrics import (
    catalog_filter_evaluation,
    gap_table,
    monthly_reliability,
    service_level_success,
    summary_metrics,
    survival_curve,
    wait_daily,
)

OBSERVATION_COLUMNS = [
    "aoi_id",
    "datatake_id",
    "config_hash",
    "observed_at",
    "catalog_cloud_cover",
    "covered_fraction",
    "clear_fraction",
    "usable",
    "complete",
]
AOI_COLUMNS = ["aoi_id", "name", "country", "lat", "lon"]


class AppDataError(ValueError):
    """A helpful, user-facing error caused by unavailable or invalid app data."""


@dataclass(frozen=True, slots=True)
class AppMetricTables:
    """Metric frames derived for one complete set of interactive selections."""

    observations: pd.DataFrame
    waits: pd.DataFrame
    survival: pd.DataFrame
    monthly: pd.DataFrame
    summary: pd.DataFrame


def file_signature(path: Path, *, description: str, hint: str) -> tuple[int, int]:
    """Return a (size, mtime_ns) cache signature or raise a helpful setup error."""
    try:
        stat = path.stat()
    except FileNotFoundError as exc:
        raise AppDataError(f"{description} is not available at {path}. {hint}") from exc
    if not path.is_file():
        raise AppDataError(f"{description} path is not a file: {path}")
    return stat.st_size, stat.st_mtime_ns


def source_signature(path: Path) -> tuple[int, int]:
    """Return a cache signature from the observation Parquet's size and mtime."""
    return file_signature(
        path,
        description="Observation data",
        hint=(
            "Place the pipeline's observations.parquet in the configured "
            "data directory."
        ),
    )


def aoi_signature(path: Path) -> tuple[int, int]:
    """Return a cache signature from the AOI Parquet's size and mtime."""
    return file_signature(
        path,
        description="AOI metadata",
        hint=(
            "Place the pipeline's aois.parquet in the configured data directory "
            "(open-revisit aois build)."
        ),
    )


def basemap_signature(path: Path) -> tuple[int, int]:
    """Return a cache signature from the offline GeoJSON's size and mtime."""
    return file_signature(
        path,
        description="Offline basemap",
        hint=(
            "Use the committed assets/natural_earth_europe.geojson or point "
            "OPEN_REVISIT_BASEMAP at a local GeoJSON FeatureCollection."
        ),
    )


def load_aois(path: Path) -> pd.DataFrame:
    """Load AOI centroid metadata (WGS84 degrees) without decoding geometry."""
    aoi_signature(path)
    try:
        aois = pd.read_parquet(path, columns=AOI_COLUMNS)
    except Exception as exc:
        raise AppDataError(f"Could not read AOI metadata at {path}: {exc}") from exc
    aois = aois.copy()
    aois["aoi_id"] = aois["aoi_id"].astype(str)
    if aois["aoi_id"].duplicated().any():
        raise AppDataError("AOI metadata contains duplicate aoi_id values.")
    lat = pd.to_numeric(aois["lat"], errors="coerce")
    lon = pd.to_numeric(aois["lon"], errors="coerce")
    if not (lat.between(-90.0, 90.0).all() and lon.between(-180.0, 180.0).all()):
        raise AppDataError("AOI metadata contains invalid lat/lon values.")
    aois["lat"] = lat.astype(float)
    aois["lon"] = lon.astype(float)
    return aois.sort_values("aoi_id", kind="stable").reset_index(drop=True)


def load_basemap(path: Path) -> dict[str, Any]:
    """Load a local GeoJSON FeatureCollection. No URL or network is ever used."""
    basemap_signature(path)
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AppDataError(f"Could not read offline basemap at {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise AppDataError(
            f"Offline basemap at {path} is not a GeoJSON FeatureCollection."
        )
    if raw.get("type") != "FeatureCollection" or not isinstance(
        raw.get("features"), list
    ):
        raise AppDataError(
            f"Offline basemap at {path} is not a GeoJSON FeatureCollection."
        )
    return raw


def load_observations(path: Path, *, config_hash: str) -> pd.DataFrame:
    """Load one config's columns from Parquet without mutating the source."""
    source_signature(path)
    try:
        observations = pd.read_parquet(path, columns=OBSERVATION_COLUMNS)
    except Exception as exc:
        raise AppDataError(f"Could not read observation data at {path}: {exc}") from exc

    missing = set(OBSERVATION_COLUMNS) - set(observations.columns)
    if missing:
        raise AppDataError(
            f"Observation data is missing required columns: {sorted(missing)}"
        )
    selected = observations.loc[observations["config_hash"] == config_hash].copy()
    if selected.empty:
        raise AppDataError(
            "No observations match the configured analysis context "
            f"(config_hash={config_hash})."
        )
    if selected.duplicated(["aoi_id", "datatake_id", "config_hash"]).any():
        raise AppDataError("Observation data contains duplicate primary keys.")
    selected["observed_at"] = pd.to_datetime(selected["observed_at"], utc=True)
    return selected.sort_values(
        ["aoi_id", "observed_at", "datatake_id"], kind="stable"
    ).reset_index(drop=True)


def validate_selection(
    observations: pd.DataFrame,
    *,
    aoi_ids: tuple[str, ...],
    start: date,
    end: date,
    min_clear: float,
    min_coverage: float,
    horizon_days: int,
    every_days: int,
) -> None:
    if not aoi_ids:
        raise AppDataError("Select at least one AOI.")
    available_aois = set(str(value) for value in observations["aoi_id"].unique())
    unknown = set(aoi_ids) - available_aois
    if unknown:
        raise AppDataError(f"Selected AOIs have no input data: {sorted(unknown)}")
    if end < start:
        raise AppDataError("The analysis end must be on or after the start.")
    if (end - start).days < horizon_days:
        raise AppDataError(
            f"Select at least {horizon_days} days so the wait horizon is observable."
        )
    if not 0.0 <= min_clear <= 1.0:
        raise AppDataError("min_clear must be between 0 and 1.")
    if not 0.0 <= min_coverage <= 1.0:
        raise AppDataError("min_coverage must be between 0 and 1.")
    if not 1 <= every_days <= horizon_days:
        raise AppDataError(
            f"The service interval W must be between 1 and {horizon_days} days."
        )


def select_observations(
    observations: pd.DataFrame,
    *,
    aoi_ids: tuple[str, ...],
    start: date,
    end: date,
    min_clear: float,
    min_coverage: float,
) -> pd.DataFrame:
    """Filter a period and derive usability; denominator: complete observations."""
    start_utc = pd.Timestamp(start, tz="UTC")
    end_exclusive = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    observed_at = pd.to_datetime(observations["observed_at"], utc=True)
    selected = observations.loc[
        observations["aoi_id"].isin(aoi_ids)
        & observed_at.ge(start_utc)
        & observed_at.lt(end_exclusive)
    ].copy()
    selected["observed_at"] = observed_at.loc[selected.index]
    selected["usable"] = (
        selected["complete"].astype(bool)
        & pd.to_numeric(selected["covered_fraction"], errors="raise").ge(min_coverage)
        & pd.to_numeric(selected["clear_fraction"], errors="raise").ge(min_clear)
    )
    return selected.sort_values(
        ["aoi_id", "observed_at", "datatake_id"], kind="stable"
    ).reset_index(drop=True)


def _tag(frame: pd.DataFrame, *, aoi_id: str) -> pd.DataFrame:
    tagged = frame.copy()
    tagged.insert(0, "aoi_id", aoi_id)
    return tagged


def build_app_metrics(
    observations: pd.DataFrame,
    *,
    aoi_ids: tuple[str, ...],
    start: date,
    end: date,
    min_clear: float,
    min_coverage: float,
    horizon_days: int,
    every_days: int,
) -> AppMetricTables:
    """Build app metrics by orchestrating the metric-contract functions in memory."""
    validate_selection(
        observations,
        aoi_ids=aoi_ids,
        start=start,
        end=end,
        min_clear=min_clear,
        min_coverage=min_coverage,
        horizon_days=horizon_days,
        every_days=every_days,
    )
    selected = select_observations(
        observations,
        aoi_ids=aoi_ids,
        start=start,
        end=end,
        min_clear=min_clear,
        min_coverage=min_coverage,
    )

    waits_frames: list[pd.DataFrame] = []
    survival_frames: list[pd.DataFrame] = []
    monthly_frames: list[pd.DataFrame] = []
    summary_rows: list[dict[str, object]] = []
    for aoi_id in aoi_ids:
        aoi_observations = selected.loc[selected["aoi_id"] == aoi_id].copy()
        if aoi_observations.empty:
            raise AppDataError(
                f"AOI {aoi_id!r} has no observations in the selected period."
            )
        complete = aoi_observations.loc[aoi_observations["complete"].astype(bool)]
        nominal = pd.Series(complete["observed_at"])
        effective = pd.Series(
            complete.loc[complete["usable"].astype(bool), "observed_at"]
        )
        waits = wait_daily(
            effective,
            start=pd.Timestamp(start),
            end=pd.Timestamp(end),
            horizon_days=horizon_days,
        )
        survival = survival_curve(waits, horizon_days=horizon_days)
        monthly = monthly_reliability(waits)
        nonempty_gaps = [
            frame
            for frame in (
                gap_table(nominal, kind="nominal"),
                gap_table(effective, kind="effective"),
            )
            if not frame.empty
        ]
        if len(nonempty_gaps) > 1:
            gaps = pd.concat(nonempty_gaps, ignore_index=True)
        elif nonempty_gaps:
            gaps = nonempty_gaps[0].copy()
        else:
            gaps = pd.DataFrame(columns=["kind", "gap_start", "gap_end", "gap_days"])
        catalog = catalog_filter_evaluation(aoi_observations)
        aoi_catalog = catalog.loc[catalog["aoi_id"] == aoi_id]
        summary_rows.append(
            {
                "aoi_id": aoi_id,
                **summary_metrics(
                    aoi_observations,
                    gaps,
                    survival,
                    monthly,
                    aoi_catalog,
                ),
                "sla_success": service_level_success(waits, every_days),
            }
        )
        waits_frames.append(_tag(waits, aoi_id=aoi_id))
        survival_frames.append(_tag(survival, aoi_id=aoi_id))
        monthly_frames.append(_tag(monthly, aoi_id=aoi_id))

    return AppMetricTables(
        observations=selected,
        waits=pd.concat(waits_frames, ignore_index=True),
        survival=pd.concat(survival_frames, ignore_index=True),
        monthly=pd.concat(monthly_frames, ignore_index=True),
        summary=pd.DataFrame(summary_rows),
    )
