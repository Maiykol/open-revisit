from __future__ import annotations

import hashlib
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from rasterio.crs import CRS
from rasterio.windows import Window

from open_revisit.aoi import build_square
from open_revisit.config import AppConfig
from open_revisit.grid import AnalysisGrid
from open_revisit.metric_pipeline import run_metrics
from open_revisit.raster import RgbChipRead
from open_revisit.report import DEFAULT_COASTLINE_PATH, run_report
from open_revisit.report_data import (
    render_summary_table,
    select_rgb_examples,
    select_survival_aois,
    service_level_frame,
)


def _report_inputs(tmp_path: Path) -> AppConfig:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    aoi_ids = tuple(f"aoi-{index}" for index in range(6))
    config = AppConfig.model_validate(
        {
            "start": "2024-01-01",
            "end": "2024-03-31",
            "aois": aoi_ids,
            "data_dir": data_dir,
            "horizon_days": 30,
        }
    )
    aoi_rows: list[dict[str, object]] = []
    observation_rows: list[dict[str, object]] = []
    scene_rows: list[dict[str, object]] = []
    clouds = [5.0, 10.0, 80.0, 90.0, 25.0, 30.0]
    usable = [True, False, True, False, True, False]
    offsets = [0, 5, 10, 20, 40, 70]
    for aoi_index, aoi_id in enumerate(aoi_ids):
        geometry, epsg = build_square(
            lat=40.0 + aoi_index * 2.0,
            lon=-5.0 + aoi_index * 5.0,
            size_km=20.0,
        )
        aoi_rows.append(
            {
                "aoi_id": aoi_id,
                "name": f"AOI {aoi_index}",
                "utm_epsg": epsg,
                "geometry": geometry,
            }
        )
        for observation_index, (cloud, is_usable, offset) in enumerate(
            zip(clouds, usable, offsets, strict=True)
        ):
            scene_id = f"scene-{aoi_index}-{observation_index}"
            observation_rows.append(
                {
                    "aoi_id": aoi_id,
                    "datatake_id": f"take-{aoi_index}-{observation_index}",
                    "config_hash": config.config_hash(),
                    "observed_at": pd.Timestamp("2024-01-01T10:00:00Z")
                    + pd.Timedelta(days=offset + aoi_index),
                    "primary_scene_id": scene_id,
                    "catalog_cloud_cover": cloud,
                    "clear_fraction": 0.9 if is_usable else 0.2,
                    "cloud_fraction": 0.05 if is_usable else 0.7,
                    "usable": is_usable,
                    "complete": True,
                }
            )
            scene_rows.append(
                {
                    "scene_id": scene_id,
                    "datatake_id": f"take-{aoi_index}-{observation_index}",
                    "s2_nodata_pct": 0.0,
                    "visual_href": f"memory://{scene_id}.tif",
                }
            )
    gpd.GeoDataFrame(aoi_rows, geometry="geometry", crs=4326).to_parquet(
        data_dir / "aois.parquet", index=False
    )
    pd.DataFrame(observation_rows).to_parquet(
        data_dir / "observations.parquet", index=False
    )
    pd.DataFrame(scene_rows).to_parquet(data_dir / "scenes.parquet", index=False)
    pd.DataFrame(
        [
            {
                "aoi_id": observation["aoi_id"],
                "scene_id": observation["primary_scene_id"],
            }
            for observation in observation_rows
        ]
    ).to_parquet(data_dir / "scene_aoi.parquet", index=False)
    run_metrics(config)
    return config


def test_report_data_selection_and_table_are_deterministic() -> None:
    summary = pd.DataFrame(
        {
            "aoi_id": [f"aoi-{index}" for index in range(8)],
            "effective_median_gap_days": [8.0, 1.0, 7.0, 2.0, 6.0, 3.0, 5.0, 4.0],
        }
    )
    selected = select_survival_aois(summary)

    assert selected[0] == "aoi-1"
    assert selected[-1] == "aoi-0"
    assert len(selected) == len(set(selected)) == 6

    table_frame = pd.DataFrame(
        [
            {
                "aoi_id": "aoi-1",
                "n_observations": 100,
                "n_usable": 42,
                "usable_rate": 0.42,
                "nominal_median_gap_days": 4.123,
                "effective_median_gap_days": 9.876,
                "p_within_7d": 0.3333,
                "longest_outage_days": 31.234,
            }
        ]
    )
    rendered = render_summary_table(table_frame, {"aoi-1": "Example"})

    assert "| Example | 100 | 42 | 42.0% | 4.12 | 9.88 | 33.3% | 31.23 |" in rendered


def test_service_and_rgb_preparation_use_metric_contracts() -> None:
    waits = pd.DataFrame(
        {
            "aoi_id": ["a", "a", "b", "b"],
            "wait_days": [0.0, 7.0, 1.0, 9.0],
            "t0": pd.to_datetime(["2024-01-01"] * 4, utc=True),
        }
    )
    service = service_level_frame(waits)
    at_seven = service.loc[service["window_days"] == 7].set_index("aoi_id")

    assert len(service) == 90
    assert at_seven.loc["a", "success_rate"] == 0.5
    assert at_seven.loc["b", "success_rate"] == 0.5
    assert at_seven.loc["MEDIAN", "success_rate"] == 0.5

    observations = pd.DataFrame(
        [
            {
                "aoi_id": "a",
                "datatake_id": "false-clear",
                "complete": True,
                "usable": False,
                "catalog_cloud_cover": 1.0,
                "clear_fraction": 0.1,
                "cloud_fraction": 0.8,
            },
            {
                "aoi_id": "b",
                "datatake_id": "false-cloudy",
                "complete": True,
                "usable": True,
                "catalog_cloud_cover": 99.0,
                "clear_fraction": 0.95,
                "cloud_fraction": 0.02,
            },
        ]
    )
    examples = select_rgb_examples(observations)

    assert examples["datatake_id"].tolist() == ["false-clear", "false-cloudy"]


def test_run_report_generates_all_artifacts_byte_identically(tmp_path: Path) -> None:
    config = _report_inputs(tmp_path)
    output_dir = tmp_path / "reports"

    def fake_rgb(href: str | Path, grid: AnalysisGrid) -> RgbChipRead:
        del href
        height = grid.height
        width = grid.width
        values = np.zeros((height, width, 3), dtype=np.uint8)
        values[..., 1] = 140
        return RgbChipRead(
            values=values,
            source_window=Window(0, 0, width, height),
            source_crs=CRS.from_epsg(32631),
            bytes_transferred=values.nbytes,
        )

    first = run_report(
        config,
        output_dir=output_dir,
        coastline_path=DEFAULT_COASTLINE_PATH,
        rgb_reader=fake_rgb,
    )
    first_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in first.artifacts
    }
    second = run_report(
        config,
        output_dir=output_dir,
        coastline_path=DEFAULT_COASTLINE_PATH,
        rgb_reader=fake_rgb,
    )

    assert len(first.artifacts) == 8
    assert all(path.exists() and path.stat().st_size > 0 for path in first.artifacts)
    assert len(first.survival_aois) == 6
    assert len(first.rgb_examples) == 2
    assert first.bytes_transferred > 0
    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in second.artifacts
    } == first_hashes
    assert "AOI 0" in (output_dir / "tables" / "aoi_summary.md").read_text()
