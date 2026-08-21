from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from open_revisit.app_analytics import (
    catalog_threshold_counts,
    map_metric_spec,
    map_points,
    observation_timeline,
    quality_scatter,
    revisit_dumbbell,
    seasonal_comparison,
    sla_curve,
    threshold_grid,
    threshold_sensitivity,
)
from open_revisit.app_data import AppDataError, build_app_metrics, select_observations
from open_revisit.metrics import (
    catalog_filter_evaluation,
    gap_table,
    service_level_success,
)

CONFIG_HASH = "test-config"
START = date(2024, 1, 1)
END = date(2024, 3, 31)


def _row(
    aoi_id,
    datatake_id,
    observed_at,
    *,
    clear,
    covered=1.0,
    complete=True,
    persisted_usable=False,
    catalog_cloud_cover=10.0,
):
    return {
        "aoi_id": aoi_id,
        "datatake_id": datatake_id,
        "config_hash": CONFIG_HASH,
        "observed_at": pd.Timestamp(observed_at),
        "catalog_cloud_cover": catalog_cloud_cover,
        "covered_fraction": covered,
        "clear_fraction": clear,
        "usable": persisted_usable,
        "complete": complete,
    }


def _observations() -> pd.DataFrame:
    return pd.DataFrame(
        [
            _row(
                "alpha",
                "a1",
                "2024-01-01T12:00:00Z",
                clear=0.85,
                catalog_cloud_cover=5.0,
            ),
            _row(
                "alpha",
                "low-coverage",
                "2024-01-05T06:00:00Z",
                clear=0.99,
                covered=0.94,
                persisted_usable=True,
                catalog_cloud_cover=2.0,
            ),
            _row(
                "alpha",
                "a2",
                "2024-01-20T18:00:00Z",
                clear=0.90,
                catalog_cloud_cover=15.0,
            ),
            _row(
                "alpha",
                "incomplete",
                "2024-02-10T03:00:00Z",
                clear=1.0,
                complete=False,
                persisted_usable=True,
                catalog_cloud_cover=0.0,
            ),
            _row(
                "alpha",
                "a3",
                "2024-03-05T10:30:00Z",
                clear=0.60,
                catalog_cloud_cover=55.0,
            ),
            _row(
                "beta",
                "b1",
                "2024-01-03T05:30:00Z",
                clear=0.70,
                catalog_cloud_cover=30.0,
            ),
            _row(
                "beta",
                "b2",
                "2024-02-02T17:45:00Z",
                clear=0.82,
                catalog_cloud_cover=12.0,
            ),
        ]
    )


def _aois() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "aoi_id": "alpha",
                "name": "Alpha",
                "country": "AA",
                "lat": 52.5,
                "lon": 13.4,
            },
            {
                "aoi_id": "beta",
                "name": "Beta",
                "country": "BB",
                "lat": 69.6,
                "lon": 18.9,
            },
            {
                "aoi_id": "gamma",
                "name": "Gamma",
                "country": "GG",
                "lat": 41.0,
                "lon": 2.0,
            },
        ]
    )


def _metrics(
    aoi_ids=("alpha", "beta"),
    *,
    min_clear=0.80,
    every_days=7,
    horizon_days=60,
):
    return build_app_metrics(
        _observations(),
        aoi_ids=aoi_ids,
        start=START,
        end=END,
        min_clear=min_clear,
        min_coverage=0.95,
        horizon_days=horizon_days,
        every_days=every_days,
    )


def test_map_points_contain_exactly_selected_aois_and_switch_metric_values() -> None:
    metrics = _metrics()
    within = map_points(metrics.summary, _aois(), metric="p_within_7d")
    outage = map_points(metrics.summary, _aois(), metric="longest_outage_days")
    assert within["aoi_id"].tolist() == ["alpha", "beta"]
    assert outage["aoi_id"].tolist() == within["aoi_id"].tolist()
    assert list(within.columns) == [
        "aoi_id",
        "name",
        "country",
        "lat",
        "lon",
        "value",
    ]
    summary = metrics.summary.set_index("aoi_id")
    assert within.set_index("aoi_id")["value"].to_dict() == pytest.approx(
        summary["p_within_7d"].to_dict()
    )
    assert outage.set_index("aoi_id")["value"].to_dict() == pytest.approx(
        summary["longest_outage_days"].to_dict()
    )
    assert not within["value"].equals(outage["value"])
    assert within["value"].between(0.0, 1.0).all()

    single = map_points(_metrics(("beta",)).summary, _aois(), metric="usable_rate")
    assert single["aoi_id"].tolist() == ["beta"]
    with pytest.raises(AppDataError, match="missing selected AOIs"):
        map_points(metrics.summary, _aois().iloc[[1]], metric="usable_rate")


