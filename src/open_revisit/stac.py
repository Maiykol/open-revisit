"""STAC search, item mapping, deduplication, and discovery state."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
from pystac_client import Client
from pystac_client.stac_api_io import StacApiIO
from shapely import to_wkb
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from open_revisit.config import AppConfig

ORBIT_PATTERN = re.compile(r"_R(\d{3})_")

SCENE_COLUMNS = [
    "scene_id",
    "collection",
    "datetime",
    "platform",
    "datatake_id",
    "relative_orbit",
    "mgrs_tile",
    "epsg",
    "processing_baseline",
    "sequence",
    "generation_time",
    "eo_cloud_cover",
    "s2_nodata_pct",
    "s2_cloud_shadow_pct",
    "s2_medium_cloud_pct",
    "s2_high_cloud_pct",
    "s2_cirrus_pct",
    "s2_snow_pct",
    "s2_unclassified_pct",
    "scl_href",
    "visual_href",
    "geometry",
    "ingested_at",
]
SUPERSEDED_COLUMNS = ["scene_id", "superseded_by"]


def _nullable_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _nullable_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _timestamp(value: Any) -> pd.Timestamp | None:
    return None if value is None else pd.Timestamp(value).tz_convert("UTC")


def _asset_href(assets: Mapping[str, Any], name: str) -> str | None:
    asset = assets.get(name)
    if not isinstance(asset, Mapping):
        return None
    href = asset.get("href")
    return None if href is None else str(href)


def item_to_scene_row(
    item: Mapping[str, Any], *, ingested_at: datetime
) -> dict[str, Any]:
    """Map one STAC item to the §5.2 scenes schema."""
    properties_value = item.get("properties", {})
    assets_value = item.get("assets", {})
    geometry_value = item.get("geometry")
    if not isinstance(properties_value, Mapping):
        raise ValueError("STAC item properties must be a mapping")
    if not isinstance(assets_value, Mapping):
        raise ValueError("STAC item assets must be a mapping")
    if not isinstance(geometry_value, Mapping):
        raise ValueError("STAC item geometry must be present")

    product_uri_value = properties_value.get("s2:product_uri")
    product_uri = None if product_uri_value is None else str(product_uri_value)
    orbit_match = None if product_uri is None else ORBIT_PATTERN.search(product_uri)
    grid_code_value = properties_value.get("grid:code")
    grid_code = None if grid_code_value is None else str(grid_code_value)
    epsg_value = properties_value.get("proj:epsg")
    if epsg_value is None:
        proj_code = properties_value.get("proj:code")
        if isinstance(proj_code, str) and proj_code.startswith("EPSG:"):
            epsg_value = proj_code.removeprefix("EPSG:")

    return {
        "scene_id": str(item["id"]),
        "collection": str(item.get("collection", "sentinel-2-l2a")),
        "datetime": _timestamp(properties_value.get("datetime")),
        "platform": properties_value.get("platform"),
        "datatake_id": properties_value.get("s2:datatake_id"),
        "relative_orbit": None if orbit_match is None else int(orbit_match.group(1)),
        "mgrs_tile": None if grid_code is None else grid_code.removeprefix("MGRS-"),
        "epsg": _nullable_int(epsg_value),
        "processing_baseline": properties_value.get("s2:processing_baseline"),
        "sequence": _nullable_int(properties_value.get("s2:sequence")),
        "generation_time": _timestamp(properties_value.get("s2:generation_time")),
        "eo_cloud_cover": _nullable_float(properties_value.get("eo:cloud_cover")),
        "s2_nodata_pct": _nullable_float(
            properties_value.get("s2:nodata_pixel_percentage")
        ),
        "s2_cloud_shadow_pct": _nullable_float(
            properties_value.get("s2:cloud_shadow_percentage")
        ),
        "s2_medium_cloud_pct": _nullable_float(
            properties_value.get("s2:medium_proba_clouds_percentage")
        ),
        "s2_high_cloud_pct": _nullable_float(
            properties_value.get("s2:high_proba_clouds_percentage")
        ),
        "s2_cirrus_pct": _nullable_float(
            properties_value.get("s2:thin_cirrus_percentage")
        ),
        "s2_snow_pct": _nullable_float(properties_value.get("s2:snow_ice_percentage")),
        "s2_unclassified_pct": _nullable_float(
            properties_value.get("s2:unclassified_percentage")
        ),
        "scl_href": _asset_href(assets_value, "scl"),
        "visual_href": _asset_href(assets_value, "visual"),
        "geometry": to_wkb(shape(dict(geometry_value)), hex=False),
        "ingested_at": pd.Timestamp(ingested_at),
    }


def deduplicate_scenes(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Keep the highest sequence per tile/datatake and return loser→winner rows."""
    if frame.empty:
        return frame.copy(), pd.DataFrame(columns=SUPERSEDED_COLUMNS)
    working = frame.copy()
    missing_group = working["mgrs_tile"].isna() | working["datatake_id"].isna()
    working["_group"] = (
        working["mgrs_tile"].astype(str) + "|" + working["datatake_id"].astype(str)
    )
    working.loc[missing_group, "_group"] = "scene|" + working.loc[
        missing_group, "scene_id"
    ].astype(str)
    working["_sequence"] = pd.to_numeric(working["sequence"], errors="coerce").fillna(
        -1
    )
    working["_generation"] = pd.to_datetime(
        working["generation_time"], utc=True, errors="coerce"
    ).fillna(pd.Timestamp("1900-01-01", tz="UTC"))
    working = working.sort_values(
        ["_group", "_sequence", "_generation", "scene_id"], kind="stable"
    )

    winner_by_group = working.groupby("_group", sort=False)["scene_id"].last()
    winners = set(winner_by_group.astype(str))
    loser_rows: list[dict[str, str]] = []
    losers = working.loc[~working["scene_id"].astype(str).isin(winners)]
    for scene_id_value, group_value in zip(
        losers["scene_id"], losers["_group"], strict=True
    ):
        scene_id = str(scene_id_value)
        loser_rows.append(
            {
                "scene_id": scene_id,
                "superseded_by": str(winner_by_group.loc[group_value]),
            }
        )

    original_columns = list(frame.columns)
    kept = working.loc[working["scene_id"].astype(str).isin(winners), original_columns]
    kept = kept.sort_values("scene_id", kind="stable").reset_index(drop=True)
    superseded = pd.DataFrame(loser_rows, columns=SUPERSEDED_COLUMNS)
    superseded = superseded.sort_values("scene_id", kind="stable").reset_index(
        drop=True
    )
    return kept, superseded


