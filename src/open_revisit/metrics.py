"""Pure tabular service metrics for observation timelines."""

from __future__ import annotations

from itertools import pairwise
from typing import Literal

import numpy as np
import pandas as pd

GapKind = Literal["nominal", "effective"]
SECONDS_PER_DAY = 86_400.0
CATALOG_THRESHOLDS = tuple(range(0, 101, 5))


def _utc_timestamp(value: pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _sorted_timestamps(values: pd.Series) -> pd.DatetimeIndex:
    converted = pd.to_datetime(values, utc=True)
    return pd.DatetimeIndex(converted.dropna().sort_values(kind="stable"))


def _safe_ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def gap_table(observed_at: pd.Series, *, kind: GapKind) -> pd.DataFrame:
    """Return consecutive gaps. Unit: days. Denominator: adjacent timestamp pairs."""
    timestamps = _sorted_timestamps(observed_at)
    rows: list[dict[str, object]] = []
    for gap_start, gap_end in pairwise(timestamps):
        rows.append(
            {
                "kind": kind,
                "gap_start": gap_start,
                "gap_end": gap_end,
                "gap_days": (gap_end - gap_start).total_seconds() / SECONDS_PER_DAY,
            }
        )
    return pd.DataFrame(
        rows,
        columns=["kind", "gap_start", "gap_end", "gap_days"],
    )


def wait_daily(
    usable_observed_at: pd.Series,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    horizon_days: int,
) -> pd.DataFrame:
    """Return waits. Unit: days. Denominator: days from start through end minus H."""
    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")
    start_utc = _utc_timestamp(start).normalize()
    end_utc = _utc_timestamp(end).normalize()
    last_start = end_utc - pd.Timedelta(days=horizon_days)
    if last_start < start_utc:
        raise ValueError("period must be at least horizon_days long")

    timestamps = _sorted_timestamps(usable_observed_at)
    start_days = pd.date_range(start_utc, last_start, freq="D")
    rows: list[dict[str, object]] = []
    horizon_delta = pd.Timedelta(days=horizon_days)
    for t0 in start_days:
        position = int(timestamps.searchsorted(t0, side="left"))
        has_candidate = position < len(timestamps)
        candidate_wait = timestamps[position] - t0 if has_candidate else horizon_delta
        censored = not has_candidate or candidate_wait > horizon_delta
        wait = horizon_delta if censored else candidate_wait
        rows.append(
            {
                "t0": t0,
                "wait_days": wait.total_seconds() / SECONDS_PER_DAY,
                "censored": censored,
            }
        )
    return pd.DataFrame(rows, columns=["t0", "wait_days", "censored"])


def survival_curve(wait_frame: pd.DataFrame, *, horizon_days: int) -> pd.DataFrame:
    """Return P(wait > n). Unit: probability. Denominator: evaluated start days."""
    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")
    waits = pd.to_numeric(wait_frame["wait_days"], errors="raise").to_numpy(
        dtype=np.float64
    )
    denominator = len(waits)
    rows = [
        {
            "n_days": n_days,
            "p_waiting": _safe_ratio(
                int(np.count_nonzero(waits > n_days)), denominator
            ),
        }
        for n_days in range(horizon_days + 1)
    ]
    return pd.DataFrame(rows, columns=["n_days", "p_waiting"])


def within_probability(survival: pd.DataFrame, n_days: int) -> float:
    """Return P(wait <= N). Unit: probability. Denominator: evaluated start days."""
    matches = survival.loc[survival["n_days"] == n_days, "p_waiting"]
    if len(matches) != 1:
        raise ValueError(
            f"survival curve must contain exactly one row for day {n_days}"
        )
    return 1.0 - float(matches.iloc[0])


def service_level_success(
    wait_frame: pd.DataFrame,
    every_days: int,
    *,
    month: int | None = None,
) -> float:
    """Return P(wait < W). Unit: probability. Denominator: selected start days."""
    if every_days <= 0:
        raise ValueError("every_days must be positive")
    selected = wait_frame
    if month is not None:
        if not 1 <= month <= 12:
            raise ValueError("month must be between 1 and 12")
        t0 = pd.to_datetime(wait_frame["t0"], utc=True)
        selected = wait_frame.loc[t0.dt.month == month]
    if selected.empty:
        return 0.0
    waits = pd.to_numeric(selected["wait_days"], errors="raise")
    return float(waits.lt(every_days).mean())


def monthly_reliability(wait_frame: pd.DataFrame) -> pd.DataFrame:
    """Return monthly reliability. Unit: probability. Denominator: t0 days in month."""
    t0 = pd.to_datetime(wait_frame["t0"], utc=True)
    waits = pd.to_numeric(wait_frame["wait_days"], errors="raise")
    rows: list[dict[str, object]] = []
    for month in range(1, 13):
        month_waits = waits.loc[t0.dt.month == month]
        n_days = len(month_waits)
        rows.append(
            {
                "month": month,
                "p_within_5d": (float(month_waits.le(5).mean()) if n_days else 0.0),
                "p_within_7d": (float(month_waits.le(7).mean()) if n_days else 0.0),
                "p_within_14d": (float(month_waits.le(14).mean()) if n_days else 0.0),
                "n_days": n_days,
            }
        )
    return pd.DataFrame(
        rows,
        columns=["month", "p_within_5d", "p_within_7d", "p_within_14d", "n_days"],
    )


def _catalog_rows(aoi_id: str, observations: pd.DataFrame) -> list[dict[str, object]]:
    actual = observations["usable"].astype(bool).to_numpy()
    cloud_cover = pd.to_numeric(
        observations["catalog_cloud_cover"], errors="coerce"
    ).to_numpy(dtype=np.float64)
    rows: list[dict[str, object]] = []
    for threshold in CATALOG_THRESHOLDS:
        predicted = cloud_cover <= threshold
        tp = int(np.count_nonzero(predicted & actual))
        fp = int(np.count_nonzero(predicted & ~actual))
        fn = int(np.count_nonzero(~predicted & actual))
        tn = int(np.count_nonzero(~predicted & ~actual))
        precision = _safe_ratio(tp, tp + fp)
        recall = _safe_ratio(tp, tp + fn)
        rows.append(
            {
                "aoi_id": aoi_id,
                "threshold": threshold,
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
                "precision": precision,
                "recall": recall,
                "f1": _safe_ratio(2 * tp, 2 * tp + fp + fn),
                "kept_unusable_rate": _safe_ratio(fp, tp + fp),
                "discarded_usable_rate": _safe_ratio(fn, tp + fn),
            }
        )
    return rows


def catalog_filter_evaluation(observations: pd.DataFrame) -> pd.DataFrame:
    """Return catalog confusion metrics.

    Unit: observation counts and unitless rates. Denominator: complete observations.
    """
    required = {"aoi_id", "catalog_cloud_cover", "usable", "complete"}
    missing = required - set(observations.columns)
    if missing:
        raise ValueError(f"observations missing columns: {sorted(missing)}")
    aoi_ids = sorted(str(value) for value in observations["aoi_id"].unique())
    if "ALL" in aoi_ids:
        raise ValueError("observation aoi_id 'ALL' is reserved for pooled metrics")
    complete = observations.loc[observations["complete"].astype(bool)].copy()
    rows: list[dict[str, object]] = []
    for aoi_id in aoi_ids:
        rows.extend(_catalog_rows(aoi_id, complete.loc[complete["aoi_id"] == aoi_id]))
    rows.extend(_catalog_rows("ALL", complete))
    return pd.DataFrame(
        rows,
        columns=[
            "aoi_id",
            "threshold",
            "tp",
            "fp",
            "fn",
            "tn",
            "precision",
            "recall",
            "f1",
            "kept_unusable_rate",
            "discarded_usable_rate",
        ],
    )


def _gap_stat(gaps: pd.DataFrame, kind: GapKind, statistic: str) -> float:
    values = pd.to_numeric(gaps.loc[gaps["kind"] == kind, "gap_days"], errors="raise")
    if values.empty:
        return 0.0
    if statistic == "median":
        return float(values.median())
    if statistic == "p90":
        return float(values.quantile(0.9))
    if statistic == "max":
        return float(values.max())
    raise ValueError(f"unknown gap statistic: {statistic}")


def summary_metrics(
    observations: pd.DataFrame,
    gaps: pd.DataFrame,
    survival: pd.DataFrame,
    monthly: pd.DataFrame,
    catalog_filter: pd.DataFrame,
) -> dict[str, int | float]:
    """Return headlines. Unit: counts/days/rates. Denominator: complete or t0 days."""
    complete = observations.loc[observations["complete"].astype(bool)]
    n_observations = len(complete)
    n_usable = int(complete["usable"].astype(bool).sum())
    valid_months = monthly.loc[monthly["n_days"] > 0]
    if valid_months.empty:
        best_month = worst_month = 0
        best_probability = worst_probability = 0.0
    else:
        best = valid_months.sort_values(
            ["p_within_7d", "month"], ascending=[False, True], kind="stable"
        ).iloc[0]
        worst = valid_months.sort_values(
            ["p_within_7d", "month"], ascending=[True, True], kind="stable"
        ).iloc[0]
        best_month = int(best["month"])
        worst_month = int(worst["month"])
        best_probability = float(best["p_within_7d"])
        worst_probability = float(worst["p_within_7d"])

    t20 = catalog_filter.loc[catalog_filter["threshold"] == 20]
    if len(t20) != 1:
        raise ValueError("catalog filter table must contain one threshold-20 row")
    return {
        "n_observations": n_observations,
        "n_usable": n_usable,
        "usable_rate": _safe_ratio(n_usable, n_observations),
        "nominal_median_gap_days": _gap_stat(gaps, "nominal", "median"),
        "effective_median_gap_days": _gap_stat(gaps, "effective", "median"),
        "effective_p90_gap_days": _gap_stat(gaps, "effective", "p90"),
        "longest_outage_days": _gap_stat(gaps, "effective", "max"),
        "n_outages_over_30d": int(
            (
                (gaps["kind"] == "effective")
                & pd.to_numeric(gaps["gap_days"], errors="raise").gt(30)
            ).sum()
        ),
        "p_within_3d": within_probability(survival, 3),
        "p_within_5d": within_probability(survival, 5),
        "p_within_7d": within_probability(survival, 7),
        "p_within_14d": within_probability(survival, 14),
        "p_within_30d": within_probability(survival, 30),
        "best_month": best_month,
        "worst_month": worst_month,
        "p_within_7d_best_month": best_probability,
        "p_within_7d_worst_month": worst_probability,
        "catalog_filter_precision_t20": float(t20["precision"].iloc[0]),
        "catalog_filter_recall_t20": float(t20["recall"].iloc[0]),
    }
