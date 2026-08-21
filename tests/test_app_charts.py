from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from open_revisit.app_analytics import (
    MONTH_NAMES,
    TIMELINE_STATUSES,
    map_metric_spec,
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


def _walk(node: Any):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key, value
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _assert_offline(spec: dict[str, Any]) -> None:
    for key, value in _walk(spec):
        assert key != "url", "Vega-Lite data.url is forbidden"
        assert not (
            isinstance(value, str) and value.lower().startswith(("http://", "https://"))
        )
    json.dumps(spec)  # must be serialisable without pandas objects


def _rule_values(spec: dict[str, Any], field: str) -> list[Any]:
    values = []
    for layer in spec.get("layer", []):
        mark = layer.get("mark")
        mark_type = mark.get("type") if isinstance(mark, dict) else mark
        if mark_type == "rule":
            values.extend(
                record[field] for record in layer["data"]["values"] if field in record
            )
    return values


def test_map_chart_is_offline_and_uses_inline_basemap() -> None:
    basemap = json.loads(
        Path("assets/natural_earth_europe.geojson").read_text(encoding="utf-8")
    )
    metric = map_metric_spec("longest_outage_days", every_days=7, max_outage_days=12.0)
    spec = map_chart(basemap, metric)
    _assert_offline(spec)
    assert spec["projection"]["type"] == "mercator"
    basemap_layer, points_layer = spec["layer"]
    assert basemap_layer["mark"]["type"] == "geoshape"
    assert basemap_layer["data"]["values"] is basemap["features"]
    assert len(basemap_layer["data"]["values"]) == len(basemap["features"])
    encoding = points_layer["encoding"]
    assert (
        encoding["longitude"]["field"] == "lon"
        and encoding["latitude"]["field"] == "lat"
    )
    assert encoding["color"]["scale"]["domain"] == [0.0, 30.0]
    assert encoding["color"]["scale"]["reverse"] is True
    assert metric.unit in encoding["color"]["title"]
    tooltip_fields = {item["field"] for item in encoding["tooltip"]}
    assert {"name", "aoi_id", "value"} <= tooltip_fields
    probability = map_chart(
        basemap,
        map_metric_spec("p_within_7d", every_days=7, max_outage_days=0.0),
    )
    assert probability["layer"][1]["encoding"]["color"]["scale"]["reverse"] is False


def test_sla_and_sensitivity_charts_mark_current_selection() -> None:
    sla = sla_curve_chart(every_days=9, horizon_days=60)
    _assert_offline(sla)
    assert _rule_values(sla, "every_days") == [9]
    assert sla["layer"][0]["encoding"]["x"]["scale"]["domain"] == [1, 60]
    assert sla["layer"][0]["encoding"]["y"]["scale"]["domain"] == [0, 1]
    assert any("9" in str(value) for _, value in _walk(sla) if isinstance(value, str))

    sensitivity = sensitivity_chart(field="sla_success", title="SLA", min_clear=0.83)
    _assert_offline(sensitivity)
    assert _rule_values(sensitivity, "min_clear") == [0.83]
    assert sensitivity["layer"][0]["encoding"]["y"]["field"] == "sla_success"


def test_quality_scatter_reference_lines_follow_selections() -> None:
    spec = quality_scatter_chart(min_clear=0.65, catalog_threshold=40)
    _assert_offline(spec)
    assert _rule_values(spec, "clear_fraction") == [0.65]
    assert _rule_values(spec, "catalog_cloud_cover") == [40]
    points = spec["layer"][0]["encoding"]
    assert (
        points["x"]["field"] == "catalog_cloud_cover"
        and points["y"]["field"] == "clear_fraction"
    )
    assert points["color"]["scale"]["domain"] == ["usable", "unusable"]
    assert points["shape"]["field"] == "status"


def test_timeline_dumbbell_and_seasonal_specs() -> None:
    outages = pd.DataFrame(
        {
            "aoi_id": ["alpha"],
            "gap_start": [pd.Timestamp("2024-01-20T18:00:00Z")],
            "gap_end": [pd.Timestamp("2024-03-25T12:00:00Z")],
            "gap_days": [64.75],
        }
    )
    timeline = timeline_chart(outages)
    _assert_offline(timeline)
    band = timeline["layer"][0]
    assert band["mark"]["type"] == "rect"
    assert band["data"]["values"][0]["gap_days"] == 64.75
    assert band["data"]["values"][0]["gap_start"].startswith("2024-01-20T18:00:00")
    assert timeline["layer"][1]["encoding"]["y"]["sort"] == list(TIMELINE_STATUSES)
    assert "excluded from metrics" in json.dumps(timeline)
    assert timeline_chart(outages.iloc[0:0])["layer"][0]["data"]["values"] == []

    dumbbell = dumbbell_chart(n_rows=3)
    _assert_offline(dumbbell)
    rule = dumbbell["layer"][0]["encoding"]
    assert rule["x"]["field"] == "nominal_median_gap_days"
    assert rule["x2"]["field"] == "effective_median_gap_days"
    assert "lower is better" in rule["x"]["title"]
    assert dumbbell["height"] >= 3 * 28

    seasonal = seasonal_chart()
    _assert_offline(seasonal)
    assert seasonal["encoding"]["x"]["sort"] == list(MONTH_NAMES)
    assert seasonal["encoding"]["y"]["scale"]["domain"] == [0, 1]