def test_map_metric_spec_domains_and_labels() -> None:
    assert map_metric_spec("p_within_7d", every_days=7, max_outage_days=5.0).domain == (
        0.0,
        1.0,
    )
    sla = map_metric_spec("sla_success", every_days=5, max_outage_days=5.0)
    assert "5" in sla.title and sla.domain == (0.0, 1.0) and not sla.lower_is_better
    outage = map_metric_spec("longest_outage_days", every_days=7, max_outage_days=12.5)
    assert (
        outage.domain == (0.0, 30.0)
        and outage.unit == "days"
        and outage.lower_is_better
    )
    assert map_metric_spec(
        "longest_outage_days", every_days=7, max_outage_days=44.0
    ).domain == (0.0, 44.0)


def test_sla_curve_emits_every_w_and_keeps_strict_boundary() -> None:
    metrics = _metrics(horizon_days=60)
    curve = sla_curve(metrics.waits, horizon_days=60)
    assert curve.groupby("aoi_id")["every_days"].apply(list).to_dict() == {
        "alpha": list(range(1, 61)),
        "beta": list(range(1, 61)),
    }
    assert curve["sla_success"].between(0.0, 1.0).all()
    alpha_waits = metrics.waits.loc[metrics.waits["aoi_id"] == "alpha"]
    for every_days in (1, 7, 30, 60):
        expected = service_level_success(alpha_waits, every_days)
        actual = curve.loc[
            (curve["aoi_id"] == "alpha") & (curve["every_days"] == every_days),
            "sla_success",
        ].iloc[0]
        assert actual == expected
    assert (
        curve.groupby("aoi_id")["sla_success"]
        .apply(lambda s: s.is_monotonic_increasing)
        .all()
    )

    one = build_app_metrics(
        pd.DataFrame([_row("alpha", "at-w", "2024-01-02T00:00:00Z", clear=1.0)]),
        aoi_ids=("alpha",),
        start=date(2024, 1, 1),
        end=date(2024, 3, 1),
        min_clear=0.8,
        min_coverage=0.95,
        horizon_days=60,
        every_days=1,
    )
    strict = sla_curve(one.waits, horizon_days=60).set_index("every_days")[
        "sla_success"
    ]
    assert strict.loc[1] == 0.0 and strict.loc[2] == 1.0


def test_revisit_dumbbell_matches_gap_table_and_keeps_fractions() -> None:
    metrics = _metrics()
    dumbbell = revisit_dumbbell(metrics.summary)
    assert list(dumbbell.columns) == [
        "aoi_id",
        "nominal_median_gap_days",
        "effective_median_gap_days",
        "delta_days",
    ]
    selected = select_observations(
        _observations(),
        aoi_ids=("alpha", "beta"),
        start=START,
        end=END,
        min_clear=0.80,
        min_coverage=0.95,
    )
    for aoi_id in ("alpha", "beta"):
        complete = selected.loc[(selected["aoi_id"] == aoi_id) & selected["complete"]]
        nominal = gap_table(pd.Series(complete["observed_at"]), kind="nominal")[
            "gap_days"
        ]
        effective = gap_table(
            pd.Series(complete.loc[complete["usable"], "observed_at"]),
            kind="effective",
        )["gap_days"]
        row = dumbbell.set_index("aoi_id").loc[aoi_id]
        assert row["nominal_median_gap_days"] == pytest.approx(float(nominal.median()))
        expected_effective = 0.0 if effective.empty else float(effective.median())
        assert row["effective_median_gap_days"] == pytest.approx(expected_effective)
        assert row["delta_days"] == pytest.approx(
            expected_effective - float(nominal.median())
        )
    alpha = dumbbell.set_index("aoi_id").loc["alpha"]
    assert alpha["effective_median_gap_days"] == pytest.approx(19.25)
    assert alpha["nominal_median_gap_days"] != round(alpha["nominal_median_gap_days"])
    assert dumbbell["effective_median_gap_days"].is_monotonic_increasing

    empty = build_app_metrics(
        pd.DataFrame(
            [
                _row(
                    "alpha",
                    "only",
                    "2024-01-05T12:00:00Z",
                    clear=1.0,
                    complete=False,
                )
            ]
        ),
        aoi_ids=("alpha",),
        start=START,
        end=END,
        min_clear=0.8,
        min_coverage=0.95,
        horizon_days=60,
        every_days=7,
    )
    zero = revisit_dumbbell(empty.summary).iloc[0]
    assert (
        zero["nominal_median_gap_days"] == 0.0
        and zero["effective_median_gap_days"] == 0.0
    )
    assert np.isfinite(
        revisit_dumbbell(empty.summary).select_dtypes("number").to_numpy()
    ).all()


