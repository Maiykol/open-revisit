"""Vega-Lite specifications for the app: pure dictionaries, no Streamlit, no URLs."""

from __future__ import annotations

from typing import Any

import pandas as pd

from open_revisit.app_analytics import (
    MONTH_NAMES,
    OUTAGE_THRESHOLD_DAYS,
    TIMELINE_STATUSES,
    MapMetricSpec,
)

Spec = dict[str, Any]

USABLE_COLOR = "#2a9d8f"
UNUSABLE_COLOR = "#e76f51"
INCOMPLETE_COLOR = "#8d99ae"
NOMINAL_COLOR = "#adb5bd"
EFFECTIVE_COLOR = "#264653"
OUTAGE_COLOR = "#e63946"
REFERENCE_COLOR = "#343a40"
BASEMAP_FILL = "#f4f1ea"
BASEMAP_STROKE = "#9aa6ad"
STATUS_SCALE = {
    "domain": list(TIMELINE_STATUSES),
    "range": [USABLE_COLOR, UNUSABLE_COLOR, INCOMPLETE_COLOR],
}


def _aoi_color() -> Spec:
    return {"field": "aoi_id", "type": "nominal", "title": "AOI"}


def _reference_rule(
    field: str, value: float | int, *, axis: str, label: str
) -> list[Spec]:
    """Return a dashed rule plus a text label from one inline record."""
    data: Spec = {"values": [{field: value, "label": label}]}
    position: Spec = {axis: {"field": field, "type": "quantitative"}}
    text_position: Spec = dict(position)
    text_position["y" if axis == "x" else "x"] = {"value": 0}
    return [
        {
            "data": data,
            "mark": {
                "type": "rule",
                "strokeDash": [6, 4],
                "color": REFERENCE_COLOR,
            },
            "encoding": position,
        },
        {
            "data": data,
            "mark": {
                "type": "text",
                "align": "left",
                "baseline": "top",
                "dx": 4,
                "dy": 4,
                "color": REFERENCE_COLOR,
            },
            "encoding": {**text_position, "text": {"field": "label"}},
        },
    ]


def map_chart(basemap: dict[str, Any], metric: MapMetricSpec) -> Spec:
    """Layer selected-AOI circles over the inline offline Natural Earth outline."""
    return {
        "height": 520,
        "projection": {"type": "mercator"},
        "layer": [
            {
                "data": {"values": basemap["features"]},
                "mark": {
                    "type": "geoshape",
                    "fill": BASEMAP_FILL,
                    "stroke": BASEMAP_STROKE,
                    "strokeWidth": 0.6,
                },
            },
            {
                "mark": {
                    "type": "circle",
                    "size": 170,
                    "stroke": "white",
                    "strokeWidth": 0.8,
                },
                "encoding": {
                    "longitude": {"field": "lon", "type": "quantitative"},
                    "latitude": {"field": "lat", "type": "quantitative"},
                    "color": {
                        "field": "value",
                        "type": "quantitative",
                        "title": f"{metric.title} ({metric.unit})",
                        "scale": {
                            "domain": list(metric.domain),
                            "scheme": "viridis",
                            "reverse": metric.lower_is_better,
                        },
                    },
                    "tooltip": [
                        {"field": "name", "type": "nominal", "title": "City"},
                        {
                            "field": "aoi_id",
                            "type": "nominal",
                            "title": "AOI id",
                        },
                        {
                            "field": "country",
                            "type": "nominal",
                            "title": "Country",
                        },
                        {
                            "field": "value",
                            "type": "quantitative",
                            "format": metric.value_format,
                            "title": metric.title,
                        },
                        {
                            "field": "lat",
                            "type": "quantitative",
                            "format": ".3f",
                            "title": "Latitude",
                        },
                        {
                            "field": "lon",
                            "type": "quantitative",
                            "format": ".3f",
                            "title": "Longitude",
                        },
                    ],
                },
            },
        ],
    }


def sla_curve_chart(*, every_days: int, horizon_days: int) -> Spec:
    """Lines of P(wait < W) per AOI with the selected W marked."""
    return {
        "height": 320,
        "layer": [
            {
                "mark": {"type": "line"},
                "encoding": {
                    "x": {
                        "field": "every_days",
                        "type": "quantitative",
                        "title": "Service interval W (days)",
                        "scale": {"domain": [1, horizon_days]},
                    },
                    "y": {
                        "field": "sla_success",
                        "type": "quantitative",
                        "title": "SLA success = P(wait < W)",
                        "scale": {"domain": [0, 1]},
                        "axis": {"format": ".0%"},
                    },
                    "color": _aoi_color(),
                    "tooltip": [
                        {"field": "aoi_id", "type": "nominal", "title": "AOI"},
                        {
                            "field": "every_days",
                            "type": "quantitative",
                            "title": "W (days)",
                        },
                        {
                            "field": "sla_success",
                            "type": "quantitative",
                            "format": ".1%",
                            "title": "P(wait < W)",
                        },
                    ],
                },
            },
            *_reference_rule(
                "every_days",
                every_days,
                axis="x",
                label=f"Selected W = {every_days}",
            ),
        ],
    }