def search_start(
    configured_start: datetime,
    watermark: datetime | None,
    *,
    overlap_days: int = 7,
) -> datetime:
    """Return the discovery start using the configured watermark overlap."""
    if watermark is None:
        return configured_start
    return max(configured_start, watermark - timedelta(days=overlap_days))


def search_intervals(
    start: datetime,
    end: datetime,
    *,
    chunk_days: int = 90,
) -> list[tuple[datetime, datetime]]:
    """Split a closed search period into contiguous, non-overlapping chunks."""
    if chunk_days <= 0:
        raise ValueError("chunk_days must be positive")
    if end < start:
        raise ValueError("search end must be on or after start")
    intervals: list[tuple[datetime, datetime]] = []
    cursor = start
    one_second = timedelta(seconds=1)
    chunk_span = timedelta(days=chunk_days)
    while cursor <= end:
        chunk_end = min(end, cursor + chunk_span - one_second)
        intervals.append((cursor, chunk_end))
        cursor = chunk_end + one_second
    return intervals


def _rfc3339_utc(value: datetime) -> str:
    """Serialize a UTC timestamp in the STAC API's accepted ``Z`` form."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential_jitter(initial=1, max=30),
    reraise=True,
)
def _fetch_live_interval(
    stac_url: str,
    collection: str,
    geometry: BaseGeometry,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    stac_io = StacApiIO(timeout=(10.0, 60.0), max_retries=0)
    client = Client.open(stac_url, stac_io=stac_io)
    search = client.search(
        collections=[collection],
        intersects=mapping(geometry),
        datetime=f"{_rfc3339_utc(start)}/{_rfc3339_utc(end)}",
        limit=500,
    )
    return [item.to_dict() for item in search.items()]


def _fetch_live_items(
    stac_url: str,
    collection: str,
    geometry: BaseGeometry,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    unique_items: dict[str, dict[str, Any]] = {}
    for interval_start, interval_end in search_intervals(start, end):
        for item in _fetch_live_interval(
            stac_url,
            collection,
            geometry,
            interval_start,
            interval_end,
        ):
            unique_items[str(item["id"])] = item
    return [unique_items[item_id] for item_id in sorted(unique_items)]


def fetch_items(
    config: AppConfig,
    geometry: BaseGeometry,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """Fetch all paginated items, or load the explicit offline test fixture."""
    if config.stac_fixture is None:
        return _fetch_live_items(
            config.stac_url, config.collection, geometry, start, end
        )
    payload: Any = json.loads(config.stac_fixture.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        raise ValueError(f"invalid STAC fixture: {config.stac_fixture}")
    return [item for item in payload["features"] if isinstance(item, dict)]
