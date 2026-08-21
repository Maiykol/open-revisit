from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import yaml
from streamlit.testing.v1 import AppTest

from open_revisit.config import load_config

APP = "app/streamlit_app.py"


def _app_fixture(tmp_path: Path, *, with_aois: bool = True) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    config_path = tmp_path / "app.yaml"
    raw = {
        "start": "2024-01-01",
        "end": "2024-03-31",
        "aois": ["alpha", "beta"],
        "data_dir": str(data_dir),
        "horizon_days": 60,
        "thresholds": {"min_clear": 0.8, "min_coverage": 0.95},
    }
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    config = load_config(config_path)
    rows = []
    for aoi_id, clear_values in {
        "alpha": (0.82, 0.92),
        "beta": (0.75, 0.85),
    }.items():
        for index, clear in enumerate(clear_values):
            rows.append(
                {
                    "aoi_id": aoi_id,
                    "datatake_id": f"{aoi_id}-{index}",
                    "config_hash": config.config_hash(),
                    "observed_at": pd.Timestamp("2024-01-05T12:30:00Z")
                    + pd.Timedelta(days=index * 20),
                    "catalog_cloud_cover": 10.0 + 30.0 * index,
                    "covered_fraction": 1.0,
                    "clear_fraction": clear,
                    "usable": False,
                    "complete": True,
                }
            )
    rows.append(
        {
            "aoi_id": "alpha",
            "datatake_id": "alpha-incomplete",
            "config_hash": config.config_hash(),
            "observed_at": pd.Timestamp("2024-02-20T09:00:00Z"),
            "catalog_cloud_cover": 0.0,
            "covered_fraction": 1.0,
            "clear_fraction": 1.0,
            "usable": True,
            "complete": False,
        }
    )
    pd.DataFrame(rows).to_parquet(data_dir / "observations.parquet", index=False)
    if with_aois:
        pd.DataFrame(
            [
                {
                    "aoi_id": "alpha",
                    "name": "Alpha",
                    "country": "AA",
                    "lat": 52.5,
                    "lon": 13.4,
                    "utm_epsg": 32633,
                    "area_km2": 400.0,
                    "geometry": b"\x00",
                },
                {
                    "aoi_id": "beta",
                    "name": "Beta",
                    "country": "BB",
                    "lat": 69.6,
                    "lon": 18.9,
                    "utm_epsg": 32634,
                    "area_km2": 400.0,
                    "geometry": b"\x00",
                },
            ]
        ).to_parquet(data_dir / "aois.parquet", index=False)
    return config_path


def _charts(app: AppTest) -> list[str]:
    return [element.proto.spec for element in app.get("arrow_vega_lite_chart")]


def _caption_with(app: AppTest, text: str) -> str:
    matches = [caption.value for caption in app.caption if text in caption.value]
    assert matches, f"no caption contains {text!r}"
    return matches[0]


def test_default_render_and_interactive_controls(tmp_path: Path, monkeypatch) -> None:
    config_path = _app_fixture(tmp_path)
    monkeypatch.setenv("OPEN_REVISIT_CONFIG", str(config_path))
    app = AppTest.from_file(APP).run(timeout=15)

    assert not app.exception
    assert app.title[0].value == "Open Revisit"
    assert app.multiselect[0].value == ["alpha", "beta"]
    assert len(app.date_input) == 1
    assert [slider.label for slider in app.sidebar.slider] == [
        "min_clear",
        "Service interval W (days)",
    ]
    assert [slider.label for slider in app.main.slider] == [
        "Catalog cloud-cover threshold (%)"
    ]
    assert [tab.label for tab in app.tabs] == [
        "Overview",
        "Reliability",
        "Diagnostics",
    ]
    assert len(app.dataframe) == 1
    assert len(_charts(app)) == 8
    assert app.toggle(key="sensitivity_enabled").value is False
    initial_summary = app.dataframe[0].value
    assert initial_summary["Usable observations"].tolist() == [2, 1]
    assert initial_summary["Complete observations"].tolist() == [2, 2]

    app.multiselect[0].set_value(["alpha"])
    app.date_input[0].set_value((date(2024, 1, 15), date(2024, 3, 31)))
    app.sidebar.slider[0].set_value(0.90)
    app.sidebar.slider[1].set_value(5)
    app.run(timeout=15)
    assert not app.exception
    assert "min_clear=0.90" in app.caption[0].value
    assert "period 2024-01-15 through 2024-03-31" in app.caption[0].value
    assert "W=5 days" in app.caption[1].value
    changed_summary = app.dataframe[0].value
    assert changed_summary["AOI"].tolist() == ["alpha"]
    assert changed_summary["Complete observations"].tolist() == [1]
    assert changed_summary["Usable observations"].tolist() == [1]
    assert "Selected W = 5" in json.dumps(_charts(app))
    assert "min_clear = 0.90" in json.dumps(_charts(app))


