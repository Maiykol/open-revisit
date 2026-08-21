"""Streamlit composition for the read-only observation reliability app."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from open_revisit.app_data import (
    AppDataError,
    AppMetricTables,
    build_app_metrics,
    load_observations,
    source_signature,
)
from open_revisit.config import AppConfig, load_config

CONFIG_ENV = "OPEN_REVISIT_CONFIG"


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
    try:
        metrics = _metrics_cached(
            observations,
            selected_aois,
            start,
            end,
            min_clear,
            float(config.thresholds.min_coverage),
            config.horizon_days,
            every_days,
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
    _render_summary(metrics, every_days)
    _render_survival(metrics)
    _render_heatmap(metrics)
    st.caption(
        "Read-only view: no discovery, network, raster processing, table writes, "
        "or calendar-day rounding is performed."
    )


main()
