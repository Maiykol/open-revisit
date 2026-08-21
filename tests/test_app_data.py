from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from open_revisit.app_data import (
    AppDataError,
    build_app_metrics,
    load_observations,
    select_observations,
)

CONFIG_HASH = "test-config"


def _row(
    aoi_id: str,
    datatake_id: str,
    observed_at: str,
    *,
    clear: float,
    covered: float = 1.0,
    complete: bool = True,
    persisted_usable: bool = False,
) -> dict[str, object]:
    return {
        "aoi_id": aoi_id,
        "datatake_id": datatake_id,
        "config_hash": CONFIG_HASH,
        "observed_at": pd.Timestamp(observed_at),
        "catalog_cloud_cover": 10.0,
        "covered_fraction": covered,
        "clear_fraction": clear,
        "usable": persisted_usable,
        "complete": complete,
    }


def _observations() -> pd.DataFrame:
    return pd.DataFrame(
        [
            _row("alpha", "outside-before", "2023-12-31T23:59:59Z", clear=1.0),
            _row("alpha", "a1", "2024-01-01T12:00:00Z", clear=0.85),
            _row(
                "alpha",
                "low-coverage",
                "2024-01-05T06:00:00Z",
                clear=0.99,
                covered=0.94,
                persisted_usable=True,
            ),
            _row(
                "alpha",
                "a2",
                "2024-01-20T18:00:00Z",
                clear=0.90,
            ),
            _row(
                "alpha",
                "incomplete",
                "2024-02-10T03:00:00Z",
                clear=1.0,
                complete=False,
                persisted_usable=True,
            ),
            _row("alpha", "outside-after", "2024-04-01T00:00:00Z", clear=1.0),
            _row("beta", "b1", "2024-01-03T05:30:00Z", clear=0.70),
            _row("beta", "b2", "2024-02-02T17:45:00Z", clear=0.82),
        ]
    )


def _build(
    observations: pd.DataFrame,
    *,
    aoi_ids: tuple[str, ...] = ("alpha", "beta"),
    start: date = date(2024, 1, 1),
    end: date = date(2024, 3, 31),
    min_clear: float = 0.80,
    horizon_days: int = 60,
    every_days: int = 7,
):
    return build_app_metrics(
        observations,
        aoi_ids=aoi_ids,
        start=start,
        end=end,
        min_clear=min_clear,
        min_coverage=0.95,
        horizon_days=horizon_days,
        every_days=every_days,
    )


def test_dynamic_threshold_coverage_incomplete_and_fractional_values() -> None:
    observations = _observations()
    default = _build(observations, aoi_ids=("alpha",), min_clear=0.80)
    stricter = _build(observations, aoi_ids=("alpha",), min_clear=0.88)

    default_summary = default.summary.iloc[0]
    strict_summary = stricter.summary.iloc[0]
    assert default_summary["n_observations"] == 3
    assert default_summary["n_usable"] == 2
    assert strict_summary["n_usable"] == 1
    assert strict_summary["p_within_7d"] < default_summary["p_within_7d"]

    selected = default.observations.set_index("datatake_id")
    assert not bool(selected.loc["low-coverage", "usable"])
    assert not bool(selected.loc["incomplete", "usable"])
    assert bool(selected.loc["a1", "usable"])
    assert default_summary["effective_median_gap_days"] == pytest.approx(19.25)
    assert pd.Timestamp(selected.loc["a1", "observed_at"]).hour == 12


