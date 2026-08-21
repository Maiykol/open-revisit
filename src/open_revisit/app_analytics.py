"""Pure preparation functions for the M6.1 visual analytics views.

Every service number is produced by :mod:`open_revisit.metrics`; this module
only selects, joins, and reshapes frames for display.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

import pandas as pd

from open_revisit.app_data import (
    AOI_COLUMNS,
    AppDataError,
    select_observations,
    validate_selection,
)
from open_revisit.metrics import (
    service_level_success,
    survival_curve,
    wait_daily,
    within_probability,
)

MapMetric = Literal["p_within_7d", "sla_success", "usable_rate", "longest_outage_days"]
MAP_METRICS: tuple[MapMetric, ...] = (
    "p_within_7d",
    "sla_success",
    "usable_rate",
    "longest_outage_days",
)
MAP_METRIC_TITLES: dict[MapMetric, str] = {
    "p_within_7d": "P(within 7 days)",
    "sla_success": "SLA success at selected W",
    "usable_rate": "Usable rate",
    "longest_outage_days": "Longest outage (days)",
}
OUTAGE_THRESHOLD_DAYS = 30.0
DEFAULT_THRESHOLD_STEP = 0.05
DEFAULT_CATALOG_THRESHOLD = 20
MONTH_NAMES = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
TIMELINE_STATUSES = ("usable", "unusable", "incomplete")


@dataclass(frozen=True, slots=True)
class MapMetricSpec:
    """How one summary metric is coloured and labelled on the map."""

    field: MapMetric
    title: str
    unit: str
    domain: tuple[float, float]
    value_format: str
    lower_is_better: bool


def map_metric_spec(
    metric: MapMetric, *, every_days: int, max_outage_days: float
) -> MapMetricSpec:
    """Describe a map metric. Unit: probability or days; domain is comparable."""
    if metric == "p_within_7d":
        return MapMetricSpec(
            metric,
            "P(wait ≤ 7 days)",
            "probability",
            (0.0, 1.0),
            ".1%",
            False,
        )
    if metric == "sla_success":
        return MapMetricSpec(
            metric,
            f"SLA success (wait < {every_days} days)",
            "probability",
            (0.0, 1.0),
            ".1%",
            False,
        )
    if metric == "usable_rate":
        return MapMetricSpec(
            metric,
            "Usable rate",
            "fraction of complete observations",
            (0.0, 1.0),
            ".1%",
            False,
        )
    upper = max(OUTAGE_THRESHOLD_DAYS, float(max_outage_days))
    return MapMetricSpec(
        metric, "Longest effective outage", "days", (0.0, upper), ".1f", True
    )


def map_points(
    summary: pd.DataFrame, aois: pd.DataFrame, *, metric: MapMetric
) -> pd.DataFrame:
    """Join selected-AOI summary values onto centroids. Unit: the metric's unit."""
    selected = summary[["aoi_id", metric]].rename(columns={metric: "value"})
    missing = sorted(
        set(selected["aoi_id"].astype(str)) - set(aois["aoi_id"].astype(str))
    )
    if missing:
        raise AppDataError(f"AOI metadata is missing selected AOIs: {missing}")
    points = selected.merge(
        aois[AOI_COLUMNS], on="aoi_id", how="inner", validate="one_to_one"
    )
    points["value"] = pd.to_numeric(points["value"], errors="raise").astype(float)
    return (
        points[["aoi_id", "name", "country", "lat", "lon", "value"]]
        .sort_values("aoi_id", kind="stable")
        .reset_index(drop=True)
    )


def sla_curve(waits: pd.DataFrame, *, horizon_days: int) -> pd.DataFrame:
    """Return P(wait < W) for W = 1..horizon per AOI.

    Denominator: evaluated start days.
    """
    rows: list[dict[str, object]] = []
    for aoi_id in pd.unique(waits["aoi_id"]):
        aoi_waits = waits.loc[waits["aoi_id"] == aoi_id]
        for every_days in range(1, horizon_days + 1):
            rows.append(
                {
                    "aoi_id": str(aoi_id),
                    "every_days": every_days,
                    "sla_success": service_level_success(aoi_waits, every_days),
                }
            )
    return pd.DataFrame(rows, columns=["aoi_id", "every_days", "sla_success"])


def revisit_dumbbell(summary: pd.DataFrame) -> pd.DataFrame:
    """Return nominal and effective median gaps per AOI. Unit: fractional days."""
    columns = ["nominal_median_gap_days", "effective_median_gap_days"]
    frame = summary[["aoi_id", *columns]].copy()
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype(float)
    frame["delta_days"] = (
        frame["effective_median_gap_days"] - frame["nominal_median_gap_days"]
    )
    return frame.sort_values(
        ["effective_median_gap_days", "aoi_id"],
        ascending=[True, True],
        kind="stable",
    ).reset_index(drop=True)


def threshold_grid(
    min_clear: float, *, step: float = DEFAULT_THRESHOLD_STEP
) -> tuple[float, ...]:
    """Return a deterministic min_clear grid over [0, 1] including the current value."""
    if not 0.0 <= min_clear <= 1.0:
        raise AppDataError("min_clear must be between 0 and 1.")
    count = round(1.0 / step)
    values = {round(index / count, 6) for index in range(count + 1)}
    values.add(float(min_clear))
    return tuple(sorted(values))


def threshold_sensitivity(
    observations: pd.DataFrame,
    *,
    aoi_ids: tuple[str, ...],
    start: date,
    end: date,
    min_coverage: float,
    thresholds: tuple[float, ...],
    horizon_days: int,
    every_days: int,
) -> pd.DataFrame:
    """Recompute usability per min_clear. Units: counts, rates, probabilities.

    Denominators: complete observations (usable_rate) and evaluated start days
    (p_within_7d, sla_success). The persisted ``usable`` flag is never used.
    """
    rows: list[dict[str, object]] = []
    for min_clear in thresholds:
        validate_selection(
            observations,
            aoi_ids=aoi_ids,
            start=start,
            end=end,
            min_clear=min_clear,
            min_coverage=min_coverage,
            horizon_days=horizon_days,
            every_days=every_days,
        )
        selected = select_observations(
            observations,
            aoi_ids=aoi_ids,
            start=start,
            end=end,
            min_clear=min_clear,
            min_coverage=min_coverage,
        )
        for aoi_id in aoi_ids:
            complete = selected.loc[
                (selected["aoi_id"] == aoi_id) & selected["complete"].astype(bool)
            ]
            usable = complete.loc[complete["usable"].astype(bool)]
            waits = wait_daily(
                pd.Series(usable["observed_at"]),
                start=pd.Timestamp(start),
                end=pd.Timestamp(end),
                horizon_days=horizon_days,
            )
            survival = survival_curve(waits, horizon_days=horizon_days)
            n_observations = len(complete)
            n_usable = len(usable)
            rows.append(
                {
                    "aoi_id": aoi_id,
                    "min_clear": float(min_clear),
                    "n_observations": n_observations,
                    "n_usable": n_usable,
                    "usable_rate": (
                        0.0 if n_observations == 0 else n_usable / n_observations
                    ),
                    "p_within_7d": within_probability(survival, 7),
                    "sla_success": service_level_success(waits, every_days),
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "aoi_id",
            "min_clear",
            "n_observations",
            "n_usable",
            "usable_rate",
            "p_within_7d",
            "sla_success",
        ],
    )
