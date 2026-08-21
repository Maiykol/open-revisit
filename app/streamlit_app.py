"""Streamlit composition for the read-only observation reliability app."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from open_revisit.app_analytics import (
    DEFAULT_CATALOG_THRESHOLD,
    MAP_METRIC_TITLES,
    MAP_METRICS,
    OUTAGE_THRESHOLD_DAYS,
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
from open_revisit.app_charts import (
    dumbbell_chart,
    map_chart,
    quality_scatter_chart,
    seasonal_chart,
    sensitivity_chart,
    sla_curve_chart,
    timeline_chart,
)
from open_revisit.app_data import (
    AppDataError,
    AppMetricTables,
    aoi_signature,
    basemap_signature,
    build_app_metrics,
    load_aois,
    load_basemap,
    load_observations,
    source_signature,
)
from open_revisit.config import AppConfig, load_config

CONFIG_ENV = "OPEN_REVISIT_CONFIG"
BASEMAP_ENV = "OPEN_REVISIT_BASEMAP"
DEFAULT_BASEMAP = Path("assets/natural_earth_europe.geojson")


@dataclass(frozen=True, slots=True)
class Selection:
    """The sidebar selections that every view depends on."""

    aoi_ids: tuple[str, ...]
    start: date
    end: date
    min_clear: float
    min_coverage: float
    horizon_days: int
    every_days: int


@st.cache_data(show_spinner=False)
def _load_cached(
    path_text: str,
    config_hash: str,
    source_size: int,
    source_mtime_ns: int,
) -> pd.DataFrame:
    del source_size, source_mtime_ns
    return load_observations(Path(path_text), config_hash=config_hash)


@st.cache_data(show_spinner=False)
def _metrics_cached(
    observations: pd.DataFrame,
    aoi_ids: tuple[str, ...],
    start: date,
    end: date,
    min_clear: float,
    min_coverage: float,
    horizon_days: int,
    every_days: int,
) -> AppMetricTables:
    return build_app_metrics(
        observations,
        aoi_ids=aoi_ids,
        start=start,
        end=end,
        min_clear=min_clear,
        min_coverage=min_coverage,
        horizon_days=horizon_days,
        every_days=every_days,
    )


@st.cache_data(show_spinner=False)
def _load_aois_cached(
    path_text: str, source_size: int, source_mtime_ns: int
) -> pd.DataFrame:
    del source_size, source_mtime_ns
    return load_aois(Path(path_text))


@st.cache_data(show_spinner=False)
def _load_basemap_cached(
    path_text: str, source_size: int, source_mtime_ns: int
) -> dict[str, Any]:
    del source_size, source_mtime_ns
    return load_basemap(Path(path_text))


@st.cache_data(show_spinner="Computing min_clear sensitivity…")
def _sensitivity_cached(
    observations: pd.DataFrame,
    aoi_ids: tuple[str, ...],
    start: date,
    end: date,
    min_coverage: float,
    thresholds: tuple[float, ...],
    horizon_days: int,
    every_days: int,
) -> pd.DataFrame:
    return threshold_sensitivity(
        observations,
        aoi_ids=aoi_ids,
        start=start,
        end=end,
        min_coverage=min_coverage,
        thresholds=thresholds,
        horizon_days=horizon_days,
        every_days=every_days,
    )


def _map_inputs(config: AppConfig) -> tuple[pd.DataFrame, dict[str, Any]]:
    aoi_path = config.data_dir / "aois.parquet"
    basemap_path = Path(os.environ.get(BASEMAP_ENV, str(DEFAULT_BASEMAP)))
    size, mtime_ns = aoi_signature(aoi_path)
    aois = _load_aois_cached(str(aoi_path), size, mtime_ns)
    size, mtime_ns = basemap_signature(basemap_path)
    basemap = _load_basemap_cached(str(basemap_path), size, mtime_ns)
    return aois, basemap


def _load_context() -> tuple[AppConfig, pd.DataFrame, Path]:
    config_path = Path(os.environ.get(CONFIG_ENV, "config/default.yaml"))
    if not config_path.exists():
        raise AppDataError(
            f"Configuration is not available at {config_path}. Set {CONFIG_ENV} "
            "to a readable project configuration."
        )
    config = load_config(config_path)
    observation_path = config.data_dir / "observations.parquet"
    size, mtime_ns = source_signature(observation_path)
    observations = _load_cached(
        str(observation_path), config.config_hash(), size, mtime_ns
    )
    return config, observations, observation_path


def _period_value(value: object) -> tuple[date, date] | None:
    if isinstance(value, tuple | list) and len(value) == 2:
        start, end = value
        if isinstance(start, date) and isinstance(end, date):
            return start, end
    return None


def _render_survival(metrics: AppMetricTables) -> None:
    st.subheader("Wait-time survival")
    st.caption(
        "S(n) = P(wait_days > n), using fractional UTC acquisition timestamps; "
        "denominator: all evaluated daily starts."
    )
    chart = metrics.survival.pivot(index="n_days", columns="aoi_id", values="p_waiting")
    st.line_chart(
        chart,
        x_label="Wait threshold n (days)",
        y_label="P(wait > n)",
        use_container_width=True,
    )


def _render_heatmap(metrics: AppMetricTables) -> None:
    st.subheader("Monthly reliability heatmap")
    st.caption(
        "AOI by start month; each cell is P(wait_days ≤ 7), denominator: evaluated "
        "daily starts in that month. Empty denominators display 0.0%."
    )
    heatmap = metrics.monthly.copy()
    heatmap["month_name"] = pd.to_datetime(heatmap["month"], format="%m").dt.strftime(
        "%b"
    )
    st.vega_lite_chart(
        heatmap,
        {
            "mark": "rect",
            "encoding": {
                "x": {
                    "field": "month_name",
                    "type": "ordinal",
                    "sort": [
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
                    ],
                    "title": "Start month",
                },
                "y": {"field": "aoi_id", "type": "nominal", "title": "AOI"},
                "color": {
                    "field": "p_within_7d",
                    "type": "quantitative",
                    "scale": {"domain": [0.0, 1.0], "scheme": "yellowgreenblue"},
                    "title": "P(within 7d)",
                },
                "tooltip": [
                    {"field": "aoi_id", "type": "nominal", "title": "AOI"},
                    {
                        "field": "month_name",
                        "type": "ordinal",
                        "title": "Month",
                    },
                    {
                        "field": "p_within_7d",
                        "type": "quantitative",
                        "format": ".1%",
                        "title": "P(within 7d)",
                    },
                    {
                        "field": "n_days",
                        "type": "quantitative",
                        "title": "Start-day denominator",
                    },
                ],
            },
        },
        use_container_width=True,
    )


def _render_summary(metrics: AppMetricTables, every_days: int) -> None:
    st.subheader("AOI summary and SLA")
    st.caption(
        f"SLA success is P(wait_days < W) with W={every_days} days (strict boundary). "
        "Observation counts exclude incomplete datatakes; rates are unitless and gap "
        "values retain fractional-day calculations."
    )
    columns = {
        "aoi_id": "AOI",
        "n_observations": "Complete observations",
        "n_usable": "Usable observations",
        "usable_rate": "Usable rate",
        "nominal_median_gap_days": "Nominal median gap (days)",
        "effective_median_gap_days": "Effective median gap (days)",
        "p_within_7d": "P(within 7 days)",
        "longest_outage_days": "Longest outage (days)",
        "sla_success": f"SLA success (< {every_days} days)",
    }
    summary = metrics.summary[list(columns)].rename(columns=columns)
    st.dataframe(
        summary,
        hide_index=True,
        width="stretch",
        column_config={
            "Usable rate": st.column_config.NumberColumn(format="percent"),
            "P(within 7 days)": st.column_config.NumberColumn(format="percent"),
            f"SLA success (< {every_days} days)": st.column_config.NumberColumn(
                format="percent"
            ),
            "Nominal median gap (days)": st.column_config.NumberColumn(format="%.2f"),
            "Effective median gap (days)": st.column_config.NumberColumn(format="%.2f"),
            "Longest outage (days)": st.column_config.NumberColumn(format="%.2f"),
        },
    )


def _render_map(
    config: AppConfig, metrics: AppMetricTables, selection: Selection
) -> None:
    st.subheader("Selected-city reliability map")
    metric = st.selectbox(
        "Colour markers by",
        MAP_METRICS,
        format_func=lambda value: MAP_METRIC_TITLES[value],
        key="map_metric",
    )
    try:
        aois, basemap = _map_inputs(config)
        points = map_points(metrics.summary, aois, metric=metric)
    except AppDataError as exc:
        st.warning(f"Map unavailable: {exc}")
        return
    spec = map_metric_spec(
        metric,
        every_days=selection.every_days,
        max_outage_days=float(metrics.summary["longest_outage_days"].max()),
    )
    better = "lower is better" if spec.lower_is_better else "higher is better"
    st.caption(
        f"{spec.title}; unit: {spec.unit}; colour domain {spec.domain[0]:g} to "
        f"{spec.domain[1]:g} ({better}). Thresholds: "
        f"min_clear={selection.min_clear:.2f}, "
        f"min_coverage={selection.min_coverage:.2f}, "
        f"W={selection.every_days} days. Offline Natural Earth outline from the "
        "committed asset; no tiles or web services."
    )
    st.vega_lite_chart(points, map_chart(basemap, spec), use_container_width=True)


def _render_dumbbell(metrics: AppMetricTables) -> None:
    st.subheader("Nominal versus effective revisit")
    st.caption(
        "Median gap between consecutive complete observations (nominal) versus "
        "consecutive usable observations (effective), in fractional days. Lower "
        "values mean more frequent observations. Denominator: adjacent timestamp "
        "pairs; an AOI without pairs shows 0.0."
    )
    dumbbell = revisit_dumbbell(metrics.summary)
    st.vega_lite_chart(
        dumbbell, dumbbell_chart(n_rows=len(dumbbell)), use_container_width=True
    )


def _render_sla_curve(metrics: AppMetricTables, selection: Selection) -> None:
    st.subheader("SLA curve across service intervals")
    st.caption(
        f"P(wait_days < W) for every W from 1 through {selection.horizon_days} "
        f"days (strict boundary); the dashed line marks the selected "
        f"W={selection.every_days}. Denominator: all evaluated daily starts."
    )
    curve = sla_curve(metrics.waits, horizon_days=selection.horizon_days)
    st.vega_lite_chart(
        curve,
        sla_curve_chart(
            every_days=selection.every_days, horizon_days=selection.horizon_days
        ),
        use_container_width=True,
    )


def _render_seasonal(metrics: AppMetricTables) -> None:
    st.subheader("Seasonal comparison")
    st.caption(
        "Monthly P(wait_days ≤ 7) per AOI; month is the month of t0, all twelve "
        "months are shown, and months without evaluated start days display 0.0."
    )
    st.vega_lite_chart(
        seasonal_comparison(metrics.monthly),
        seasonal_chart(),
        use_container_width=True,
    )


def _render_sensitivity(observations: pd.DataFrame, selection: Selection) -> None:
    st.subheader("min_clear threshold sensitivity")
    st.caption(
        "Recomputes usable = complete AND covered_fraction ≥ min_coverage AND "
        "clear_fraction ≥ min_clear for grid values from 0.00 to 1.00 in 0.05 "
        "steps plus the current slider value. Persisted usable flags are ignored; "
        f"min_coverage={selection.min_coverage:.2f} stays enforced. Calculated on "
        "demand and cached."
    )
    if not st.toggle("Compute min_clear sensitivity", key="sensitivity_enabled"):
        st.info(
            "Enable to calculate the grid for the selected AOIs (about 0.13 s per "
            "threshold for 20 AOIs)."
        )
        return
    thresholds = threshold_grid(selection.min_clear)
    try:
        sensitivity = _sensitivity_cached(
            observations,
            selection.aoi_ids,
            selection.start,
            selection.end,
            selection.min_coverage,
            thresholds,
            selection.horizon_days,
            selection.every_days,
        )
    except AppDataError as exc:
        st.warning(str(exc))
        return
    panels = (
        ("usable_rate", "Usable rate (of complete observations)"),
        ("p_within_7d", "P(wait ≤ 7 days)"),
        ("sla_success", f"SLA success (wait < {selection.every_days} days)"),
    )
    for field, title in panels:
        st.vega_lite_chart(
            sensitivity,
            sensitivity_chart(field=field, title=title, min_clear=selection.min_clear),
            use_container_width=True,
        )


def _render_timeline(metrics: AppMetricTables, selection: Selection) -> None:
    st.subheader("Observation timeline")
    if st.session_state.get("timeline_aoi") not in selection.aoi_ids:
        st.session_state.pop("timeline_aoi", None)
    aoi_id = st.selectbox("Timeline AOI", selection.aoi_ids, key="timeline_aoi")
    try:
        timeline = observation_timeline(metrics.observations, aoi_id=aoi_id)
    except AppDataError as exc:
        st.warning(str(exc))
        return
    counts = timeline.marks["status"].value_counts()
    st.caption(
        f"Timeline for {aoi_id}: {len(timeline.marks)} datatakes "
        f"({int(counts.get('usable', 0))} usable, "
        f"{int(counts.get('unusable', 0))} unusable, "
        f"{int(counts.get('incomplete', 0))} incomplete and excluded from every "
        "metric); one mark per (aoi_id, datatake_id, config_hash), never regrouped "
        f"by date. Shaded bands: effective gaps > {OUTAGE_THRESHOLD_DAYS:g} days "
        f"({len(timeline.outages)} outages)."
    )
    st.vega_lite_chart(
        timeline.marks,
        timeline_chart(timeline.outages),
        use_container_width=True,
    )


def _render_quality(metrics: AppMetricTables, selection: Selection) -> None:
    st.subheader("Catalog versus AOI quality")
    catalog_threshold = int(
        st.slider(
            "Catalog cloud-cover threshold (%)",
            min_value=0,
            max_value=100,
            value=DEFAULT_CATALOG_THRESHOLD,
            step=5,
            key="catalog_threshold",
        )
    )
    scatter = quality_scatter(metrics.observations)
    counts = catalog_threshold_counts(
        metrics.observations, catalog_threshold=catalog_threshold
    )
    st.caption(
        "Complete observations only; incomplete datatakes are excluded. Horizontal "
        f"line: min_clear={selection.min_clear:.2f}; vertical line: catalog "
        f"threshold={catalog_threshold}%. Pooled over selected AOIs at that "
        f"threshold: precision {counts['precision']:.1%}, "
        f"recall {counts['recall']:.1%} (TP {counts['tp']}, FP {counts['fp']}, "
        f"FN {counts['fn']}, TN {counts['tn']}). Catalog cloud cover is scene-level "
        "metadata and SCL is a per-pixel classifier; both are imperfect signals."
    )
    st.vega_lite_chart(
        scatter,
        quality_scatter_chart(
            min_clear=selection.min_clear, catalog_threshold=catalog_threshold
        ),
        use_container_width=True,
    )


def main() -> None:
    st.set_page_config(page_title="Open Revisit", page_icon="🛰️", layout="wide")
    st.title("Open Revisit")
    st.markdown(
        "Explore how cloud, coverage, and acquisition timing change Sentinel-2 "
        "observation reliability."
    )

    try:
        config, observations, observation_path = _load_context()
    except (AppDataError, ValueError) as exc:
        st.error(f"Setup required: {exc}")
        st.info(
            "The app is read-only. Supply an existing observations.parquet through "
            "the data_dir in config/default.yaml (or OPEN_REVISIT_CONFIG)."
        )
        st.stop()

    available_aois = tuple(
        sorted(str(value) for value in observations["aoi_id"].unique())
    )
    with st.sidebar:
        st.header("Analysis controls")
        selected_aois = tuple(
            st.multiselect(
                "Areas of interest (AOIs)",
                available_aois,
                default=available_aois,
                key="aoi_ids",
            )
        )
        period_raw = st.date_input(
            "Analysis period",
            value=(config.start, config.end),
            min_value=config.start,
            max_value=config.end,
            key="period",
            help=(
                f"Must span at least the configured {config.horizon_days}-day horizon."
            ),
        )
        min_clear = st.slider(
            "min_clear",
            min_value=0.0,
            max_value=1.0,
            value=float(config.thresholds.min_clear),
            step=0.01,
            format="%.2f",
            help="Minimum clear fraction of the full AOI; evaluated in memory.",
        )
        every_days = st.slider(
            "Service interval W (days)",
            min_value=1,
            max_value=config.horizon_days,
            value=7,
            step=1,
            key="every_days",
        )

    period = _period_value(period_raw)
    if period is None:
        st.warning("Select both a start date and an end date.")
        st.stop()
    start, end = period
    selection = Selection(
        aoi_ids=selected_aois,
        start=start,
        end=end,
        min_clear=float(min_clear),
        min_coverage=float(config.thresholds.min_coverage),
        horizon_days=config.horizon_days,
        every_days=int(every_days),
    )
    try:
        metrics = _metrics_cached(
            observations,
            selection.aoi_ids,
            selection.start,
            selection.end,
            selection.min_clear,
            selection.min_coverage,
            selection.horizon_days,
            selection.every_days,
        )
    except AppDataError as exc:
        st.warning(str(exc))
        st.stop()

    st.caption(
        f"Source: {observation_path} · config {config.config_hash()} · "
        f"period {start.isoformat()} through {end.isoformat()} · "
        f"min_clear={min_clear:.2f} · min_coverage="
        f"{config.thresholds.min_coverage:.2f} · horizon={config.horizon_days} days · "
        "observations keyed by AOI + s2:datatake_id."
    )
    overview, reliability, diagnostics = st.tabs(
        ["Overview", "Reliability", "Diagnostics"]
    )
    with overview:
        _render_summary(metrics, selection.every_days)
        _render_map(config, metrics, selection)
        _render_dumbbell(metrics)
    with reliability:
        _render_survival(metrics)
        _render_sla_curve(metrics, selection)
        _render_heatmap(metrics)
        _render_seasonal(metrics)
    with diagnostics:
        _render_sensitivity(observations, selection)
        _render_timeline(metrics, selection)
        _render_quality(metrics, selection)
    st.caption(
        "Read-only view: no discovery, network, raster processing, table writes, "
        "or calendar-day rounding is performed."
    )


main()