def sensitivity_chart(*, field: str, title: str, min_clear: float) -> Spec:
    """Lines of one sensitivity metric per AOI across min_clear."""
    return {
        "height": 260,
        "layer": [
            {
                "mark": {"type": "line", "point": True},
                "encoding": {
                    "x": {
                        "field": "min_clear",
                        "type": "quantitative",
                        "title": "min_clear threshold",
                        "scale": {"domain": [0, 1]},
                    },
                    "y": {
                        "field": field,
                        "type": "quantitative",
                        "title": title,
                        "scale": {"domain": [0, 1]},
                        "axis": {"format": ".0%"},
                    },
                    "color": _aoi_color(),
                    "tooltip": [
                        {"field": "aoi_id", "type": "nominal", "title": "AOI"},
                        {
                            "field": "min_clear",
                            "type": "quantitative",
                            "format": ".2f",
                        },
                        {
                            "field": field,
                            "type": "quantitative",
                            "format": ".1%",
                            "title": title,
                        },
                        {
                            "field": "n_usable",
                            "type": "quantitative",
                            "title": "Usable observations",
                        },
                        {
                            "field": "n_observations",
                            "type": "quantitative",
                            "title": "Complete observations",
                        },
                    ],
                },
            },
            *_reference_rule(
                "min_clear",
                min_clear,
                axis="x",
                label=f"Current min_clear = {min_clear:.2f}",
            ),
        ],
    }


def dumbbell_chart(*, n_rows: int) -> Spec:
    """Nominal-to-effective median gap per AOI; lower is better."""
    y = {"field": "aoi_id", "type": "nominal", "sort": None, "title": "AOI"}
    return {
        "height": max(160, 28 * n_rows),
        "layer": [
            {
                "mark": {"type": "rule", "color": "#c9d2d9", "strokeWidth": 2},
                "encoding": {
                    "y": y,
                    "x": {
                        "field": "nominal_median_gap_days",
                        "type": "quantitative",
                        "title": (
                            "Median gap between observations (days; lower is better)"
                        ),
                        "scale": {"zero": True},
                    },
                    "x2": {"field": "effective_median_gap_days"},
                },
            },
            {
                "transform": [
                    {
                        "fold": [
                            "nominal_median_gap_days",
                            "effective_median_gap_days",
                        ],
                        "as": ["kind", "gap_days"],
                    }
                ],
                "mark": {"type": "circle", "size": 110},
                "encoding": {
                    "y": y,
                    "x": {"field": "gap_days", "type": "quantitative"},
                    "color": {
                        "field": "kind",
                        "type": "nominal",
                        "title": "Median gap",
                        "scale": {
                            "domain": [
                                "nominal_median_gap_days",
                                "effective_median_gap_days",
                            ],
                            "range": [NOMINAL_COLOR, EFFECTIVE_COLOR],
                        },
                        "legend": {
                            "labelExpr": (
                                "datum.label == 'nominal_median_gap_days' "
                                "? 'Nominal (all complete)' : 'Effective (usable)'"
                            )
                        },
                    },
                    "tooltip": [
                        {"field": "aoi_id", "type": "nominal", "title": "AOI"},
                        {"field": "kind", "type": "nominal", "title": "Series"},
                        {
                            "field": "gap_days",
                            "type": "quantitative",
                            "format": ".2f",
                            "title": "Median gap (days)",
                        },
                    ],
                },
            },
        ],
    }


