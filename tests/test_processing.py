from __future__ import annotations

import hashlib
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from rasterio.crs import CRS
from rasterio.errors import RasterioIOError
from rasterio.windows import Window
from shapely.geometry import box
from shapely.ops import transform

from open_revisit.config import AppConfig, Thresholds
from open_revisit.grid import AnalysisGrid
from open_revisit.processing import run_processing
from open_revisit.raster import SclWindowRead


def _inputs(data_dir: Path) -> tuple[AppConfig, gpd.GeoDataFrame]:
    to_wgs84 = Transformer.from_crs(32633, 4326, always_xy=True).transform
    geometry = transform(
        to_wgs84,
        box(500_003.0, 5_800_003.0, 500_037.0, 5_800_037.0),
    )
    aois = gpd.GeoDataFrame(
        [{"aoi_id": "test", "utm_epsg": 32633, "geometry": geometry}],
        geometry="geometry",
        crs="EPSG:4326",
    )
    scenes = pd.DataFrame(
        [
            {
                "scene_id": "scene-a",
                "datetime": pd.Timestamp("2024-01-01T10:00:00Z"),
                "platform": "sentinel-2a",
                "datatake_id": "take-1",
                "relative_orbit": 1,
                "eo_cloud_cover": 10.0,
                "s2_nodata_pct": 10.0,
                "scl_href": "scene-a.tif",
            },
            {
                "scene_id": "scene-b",
                "datetime": pd.Timestamp("2024-01-01T10:00:01Z"),
                "platform": "sentinel-2a",
                "datatake_id": "take-1",
                "relative_orbit": 1,
                "eo_cloud_cover": 40.0,
                "s2_nodata_pct": 20.0,
                "scl_href": "scene-b.tif",
            },
            {
                "scene_id": "scene-failed",
                "datetime": pd.Timestamp("2024-01-02T10:00:00Z"),
                "platform": "sentinel-2a",
                "datatake_id": "take-2",
                "relative_orbit": 1,
                "eo_cloud_cover": 80.0,
                "s2_nodata_pct": 0.0,
                "scl_href": "failed.tif",
            },
        ]
    )
    links = pd.DataFrame(
        [{"aoi_id": "test", "scene_id": scene_id} for scene_id in scenes["scene_id"]]
    )
    data_dir.mkdir(parents=True)
    scenes.to_parquet(data_dir / "scenes.parquet", index=False)
    links.to_parquet(data_dir / "scene_aoi.parquet", index=False)
    config = AppConfig.model_validate(
        {
            "start": "2024-01-01",
            "end": "2024-01-02",
            "aois": ["test"],
            "data_dir": data_dir,
            "thresholds": {"min_clear": 0.5, "min_coverage": 1.0},
        }
    )
    return config, aois


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_processing_failure_idempotency_force_and_config_isolation(
    tmp_path: Path,
) -> None:
    config, aois = _inputs(tmp_path / "data")
    calls: list[str] = []

    def fake_reader(href: str | Path, grid: AnalysisGrid) -> SclWindowRead:
        name = str(href)
        calls.append(name)
        if name == "failed.tif":
            raise RasterioIOError("synthetic broken raster")
        values = np.zeros(grid.shape, dtype=np.uint8)
        positions = np.flatnonzero(grid.aoi_mask)
        if name == "scene-a.tif":
            values.flat[positions[: len(positions) // 2]] = 4
        else:
            values[grid.aoi_mask] = 9
        return SclWindowRead(
            values=values,
            source_window=Window(0, 0, grid.width, grid.height),
            source_crs=CRS.from_epsg(32633),
        )

    first = run_processing(
        config, aois, workers=2, keep_rasters=True, reader=fake_reader
    )
    stats_path = config.data_dir / "scene_aoi_stats.parquet"
    observations_path = config.data_dir / "observations.parquet"
    first_hashes = (_sha256(stats_path), _sha256(observations_path))
    first_stats = pd.read_parquet(stats_path)
    first_observations = pd.read_parquet(observations_path)

    assert first.processed_scenes == 3
    assert first.processed_observations == 2
    assert first.failed_scenes == 1
    assert len(calls) == 3
    assert len(list((config.data_dir / "rasters" / "test").glob("*.tif"))) == 2
    assert (
        first_stats.filter(like="count_class_")
        .sum(axis=1)
        .eq(first_stats["n_aoi_pixels"])
        .all()
    )
    assert first_observations["covered_fraction"].le(1.0).all()
    assert (
        first_observations["clear_fraction"]
        .le(first_observations["covered_fraction"])
        .all()
    )
    assert first_observations["n_scenes"].ge(1).all()
    failed = first_stats.loc[first_stats["scene_id"] == "scene-failed"].iloc[0]
    assert failed["read_ok"] is np.False_
    assert "synthetic broken raster" in failed["error"]
    take_1 = first_observations.loc[first_observations["datatake_id"] == "take-1"].iloc[
        0
    ]
    assert take_1["n_scenes"] == 2
    assert take_1["covered_fraction"] == 1.0
    assert take_1["clear_fraction"] == 0.5
    assert take_1["usable"] is np.True_

    second = run_processing(config, aois, workers=2, reader=fake_reader)

    assert second.processed_scenes == 0
    assert second.processed_observations == 0
    assert second.skipped_scenes == 3
    assert second.skipped_observations == 2
    assert len(calls) == 3
    assert (_sha256(stats_path), _sha256(observations_path)) == first_hashes

    forced = run_processing(config, aois, workers=2, force=True, reader=fake_reader)

    assert forced.processed_scenes == 3
    assert forced.processed_observations == 2
    assert len(calls) == 6
    pd.testing.assert_frame_equal(pd.read_parquet(stats_path), first_stats)
    pd.testing.assert_frame_equal(
        pd.read_parquet(observations_path), first_observations
    )

    changed = config.model_copy(
        update={"thresholds": Thresholds(min_clear=0.75, min_coverage=1.0)}
    )
    assert changed.config_hash() != config.config_hash()
    changed_result = run_processing(changed, aois, workers=2, reader=fake_reader)
    all_stats = pd.read_parquet(stats_path)
    all_observations = pd.read_parquet(observations_path)

    assert changed_result.processed_scenes == 3
    assert changed_result.processed_observations == 2
    assert len(all_stats) == 6
    assert len(all_observations) == 4
    assert len(calls) == 9
    assert set(all_stats["config_hash"]) == {
        config.config_hash(),
        changed.config_hash(),
    }
    original_take = all_observations.loc[
        (all_observations["config_hash"] == config.config_hash())
        & (all_observations["datatake_id"] == "take-1")
    ].iloc[0]
    changed_take = all_observations.loc[
        (all_observations["config_hash"] == changed.config_hash())
        & (all_observations["datatake_id"] == "take-1")
    ].iloc[0]
    assert original_take["usable"] is np.True_
    assert changed_take["usable"] is np.False_
