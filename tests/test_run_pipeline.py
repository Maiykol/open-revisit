from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box

from open_revisit.config import AppConfig
from open_revisit.discovery import DiscoverySummary
from open_revisit.metric_pipeline import MetricRunSummary
from open_revisit.processing import ProcessingSummary
from open_revisit.report import ReportSummary
from open_revisit.run_pipeline import execute_run, software_versions


def _config(tmp_path: Path) -> AppConfig:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    gpd.GeoDataFrame(
        [{"aoi_id": "alpha", "geometry": box(0, 0, 1, 1)}],
        geometry="geometry",
        crs=4326,
    ).to_parquet(data_dir / "aois.parquet", index=False)
    return AppConfig.model_validate(
        {
            "start": "2024-01-01",
            "end": "2024-12-31",
            "aois": ["alpha"],
            "data_dir": data_dir,
        }
    )


def test_execute_run_records_config_versions_counts_timing_and_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    artifact = tmp_path / "reports" / "figure.png"
    artifact.parent.mkdir()
    artifact.write_bytes(b"png")
    monkeypatch.setattr(
        "open_revisit.run_pipeline.run_discovery",
        lambda config, aois: DiscoverySummary((), 10, 12, 1, 120),
    )
    monkeypatch.setattr(
        "open_revisit.run_pipeline.run_processing",
        lambda config, aois, workers: ProcessingSummary(
            (), 11, 9, 0, 0, 1, 11, 9, 4, 340
        ),
    )
    monkeypatch.setattr(
        "open_revisit.run_pipeline.run_metrics",
        lambda config: MetricRunSummary(config.config_hash(), {"aoi_summary": 1}),
    )
    monkeypatch.setattr(
        "open_revisit.run_pipeline.run_report",
        lambda config, output_dir: ReportSummary(
            config.config_hash(), (artifact,), ("alpha",), ("take-1", "take-2"), 560
        ),
    )

    summary = execute_run(config, report_dir=tmp_path / "reports")
    record = json.loads(summary.record_path.read_text(encoding="utf-8"))

    assert record["status"] == "completed"
    assert record["config_hash"] == config.config_hash()
    assert record["resolved_config"]["aois"] == ["alpha"]
    assert record["versions"]["python"] == software_versions()["python"]
    assert record["stages"]["discover"]["n_scenes"] == 10
    assert record["stages"]["process"]["n_observations"] == 9
    assert record["stages"]["metrics"]["table_rows"] == {"aoi_summary": 1}
    assert record["bytes_transferred"] == summary.bytes_transferred == 1020
    assert record["wall_clock_seconds"] > 0
    assert record["completed_at"] is not None


def test_execute_run_persists_failure_stage_before_reraising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)

    def fail_discovery(config: object, aois: object) -> object:
        del config, aois
        raise RuntimeError("fixture failure")

    monkeypatch.setattr("open_revisit.run_pipeline.run_discovery", fail_discovery)

    with pytest.raises(RuntimeError, match="fixture failure"):
        execute_run(config, report_dir=tmp_path / "reports")

    records = list((config.data_dir / "runs").glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert record["failure"] == {
        "stage": "discover",
        "type": "RuntimeError",
        "message": "fixture failure",
    }
