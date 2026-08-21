"""Full-pipeline orchestration and reproducibility run records."""

from __future__ import annotations

import json
import platform
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import perf_counter
from typing import Any

import geopandas as gpd

from open_revisit import __version__
from open_revisit.config import AppConfig
from open_revisit.discovery import run_discovery
from open_revisit.metric_pipeline import run_metrics
from open_revisit.processing import run_processing
from open_revisit.report import run_report

VERSION_PACKAGES = (
    "duckdb",
    "geopandas",
    "matplotlib",
    "numpy",
    "pandas",
    "pyarrow",
    "pydantic",
    "pyproj",
    "pystac-client",
    "rasterio",
    "shapely",
    "typer",
)


@dataclass(frozen=True, slots=True)
class PipelineRunSummary:
    """Location and totals for one full pipeline invocation."""

    config_hash: str
    record_path: Path
    wall_clock_seconds: float
    bytes_transferred: int


def software_versions() -> dict[str, str]:
    """Return stable runtime and dependency versions for reproducibility."""
    versions = {"python": platform.python_version(), "open-revisit": __version__}
    for package in VERSION_PACKAGES:
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def _write_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _stage_counts(value: Any) -> dict[str, Any]:
    payload = asdict(value)
    return {key: item for key, item in payload.items() if key != "per_aoi"}


def execute_run(
    config: AppConfig,
    *,
    workers: int = 8,
    report_dir: Path = Path("reports"),
) -> PipelineRunSummary:
    """Run discover, process, metrics, and report with durable failure logging."""
    started_at = datetime.now(UTC)
    started_clock = perf_counter()
    config_hash = config.config_hash()
    timestamp = started_at.strftime("%Y%m%dT%H%M%S.%fZ")
    record_path = config.data_dir / "runs" / f"{timestamp}_{config_hash}.json"
    record: dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "started_at": started_at.isoformat(),
        "completed_at": None,
        "config_hash": config_hash,
        "resolved_config": config.model_dump(mode="json", by_alias=True),
        "versions": software_versions(),
        "stages": {},
        "wall_clock_seconds": 0.0,
        "bytes_transferred": 0,
        "byte_accounting": {
            "method": "UTF-8 STAC item JSON plus decoded raster source-window bytes",
            "stac_json_bytes": 0,
            "scl_window_bytes": 0,
            "visual_window_bytes": 0,
        },
        "failure": None,
    }
    _write_record(record_path, record)
    active_stage = "initialise"
    try:
        aoi_path = config.data_dir / "aois.parquet"
        if not aoi_path.exists():
            raise FileNotFoundError(
                f"AOI table not found: {aoi_path}; run 'open-revisit aois build' first"
            )
        aois = gpd.read_parquet(aoi_path)

        active_stage = "discover"
        stage_clock = perf_counter()
        discovery = run_discovery(config, aois)
        record["stages"][active_stage] = {
            **_stage_counts(discovery),
            "duration_seconds": perf_counter() - stage_clock,
        }
        record["byte_accounting"]["stac_json_bytes"] = discovery.bytes_transferred
        _write_record(record_path, record)

        active_stage = "process"
        stage_clock = perf_counter()
        processing = run_processing(config, aois, workers=workers)
        record["stages"][active_stage] = {
            **_stage_counts(processing),
            "duration_seconds": perf_counter() - stage_clock,
        }
        record["byte_accounting"]["scl_window_bytes"] = processing.bytes_transferred
        _write_record(record_path, record)

        active_stage = "metrics"
        stage_clock = perf_counter()
        metric = run_metrics(config)
        record["stages"][active_stage] = {
            "table_rows": metric.table_rows,
            "duration_seconds": perf_counter() - stage_clock,
        }
        _write_record(record_path, record)

        active_stage = "report"
        stage_clock = perf_counter()
        report = run_report(config, output_dir=report_dir)
        record["stages"][active_stage] = {
            "artifacts": [str(path) for path in report.artifacts],
            "survival_aois": list(report.survival_aois),
            "rgb_examples": list(report.rgb_examples),
            "duration_seconds": perf_counter() - stage_clock,
            "bytes_transferred": report.bytes_transferred,
        }
        record["byte_accounting"]["visual_window_bytes"] = report.bytes_transferred
        completed_at = datetime.now(UTC)
        wall_clock = perf_counter() - started_clock
        transferred = int(
            sum(
                record["byte_accounting"][key]
                for key in (
                    "stac_json_bytes",
                    "scl_window_bytes",
                    "visual_window_bytes",
                )
            )
        )
        record.update(
            {
                "status": "completed",
                "completed_at": completed_at.isoformat(),
                "wall_clock_seconds": wall_clock,
                "bytes_transferred": transferred,
            }
        )
        _write_record(record_path, record)
        return PipelineRunSummary(
            config_hash=config_hash,
            record_path=record_path,
            wall_clock_seconds=wall_clock,
            bytes_transferred=transferred,
        )
    except BaseException as error:
        record.update(
            {
                "status": "failed",
                "completed_at": datetime.now(UTC).isoformat(),
                "wall_clock_seconds": perf_counter() - started_clock,
                "bytes_transferred": int(
                    sum(
                        record["byte_accounting"][key]
                        for key in (
                            "stac_json_bytes",
                            "scl_window_bytes",
                            "visual_window_bytes",
                        )
                    )
                ),
                "failure": {
                    "stage": active_stage,
                    "type": type(error).__name__,
                    "message": str(error),
                },
            }
        )
        _write_record(record_path, record)
        raise