def test_threshold_grid_is_deterministic_and_includes_endpoints_and_current() -> None:
    grid = threshold_grid(0.83)
    assert grid[0] == 0.0 and grid[-1] == 1.0 and 0.83 in grid
    assert len(grid) == 22 and list(grid) == sorted(set(grid))
    assert threshold_grid(0.80) == threshold_grid(0.8)
    assert len(threshold_grid(0.80)) == 21
    assert 0.15 in threshold_grid(0.5)
    with pytest.raises(AppDataError):
        threshold_grid(1.5)


def _sensitivity(observations, *, aoi_ids=("alpha", "beta"), every_days=7):
    return threshold_sensitivity(
        observations,
        aoi_ids=aoi_ids,
        start=START,
        end=END,
        min_coverage=0.95,
        thresholds=threshold_grid(0.80),
        horizon_days=60,
        every_days=every_days,
    )


def test_threshold_sensitivity_recomputes_usability_and_is_monotonic() -> None:
    observations = _observations()
    sensitivity = _sensitivity(observations)
    assert list(sensitivity.columns) == [
        "aoi_id",
        "min_clear",
        "n_observations",
        "n_usable",
        "usable_rate",
        "p_within_7d",
        "sla_success",
    ]
    assert sensitivity.groupby("aoi_id")["min_clear"].apply(len).to_dict() == {
        "alpha": 21,
        "beta": 21,
    }
    for column in ("n_usable", "usable_rate", "p_within_7d", "sla_success"):
        assert (
            sensitivity.groupby("aoi_id")[column]
            .apply(lambda s: s.is_monotonic_decreasing)
            .all()
        ), column
    assert (
        sensitivity[["usable_rate", "p_within_7d", "sla_success"]]
        .apply(lambda c: c.between(0.0, 1.0).all())
        .all()
    )
    assert np.isfinite(sensitivity.select_dtypes("number").to_numpy()).all()

    alpha = sensitivity.loc[sensitivity["aoi_id"] == "alpha"].set_index("min_clear")
    assert (alpha["n_observations"] == 4).all()  # incomplete row never counted
    assert alpha.loc[0.0, "n_usable"] == 3  # low-coverage row excluded even at 0.0
    assert alpha.loc[0.8, "n_usable"] == 2
    assert alpha.loc[1.0, "n_usable"] == 0 and alpha.loc[1.0, "p_within_7d"] == 0.0

    flipped = observations.copy()
    flipped["usable"] = True
    pd.testing.assert_frame_equal(_sensitivity(flipped), sensitivity)

    parity = _metrics().summary.set_index("aoi_id")
    for aoi_id in ("alpha", "beta"):
        at_current = sensitivity.loc[
            (sensitivity["aoi_id"] == aoi_id) & (sensitivity["min_clear"] == 0.8)
        ].iloc[0]
        assert at_current["usable_rate"] == parity.loc[aoi_id, "usable_rate"]
        assert at_current["p_within_7d"] == parity.loc[aoi_id, "p_within_7d"]
        assert at_current["sla_success"] == parity.loc[aoi_id, "sla_success"]

    only_beta = _sensitivity(observations, aoi_ids=("beta",))
    assert set(only_beta["aoi_id"]) == {"beta"}
    pd.testing.assert_frame_equal(
        only_beta.reset_index(drop=True),
        sensitivity.loc[sensitivity["aoi_id"] == "beta"].reset_index(drop=True),
    )


