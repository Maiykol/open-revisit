from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from typer.testing import CliRunner

from open_revisit.cli import app
from open_revisit.config import AppConfig
from open_revisit.metric_pipeline import (
    METRIC_TABLE_SORTS,
    build_metric_tables,
    query_sla,
    run_metrics,
)


def _config(data_dir: Path) -> AppConfig:
    return AppConfig.model_validate(
        {
            "start": "2024-01-01",
            "end": "2024-03-31",
            "aois": ["alpha"],
            "data_dir": data_dir,
            "horizon_days": 60,
        }
    )


def _observations(config: AppConfig) -> pd.DataFrame:
    origin = pd.Timestamp("2024-01-01T00:00:00Z")
    rows: list[dict[str, object]] = []
    cloud_cover = [10.0, 15.0, 20.0, 25.0, 30.0, 5.0]
    usable = [True, False, True, False, True, False]
    for index, (cloud, is_usable) in enumerate(zip(cloud_cover, usable, strict=True)):
        rows.append(
            {
                "aoi_id": "alpha",
                "datatake_id": f"take-{index}",
                "config_hash": config.config_hash(),
                "observed_at": origin + pd.Timedelta(days=[0, 2, 5, 10, 40, 50][index]),
                "catalog_cloud_cover": cloud,
                "usable": is_usable,
                "complete": True,
            }
        )
    rows.extend(
        [
            {
                "aoi_id": "alpha",
                "datatake_id": "incomplete",
                "config_hash": config.config_hash(),
                "observed_at": origin + pd.Timedelta(days=20),
                "catalog_cloud_cover": 0.0,
                "usable": True,
                "complete": False,
            },
            {
                "aoi_id": "alpha",
                "datatake_id": "historical-config",
                "config_hash": "historical",
                "observed_at": origin + pd.Timedelta(days=1),
                "catalog_cloud_cover": 0.0,
                "usable": True,
                "complete": True,
            },
        ]
    )
    return pd.DataFrame(rows)


def _hashes(data_dir: Path) -> dict[str, str]:
    return {
        name: hashlib.sha256((data_dir / f"{name}.parquet").read_bytes()).hexdigest()
        for name in METRIC_TABLE_SORTS
    }


def test_build_and_run_metrics_are_config_isolated_and_deterministic(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path / "data")
    observations = _observations(config)
    original = observations.copy(deep=True)
    config.data_dir.mkdir(parents=True)
    observations.to_parquet(config.data_dir / "observations.parquet", index=False)

    tables = build_metric_tables(observations, config)

    pd.testing.assert_frame_equal(observations, original)
    assert {name: len(frame) for name, frame in tables.items()} == {
        "aoi_wait_daily": 31,
        "aoi_survival": 61,
        "aoi_monthly": 12,
        "aoi_gaps": 7,
        "aoi_summary": 1,
        "catalog_filter_eval": 42,
    }
    summary = tables["aoi_summary"].iloc[0]
    assert summary["n_observations"] == 6
    assert summary["n_usable"] == 3
    assert summary["usable_rate"] == 0.5
    assert summary["longest_outage_days"] == 35.0
    assert summary["n_outages_over_30d"] == 1
    assert summary["catalog_filter_precision_t20"] == 0.5
    assert summary["catalog_filter_recall_t20"] == pytest.approx(2 / 3)

    first = run_metrics(config)
    first_hashes = _hashes(config.data_dir)
    second = run_metrics(config)

    assert first.table_rows == second.table_rows
    assert _hashes(config.data_dir) == first_hashes
    for name, keys in METRIC_TABLE_SORTS.items():
        table = pd.read_parquet(config.data_dir / f"{name}.parquet")
        assert set(table["config_hash"]) == {config.config_hash()}
        assert not table.duplicated(keys).any()
    with duckdb.connect(str(config.data_dir / "open_revisit.duckdb")) as connection:
        views = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.views"
            ).fetchall()
        }
    assert set(METRIC_TABLE_SORTS) <= views


def test_metrics_and_sla_cli_use_existing_tables(tmp_path: Path) -> None:
    config = _config(tmp_path / "data")
    config.data_dir.mkdir(parents=True)
    _observations(config).to_parquet(
        config.data_dir / "observations.parquet", index=False
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "start: 2024-01-01",
                "end: 2024-03-31",
                "aois: [alpha]",
                f"data_dir: {config.data_dir}",
                "horizon_days: 60",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    runner = CliRunner()

    metrics_result = runner.invoke(app, ["metrics", "--config", str(config_path)])
    sla_result = runner.invoke(
        app,
        [
            "sla",
            "--config",
            str(config_path),
            "--aoi",
            "alpha",
            "--every",
            "7",
        ],
    )
    expected = query_sla(
        config.data_dir,
        aoi_id="alpha",
        config_hash=config.config_hash(),
        every_days=7,
    )

    assert metrics_result.exit_code == 0, metrics_result.output
    assert "metrics.complete" in metrics_result.output
    assert sla_result.exit_code == 0, sla_result.output
    assert f"success_rate={expected.success_rate:.12g}" in sla_result.output
    assert f"n_days={expected.n_days}" in sla_result.output