def test_multi_aoi_month_survival_probability_and_period_contract() -> None:
    observations = _observations()
    metrics = _build(observations)

    assert metrics.summary["aoi_id"].tolist() == ["alpha", "beta"]
    assert metrics.summary.set_index("aoi_id").loc["alpha", "n_observations"] == 3
    assert metrics.summary.set_index("aoi_id").loc["beta", "n_observations"] == 2
    assert metrics.survival.groupby("aoi_id")["n_days"].apply(list).to_dict() == {
        "alpha": list(range(61)),
        "beta": list(range(61)),
    }
    assert metrics.monthly.groupby("aoi_id")["month"].apply(list).to_dict() == {
        "alpha": list(range(1, 13)),
        "beta": list(range(1, 13)),
    }
    probability_columns = [
        "p_waiting",
    ]
    assert (
        metrics.survival[probability_columns]
        .apply(lambda column: column.between(0.0, 1.0).all())
        .all()
    )
    assert (
        metrics.monthly[["p_within_5d", "p_within_7d", "p_within_14d"]]
        .apply(lambda column: column.between(0.0, 1.0).all())
        .all()
    )

    period = select_observations(
        observations,
        aoi_ids=("alpha",),
        start=date(2024, 1, 2),
        end=date(2024, 2, 10),
        min_clear=0.80,
        min_coverage=0.95,
    )
    assert period["datatake_id"].tolist() == [
        "low-coverage",
        "a2",
        "incomplete",
    ]


def test_sla_uses_strict_boundary() -> None:
    observations = pd.DataFrame(
        [_row("alpha", "at-w", "2024-01-02T00:00:00Z", clear=1.0)]
    )
    metrics = _build(
        observations,
        aoi_ids=("alpha",),
        start=date(2024, 1, 1),
        end=date(2024, 3, 1),
        horizon_days=60,
        every_days=1,
    )
    assert metrics.waits["wait_days"].tolist() == [1.0]
    assert metrics.summary.iloc[0]["p_within_7d"] == 1.0
    assert metrics.summary.iloc[0]["sla_success"] == 0.0


def test_zero_denominators_are_finite_zero() -> None:
    observations = pd.DataFrame(
        [
            _row(
                "alpha",
                "failed",
                "2024-01-05T12:00:00Z",
                clear=1.0,
                complete=False,
                persisted_usable=True,
            )
        ]
    )
    metrics = _build(
        observations,
        aoi_ids=("alpha",),
        start=date(2024, 1, 1),
        end=date(2024, 3, 1),
        horizon_days=60,
        every_days=7,
    )
    summary = metrics.summary.iloc[0]
    assert summary["n_observations"] == 0
    assert summary["n_usable"] == 0
    assert summary["usable_rate"] == 0.0
    assert summary["nominal_median_gap_days"] == 0.0
    assert summary["effective_median_gap_days"] == 0.0
    assert summary["longest_outage_days"] == 0.0
    assert summary["p_within_7d"] == 0.0
    assert summary["sla_success"] == 0.0
    numeric = metrics.summary.select_dtypes(include=["number"]).to_numpy(float)
    assert np.isfinite(numeric).all()


def test_load_observations_uses_config_hash_and_missing_data_is_helpful(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing" / "observations.parquet"
    with pytest.raises(AppDataError, match="Observation data is not available"):
        load_observations(missing, config_hash=CONFIG_HASH)

    path = tmp_path / "observations.parquet"
    other = _row("other", "other", "2024-01-01T00:00:00Z", clear=1.0)
    other["config_hash"] = "other-config"
    pd.concat([_observations(), pd.DataFrame([other])], ignore_index=True).to_parquet(
        path, index=False
    )
    loaded = load_observations(path, config_hash=CONFIG_HASH)
    assert set(loaded["aoi_id"]) == {"alpha", "beta"}
    assert loaded["config_hash"].eq(CONFIG_HASH).all()


def test_invalid_period_and_empty_selection_are_rejected() -> None:
    observations = _observations()
    with pytest.raises(AppDataError, match="Select at least one AOI"):
        _build(observations, aoi_ids=())
    with pytest.raises(AppDataError, match="Select at least 60 days"):
        _build(
            observations,
            aoi_ids=("alpha",),
            start=date(2024, 1, 1),
            end=date(2024, 1, 10),
        )
