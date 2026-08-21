"""Discovery orchestration and persistence for STAC scene metadata."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from open_revisit.config import AppConfig
from open_revisit.stac import (
    SCENE_COLUMNS,
    SUPERSEDED_COLUMNS,
    deduplicate_scenes,
    fetch_items,
    item_to_scene_row,
    search_start,
)
from open_revisit.store import (
    read_parquet_or_empty,
    refresh_duckdb_views,
    upsert_frame,
    write_parquet,
)

SCENE_AOI_COLUMNS = ["aoi_id", "scene_id", "footprint_overlap_fraction"]
INGEST_STATE_COLUMNS = ["aoi_id", "collection", "watermark", "last_run_at"]


@dataclass(frozen=True, slots=True)
class AoiDiscoveryCounts:
    """Discovery counts and watermark for one AOI."""

    aoi_id: str
    fetched: int
    new: int
    superseded: int
    watermark: datetime | None


@dataclass(frozen=True, slots=True)
class DiscoverySummary:
    """Aggregate result of one discovery invocation."""

    per_aoi: tuple[AoiDiscoveryCounts, ...]
    n_scenes: int
    n_scene_aoi: int
    n_superseded: int


def _resolve_scene_id(scene_id: str, mapping_by_id: Mapping[str, str]) -> str:
    seen: set[str] = set()
    current = scene_id
    while current in mapping_by_id and current not in seen:
        seen.add(current)
        current = mapping_by_id[current]
    return current


def _normalise_superseded(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=SUPERSEDED_COLUMNS)
    latest = frame.drop_duplicates(subset=["scene_id"], keep="last").copy()
    direct = dict(
        zip(
            latest["scene_id"].astype(str),
            latest["superseded_by"].astype(str),
            strict=False,
        )
    )
    latest["superseded_by"] = latest["scene_id"].map(
        lambda value: _resolve_scene_id(str(value), direct)
    )
    return latest.sort_values("scene_id", kind="stable").reset_index(drop=True)


def _watermark_by_aoi(state: pd.DataFrame, collection: str) -> dict[str, datetime]:
    if state.empty:
        return {}
    relevant = state.loc[state["collection"] == collection]
    result: dict[str, datetime] = {}
    for row in relevant.itertuples(index=False):
        if pd.isna(row.watermark):
            continue
        watermark_value: Any = row.watermark
        result[str(row.aoi_id)] = pd.Timestamp(watermark_value).to_pydatetime()
    return result


def _overlap_fraction(aoi: BaseGeometry, item: Mapping[str, Any]) -> float:
    geometry_value = item.get("geometry")
    if not isinstance(geometry_value, Mapping):
        return 0.0
    footprint = shape(dict(geometry_value))
    if aoi.area == 0.0:
        raise ValueError("AOI geometry has zero area")
    fraction = aoi.intersection(footprint).area / aoi.area
    return float(min(1.0, max(0.0, fraction)))


def run_discovery(
    config: AppConfig,
    aois: gpd.GeoDataFrame,
    *,
    on_aoi_complete: Callable[[AoiDiscoveryCounts], None] | None = None,
) -> DiscoverySummary:
    """Discover configured AOIs, deduplicate scenes, and persist M1 tables."""
    data_dir = config.data_dir
    scenes_path = data_dir / "scenes.parquet"
    links_path = data_dir / "scene_aoi.parquet"
    superseded_path = data_dir / "scenes_superseded.parquet"
    state_path = data_dir / "ingest_state.parquet"
    existing_scenes = read_parquet_or_empty(scenes_path, SCENE_COLUMNS)
    existing_links = read_parquet_or_empty(links_path, SCENE_AOI_COLUMNS)
    existing_superseded = read_parquet_or_empty(superseded_path, SUPERSEDED_COLUMNS)
    existing_state = read_parquet_or_empty(state_path, INGEST_STATE_COLUMNS)
    known_ids = set(existing_scenes.get("scene_id", pd.Series(dtype=str)).astype(str))
    known_ids.update(
        existing_superseded.get("scene_id", pd.Series(dtype=str)).astype(str)
    )
    previous_watermarks = _watermark_by_aoi(existing_state, config.collection)

    aoi_lookup = {str(row.aoi_id): row.geometry for row in aois.itertuples(index=False)}
    missing_aois = set(config.aoi_ids) - aoi_lookup.keys()
    if missing_aois:
        raise ValueError(f"AOIs missing from data/aois.parquet: {sorted(missing_aois)}")

    configured_start = datetime.combine(config.start, time.min, tzinfo=UTC)
    configured_end = datetime.combine(config.end, time.max, tzinfo=UTC)
    run_at = datetime.now(UTC)
    candidate_rows: dict[str, dict[str, Any]] = {}
    raw_links: list[dict[str, Any]] = []
    raw_ids_by_aoi: dict[str, set[str]] = {}
    fetched_count_by_aoi: dict[str, int] = {}
    watermark_rows: list[dict[str, Any]] = []

    for aoi_id in config.aoi_ids:
        geometry = aoi_lookup[aoi_id]
        previous = previous_watermarks.get(aoi_id)
        start = search_start(
            configured_start, previous, overlap_days=config.late_overlap_days
        )
        items = fetch_items(config, geometry, start, configured_end)
        raw_ids = {str(item["id"]) for item in items}
        raw_ids_by_aoi[aoi_id] = raw_ids
        fetched_count_by_aoi[aoi_id] = len(items)
        item_datetimes: list[datetime] = []
        for item in items:
            row = item_to_scene_row(item, ingested_at=run_at)
            scene_id = str(row["scene_id"])
            if scene_id in known_ids and not existing_scenes.empty:
                match = existing_scenes.loc[existing_scenes["scene_id"] == scene_id]
                if not match.empty:
                    row["ingested_at"] = match.iloc[-1]["ingested_at"]
            candidate_rows[scene_id] = row
            if row["datetime"] is not None:
                item_datetimes.append(pd.Timestamp(row["datetime"]).to_pydatetime())
            raw_links.append(
                {
                    "aoi_id": aoi_id,
                    "scene_id": scene_id,
                    "footprint_overlap_fraction": _overlap_fraction(geometry, item),
                }
            )
        newest = max(item_datetimes, default=previous)
        if previous is not None and newest is not None:
            newest = max(previous, newest)
        watermark_rows.append(
            {
                "aoi_id": aoi_id,
                "collection": config.collection,
                "watermark": newest,
                "last_run_at": run_at,
            }
        )

    candidates = pd.DataFrame(candidate_rows.values(), columns=SCENE_COLUMNS)
    combined_scenes = upsert_frame(existing_scenes, candidates, keys=["scene_id"])
    active_scenes, newly_superseded = deduplicate_scenes(combined_scenes)
    all_superseded = _normalise_superseded(
        upsert_frame(existing_superseded, newly_superseded, keys=["scene_id"])
    )
    superseded_map = dict(
        zip(
            all_superseded["scene_id"].astype(str),
            all_superseded["superseded_by"].astype(str),
            strict=False,
        )
    )

    incoming_links = pd.DataFrame(raw_links, columns=SCENE_AOI_COLUMNS)
    all_links = upsert_frame(
        existing_links, incoming_links, keys=["aoi_id", "scene_id"]
    )
    if not all_links.empty:
        all_links["scene_id"] = all_links["scene_id"].map(
            lambda value: _resolve_scene_id(str(value), superseded_map)
        )
        all_links = all_links.sort_values(
            "footprint_overlap_fraction", kind="stable"
        ).drop_duplicates(
            subset=["aoi_id", "scene_id"],
            keep="last",
        )
        active_ids = set(active_scenes["scene_id"].astype(str))
        all_links = all_links.loc[all_links["scene_id"].isin(active_ids)].copy()
    else:
        active_ids = set(active_scenes["scene_id"].astype(str))

    incoming_state = pd.DataFrame(watermark_rows, columns=INGEST_STATE_COLUMNS)
    all_state = upsert_frame(
        existing_state, incoming_state, keys=["aoi_id", "collection"]
    )

    write_parquet(active_scenes, scenes_path, sort_by=["scene_id"])
    write_parquet(all_links, links_path, sort_by=["aoi_id", "scene_id"])
    write_parquet(all_superseded, superseded_path, sort_by=["scene_id"])
    write_parquet(all_state, state_path, sort_by=["aoi_id", "collection"])
    refresh_duckdb_views(
        data_dir,
        ["scenes", "scene_aoi", "scenes_superseded", "ingest_state"],
    )

    previous_loser_ids = set(existing_superseded["scene_id"].astype(str))
    loser_ids = set(newly_superseded["scene_id"].astype(str)) - previous_loser_ids
    per_aoi: list[AoiDiscoveryCounts] = []
    state_watermarks = _watermark_by_aoi(all_state, config.collection)
    for aoi_id in config.aoi_ids:
        raw_ids = raw_ids_by_aoi[aoi_id]
        counts = AoiDiscoveryCounts(
            aoi_id=aoi_id,
            fetched=fetched_count_by_aoi[aoi_id],
            new=sum(
                scene_id not in known_ids and scene_id in active_ids
                for scene_id in raw_ids
            ),
            superseded=len(raw_ids & loser_ids),
            watermark=state_watermarks.get(aoi_id),
        )
        per_aoi.append(counts)
        if on_aoi_complete is not None:
            on_aoi_complete(counts)

    return DiscoverySummary(
        per_aoi=tuple(per_aoi),
        n_scenes=len(active_scenes),
        n_scene_aoi=len(all_links),
        n_superseded=len(all_superseded),
    )
