from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from open_revisit.app_analytics import (
    map_metric_spec,
    map_points,
    revisit_dumbbell,
    sla_curve,
)
from open_revisit.app_data import AppDataError, build_app_metrics, select_observations
from open_revisit.metrics import gap_table, service_level_success

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
