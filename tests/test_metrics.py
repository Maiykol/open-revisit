from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml

from open_revisit.metrics import (
    catalog_filter_evaluation,
    gap_table,
    monthly_reliability,
    service_level_success,
    summary_metrics,
    survival_curve,
    wait_daily,
    within_probability,
)

METRICS_DOC = Path(__file__).parents[1] / "docs" / "METRICS.md"


def test_every_public_metric_docstring_states_unit_and_denominator() -> None:
    functions = [
        gap_table,
        wait_daily,
        survival_curve,
        within_probability,
        service_level_success,
        monthly_reliability,
        catalog_filter_evaluation,
        summary_metrics,
    ]

    for function in functions:
        docstring = inspect.getdoc(function)
        assert docstring is not None
        assert "Unit:" in docstring
        assert "Denominator:" in docstring


def _documented_example() -> dict[str, Any]:
    text = METRICS_DOC.read_text(encoding="utf-8")
    match = re.search(
        r"<!-- metric-example\n(?P<yaml>.*?)\nmetric-example -->",
        text,
        flags=re.DOTALL,
    )
    assert match is not None, "docs/METRICS.md must contain the metric-example block"
    parsed = yaml.safe_load(match.group("yaml"))
    assert isinstance(parsed, dict)
    return parsed


def _timestamps(start: str, day_offsets: list[int]) -> pd.Series:
    origin = pd.Timestamp(start, tz="UTC")
    return pd.Series([origin + pd.Timedelta(days=value) for value in day_offsets])


def test_documented_example_matches_metric_functions() -> None:
    example = _documented_example()
    usable = _timestamps(example["start"], example["usable_day_offsets"])

    waits = wait_daily(
        usable,
        start=pd.Timestamp(example["start"]),
        end=pd.Timestamp(example["end"]),
        horizon_days=example["horizon_days"],
    )
    survival = survival_curve(waits, horizon_days=example["horizon_days"])
    gaps = gap_table(usable, kind="effective")

    assert waits["wait_days"].tolist() == example["expected_wait_days"]
    origin = pd.Timestamp(example["start"], tz="UTC")
    assert (
        waits.loc[waits["censored"], "t0"].sub(origin).dt.days.tolist()
        == example["expected_censored_day_offsets"]
    )
    assert survival.loc[survival["n_days"] == 7, "p_waiting"].item() == pytest.approx(
        example["expected_survival_7"]
    )
    assert within_probability(survival, 7) == pytest.approx(
        example["expected_p_within_7"]
    )
    assert service_level_success(waits, 7) == pytest.approx(example["expected_sla_7"])
    assert int(waits["censored"].sum()) == example["expected_n_censored"]
    assert gaps["gap_days"].max() == pytest.approx(
        example["expected_longest_outage_days"]
    )


def test_waits_preserve_fractional_days_and_horizon_is_inclusive() -> None:
    usable = pd.Series(
        pd.to_datetime(["2024-01-03T06:00:00Z", "2024-01-06T18:00:00Z"], utc=True)
    )

    waits = wait_daily(
        usable,
        start=pd.Timestamp("2024-01-01"),
        end=pd.Timestamp("2024-01-07"),
        horizon_days=2,
    )

    assert waits["t0"].tolist() == list(
        pd.date_range("2024-01-01", "2024-01-05", freq="D", tz="UTC")
    )
    assert waits["wait_days"].tolist() == [2.0, 1.25, 0.25, 2.0, 1.75]
    assert waits["censored"].tolist() == [True, False, False, True, False]


def test_survival_within_and_sla_keep_strict_inequalities_distinct() -> None:
    waits = pd.DataFrame(
        {
            "t0": pd.date_range("2024-01-01", periods=4, freq="D"),
            "wait_days": [0.0, 6.999, 7.0, 8.0],
            "censored": [False, False, False, False],
        }
    )

    survival = survival_curve(waits, horizon_days=8)

    assert survival.loc[survival["n_days"] == 7, "p_waiting"].item() == 0.25
    assert within_probability(survival, 7) == 0.75
    assert service_level_success(waits, 7) == 0.5


