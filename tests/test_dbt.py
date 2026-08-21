from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pytest

from open_revisit.config import AppConfig, load_config
from open_revisit.dbt_runner import dbt_variables, main, run_dbt_build
from open_revisit.metric_pipeline import METRIC_TABLE_SORTS, build_metric_tables

PROJECT_ROOT = Path(__file__).parents[1]
DEV_CONFIG = PROJECT_ROOT / "config" / "dev.yaml"
FLOAT_ATOL = 1e-12


def _fixture_config(data_dir: Path) -> AppConfig:
    return AppConfig.model_validate(
        {
            "start": "2024-01-01",
            "end": "2024-03-31",
            "aois": ["alpha", "beta"],
            "data_dir": data_dir,
            "horizon_days": 60,
        }
    )


def _fixture_observations(config: AppConfig) -> pd.DataFrame:
    origin = pd.Timestamp("2024-01-01T00:00:00Z")
    rows: list[dict[str, object]] = []
    for index, (day, hour, cloud, usable) in enumerate(
        [
            (0, 6, 10.0, True),
            (2, 12, 15.0, False),
            (5, 18, 20.0, True),
            (10, 3, 25.0, False),
            (40, 9, 30.0, True),
            (50, 15, 5.0, False),
        ]
    ):
        rows.append(
            {
                "aoi_id": "alpha",
                "datatake_id": f"take-{index}",
                "config_hash": config.config_hash(),
                "observed_at": origin
                + pd.Timedelta(days=day)
                + pd.Timedelta(hours=hour),
                "catalog_cloud_cover": cloud,
                "usable": usable,
                "complete": True,
            }
        )
    rows.extend(
        [
            {
                "aoi_id": "alpha",
                "datatake_id": "alpha-incomplete",
                "config_hash": config.config_hash(),
                "observed_at": origin + pd.Timedelta(days=20),
                "catalog_cloud_cover": 0.0,
                "usable": True,
                "complete": False,
            },
            {
                "aoi_id": "beta",
                "datatake_id": "beta-complete-unusable",
                "config_hash": config.config_hash(),
                "observed_at": origin + pd.Timedelta(days=1, hours=7),
                "catalog_cloud_cover": None,
                "usable": False,
                "complete": True,
            },
            {
                "aoi_id": "beta",
                "datatake_id": "beta-incomplete-usable",
                "config_hash": config.config_hash(),
                "observed_at": origin + pd.Timedelta(days=8),
                "catalog_cloud_cover": 0.0,
                "usable": True,
                "complete": False,
            },
            {
                "aoi_id": "alpha",
                "datatake_id": "other-config",
                "config_hash": "other",
                "observed_at": origin + pd.Timedelta(days=1),
                "catalog_cloud_cover": 0.0,
                "usable": True,
                "complete": True,
            },
        ]
    )
    return pd.DataFrame(rows)


def _dtype_kind(series: pd.Series[Any]) -> str:
    if pd.api.types.is_datetime64_any_dtype(series.dtype):
        return "timestamp"
    if pd.api.types.is_bool_dtype(series.dtype):
        return "boolean"
    if pd.api.types.is_integer_dtype(series.dtype):
        return "integer"
    if pd.api.types.is_float_dtype(series.dtype):
        return "float"
    if pd.api.types.is_string_dtype(series.dtype):
        return "string"
    return str(series.dtype)


def _assert_model_parity(
    name: str,
    expected: pd.DataFrame,
    actual: pd.DataFrame,
) -> None:
    keys = METRIC_TABLE_SORTS[name]
    assert list(actual.columns) == list(expected.columns)
    assert len(actual) == len(expected)
    assert not expected.duplicated(keys).any()
    assert not actual.duplicated(keys).any()
    expected = expected.sort_values(keys, kind="stable").reset_index(drop=True)
    actual = actual.sort_values(keys, kind="stable").reset_index(drop=True)
    assert actual.isna().sum().to_dict() == expected.isna().sum().to_dict()

    for column in expected.columns:
        expected_column = expected[column]
        actual_column = actual[column]
        assert _dtype_kind(actual_column) == _dtype_kind(expected_column), (
            name,
            column,
            expected_column.dtype,
            actual_column.dtype,
        )
        if pd.api.types.is_datetime64_any_dtype(expected_column.dtype):
            expected_utc = pd.to_datetime(expected_column, utc=True)
            actual_utc = pd.to_datetime(actual_column, utc=True)
            assert expected_utc.tolist() == actual_utc.tolist()
        elif pd.api.types.is_float_dtype(expected_column.dtype):
            np.testing.assert_allclose(
                actual_column.to_numpy(dtype=np.float64),
                expected_column.to_numpy(dtype=np.float64),
                rtol=0.0,
                atol=FLOAT_ATOL,
                equal_nan=False,
                err_msg=f"{name}.{column}",
            )
        else:
            assert actual_column.tolist() == expected_column.tolist(), (name, column)


def _read_models(database_path: Path) -> dict[str, pd.DataFrame]:
    with duckdb.connect(str(database_path), read_only=True) as connection:
        return {
            name: connection.execute(f'select * from "{name}"').fetchdf()
            for name in METRIC_TABLE_SORTS
        }


