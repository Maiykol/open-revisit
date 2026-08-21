"""M2 raster-processing orchestration and Parquet persistence."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import geopandas as gpd
import pandas as pd

from open_revisit._processing_group import (
    GroupResult,
    RasterReader,
    process_group,
)
from open_revisit.config import AppConfig
from open_revisit.grid import build_analysis_grid
from open_revisit.raster import read_scl_window
from open_revisit.store import (
    read_parquet_or_empty,
    refresh_duckdb_views,
    upsert_frame,
    write_parquet,
)

SCENE_STATS_COLUMNS = [
    "aoi_id",
    "scene_id",
    "config_hash",
    "n_aoi_pixels",
    "n_covered",
    *(f"count_class_{value}" for value in range(12)),
    "read_ok",
    "error",
    "processed_at",
]
OBSERVATION_COLUMNS = [
    "aoi_id",
    "datatake_id",
    "config_hash",
    "observed_at",
    "platform",
    "relative_orbit",
    "n_scenes",
    "primary_scene_id",
    "catalog_cloud_cover",
    "catalog_cloud_cover_wmean",
    "n_aoi_pixels",
    "covered_fraction",
    "clear_fraction",
    "cloud_fraction",
    "shadow_fraction",
    "snow_fraction",
    "unclassified_fraction",
    "defective_fraction",
    "usable",
    "complete",
]


@dataclass(frozen=True, slots=True)
class AoiProcessCounts:
    """Invocation counts for one AOI."""

    aoi_id: str
    scenes: int
    observations: int
    usable: int
    failed_scenes: int
    skipped_scenes: int
    skipped_observations: int


@dataclass(frozen=True, slots=True)
class ProcessingSummary:
    """Aggregate counts from one process invocation."""

    per_aoi: tuple[AoiProcessCounts, ...]
    processed_scenes: int
    processed_observations: int
    skipped_scenes: int
    skipped_observations: int
    failed_scenes: int
    n_scene_stats: int
    n_observations: int
    n_usable: int


def _nullable_int(value: Any) -> int | None:
    return None if pd.isna(value) else int(value)


def _existing_keys(frame: pd.DataFrame, columns: list[str]) -> set[tuple[str, ...]]:
    if frame.empty:
        return set()
    return {
        tuple(str(value) for value in row)
        for row in frame.loc[:, columns].itertuples(index=False, name=None)
    }


def _preserve_processed_at(
    incoming: pd.DataFrame, existing: pd.DataFrame
) -> pd.DataFrame:
    if incoming.empty or existing.empty:
        return incoming
    timestamps = {
        (str(row.aoi_id), str(row.scene_id), str(row.config_hash)): row.processed_at
        for row in existing.itertuples(index=False)
    }
    result = incoming.copy()
    result["processed_at"] = [
        timestamps.get(
            (str(row.aoi_id), str(row.scene_id), str(row.config_hash)),
            row.processed_at,
        )
        for row in result.itertuples(index=False)
    ]
    return result


def run_processing(
    config: AppConfig,
    aois: gpd.GeoDataFrame,
    *,
    workers: int = 8,
    force: bool = False,
    keep_rasters: bool = False,
    reader: RasterReader | None = None,
    on_aoi_complete: Callable[[AoiProcessCounts], None] | None = None,
) -> ProcessingSummary:
    """Process scene groups by datatake and persist the two M2 tables."""
    if workers < 1:
        raise ValueError("workers must be at least one")
    raster_reader = read_scl_window if reader is None else reader
    data_dir = config.data_dir
    scenes_path = data_dir / "scenes.parquet"
    links_path = data_dir / "scene_aoi.parquet"
    if not scenes_path.exists() or not links_path.exists():
        raise FileNotFoundError("discovery tables are missing; run discover first")
    scenes = pd.read_parquet(scenes_path)
    links = pd.read_parquet(links_path)
    configured_links = links.loc[links["aoi_id"].isin(config.aoi_ids)].copy()
    joined = configured_links.merge(
        scenes,
        on="scene_id",
        how="left",
        validate="many_to_one",
        indicator=True,
    )
    missing_scene_ids = joined.loc[joined["_merge"] != "both", "scene_id"].tolist()
    if missing_scene_ids:
        raise ValueError(
            f"scene links have missing scene rows: {missing_scene_ids[:5]}"
        )
    joined = joined.drop(columns=["_merge"])
    if joined["datatake_id"].isna().any():
        raise ValueError("all processable scenes must have s2:datatake_id")

    stats_path = data_dir / "scene_aoi_stats.parquet"
    observations_path = data_dir / "observations.parquet"
    existing_stats = read_parquet_or_empty(stats_path, SCENE_STATS_COLUMNS)
    existing_observations = read_parquet_or_empty(
        observations_path, OBSERVATION_COLUMNS
    )
    config_hash = config.config_hash()
    stats_keys = _existing_keys(existing_stats, ["aoi_id", "scene_id", "config_hash"])
    observation_keys = _existing_keys(
        existing_observations, ["aoi_id", "datatake_id", "config_hash"]
    )
    existing_n_scenes = {
        (
            str(row.aoi_id),
            str(row.datatake_id),
            str(row.config_hash),
        ): _nullable_int(row.n_scenes)
        for row in existing_observations.itertuples(index=False)
    }

    aoi_lookup = {
        str(row.aoi_id): (row.geometry, int(row.utm_epsg))
        for row in aois.itertuples(index=False)
    }
    missing_aois = set(config.aoi_ids) - aoi_lookup.keys()
    if missing_aois:
        raise ValueError(f"AOIs missing from AOI table: {sorted(missing_aois)}")
    grids = {
        aoi_id: build_analysis_grid(
            aoi_lookup[aoi_id][0], utm_epsg=aoi_lookup[aoi_id][1]
        )
        for aoi_id in config.aoi_ids
    }

    pending: list[tuple[str, str, pd.DataFrame]] = []
    skipped_by_aoi: dict[str, tuple[int, int]] = {
        aoi_id: (0, 0) for aoi_id in config.aoi_ids
    }
    group_columns = ["aoi_id", "datatake_id"]
    for (aoi_value, datatake_value), group in joined.groupby(
        group_columns, sort=True, dropna=False
    ):
        aoi_id = str(aoi_value)
        datatake_id = str(datatake_value)
        observation_key = (aoi_id, datatake_id, config_hash)
        member_keys = {
            (aoi_id, str(scene_id), config_hash) for scene_id in group["scene_id"]
        }
        current_n_scenes = len(group)
        complete = (
            observation_key in observation_keys
            and member_keys <= stats_keys
            and existing_n_scenes.get(observation_key) == current_n_scenes
        )
        if not force and complete:
            skipped_scenes, skipped_observations = skipped_by_aoi[aoi_id]
            skipped_by_aoi[aoi_id] = (
                skipped_scenes + current_n_scenes,
                skipped_observations + 1,
            )
            continue
        pending.append((aoi_id, datatake_id, group.copy()))

    processed_at = datetime.now(UTC)
    results: list[GroupResult] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                process_group,
                aoi_id=aoi_id,
                datatake_id=datatake_id,
                scenes=group,
                grid=grids[aoi_id],
                config=config,
                reader=raster_reader,
                processed_at=processed_at,
                composite_path=(
                    data_dir / "rasters" / aoi_id / f"{datatake_id}_{config_hash}.tif"
                    if keep_rasters
                    else None
                ),
            )
            for aoi_id, datatake_id, group in pending
        ]
        for future in as_completed(futures):
            results.append(future.result())

    if results:
        incoming_stats = pd.DataFrame(
            [row for result in results for row in result.stats],
            columns=SCENE_STATS_COLUMNS,
        )
        incoming_stats = _preserve_processed_at(incoming_stats, existing_stats)
        incoming_observations = pd.DataFrame(
            [result.observation for result in results],
            columns=OBSERVATION_COLUMNS,
        )
        all_stats = upsert_frame(
            existing_stats,
            incoming_stats,
            keys=["aoi_id", "scene_id", "config_hash"],
        )
        all_observations = upsert_frame(
            existing_observations,
            incoming_observations,
            keys=["aoi_id", "datatake_id", "config_hash"],
        )
        write_parquet(
            all_stats,
            stats_path,
            sort_by=["aoi_id", "scene_id", "config_hash"],
        )
        write_parquet(
            all_observations,
            observations_path,
            sort_by=["aoi_id", "datatake_id", "config_hash"],
        )
        refresh_duckdb_views(data_dir, ["scene_aoi_stats", "observations"])
    else:
        all_stats = existing_stats
        all_observations = existing_observations

    result_by_aoi: dict[str, list[GroupResult]] = {
        aoi_id: [] for aoi_id in config.aoi_ids
    }
    for result in results:
        result_by_aoi[result.aoi_id].append(result)
    per_aoi: list[AoiProcessCounts] = []
    for aoi_id in config.aoi_ids:
        aoi_results = result_by_aoi[aoi_id]
        skipped_scenes, skipped_observations = skipped_by_aoi[aoi_id]
        counts = AoiProcessCounts(
            aoi_id=aoi_id,
            scenes=sum(len(result.stats) for result in aoi_results),
            observations=len(aoi_results),
            usable=sum(bool(result.observation["usable"]) for result in aoi_results),
            failed_scenes=sum(
                not bool(row["read_ok"])
                for result in aoi_results
                for row in result.stats
            ),
            skipped_scenes=skipped_scenes,
            skipped_observations=skipped_observations,
        )
        per_aoi.append(counts)
        if on_aoi_complete is not None:
            on_aoi_complete(counts)

    current_stats = all_stats.loc[all_stats["config_hash"] == config_hash]
    current_observations = all_observations.loc[
        all_observations["config_hash"] == config_hash
    ]
    return ProcessingSummary(
        per_aoi=tuple(per_aoi),
        processed_scenes=sum(counts.scenes for counts in per_aoi),
        processed_observations=sum(counts.observations for counts in per_aoi),
        skipped_scenes=sum(counts.skipped_scenes for counts in per_aoi),
        skipped_observations=sum(counts.skipped_observations for counts in per_aoi),
        failed_scenes=sum(counts.failed_scenes for counts in per_aoi),
        n_scene_stats=len(current_stats),
        n_observations=len(current_observations),
        n_usable=int(current_observations["usable"].sum())
        if not current_observations.empty
        else 0,
    )