def test_observation_timeline_keeps_datatake_rows_and_marks_long_outages() -> None:
    metrics = _metrics()
    timeline = observation_timeline(metrics.observations, aoi_id="alpha")
    marks = timeline.marks
    assert marks["datatake_id"].tolist() == [
        "a1",
        "low-coverage",
        "a2",
        "incomplete",
        "a3",
    ]
    assert (
        marks[["aoi_id", "datatake_id", "config_hash"]].drop_duplicates().shape[0] == 5
    )
    assert marks.set_index("datatake_id")["status"].to_dict() == {
        "a1": "usable",
        "low-coverage": "unusable",
        "a2": "usable",
        "incomplete": "incomplete",
        "a3": "unusable",
    }
    assert (
        pd.Timestamp(marks.set_index("datatake_id").loc["a1", "observed_at"]).hour == 12
    )
    assert timeline.outages.columns.tolist() == [
        "aoi_id",
        "gap_start",
        "gap_end",
        "gap_days",
    ]
    assert timeline.outages.empty  # usable gap a1→a2 is 19.25 days

    shifted = pd.concat(
        [
            _observations(),
            pd.DataFrame(
                [
                    _row(
                        "alpha",
                        "a4",
                        "2024-03-25T12:00:00Z",
                        clear=0.95,
                    )
                ]
            ),
        ],
        ignore_index=True,
    )
    later = build_app_metrics(
        shifted,
        aoi_ids=("alpha",),
        start=START,
        end=END,
        min_clear=0.8,
        min_coverage=0.95,
        horizon_days=60,
        every_days=7,
    )
    outages = observation_timeline(later.observations, aoi_id="alpha").outages
    assert outages["gap_days"].tolist() == pytest.approx(
        [64.75]
    )  # a2 → a4, incomplete ignored
    assert outages["aoi_id"].tolist() == ["alpha"]

    beta = observation_timeline(metrics.observations, aoi_id="beta").marks
    assert set(beta["aoi_id"]) == {"beta"} and len(beta) == 2
    with pytest.raises(AppDataError, match="no observations"):
        observation_timeline(metrics.observations, aoi_id="gamma")


def test_quality_scatter_excludes_incomplete_and_counts_follow_threshold() -> None:
    metrics = _metrics()
    scatter = quality_scatter(metrics.observations)
    assert "incomplete" not in set(scatter["datatake_id"])
    assert len(scatter) == 6
    assert set(scatter["status"]) == {"usable", "unusable"}
    assert scatter.set_index("datatake_id").loc["low-coverage", "status"] == "unusable"
    assert list(scatter.columns) == [
        "aoi_id",
        "datatake_id",
        "observed_at",
        "catalog_cloud_cover",
        "clear_fraction",
        "covered_fraction",
        "status",
    ]

    counts = catalog_threshold_counts(metrics.observations, catalog_threshold=20)
    evaluation = catalog_filter_evaluation(metrics.observations)
    pooled = evaluation.loc[
        (evaluation["aoi_id"] == "ALL") & (evaluation["threshold"] == 20)
    ].iloc[0]
    assert counts == {
        "tp": int(pooled["tp"]),
        "fp": int(pooled["fp"]),
        "fn": int(pooled["fn"]),
        "tn": int(pooled["tn"]),
        "precision": float(pooled["precision"]),
        "recall": float(pooled["recall"]),
    }
    assert counts["tp"] + counts["fp"] + counts["fn"] + counts["tn"] == 6
    assert (
        catalog_threshold_counts(metrics.observations, catalog_threshold=0)["tp"] == 0
    )
    with pytest.raises(AppDataError, match="multiple of 5"):
        catalog_threshold_counts(metrics.observations, catalog_threshold=17)


def test_seasonal_comparison_emits_twelve_months_per_aoi_with_finite_zeroes() -> None:
    metrics = _metrics()
    seasonal = seasonal_comparison(metrics.monthly)
    assert list(seasonal.columns) == [
        "aoi_id",
        "month",
        "month_name",
        "p_within_7d",
        "n_days",
    ]
    assert seasonal.groupby("aoi_id")["month"].apply(list).to_dict() == {
        "alpha": list(range(1, 13)),
        "beta": list(range(1, 13)),
    }
    assert seasonal["month_name"].tolist()[:3] == ["Jan", "Feb", "Mar"]
    empty_months = seasonal.loc[seasonal["n_days"] == 0]
    assert len(empty_months) > 0 and (empty_months["p_within_7d"] == 0.0).all()
    assert seasonal["p_within_7d"].between(0.0, 1.0).all()
    assert np.isfinite(seasonal["p_within_7d"].to_numpy()).all()
    monthly = metrics.monthly.set_index(["aoi_id", "month"])["p_within_7d"]
    assert seasonal.set_index(["aoi_id", "month"])["p_within_7d"].equals(monthly)
    with pytest.raises(AppDataError, match="12 months"):
        seasonal_comparison(metrics.monthly.iloc[:-1])