def _assert_invariants(
    tables: dict[str, pd.DataFrame],
    observations: pd.DataFrame,
    config: AppConfig,
) -> None:
    config_hash = config.config_hash()
    selected = observations.loc[
        (observations["config_hash"] == config_hash)
        & observations["aoi_id"].isin(config.aoi_ids)
    ]
    complete = selected.loc[selected["complete"].astype(bool)]

    for name, keys in METRIC_TABLE_SORTS.items():
        table = tables[name]
        assert not table.duplicated(keys).any(), name
        assert set(table["config_hash"]) == {config_hash}

    probability_columns = {
        "aoi_survival": ["p_waiting"],
        "aoi_monthly": ["p_within_5d", "p_within_7d", "p_within_14d"],
        "aoi_summary": [
            "usable_rate",
            "p_within_3d",
            "p_within_5d",
            "p_within_7d",
            "p_within_14d",
            "p_within_30d",
            "p_within_7d_best_month",
            "p_within_7d_worst_month",
            "catalog_filter_precision_t20",
            "catalog_filter_recall_t20",
        ],
        "catalog_filter_eval": [
            "precision",
            "recall",
            "f1",
            "kept_unusable_rate",
            "discarded_usable_rate",
        ],
    }
    for name, columns in probability_columns.items():
        values = tables[name][columns].to_numpy(dtype=np.float64)
        assert np.isfinite(values).all(), name
        assert np.logical_and(values >= 0.0, values <= 1.0).all(), name

    monthly = tables["aoi_monthly"]
    survival = tables["aoi_survival"]
    catalog = tables["catalog_filter_eval"]
    assert all(
        set(group["month"]) == set(range(1, 13))
        for _, group in monthly.groupby("aoi_id")
    )
    assert all(
        set(group["n_days"]) == set(range(config.horizon_days + 1))
        for _, group in survival.groupby("aoi_id")
    )
    assert all(
        set(group["threshold"]) == set(range(0, 101, 5))
        for _, group in catalog.groupby("aoi_id")
    )
    assert set(catalog["aoi_id"]) == {*config.aoi_ids, "ALL"}

    expected_complete = complete.groupby("aoi_id").size().to_dict()
    summary = tables["aoi_summary"].set_index("aoi_id")
    assert summary["n_observations"].to_dict() == expected_complete
    for row in catalog.itertuples(index=False):
        expected_count = len(complete)
        if row.aoi_id != "ALL":
            expected_count = expected_complete[row.aoi_id]
        assert row.tp + row.fp + row.fn + row.tn == expected_count


def _build_and_compare(
    observations: pd.DataFrame,
    config: AppConfig,
    database_path: Path,
    *,
    write_observations: bool = True,
) -> dict[str, pd.DataFrame]:
    if write_observations:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        observations.to_parquet(config.data_dir / "observations.parquet", index=False)
    expected = build_metric_tables(observations, config)
    run_dbt_build(config, database_path=database_path)
    actual = _read_models(database_path)
    for name in METRIC_TABLE_SORTS:
        _assert_model_parity(name, expected[name], actual[name])
    _assert_invariants(actual, observations, config)
    return actual


def test_dbt_models_match_python_on_deterministic_fixture(tmp_path: Path) -> None:
    config = _fixture_config(tmp_path / "data")
    observations = _fixture_observations(config)

    actual = _build_and_compare(observations, config, tmp_path / "fixture.duckdb")

    beta_summary = actual["aoi_summary"].set_index("aoi_id").loc["beta"]
    assert beta_summary["n_observations"] == 1
    assert beta_summary["n_usable"] == 0
    assert beta_summary["usable_rate"] == 0.0
    beta_catalog = actual["catalog_filter_eval"].loc[
        actual["catalog_filter_eval"]["aoi_id"] == "beta"
    ]
    assert not beta_catalog.isna().any(axis=None)
    assert (
        beta_catalog[
            ["precision", "recall", "f1", "kept_unusable_rate", "discarded_usable_rate"]
        ]
        == 0.0
    ).all(axis=None)


def test_dbt_models_match_python_on_base_dev_dataset(tmp_path: Path) -> None:
    config = load_config(DEV_CONFIG)
    observations_path = config.data_dir / "observations.parquet"
    if not observations_path.exists():
        pytest.skip("local dev observations are not present in a clean clone")
    observations = pd.read_parquet(observations_path)
    selected = observations.loc[observations["config_hash"] == config.config_hash()]
    assert len(selected) == 396
    assert set(selected["aoi_id"]) == {"berlin", "athens", "tromso"}

    _build_and_compare(
        observations,
        config,
        tmp_path / "dev-parity.duckdb",
        write_observations=False,
    )


def test_dbt_runner_uses_local_profile_and_reports_missing_input(
    tmp_path: Path,
) -> None:
    config = _fixture_config(tmp_path / "missing")
    variables = dbt_variables(config)

    assert variables["config_hash"] == config.config_hash()
    assert variables["aoi_ids"] == ["alpha", "beta"]
    assert variables["horizon_days"] == 60
    with pytest.raises(FileNotFoundError, match="run process first"):
        run_dbt_build(config, database_path=tmp_path / "unused.duckdb")


def test_dbt_runner_main_delegates_to_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.yaml"
    data_dir = tmp_path / "data"
    config_path.write_text(
        "start: 2024-01-01\nend: 2024-03-31\naois: [alpha]\n"
        f"data_dir: {data_dir}\nhorizon_days: 60\n",
        encoding="utf-8",
    )
    calls: list[tuple[AppConfig, Path, Path | None]] = []

    def fake_run(
        config: AppConfig,
        *,
        project_dir: Path,
        database_path: Path | None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((config, project_dir, database_path))
        return subprocess.CompletedProcess(["dbt", "build"], 0)

    monkeypatch.setattr("open_revisit.dbt_runner.run_dbt_build", fake_run)
    project_dir = tmp_path / "dbt"
    database_path = tmp_path / "result.duckdb"

    result = main(
        [
            "--config",
            str(config_path),
            "--project-dir",
            str(project_dir),
            "--database-path",
            str(database_path),
        ]
    )

    assert result == 0
    assert calls == [(load_config(config_path), project_dir, database_path)]