def test_monthly_reliability_uses_t0_month_and_defines_empty_months() -> None:
    waits = pd.DataFrame(
        {
            "t0": pd.to_datetime(
                ["2024-01-31T00:00:00Z", "2024-02-01T00:00:00Z"], utc=True
            ),
            "wait_days": [4.5, 10.0],
            "censored": [False, False],
        }
    )

    monthly = monthly_reliability(waits)

    assert monthly["month"].tolist() == list(range(1, 13))
    january = monthly.loc[monthly["month"] == 1].iloc[0]
    february = monthly.loc[monthly["month"] == 2].iloc[0]
    march = monthly.loc[monthly["month"] == 3].iloc[0]
    assert january[["p_within_5d", "p_within_7d", "p_within_14d"]].tolist() == [
        1.0,
        1.0,
        1.0,
    ]
    assert february[["p_within_5d", "p_within_7d", "p_within_14d"]].tolist() == [
        0.0,
        0.0,
        1.0,
    ]
    assert january["n_days"] == february["n_days"] == 1
    assert march["n_days"] == 0
    assert march[["p_within_5d", "p_within_7d", "p_within_14d"]].tolist() == [
        0.0,
        0.0,
        0.0,
    ]


def test_gap_statistics_use_fractional_timestamps_and_no_boundary_gaps() -> None:
    observations = pd.Series(
        pd.to_datetime(
            [
                "2024-01-01T12:00:00Z",
                "2024-01-06T00:00:00Z",
                "2024-02-10T06:00:00Z",
            ],
            utc=True,
        )
    )

    gaps = gap_table(observations, kind="effective")

    assert gaps["kind"].tolist() == ["effective", "effective"]
    assert gaps["gap_days"].tolist() == [4.5, 35.25]
    assert gaps.iloc[0]["gap_start"] == observations.iloc[0]
    assert gaps.iloc[-1]["gap_end"] == observations.iloc[-1]
    assert gap_table(observations.iloc[:1], kind="nominal").empty


def test_catalog_filter_six_rows_pooled_and_zero_denominators() -> None:
    observations = pd.DataFrame(
        {
            "aoi_id": ["alpha"] * 6 + ["alpha"],
            "catalog_cloud_cover": [10.0, 15.0, 20.0, 25.0, 30.0, 5.0, 0.0],
            "usable": [True, False, True, False, True, False, True],
            "complete": [True] * 6 + [False],
        }
    )

    result = catalog_filter_evaluation(observations)

    assert len(result) == 42
    assert set(result["aoi_id"]) == {"alpha", "ALL"}
    assert set(result["threshold"]) == set(range(0, 101, 5))
    at_20 = result.loc[
        (result["aoi_id"] == "alpha") & (result["threshold"] == 20)
    ].iloc[0]
    assert at_20[["tp", "fp", "fn", "tn"]].tolist() == [2, 2, 1, 1]
    assert at_20["precision"] == 0.5
    assert at_20["recall"] == pytest.approx(2 / 3)
    assert at_20["f1"] == pytest.approx(4 / 7)
    assert at_20["kept_unusable_rate"] == 0.5
    assert at_20["discarded_usable_rate"] == pytest.approx(1 / 3)
    at_0 = result.loc[(result["aoi_id"] == "alpha") & (result["threshold"] == 0)].iloc[
        0
    ]
    assert at_0[["tp", "fp", "fn", "tn"]].tolist() == [0, 0, 3, 3]
    assert at_0[["precision", "recall", "f1", "kept_unusable_rate"]].tolist() == [
        0.0,
        0.0,
        0.0,
        0.0,
    ]
    assert at_0["discarded_usable_rate"] == 1.0
    probability_columns = [
        "precision",
        "recall",
        "f1",
        "kept_unusable_rate",
        "discarded_usable_rate",
    ]
    assert np.logical_and(
        result[probability_columns].ge(0.0),
        result[probability_columns].le(1.0),
    ).all(axis=None)