def test_map_is_offline_and_metric_control_changes_values(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPEN_REVISIT_CONFIG", str(_app_fixture(tmp_path)))
    app = AppTest.from_file(APP).run(timeout=15)
    assert not app.exception
    map_specs = [spec for spec in _charts(app) if "geoshape" in spec]
    assert len(map_specs) == 1
    assert "http" not in map_specs[0].lower() and '"url"' not in map_specs[0]
    assert "mercator" in map_specs[0]
    assert "P(wait ≤ 7 days)" in _caption_with(app, "colour domain")
    assert app.selectbox(key="map_metric").value == "p_within_7d"

    app.selectbox(key="map_metric").set_value("longest_outage_days").run(timeout=15)
    assert not app.exception
    caption = _caption_with(app, "colour domain")
    assert (
        "Longest effective outage" in caption
        and "days" in caption
        and "lower is better" in caption
    )
    assert '"reverse": true' in next(
        spec for spec in _charts(app) if "geoshape" in spec
    )


def test_diagnostics_controls_update_timeline_quality_and_sensitivity(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPEN_REVISIT_CONFIG", str(_app_fixture(tmp_path)))
    app = AppTest.from_file(APP).run(timeout=15)
    assert not app.exception
    assert app.selectbox(key="timeline_aoi").value == "alpha"
    timeline = _caption_with(app, "Timeline for")
    assert "alpha" in timeline and "3 datatakes" in timeline
    assert "1 incomplete" in timeline

    app.selectbox(key="timeline_aoi").set_value("beta").run(timeout=15)
    assert not app.exception
    timeline = _caption_with(app, "Timeline for")
    assert "beta" in timeline and "2 datatakes" in timeline
    assert "0 incomplete" in timeline

    quality = _caption_with(app, "catalog threshold=")
    assert "catalog threshold=20%" in quality and "min_clear=0.80" in quality
    app.slider(key="catalog_threshold").set_value(40).run(timeout=15)
    assert not app.exception
    assert "catalog threshold=40%" in _caption_with(app, "catalog threshold=")
    assert "catalog threshold = 40%" in json.dumps(_charts(app))

    assert len(_charts(app)) == 8
    app.toggle(key="sensitivity_enabled").set_value(True).run(timeout=15)
    assert not app.exception
    assert len(_charts(app)) == 11
    assert "Current min_clear = 0.80" in json.dumps(_charts(app))

    app.multiselect[0].set_value(["beta"]).run(timeout=15)
    assert not app.exception
    assert app.selectbox(key="timeline_aoi").value == "beta"


def test_missing_parquet_renders_setup_message(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "missing.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "start": "2024-01-01",
                "end": "2024-03-31",
                "aois": ["alpha"],
                "data_dir": str(tmp_path / "absent"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPEN_REVISIT_CONFIG", str(config_path))
    app = AppTest.from_file(APP).run(timeout=15)

    assert not app.exception
    assert len(app.error) == 1
    assert "Setup required" in app.error[0].value
    assert "observations.parquet" in app.error[0].value


def test_missing_aois_or_basemap_degrades_to_map_warning(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(
        "OPEN_REVISIT_CONFIG", str(_app_fixture(tmp_path, with_aois=False))
    )
    app = AppTest.from_file(APP).run(timeout=15)
    assert not app.exception
    assert any("aois.parquet" in warning.value for warning in app.warning)
    assert len(_charts(app)) == 7 and len(app.dataframe) == 1

    monkeypatch.setenv("OPEN_REVISIT_CONFIG", str(_app_fixture(tmp_path / "b")))
    monkeypatch.setenv("OPEN_REVISIT_BASEMAP", str(tmp_path / "absent.geojson"))
    app = AppTest.from_file(APP).run(timeout=15)
    assert not app.exception
    assert any("Offline basemap is not available" in w.value for w in app.warning)
    assert len(_charts(app)) == 7
