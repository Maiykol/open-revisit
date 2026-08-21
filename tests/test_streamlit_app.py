from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import yaml
from streamlit.testing.v1 import AppTest

from open_revisit.config import load_config


def _app_fixture(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
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
    for aoi_id, clear_values in {"alpha": (0.82, 0.92), "beta": (0.75, 0.85)}.items():
        for index, clear in enumerate(clear_values):
            rows.append(
                {
                    "aoi_id": aoi_id,
                    "datatake_id": f"{aoi_id}-{index}",
                    "config_hash": config.config_hash(),
                    "observed_at": pd.Timestamp("2024-01-05T12:30:00Z")
                    + pd.Timedelta(days=index * 20),
                    "catalog_cloud_cover": 10.0,
                    "covered_fraction": 1.0,
                    "clear_fraction": clear,
                    "usable": False,
                    "complete": True,
                }
            )
    pd.DataFrame(rows).to_parquet(data_dir / "observations.parquet", index=False)
    return config_path


def test_default_render_and_interactive_controls(tmp_path: Path, monkeypatch) -> None:
    config_path = _app_fixture(tmp_path)
    monkeypatch.setenv("OPEN_REVISIT_CONFIG", str(config_path))
    app = AppTest.from_file("app/streamlit_app.py").run(timeout=15)

    assert not app.exception
    assert app.title[0].value == "Open Revisit"
    assert app.multiselect[0].value == ["alpha", "beta"]
    assert len(app.date_input) == 1
    assert [slider.label for slider in app.slider] == [
        "min_clear",
        "Service interval W (days)",
    ]
    assert len(app.dataframe) == 1
    assert len(app.get("arrow_vega_lite_chart")) == 2
    initial_summary = app.dataframe[0].value
    assert initial_summary["Usable observations"].tolist() == [2, 1]

    app.multiselect[0].set_value(["alpha"])
    app.date_input[0].set_value((date(2024, 1, 15), date(2024, 3, 31)))
    app.slider[0].set_value(0.90)
    app.slider[1].set_value(5)
    app.run(timeout=15)
    assert not app.exception
    assert "min_clear=0.90" in app.caption[0].value
    assert "period 2024-01-15 through 2024-03-31" in app.caption[0].value
    assert "W=5 days" in app.caption[1].value
    changed_summary = app.dataframe[0].value
    assert changed_summary["AOI"].tolist() == ["alpha"]
    assert changed_summary["Complete observations"].tolist() == [1]
    assert changed_summary["Usable observations"].tolist() == [1]


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
    app = AppTest.from_file("app/streamlit_app.py").run(timeout=15)

    assert not app.exception
    assert len(app.error) == 1
    assert "Setup required" in app.error[0].value
    assert "observations.parquet" in app.error[0].value