def timeline_chart(outages: pd.DataFrame) -> Spec:
    """Ticks per datatake by status with shaded effective outages > 30 days."""
    records = [
        {
            "aoi_id": str(row["aoi_id"]),
            "gap_start": pd.Timestamp(row["gap_start"]).isoformat(),
            "gap_end": pd.Timestamp(row["gap_end"]).isoformat(),
            "gap_days": float(row["gap_days"]),
        }
        for row in outages[["aoi_id", "gap_start", "gap_end", "gap_days"]].to_dict(
            orient="records"
        )
    ]
    return {
        "height": 210,
        "layer": [
            {
                "data": {"values": records},
                "mark": {"type": "rect", "color": OUTAGE_COLOR, "opacity": 0.18},
                "encoding": {
                    "x": {"field": "gap_start", "type": "temporal"},
                    "x2": {"field": "gap_end"},
                    "tooltip": [
                        {
                            "field": "gap_days",
                            "type": "quantitative",
                            "format": ".1f",
                            "title": (
                                f"Effective outage > {OUTAGE_THRESHOLD_DAYS:g} "
                                "days (days)"
                            ),
                        },
                        {
                            "field": "gap_start",
                            "type": "temporal",
                            "title": "From (UTC)",
                            "timeUnit": "utcyearmonthdatehoursminutes",
                        },
                        {
                            "field": "gap_end",
                            "type": "temporal",
                            "title": "To (UTC)",
                            "timeUnit": "utcyearmonthdatehoursminutes",
                        },
                    ],
                },
            },
            {
                "mark": {"type": "tick", "thickness": 2, "size": 26},
                "encoding": {
                    "x": {
                        "field": "observed_at",
                        "type": "temporal",
                        "title": ("Acquisition time (UTC, fractional days preserved)"),
                    },
                    "y": {
                        "field": "status",
                        "type": "nominal",
                        "sort": list(TIMELINE_STATUSES),
                        "title": None,
                        "axis": {
                            "labelExpr": (
                                "datum.label == 'incomplete' "
                                "? 'incomplete (excluded from metrics)' "
                                ": datum.label"
                            )
                        },
                    },
                    "color": {
                        "field": "status",
                        "type": "nominal",
                        "scale": STATUS_SCALE,
                        "legend": None,
                    },
                    "tooltip": [
                        {
                            "field": "datatake_id",
                            "type": "nominal",
                            "title": "Datatake",
                        },
                        {
                            "field": "observed_at",
                            "type": "temporal",
                            "title": "Observed (UTC)",
                            "timeUnit": "utcyearmonthdatehoursminutes",
                        },
                        {"field": "status", "type": "nominal", "title": "Status"},
                        {
                            "field": "clear_fraction",
                            "type": "quantitative",
                            "format": ".1%",
                        },
                        {
                            "field": "covered_fraction",
                            "type": "quantitative",
                            "format": ".1%",
                        },
                        {
                            "field": "catalog_cloud_cover",
                            "type": "quantitative",
                            "format": ".1f",
                            "title": "Catalog cloud cover (%)",
                        },
                    ],
                },
            },
        ],
    }


def quality_scatter_chart(*, min_clear: float, catalog_threshold: int) -> Spec:
    """Catalog cloud cover versus AOI clear fraction with both reference lines."""
    return {
        "height": 380,
        "layer": [
            {
                "mark": {
                    "type": "point",
                    "filled": True,
                    "opacity": 0.75,
                    "size": 55,
                },
                "encoding": {
                    "x": {
                        "field": "catalog_cloud_cover",
                        "type": "quantitative",
                        "title": "Catalog cloud cover (%)",
                        "scale": {"domain": [0, 100]},
                    },
                    "y": {
                        "field": "clear_fraction",
                        "type": "quantitative",
                        "title": ("AOI clear fraction (SCL, full-AOI denominator)"),
                        "scale": {"domain": [0, 1]},
                        "axis": {"format": ".0%"},
                    },
                    "color": {
                        "field": "status",
                        "type": "nominal",
                        "title": "Pixel-derived",
                        "scale": {
                            "domain": ["usable", "unusable"],
                            "range": [USABLE_COLOR, UNUSABLE_COLOR],
                        },
                    },
                    "shape": {
                        "field": "status",
                        "type": "nominal",
                        "title": "Pixel-derived",
                        "scale": {
                            "domain": ["usable", "unusable"],
                            "range": ["circle", "triangle-up"],
                        },
                    },
                    "tooltip": [
                        {"field": "aoi_id", "type": "nominal", "title": "AOI"},
                        {
                            "field": "datatake_id",
                            "type": "nominal",
                            "title": "Datatake",
                        },
                        {
                            "field": "observed_at",
                            "type": "temporal",
                            "title": "Observed (UTC)",
                            "timeUnit": "utcyearmonthdatehoursminutes",
                        },
                        {
                            "field": "catalog_cloud_cover",
                            "type": "quantitative",
                            "format": ".1f",
                            "title": "Catalog cloud cover (%)",
                        },
                        {
                            "field": "clear_fraction",
                            "type": "quantitative",
                            "format": ".1%",
                        },
                        {
                            "field": "covered_fraction",
                            "type": "quantitative",
                            "format": ".1%",
                        },
                        {"field": "status", "type": "nominal", "title": "Status"},
                    ],
                },
            },
            *_reference_rule(
                "clear_fraction",
                min_clear,
                axis="y",
                label=f"min_clear = {min_clear:.2f}",
            ),
            *_reference_rule(
                "catalog_cloud_cover",
                catalog_threshold,
                axis="x",
                label=f"catalog threshold = {catalog_threshold}%",
            ),
        ],
    }


def seasonal_chart() -> Spec:
    """Monthly P(wait ≤ 7) lines per AOI across all twelve start months."""
    return {
        "height": 320,
        "mark": {"type": "line", "point": True},
        "encoding": {
            "x": {
                "field": "month_name",
                "type": "ordinal",
                "sort": list(MONTH_NAMES),
                "title": "Start month (month of t0)",
            },
            "y": {
                "field": "p_within_7d",
                "type": "quantitative",
                "title": "P(wait ≤ 7 days)",
                "scale": {"domain": [0, 1]},
                "axis": {"format": ".0%"},
            },
            "color": _aoi_color(),
            "tooltip": [
                {"field": "aoi_id", "type": "nominal", "title": "AOI"},
                {"field": "month_name", "type": "ordinal", "title": "Month"},
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
    }
