"""One-datatake processing used by the M2 thread-pool orchestrator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from open_revisit.composite import (
    CompositeSummary,
    SceneLayer,
    class_counts,
    composite_scenes,
    summarize_composite,
)
from open_revisit.config import AppConfig
from open_revisit.grid import AnalysisGrid
from open_revisit.raster import SclWindowRead, write_composite

RasterReader = Callable[[str | Path, AnalysisGrid], SclWindowRead]


@dataclass(frozen=True, slots=True)
class GroupResult:
    """Per-scene stats and one observation produced for a datatake."""

    aoi_id: str
    stats: tuple[dict[str, Any], ...]
    observation: dict[str, Any]


def _failed_stats_row(
    *,
    aoi_id: str,
    scene_id: str,
    config_hash: str,
    n_aoi_pixels: int,
    error: Exception,
    processed_at: datetime,
) -> dict[str, Any]:
    counts = (n_aoi_pixels, *(0 for _ in range(11)))
    return {
        "aoi_id": aoi_id,
        "scene_id": scene_id,
        "config_hash": config_hash,
        "n_aoi_pixels": n_aoi_pixels,
        "n_covered": 0,
        **{f"count_class_{value}": counts[value] for value in range(12)},
        "read_ok": False,
        "error": f"{type(error).__name__}: {error}",
        "processed_at": pd.Timestamp(processed_at),
    }


def _successful_stats_row(
    *,
    aoi_id: str,
    scene_id: str,
    config_hash: str,
    grid: AnalysisGrid,
    values: np.ndarray[Any, np.dtype[np.uint8]],
    processed_at: datetime,
) -> dict[str, Any]:
    counts = class_counts(values, grid.aoi_mask)
    if sum(counts) != grid.n_aoi_pixels:
        raise AssertionError("scene class counts must sum to n_aoi_pixels")
    return {
        "aoi_id": aoi_id,
        "scene_id": scene_id,
        "config_hash": config_hash,
        "n_aoi_pixels": grid.n_aoi_pixels,
        "n_covered": grid.n_aoi_pixels - counts[0],
        **{f"count_class_{value}": counts[value] for value in range(12)},
        "read_ok": True,
        "error": None,
        "processed_at": pd.Timestamp(processed_at),
    }


def _nullable_int(value: Any) -> int | None:
    return None if pd.isna(value) else int(value)


def _nullable_float(value: Any) -> float | None:
    return None if pd.isna(value) else float(value)


def _first_non_null(values: pd.Series) -> Any:
    present = values.loc[values.notna()]
    return None if present.empty else present.iloc[0]


def _weighted_cloud_cover(
    scenes: pd.DataFrame, stats_by_id: dict[str, dict[str, Any]]
) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for row in scenes.itertuples(index=False):
        cloud_cover = _nullable_float(row.eo_cloud_cover)
        weight = float(stats_by_id[str(row.scene_id)]["n_covered"])
        if cloud_cover is None or weight <= 0.0:
            continue
        numerator += cloud_cover * weight
        denominator += weight
    return None if denominator == 0.0 else numerator / denominator


def _observation_row(
    *,
    aoi_id: str,
    datatake_id: str,
    config_hash: str,
    scenes: pd.DataFrame,
    stats: list[dict[str, Any]],
    summary: CompositeSummary,
) -> dict[str, Any]:
    n_scenes = len(scenes)
    if n_scenes < 1:
        raise AssertionError("every observation must contain at least one scene")
    stats_by_id = {str(row["scene_id"]): row for row in stats}
    primary_scene_id = min(
        stats_by_id,
        key=lambda scene_id: (-int(stats_by_id[scene_id]["n_covered"]), scene_id),
    )
    primary = scenes.loc[scenes["scene_id"] == primary_scene_id].iloc[0]
    if summary.covered_fraction > 1.0:
        raise AssertionError("covered_fraction must not exceed one")
    if summary.clear_fraction > summary.covered_fraction:
        raise AssertionError("clear_fraction must not exceed covered_fraction")
    return {
        "aoi_id": aoi_id,
        "datatake_id": datatake_id,
        "config_hash": config_hash,
        "observed_at": pd.to_datetime(scenes["datetime"], utc=True).min(),
        "platform": _first_non_null(scenes["platform"]),
        "relative_orbit": _nullable_int(_first_non_null(scenes["relative_orbit"])),
        "n_scenes": n_scenes,
        "primary_scene_id": primary_scene_id,
        "catalog_cloud_cover": _nullable_float(primary["eo_cloud_cover"]),
        "catalog_cloud_cover_wmean": _weighted_cloud_cover(scenes, stats_by_id),
        "n_aoi_pixels": summary.n_aoi_pixels,
        "covered_fraction": summary.covered_fraction,
        "clear_fraction": summary.clear_fraction,
        "cloud_fraction": summary.cloud_fraction,
        "shadow_fraction": summary.shadow_fraction,
        "snow_fraction": summary.snow_fraction,
        "unclassified_fraction": summary.unclassified_fraction,
        "defective_fraction": summary.defective_fraction,
        "usable": summary.usable,
        "complete": all(bool(row["read_ok"]) for row in stats),
    }


def process_group(
    *,
    aoi_id: str,
    datatake_id: str,
    scenes: pd.DataFrame,
    grid: AnalysisGrid,
    config: AppConfig,
    reader: RasterReader,
    processed_at: datetime,
    composite_path: Path | None,
) -> GroupResult:
    """Read and composite every member scene of one AOI datatake."""
    config_hash = config.config_hash()
    layers: list[SceneLayer] = []
    stats: list[dict[str, Any]] = []
    ordered = scenes.sort_values("scene_id", kind="stable")
    for scene in ordered.itertuples(index=False):
        scene_id = str(scene.scene_id)
        try:
            if pd.isna(scene.scl_href):
                raise ValueError("scene has no SCL asset href")
            raster = reader(str(scene.scl_href), grid)
            stats.append(
                _successful_stats_row(
                    aoi_id=aoi_id,
                    scene_id=scene_id,
                    config_hash=config_hash,
                    grid=grid,
                    values=raster.values,
                    processed_at=processed_at,
                )
            )
            layers.append(
                SceneLayer(
                    scene_id=scene_id,
                    nodata_percentage=_nullable_float(scene.s2_nodata_pct),
                    values=raster.values,
                )
            )
        except Exception as error:
            stats.append(
                _failed_stats_row(
                    aoi_id=aoi_id,
                    scene_id=scene_id,
                    config_hash=config_hash,
                    n_aoi_pixels=grid.n_aoi_pixels,
                    error=error,
                    processed_at=processed_at,
                )
            )

    composite = (
        composite_scenes(layers, grid.aoi_mask)
        if layers
        else np.zeros(grid.shape, dtype=np.uint8)
    )
    summary = summarize_composite(
        composite,
        grid.aoi_mask,
        classes=config.classes,
        thresholds=config.thresholds,
    )
    if composite_path is not None:
        write_composite(composite_path, composite, grid)
    observation = _observation_row(
        aoi_id=aoi_id,
        datatake_id=datatake_id,
        config_hash=config_hash,
        scenes=ordered,
        stats=stats,
        summary=summary,
    )
    return GroupResult(aoi_id=aoi_id, stats=tuple(stats), observation=observation)
