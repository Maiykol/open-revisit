# M6.1 — Expanded Visual Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the read-only Streamlit app with seven new visualizations (offline map, SLA curve, min_clear sensitivity, nominal/effective dumbbell, datatake timeline, catalog/AOI quality scatter, seasonal comparison) organized into Overview / Reliability / Diagnostics tabs, without changing any metric semantics.

**Architecture:** Three pure, typed modules under `src/open_revisit/` do all the work: `app_data.py` (loading; extended with AOI metadata + offline basemap loaders), a new `app_analytics.py` (pandas preparation functions that only call `metrics.py`), and a new `app_charts.py` (Vega-Lite spec dictionaries, no Streamlit import, no URLs). `app/streamlit_app.py` stays a thin composition layer: sidebar controls, cache wrappers, tabs, and `st.vega_lite_chart(frame, spec)` calls.

**Tech Stack:** Python 3.12, uv, pandas 2.3, Streamlit 1.49.1 (`st.vega_lite_chart`, `st.tabs`, `st.toggle`, `streamlit.testing.v1.AppTest`), pytest, mypy --strict, ruff.

**Spec:** The owner's M6.1 brief (reproduced as the agent prompt in `docs/superpowers/plans/2026-08-21-m6-1-agent-prompt.md`); `docs/SPEC.md` remains the source of truth; `docs/METRICS.md` and `src/open_revisit/metrics.py` are the metric contracts.

## Global Constraints

- Everything stays local, read-only, offline. No `st.map`, tiles, Mapbox, online GeoJSON, geocoding, or web services. No writes to Parquet, DuckDB, config, run records, dbt artifacts, or reports.
- The app imports only `open_revisit.app_data`, `open_revisit.app_analytics`, `open_revisit.app_charts`, and `open_revisit.config`. Never import discovery, stac, raster, processing, report, metric_pipeline, or run_pipeline from the app or the new modules.
- **Do not add fields to `AppConfig`.** `config_hash()` hashes every model field; a new field would change the hash `f33bae2b…` and orphan the full-run data. The basemap path is configured by the env var `OPEN_REVISIT_BASEMAP` (default `assets/natural_earth_europe.geojson`, repository-relative); AOI metadata is `config.data_dir / "aois.parquet"`.
- Every service number is produced by `metrics.py` (`gap_table`, `wait_daily`, `survival_curve`, `within_probability`, `service_level_success`, `monthly_reliability`, `catalog_filter_evaluation`, `summary_metrics`). Never re-derive waits, gaps, survival, or SLA elsewhere.
- Usability is always `complete AND covered_fraction >= min_coverage AND clear_fraction >= min_clear`, recomputed by `select_observations`; the persisted `usable` column never drives a result. Incomplete observations are excluded from every metric. SLA uses strict `wait_days < W`; within-N uses `<= N`; survival uses `> n`; outages use `gap_days > 30`; horizon inclusive; fractional days preserved; observations keyed by `(aoi_id, datatake_id, config_hash)`; every zero denominator is finite `0.0`.
- Measured costs on the full dataset (20 AOIs × 2022–2025): one sensitivity threshold pass ≈ 0.13 s (21-point grid ≈ 2.7 s → must be on-demand + cached); full SLA curve W=1..60 ≈ 0.03 s (eager is fine). Cold default render must stay < 3 s (currently 0.79 s).
- Coverage gate: `--cov-fail-under=85` (currently 90.56%). `make check` = ruff check + ruff format --check + mypy --strict src/ + pytest -m "not network".
- Ruff enforces `line-length = 88` with `E` (so E501), `F`, `I`, `UP`, `B`, `SIM`, `RUF` on `src/` and `tests/`. The code blocks in this plan are written compactly; after pasting, run `uv run ruff format .` and split any remaining over-long string literals with implicit concatenation. Never change behaviour while reformatting.
- Tests use temporary Parquet fixtures only; never touch `data/`. The real basemap at `assets/natural_earth_europe.geojson` may be read by tests (it is committed, 66 KB, 46 Polygon features with `name`/`iso_a3` properties).
- Conventional commits, one per task. Do not push. Protected artifacts (hash before and after): `data/*.parquet`, `data/open_revisit.duckdb`, `data/runs/*.json`, `reports/**`, `dbt/models/**`, `dbt/dbt_project.yml`, `dbt/macros/**`, `dbt/tests/**`.
- AppTest facts verified on Streamlit 1.49.1: elements inside `st.tabs` are visible via `app.get("arrow_vega_lite_chart")`, `app.tabs[i].label`; widgets are addressable by key (`app.selectbox(key=...)`, `app.slider(key=...)`, `app.toggle(key=...)`); a chart's spec JSON is `element.proto.spec` (nested layer `data.values` survive in it; top-level data does not). `st.line_chart` also counts as `arrow_vega_lite_chart`.

---

## File structure

| Path | Responsibility |
|---|---|
| `docs/DECISIONS.md` | Add `M6.1-001` scope-extension decision record. |
| `src/open_revisit/app_data.py` (modify) | Add `file_signature`, `aoi_signature`, `basemap_signature`, `AOI_COLUMNS`, `load_aois`, `load_basemap`; rename `_validate_selection` → `validate_selection`. |
| `src/open_revisit/app_analytics.py` (new) | Pure preparation: `map_metric_spec`, `map_points`, `sla_curve`, `threshold_grid`, `threshold_sensitivity`, `revisit_dumbbell`, `observation_timeline`, `quality_scatter`, `catalog_threshold_counts`, `seasonal_comparison`. |
| `src/open_revisit/app_charts.py` (new) | Vega-Lite dict builders: `map_chart`, `sla_curve_chart`, `sensitivity_chart`, `dumbbell_chart`, `timeline_chart`, `quality_scatter_chart`, `seasonal_chart`. |
| `app/streamlit_app.py` (modify) | Cache wrappers, tabs, render functions, focused controls. |
| `tests/test_app_data.py` (modify) | AOI/basemap loader tests. |
| `tests/test_app_analytics.py` (new) | Deterministic pure-layer tests. |
| `tests/test_app_charts.py` (new) | Spec tests incl. offline guarantee and reference lines. |
| `tests/test_streamlit_app.py` (modify) | AppTest coverage for every view and control. |
| `scripts/benchmark_app.py` (modify) | Add a third timing with sensitivity enabled. |
| `README.md`, `assets/README.md` | Describe the expanded app. |

Shared fixture shape used by every new test (mirrors `tests/test_app_data.py::_row`):

```python
CONFIG_HASH = "test-config"

def _row(aoi_id, datatake_id, observed_at, *, clear, covered=1.0, complete=True,
         persisted_usable=False, catalog_cloud_cover=10.0):
    return {
        "aoi_id": aoi_id, "datatake_id": datatake_id, "config_hash": CONFIG_HASH,
        "observed_at": pd.Timestamp(observed_at), "catalog_cloud_cover": catalog_cloud_cover,
        "covered_fraction": covered, "clear_fraction": clear,
        "usable": persisted_usable, "complete": complete,
    }
```

---

### Task 1: Record the decision and commit the plan

**Files:**
- Modify: `docs/DECISIONS.md` (append after `M1-001`)
- Add: `docs/superpowers/plans/2026-08-21-m6-1-visual-analytics.md`, `docs/superpowers/plans/2026-08-21-m6-1-agent-prompt.md` (already present, untracked)

- [ ] **Step 1: Verify the worktree contains only the plan files**

Run: `git status --short`
Expected: only `?? docs/superpowers/` lines. Anything else → stop and report.

- [ ] **Step 2: Append the decision record**

```markdown

## M6.1-001 — Owner-approved visual analytics extension of the M6 app

- **Decision:** Extend the read-only Streamlit app (M6) with seven additional
  views — an offline selected-city reliability map, an SLA curve for
  W = 1..horizon, a `min_clear` threshold-sensitivity analysis, a
  nominal-versus-effective revisit dumbbell, a datatake observation timeline,
  a catalog-versus-AOI quality scatter, and a seasonal monthly comparison —
  organized into Overview, Reliability, and Diagnostics tabs.
- **Spec alternative:** §M6 lists only the survival curve, monthly heatmap,
  summary numbers, and the SLA answer. M7 (cloud deployment) is untouched.
- **Reason:** Owner request on 2026-08-21 for richer local analysis of the
  full 20-AOI run before any cloud work.
- **Consequence:** No metric definition changes; every number still comes
  from `metrics.py`. New pure preparation code lives in
  `src/open_revisit/app_analytics.py` and `src/open_revisit/app_charts.py`.
  AOI metadata is read from `data/aois.parquet`; the map uses the committed
  `assets/natural_earth_europe.geojson` (override with `OPEN_REVISIT_BASEMAP`)
  and never a tile or web service. Threshold sensitivity is calculated on
  demand and cached so the default cold render stays under three seconds.
  `AppConfig` gains no fields, so `config_hash` values are unchanged.
```

- [ ] **Step 3: Commit**

```bash
git add docs/DECISIONS.md docs/superpowers/plans/
git commit -m "docs: record M6.1 visual analytics decision and plan"
```

---

### Task 2: AOI metadata and offline basemap loaders

**Files:**
- Modify: `src/open_revisit/app_data.py`
- Test: `tests/test_app_data.py`

**Interfaces:**
- Produces: `AOI_COLUMNS = ["aoi_id", "name", "country", "lat", "lon"]`; `file_signature(path, *, description, hint) -> tuple[int, int]`; `aoi_signature(path) -> tuple[int, int]`; `basemap_signature(path) -> tuple[int, int]`; `load_aois(path: Path) -> pd.DataFrame` (sorted by `aoi_id`, columns `AOI_COLUMNS`); `load_basemap(path: Path) -> dict[str, Any]` (GeoJSON FeatureCollection); `validate_selection(...)` (public rename of `_validate_selection`, same signature).
- `source_signature` keeps its exact error text (`"Observation data is not available at {path}. Place the pipeline's observations.parquet in the configured data directory."`).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_app_data.py`)

```python
from open_revisit.app_data import load_aois, load_basemap  # add to the import block

REPO_BASEMAP = Path("assets/natural_earth_europe.geojson")


def _aois() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"aoi_id": "beta", "name": "Beta", "country": "BB", "lat": 60.5, "lon": 10.25,
             "utm_epsg": 32632, "area_km2": 400.0, "geometry": b"\x00"},
            {"aoi_id": "alpha", "name": "Alpha", "country": "AA", "lat": 52.5, "lon": 13.4,
             "utm_epsg": 32633, "area_km2": 400.0, "geometry": b"\x00"},
        ]
    )


def test_load_aois_reads_centroids_sorted_and_validates(tmp_path: Path) -> None:
    path = tmp_path / "aois.parquet"
    _aois().to_parquet(path, index=False)
    aois = load_aois(path)
    assert list(aois.columns) == ["aoi_id", "name", "country", "lat", "lon"]
    assert aois["aoi_id"].tolist() == ["alpha", "beta"]
    assert aois.set_index("aoi_id").loc["alpha", "lat"] == pytest.approx(52.5)

    with pytest.raises(AppDataError, match="AOI metadata is not available"):
        load_aois(tmp_path / "absent.parquet")

    duplicated = pd.concat([_aois(), _aois().iloc[[0]]], ignore_index=True)
    duplicated.to_parquet(path, index=False)
    with pytest.raises(AppDataError, match="duplicate aoi_id"):
        load_aois(path)

    bad = _aois()
    bad.loc[0, "lat"] = 95.0
    bad.to_parquet(path, index=False)
    with pytest.raises(AppDataError, match="invalid lat/lon"):
        load_aois(path)


def test_load_basemap_requires_local_feature_collection(tmp_path: Path) -> None:
    basemap = load_basemap(REPO_BASEMAP)
    assert basemap["type"] == "FeatureCollection"
    assert len(basemap["features"]) > 0

    with pytest.raises(AppDataError, match="Offline basemap is not available"):
        load_basemap(tmp_path / "absent.geojson")

    not_collection = tmp_path / "bad.geojson"
    not_collection.write_text('{"type": "Feature"}', encoding="utf-8")
    with pytest.raises(AppDataError, match="not a GeoJSON FeatureCollection"):
        load_basemap(not_collection)

    not_json = tmp_path / "broken.geojson"
    not_json.write_text("{", encoding="utf-8")
    with pytest.raises(AppDataError, match="Could not read offline basemap"):
        load_basemap(not_json)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_app_data.py -k "aois or basemap" --no-cov -q`
Expected: ImportError on `load_aois`.

- [ ] **Step 3: Implement in `app_data.py`**

Add imports `import json` and `from typing import Any`. Replace `source_signature` and add loaders:

```python
AOI_COLUMNS = ["aoi_id", "name", "country", "lat", "lon"]


def file_signature(path: Path, *, description: str, hint: str) -> tuple[int, int]:
    """Return a (size, mtime_ns) cache signature or raise a helpful setup error."""
    try:
        stat = path.stat()
    except FileNotFoundError as exc:
        raise AppDataError(f"{description} is not available at {path}. {hint}") from exc
    if not path.is_file():
        raise AppDataError(f"{description} path is not a file: {path}")
    return stat.st_size, stat.st_mtime_ns


def source_signature(path: Path) -> tuple[int, int]:
    """Return a cache signature from the observation Parquet's size and mtime."""
    return file_signature(
        path,
        description="Observation data",
        hint=(
            "Place the pipeline's observations.parquet in the configured "
            "data directory."
        ),
    )


def aoi_signature(path: Path) -> tuple[int, int]:
    """Return a cache signature from the AOI Parquet's size and mtime."""
    return file_signature(
        path,
        description="AOI metadata",
        hint=(
            "Place the pipeline's aois.parquet in the configured data directory "
            "(open-revisit aois build)."
        ),
    )


def basemap_signature(path: Path) -> tuple[int, int]:
    """Return a cache signature from the offline GeoJSON's size and mtime."""
    return file_signature(
        path,
        description="Offline basemap",
        hint=(
            "Use the committed assets/natural_earth_europe.geojson or point "
            "OPEN_REVISIT_BASEMAP at a local GeoJSON FeatureCollection."
        ),
    )


def load_aois(path: Path) -> pd.DataFrame:
    """Load AOI centroid metadata (WGS84 degrees) without decoding geometry."""
    aoi_signature(path)
    try:
        aois = pd.read_parquet(path, columns=AOI_COLUMNS)
    except Exception as exc:
        raise AppDataError(f"Could not read AOI metadata at {path}: {exc}") from exc
    aois = aois.copy()
    aois["aoi_id"] = aois["aoi_id"].astype(str)
    if aois["aoi_id"].duplicated().any():
        raise AppDataError("AOI metadata contains duplicate aoi_id values.")
    lat = pd.to_numeric(aois["lat"], errors="coerce")
    lon = pd.to_numeric(aois["lon"], errors="coerce")
    if not (lat.between(-90.0, 90.0).all() and lon.between(-180.0, 180.0).all()):
        raise AppDataError("AOI metadata contains invalid lat/lon values.")
    aois["lat"] = lat.astype(float)
    aois["lon"] = lon.astype(float)
    return aois.sort_values("aoi_id", kind="stable").reset_index(drop=True)


def load_basemap(path: Path) -> dict[str, Any]:
    """Load a local GeoJSON FeatureCollection. No URL or network is ever used."""
    basemap_signature(path)
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AppDataError(
            f"Could not read offline basemap at {path}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise AppDataError(
            f"Offline basemap at {path} is not a GeoJSON FeatureCollection."
        )
    if raw.get("type") != "FeatureCollection" or not isinstance(
        raw.get("features"), list
    ):
        raise AppDataError(
            f"Offline basemap at {path} is not a GeoJSON FeatureCollection."
        )
    return raw  # narrowed to dict by the isinstance check above
```

Rename `_validate_selection` → `validate_selection` (definition and its one call in `build_app_metrics`).

- [ ] **Step 4: Run tests and the gate**

Run: `uv run pytest tests/test_app_data.py -q && uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src/`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/open_revisit/app_data.py tests/test_app_data.py
git commit -m "feat: load AOI metadata and offline basemap for the app"
```

---

### Task 3: Map points, SLA curve, and dumbbell preparation

**Files:**
- Create: `src/open_revisit/app_analytics.py`
- Test: `tests/test_app_analytics.py`

**Interfaces:**
- Consumes: `AOI_COLUMNS`, `AppDataError`, `select_observations`, `validate_selection` from `app_data`; `service_level_success`, `gap_table` from `metrics`.
- Produces:
  - `MapMetric = Literal["p_within_7d", "sla_success", "usable_rate", "longest_outage_days"]`, `MAP_METRICS: tuple[MapMetric, ...]`, `MAP_METRIC_TITLES: dict[MapMetric, str]`, `OUTAGE_THRESHOLD_DAYS = 30.0`, `MONTH_NAMES`, `TIMELINE_STATUSES = ("usable", "unusable", "incomplete")`, `DEFAULT_THRESHOLD_STEP = 0.05`, `DEFAULT_CATALOG_THRESHOLD = 20`.
  - `@dataclass(frozen=True, slots=True) MapMetricSpec(field: MapMetric, title: str, unit: str, domain: tuple[float, float], value_format: str, lower_is_better: bool)`
  - `map_metric_spec(metric, *, every_days: int, max_outage_days: float) -> MapMetricSpec`
  - `map_points(summary, aois, *, metric) -> DataFrame[aoi_id, name, country, lat, lon, value]`
  - `sla_curve(waits, *, horizon_days) -> DataFrame[aoi_id, every_days, sla_success]`
  - `revisit_dumbbell(summary) -> DataFrame[aoi_id, nominal_median_gap_days, effective_median_gap_days, delta_days]` sorted by effective asc, aoi_id.

- [ ] **Step 1: Write the failing tests** (`tests/test_app_analytics.py`)

```python
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


def _row(aoi_id, datatake_id, observed_at, *, clear, covered=1.0, complete=True,
         persisted_usable=False, catalog_cloud_cover=10.0):
    return {
        "aoi_id": aoi_id, "datatake_id": datatake_id, "config_hash": CONFIG_HASH,
        "observed_at": pd.Timestamp(observed_at), "catalog_cloud_cover": catalog_cloud_cover,
        "covered_fraction": covered, "clear_fraction": clear,
        "usable": persisted_usable, "complete": complete,
    }


def _observations() -> pd.DataFrame:
    return pd.DataFrame(
        [
            _row("alpha", "a1", "2024-01-01T12:00:00Z", clear=0.85, catalog_cloud_cover=5.0),
            _row("alpha", "low-coverage", "2024-01-05T06:00:00Z", clear=0.99, covered=0.94,
                 persisted_usable=True, catalog_cloud_cover=2.0),
            _row("alpha", "a2", "2024-01-20T18:00:00Z", clear=0.90, catalog_cloud_cover=15.0),
            _row("alpha", "incomplete", "2024-02-10T03:00:00Z", clear=1.0, complete=False,
                 persisted_usable=True, catalog_cloud_cover=0.0),
            _row("alpha", "a3", "2024-03-05T10:30:00Z", clear=0.60, catalog_cloud_cover=55.0),
            _row("beta", "b1", "2024-01-03T05:30:00Z", clear=0.70, catalog_cloud_cover=30.0),
            _row("beta", "b2", "2024-02-02T17:45:00Z", clear=0.82, catalog_cloud_cover=12.0),
        ]
    )


def _aois() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"aoi_id": "alpha", "name": "Alpha", "country": "AA", "lat": 52.5, "lon": 13.4},
            {"aoi_id": "beta", "name": "Beta", "country": "BB", "lat": 69.6, "lon": 18.9},
            {"aoi_id": "gamma", "name": "Gamma", "country": "GG", "lat": 41.0, "lon": 2.0},
        ]
    )


def _metrics(aoi_ids=("alpha", "beta"), *, min_clear=0.80, every_days=7, horizon_days=60):
    return build_app_metrics(
        _observations(), aoi_ids=aoi_ids, start=START, end=END, min_clear=min_clear,
        min_coverage=0.95, horizon_days=horizon_days, every_days=every_days,
    )


def test_map_points_contain_exactly_selected_aois_and_switch_metric_values() -> None:
    metrics = _metrics()
    within = map_points(metrics.summary, _aois(), metric="p_within_7d")
    outage = map_points(metrics.summary, _aois(), metric="longest_outage_days")
    assert within["aoi_id"].tolist() == ["alpha", "beta"]
    assert outage["aoi_id"].tolist() == within["aoi_id"].tolist()
    assert list(within.columns) == ["aoi_id", "name", "country", "lat", "lon", "value"]
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
    assert map_metric_spec("p_within_7d", every_days=7, max_outage_days=5.0).domain == (0.0, 1.0)
    sla = map_metric_spec("sla_success", every_days=5, max_outage_days=5.0)
    assert "5" in sla.title and sla.domain == (0.0, 1.0) and not sla.lower_is_better
    outage = map_metric_spec("longest_outage_days", every_days=7, max_outage_days=12.5)
    assert outage.domain == (0.0, 30.0) and outage.unit == "days" and outage.lower_is_better
    assert map_metric_spec("longest_outage_days", every_days=7, max_outage_days=44.0).domain == (0.0, 44.0)


def test_sla_curve_emits_every_w_and_keeps_strict_boundary() -> None:
    metrics = _metrics(horizon_days=60)
    curve = sla_curve(metrics.waits, horizon_days=60)
    assert curve.groupby("aoi_id")["every_days"].apply(list).to_dict() == {
        "alpha": list(range(1, 61)), "beta": list(range(1, 61)),
    }
    assert curve["sla_success"].between(0.0, 1.0).all()
    alpha_waits = metrics.waits.loc[metrics.waits["aoi_id"] == "alpha"]
    for every_days in (1, 7, 30, 60):
        expected = service_level_success(alpha_waits, every_days)
        actual = curve.loc[
            (curve["aoi_id"] == "alpha") & (curve["every_days"] == every_days), "sla_success"
        ].iloc[0]
        assert actual == expected
    assert curve.groupby("aoi_id")["sla_success"].apply(lambda s: s.is_monotonic_increasing).all()

    one = build_app_metrics(
        pd.DataFrame([_row("alpha", "at-w", "2024-01-02T00:00:00Z", clear=1.0)]),
        aoi_ids=("alpha",), start=date(2024, 1, 1), end=date(2024, 3, 1),
        min_clear=0.8, min_coverage=0.95, horizon_days=60, every_days=1,
    )
    strict = sla_curve(one.waits, horizon_days=60).set_index("every_days")["sla_success"]
    assert strict.loc[1] == 0.0 and strict.loc[2] == 1.0


def test_revisit_dumbbell_matches_gap_table_and_keeps_fractions() -> None:
    metrics = _metrics()
    dumbbell = revisit_dumbbell(metrics.summary)
    assert list(dumbbell.columns) == [
        "aoi_id", "nominal_median_gap_days", "effective_median_gap_days", "delta_days",
    ]
    selected = select_observations(
        _observations(), aoi_ids=("alpha", "beta"), start=START, end=END,
        min_clear=0.80, min_coverage=0.95,
    )
    for aoi_id in ("alpha", "beta"):
        complete = selected.loc[(selected["aoi_id"] == aoi_id) & selected["complete"]]
        nominal = gap_table(pd.Series(complete["observed_at"]), kind="nominal")["gap_days"]
        effective = gap_table(
            pd.Series(complete.loc[complete["usable"], "observed_at"]), kind="effective"
        )["gap_days"]
        row = dumbbell.set_index("aoi_id").loc[aoi_id]
        assert row["nominal_median_gap_days"] == pytest.approx(float(nominal.median()))
        assert row["effective_median_gap_days"] == pytest.approx(float(effective.median()))
        assert row["delta_days"] == pytest.approx(
            float(effective.median()) - float(nominal.median())
        )
    alpha = dumbbell.set_index("aoi_id").loc["alpha"]
    assert alpha["effective_median_gap_days"] == pytest.approx(19.25)
    assert alpha["nominal_median_gap_days"] != round(alpha["nominal_median_gap_days"])
    assert dumbbell["effective_median_gap_days"].is_monotonic_increasing

    empty = build_app_metrics(
        pd.DataFrame([_row("alpha", "only", "2024-01-05T12:00:00Z", clear=1.0, complete=False)]),
        aoi_ids=("alpha",), start=START, end=END, min_clear=0.8, min_coverage=0.95,
        horizon_days=60, every_days=7,
    )
    zero = revisit_dumbbell(empty.summary).iloc[0]
    assert zero["nominal_median_gap_days"] == 0.0 and zero["effective_median_gap_days"] == 0.0
    assert np.isfinite(revisit_dumbbell(empty.summary).select_dtypes("number").to_numpy()).all()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_app_analytics.py --no-cov -q`
Expected: ModuleNotFoundError `open_revisit.app_analytics`.

- [ ] **Step 3: Create `src/open_revisit/app_analytics.py`**

```python
"""Pure preparation functions for the M6.1 visual analytics views.

Every service number is produced by :mod:`open_revisit.metrics`; this module
only selects, joins, and reshapes frames for display.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from open_revisit.app_data import AOI_COLUMNS, AppDataError
from open_revisit.metrics import service_level_success

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
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
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
        return MapMetricSpec(metric, "P(wait ≤ 7 days)", "probability", (0.0, 1.0), ".1%", False)
    if metric == "sla_success":
        return MapMetricSpec(
            metric, f"SLA success (wait < {every_days} days)", "probability",
            (0.0, 1.0), ".1%", False,
        )
    if metric == "usable_rate":
        return MapMetricSpec(
            metric, "Usable rate", "fraction of complete observations",
            (0.0, 1.0), ".1%", False,
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
    missing = sorted(set(selected["aoi_id"].astype(str)) - set(aois["aoi_id"].astype(str)))
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
    """Return P(wait < W) for W = 1..horizon per AOI. Denominator: evaluated start days."""
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
    frame["delta_days"] = frame["effective_median_gap_days"] - frame["nominal_median_gap_days"]
    return frame.sort_values(
        ["effective_median_gap_days", "aoi_id"], ascending=[True, True], kind="stable"
    ).reset_index(drop=True)
```

(Only the imports used so far are listed; Tasks 4 and 5 extend the import block so `ruff check` stays green at every commit.)

- [ ] **Step 4: Run tests and gate**

Run: `uv run pytest tests/test_app_analytics.py tests/test_app_data.py -q && uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src/`
Expected: pass (run `uv run ruff format .` first if formatting differs).

- [ ] **Step 5: Commit**

```bash
git add src/open_revisit/app_analytics.py tests/test_app_analytics.py
git commit -m "feat: add map, SLA curve, and revisit dumbbell preparation"
```

---

### Task 4: Threshold-sensitivity analysis

**Files:**
- Modify: `src/open_revisit/app_analytics.py`
- Test: `tests/test_app_analytics.py`

**Interfaces:**
- Produces: `threshold_grid(min_clear: float, *, step: float = DEFAULT_THRESHOLD_STEP) -> tuple[float, ...]`; `threshold_sensitivity(observations, *, aoi_ids, start, end, min_coverage, thresholds, horizon_days, every_days) -> DataFrame[aoi_id, min_clear, n_observations, n_usable, usable_rate, p_within_7d, sla_success]`.

- [ ] **Step 1: Write the failing tests** (append; add `threshold_grid, threshold_sensitivity` to the import)

```python
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
        observations, aoi_ids=aoi_ids, start=START, end=END, min_coverage=0.95,
        thresholds=threshold_grid(0.80), horizon_days=60, every_days=every_days,
    )


def test_threshold_sensitivity_recomputes_usability_and_is_monotonic() -> None:
    observations = _observations()
    sensitivity = _sensitivity(observations)
    assert list(sensitivity.columns) == [
        "aoi_id", "min_clear", "n_observations", "n_usable", "usable_rate",
        "p_within_7d", "sla_success",
    ]
    assert sensitivity.groupby("aoi_id")["min_clear"].apply(len).to_dict() == {
        "alpha": 21, "beta": 21,
    }
    for column in ("n_usable", "usable_rate", "p_within_7d", "sla_success"):
        assert (
            sensitivity.groupby("aoi_id")[column]
            .apply(lambda s: s.is_monotonic_decreasing)
            .all()
        ), column
    assert sensitivity[["usable_rate", "p_within_7d", "sla_success"]].apply(
        lambda c: c.between(0.0, 1.0).all()
    ).all()
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_app_analytics.py -k threshold --no-cov -q`
Expected: ImportError.

- [ ] **Step 3: Implement**

Extend the imports: `from datetime import date`; `select_observations, validate_selection` from `open_revisit.app_data`; `survival_curve, wait_daily, within_probability` from `open_revisit.metrics`. Then add:

```python
def threshold_grid(
    min_clear: float, *, step: float = DEFAULT_THRESHOLD_STEP
) -> tuple[float, ...]:
    """Return a deterministic min_clear grid over [0, 1] including the current value."""
    if not 0.0 <= min_clear <= 1.0:
        raise AppDataError("min_clear must be between 0 and 1.")
    count = int(round(1.0 / step))
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
            observations, aoi_ids=aoi_ids, start=start, end=end, min_clear=min_clear,
            min_coverage=min_coverage, horizon_days=horizon_days, every_days=every_days,
        )
        selected = select_observations(
            observations, aoi_ids=aoi_ids, start=start, end=end,
            min_clear=min_clear, min_coverage=min_coverage,
        )
        for aoi_id in aoi_ids:
            complete = selected.loc[
                (selected["aoi_id"] == aoi_id) & selected["complete"].astype(bool)
            ]
            usable = complete.loc[complete["usable"].astype(bool)]
            waits = wait_daily(
                pd.Series(usable["observed_at"]),
                start=pd.Timestamp(start), end=pd.Timestamp(end), horizon_days=horizon_days,
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
                    "usable_rate": 0.0 if n_observations == 0 else n_usable / n_observations,
                    "p_within_7d": within_probability(survival, 7),
                    "sla_success": service_level_success(waits, every_days),
                }
            )
    return pd.DataFrame(
        rows,
        columns=["aoi_id", "min_clear", "n_observations", "n_usable",
                 "usable_rate", "p_within_7d", "sla_success"],
    )
```

- [ ] **Step 4: Run tests and gate** — `uv run pytest tests/test_app_analytics.py -q && uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src/`

- [ ] **Step 5: Commit**

```bash
git add src/open_revisit/app_analytics.py tests/test_app_analytics.py
git commit -m "feat: add min_clear threshold sensitivity analysis"
```

---

### Task 5: Timeline, quality scatter, and seasonal preparation

**Files:**
- Modify: `src/open_revisit/app_analytics.py`
- Test: `tests/test_app_analytics.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True, slots=True) TimelineTables(marks: pd.DataFrame, outages: pd.DataFrame)`; `observation_timeline(observations, *, aoi_id) -> TimelineTables` (marks columns `aoi_id, datatake_id, config_hash, observed_at, status, clear_fraction, covered_fraction, catalog_cloud_cover`; outages columns `aoi_id, gap_start, gap_end, gap_days` for effective gaps > 30); `quality_scatter(observations) -> DataFrame[aoi_id, datatake_id, observed_at, catalog_cloud_cover, clear_fraction, covered_fraction, status]` (complete only); `catalog_threshold_counts(observations, *, catalog_threshold: int) -> dict[str, int | float]` (keys `tp, fp, fn, tn, precision, recall` from the pooled `ALL` row); `seasonal_comparison(monthly) -> DataFrame[aoi_id, month, month_name, p_within_7d, n_days]`.

- [ ] **Step 1: Write the failing tests** (append; extend the import with `catalog_threshold_counts, observation_timeline, quality_scatter, seasonal_comparison`; also `from open_revisit.metrics import catalog_filter_evaluation`)

```python
def test_observation_timeline_keeps_datatake_rows_and_marks_long_outages() -> None:
    metrics = _metrics()
    timeline = observation_timeline(metrics.observations, aoi_id="alpha")
    marks = timeline.marks
    assert marks["datatake_id"].tolist() == ["a1", "low-coverage", "a2", "incomplete", "a3"]
    assert marks[["aoi_id", "datatake_id", "config_hash"]].drop_duplicates().shape[0] == 5
    assert marks.set_index("datatake_id")["status"].to_dict() == {
        "a1": "usable", "low-coverage": "unusable", "a2": "usable",
        "incomplete": "incomplete", "a3": "unusable",
    }
    assert pd.Timestamp(marks.set_index("datatake_id").loc["a1", "observed_at"]).hour == 12
    assert timeline.outages.columns.tolist() == ["aoi_id", "gap_start", "gap_end", "gap_days"]
    assert timeline.outages.empty  # usable gap a1→a2 is 19.25 days

    shifted = pd.concat(
        [_observations(), pd.DataFrame([_row("alpha", "a4", "2024-03-25T12:00:00Z", clear=0.95)])],
        ignore_index=True,
    )
    later = build_app_metrics(
        shifted, aoi_ids=("alpha",), start=START, end=END, min_clear=0.8,
        min_coverage=0.95, horizon_days=60, every_days=7,
    )
    outages = observation_timeline(later.observations, aoi_id="alpha").outages
    assert outages["gap_days"].tolist() == pytest.approx([64.75])  # a2 → a4, incomplete ignored
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
        "aoi_id", "datatake_id", "observed_at", "catalog_cloud_cover",
        "clear_fraction", "covered_fraction", "status",
    ]

    counts = catalog_threshold_counts(metrics.observations, catalog_threshold=20)
    evaluation = catalog_filter_evaluation(metrics.observations)
    pooled = evaluation.loc[(evaluation["aoi_id"] == "ALL") & (evaluation["threshold"] == 20)].iloc[0]
    assert counts == {
        "tp": int(pooled["tp"]), "fp": int(pooled["fp"]), "fn": int(pooled["fn"]),
        "tn": int(pooled["tn"]), "precision": float(pooled["precision"]),
        "recall": float(pooled["recall"]),
    }
    assert counts["tp"] + counts["fp"] + counts["fn"] + counts["tn"] == 6
    assert catalog_threshold_counts(metrics.observations, catalog_threshold=0)["tp"] == 0
    with pytest.raises(AppDataError, match="multiple of 5"):
        catalog_threshold_counts(metrics.observations, catalog_threshold=17)


def test_seasonal_comparison_emits_twelve_months_per_aoi_with_finite_zeroes() -> None:
    metrics = _metrics()
    seasonal = seasonal_comparison(metrics.monthly)
    assert list(seasonal.columns) == ["aoi_id", "month", "month_name", "p_within_7d", "n_days"]
    assert seasonal.groupby("aoi_id")["month"].apply(list).to_dict() == {
        "alpha": list(range(1, 13)), "beta": list(range(1, 13)),
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
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_app_analytics.py -k "timeline or quality or seasonal" --no-cov -q` → ImportError.

- [ ] **Step 3: Implement**

Extend the imports: `CATALOG_THRESHOLDS, catalog_filter_evaluation, gap_table` from `open_revisit.metrics`. Then add:

```python
@dataclass(frozen=True, slots=True)
class TimelineTables:
    """Datatake-level marks and long effective outages for one AOI."""

    marks: pd.DataFrame
    outages: pd.DataFrame


def observation_timeline(observations: pd.DataFrame, *, aoi_id: str) -> TimelineTables:
    """Return one mark per datatake plus effective gaps > 30 days. Unit: days.

    ``incomplete`` is a diagnostic label only; such rows contribute to no metric,
    including the outage bands, which use the usable timeline via ``gap_table``.
    """
    aoi = observations.loc[observations["aoi_id"] == aoi_id].copy()
    if aoi.empty:
        raise AppDataError(f"AOI {aoi_id!r} has no observations in the selected period.")
    complete = aoi["complete"].astype(bool)
    usable = complete & aoi["usable"].astype(bool)
    status = pd.Series("unusable", index=aoi.index, dtype="object")
    status.loc[usable] = "usable"
    status.loc[~complete] = "incomplete"
    aoi["status"] = status
    marks = (
        aoi[["aoi_id", "datatake_id", "config_hash", "observed_at", "status",
             "clear_fraction", "covered_fraction", "catalog_cloud_cover"]]
        .sort_values(["observed_at", "datatake_id"], kind="stable")
        .reset_index(drop=True)
    )
    gaps = gap_table(pd.Series(aoi.loc[usable, "observed_at"]), kind="effective")
    long = pd.to_numeric(gaps["gap_days"], errors="raise") > OUTAGE_THRESHOLD_DAYS
    outages = gaps.loc[long, ["gap_start", "gap_end", "gap_days"]].copy()
    outages.insert(0, "aoi_id", aoi_id)
    return TimelineTables(marks=marks, outages=outages.reset_index(drop=True))


def quality_scatter(observations: pd.DataFrame) -> pd.DataFrame:
    """Return complete observations for catalog-versus-pixel comparison.

    Unit: catalog percent and AOI fractions. Denominator: complete observations.
    """
    complete = observations.loc[observations["complete"].astype(bool)].copy()
    complete["status"] = complete["usable"].astype(bool).map(
        {True: "usable", False: "unusable"}
    )
    return (
        complete[["aoi_id", "datatake_id", "observed_at", "catalog_cloud_cover",
                  "clear_fraction", "covered_fraction", "status"]]
        .sort_values(["aoi_id", "observed_at", "datatake_id"], kind="stable")
        .reset_index(drop=True)
    )


def catalog_threshold_counts(
    observations: pd.DataFrame, *, catalog_threshold: int
) -> dict[str, int | float]:
    """Return pooled confusion counts at one catalog threshold via the contract function."""
    if catalog_threshold not in CATALOG_THRESHOLDS:
        raise AppDataError("Catalog threshold must be a multiple of 5 between 0 and 100.")
    evaluation = catalog_filter_evaluation(observations)
    row = evaluation.loc[
        (evaluation["aoi_id"] == "ALL") & (evaluation["threshold"] == catalog_threshold)
    ]
    if len(row) != 1:
        raise AppDataError("Catalog filter evaluation must contain one pooled row.")
    record = row.iloc[0]
    return {
        "tp": int(record["tp"]), "fp": int(record["fp"]),
        "fn": int(record["fn"]), "tn": int(record["tn"]),
        "precision": float(record["precision"]), "recall": float(record["recall"]),
    }


def seasonal_comparison(monthly: pd.DataFrame) -> pd.DataFrame:
    """Return monthly P(wait ≤ 7) per AOI with all 12 months. Denominator: t0 days in month."""
    frame = monthly[["aoi_id", "month", "p_within_7d", "n_days"]].copy()
    frame["month"] = frame["month"].astype(int)
    months = frame.groupby("aoi_id")["month"].apply(lambda s: sorted(s.tolist()))
    if not all(value == list(range(1, 13)) for value in months):
        raise AppDataError("Monthly reliability must contain all 12 months per AOI.")
    frame["month_name"] = frame["month"].map(lambda month: MONTH_NAMES[month - 1])
    return (
        frame.sort_values(["aoi_id", "month"], kind="stable")
        .reset_index(drop=True)[["aoi_id", "month", "month_name", "p_within_7d", "n_days"]]
    )
```

- [ ] **Step 4: Run tests and gate** — `uv run pytest tests/test_app_analytics.py -q && uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src/`

- [ ] **Step 5: Commit**

```bash
git add src/open_revisit/app_analytics.py tests/test_app_analytics.py
git commit -m "feat: add timeline, quality scatter, and seasonal preparation"
```

---

### Task 6: Offline Vega-Lite chart specifications

**Files:**
- Create: `src/open_revisit/app_charts.py`
- Test: `tests/test_app_charts.py`

**Interfaces:**
- Consumes: `MapMetricSpec`, `MONTH_NAMES`, `TIMELINE_STATUSES`, `OUTAGE_THRESHOLD_DAYS` from `app_analytics`.
- Produces (all return `dict[str, Any]`, no Streamlit import, no `url` keys, no `http` strings):
  - `map_chart(basemap: dict[str, Any], metric: MapMetricSpec) -> Spec` — layer 0 geoshape with inline `basemap["features"]`, layer 1 circles on `lon`/`lat` coloured by `value`.
  - `sla_curve_chart(*, every_days: int, horizon_days: int) -> Spec` — lines by `aoi_id`, dashed rule + text label at `every_days`.
  - `sensitivity_chart(*, field: str, title: str, min_clear: float) -> Spec` — lines by `aoi_id` over `min_clear`, rule at current `min_clear`.
  - `dumbbell_chart(*, n_rows: int) -> Spec` — rule `x=nominal…`, `x2=effective…`; fold-transformed circle layer with legend labels "Nominal (all complete)" / "Effective (usable)".
  - `timeline_chart(outages: pd.DataFrame) -> Spec` — rect layer with inline outage records (ISO strings) + tick layer by `status`.
  - `quality_scatter_chart(*, min_clear: float, catalog_threshold: int) -> Spec` — points coloured/shaped by `status`, horizontal rule at `min_clear`, vertical rule at `catalog_threshold`.
  - `seasonal_chart() -> Spec` — lines by `aoi_id` over `month_name` sorted by `MONTH_NAMES`.
- Test helper `rule_values(spec, field) -> list` collects `data.values[*][field]` from every layer whose mark is a rule.

- [ ] **Step 1: Write the failing tests** (`tests/test_app_charts.py`)

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from open_revisit.app_analytics import MONTH_NAMES, TIMELINE_STATUSES, map_metric_spec
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
        assert not (isinstance(value, str) and value.lower().startswith(("http://", "https://")))
    json.dumps(spec)  # must be serialisable without pandas objects


def _rule_values(spec: dict[str, Any], field: str) -> list[Any]:
    values = []
    for layer in spec.get("layer", []):
        mark = layer.get("mark")
        mark_type = mark.get("type") if isinstance(mark, dict) else mark
        if mark_type == "rule":
            values.extend(record[field] for record in layer["data"]["values"] if field in record)
    return values


def test_map_chart_is_offline_and_uses_inline_basemap() -> None:
    basemap = json.loads(Path("assets/natural_earth_europe.geojson").read_text(encoding="utf-8"))
    metric = map_metric_spec("longest_outage_days", every_days=7, max_outage_days=12.0)
    spec = map_chart(basemap, metric)
    _assert_offline(spec)
    assert spec["projection"]["type"] == "mercator"
    basemap_layer, points_layer = spec["layer"]
    assert basemap_layer["mark"]["type"] == "geoshape"
    assert basemap_layer["data"]["values"] is basemap["features"]
    assert len(basemap_layer["data"]["values"]) == len(basemap["features"])
    encoding = points_layer["encoding"]
    assert encoding["longitude"]["field"] == "lon" and encoding["latitude"]["field"] == "lat"
    assert encoding["color"]["scale"]["domain"] == [0.0, 30.0]
    assert encoding["color"]["scale"]["reverse"] is True
    assert metric.unit in encoding["color"]["title"]
    tooltip_fields = {item["field"] for item in encoding["tooltip"]}
    assert {"name", "aoi_id", "value"} <= tooltip_fields
    probability = map_chart(basemap, map_metric_spec("p_within_7d", every_days=7, max_outage_days=0.0))
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
    assert points["x"]["field"] == "catalog_cloud_cover" and points["y"]["field"] == "clear_fraction"
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
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_app_charts.py --no-cov -q` → ModuleNotFoundError.

- [ ] **Step 3: Create `src/open_revisit/app_charts.py`**

```python
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


def _reference_rule(field: str, value: float | int, *, axis: str, label: str) -> list[Spec]:
    """Return a dashed rule plus a text label from one inline record."""
    data: Spec = {"values": [{field: value, "label": label}]}
    position: Spec = {axis: {"field": field, "type": "quantitative"}}
    text_position: Spec = dict(position)
    text_position["y" if axis == "x" else "x"] = {"value": 0}
    return [
        {
            "data": data,
            "mark": {"type": "rule", "strokeDash": [6, 4], "color": REFERENCE_COLOR},
            "encoding": position,
        },
        {
            "data": data,
            "mark": {
                "type": "text", "align": "left", "baseline": "top", "dx": 4, "dy": 4,
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
                    "type": "geoshape", "fill": BASEMAP_FILL,
                    "stroke": BASEMAP_STROKE, "strokeWidth": 0.6,
                },
            },
            {
                "mark": {"type": "circle", "size": 170, "stroke": "white", "strokeWidth": 0.8},
                "encoding": {
                    "longitude": {"field": "lon", "type": "quantitative"},
                    "latitude": {"field": "lat", "type": "quantitative"},
                    "color": {
                        "field": "value", "type": "quantitative",
                        "title": f"{metric.title} ({metric.unit})",
                        "scale": {
                            "domain": list(metric.domain), "scheme": "viridis",
                            "reverse": metric.lower_is_better,
                        },
                    },
                    "tooltip": [
                        {"field": "name", "type": "nominal", "title": "City"},
                        {"field": "aoi_id", "type": "nominal", "title": "AOI id"},
                        {"field": "country", "type": "nominal", "title": "Country"},
                        {"field": "value", "type": "quantitative",
                         "format": metric.value_format, "title": metric.title},
                        {"field": "lat", "type": "quantitative", "format": ".3f", "title": "Latitude"},
                        {"field": "lon", "type": "quantitative", "format": ".3f", "title": "Longitude"},
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
                    "x": {"field": "every_days", "type": "quantitative",
                          "title": "Service interval W (days)",
                          "scale": {"domain": [1, horizon_days]}},
                    "y": {"field": "sla_success", "type": "quantitative",
                          "title": "SLA success = P(wait < W)",
                          "scale": {"domain": [0, 1]}, "axis": {"format": ".0%"}},
                    "color": _aoi_color(),
                    "tooltip": [
                        {"field": "aoi_id", "type": "nominal", "title": "AOI"},
                        {"field": "every_days", "type": "quantitative", "title": "W (days)"},
                        {"field": "sla_success", "type": "quantitative", "format": ".1%",
                         "title": "P(wait < W)"},
                    ],
                },
            },
            *_reference_rule("every_days", every_days, axis="x", label=f"Selected W = {every_days}"),
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
                    "x": {"field": "min_clear", "type": "quantitative",
                          "title": "min_clear threshold", "scale": {"domain": [0, 1]}},
                    "y": {"field": field, "type": "quantitative", "title": title,
                          "scale": {"domain": [0, 1]}, "axis": {"format": ".0%"}},
                    "color": _aoi_color(),
                    "tooltip": [
                        {"field": "aoi_id", "type": "nominal", "title": "AOI"},
                        {"field": "min_clear", "type": "quantitative", "format": ".2f"},
                        {"field": field, "type": "quantitative", "format": ".1%", "title": title},
                        {"field": "n_usable", "type": "quantitative", "title": "Usable observations"},
                        {"field": "n_observations", "type": "quantitative",
                         "title": "Complete observations"},
                    ],
                },
            },
            *_reference_rule("min_clear", min_clear, axis="x", label=f"Current min_clear = {min_clear:.2f}"),
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
                    "x": {"field": "nominal_median_gap_days", "type": "quantitative",
                          "title": "Median gap between observations (days; lower is better)",
                          "scale": {"zero": True}},
                    "x2": {"field": "effective_median_gap_days"},
                },
            },
            {
                "transform": [
                    {"fold": ["nominal_median_gap_days", "effective_median_gap_days"],
                     "as": ["kind", "gap_days"]}
                ],
                "mark": {"type": "circle", "size": 110},
                "encoding": {
                    "y": y,
                    "x": {"field": "gap_days", "type": "quantitative"},
                    "color": {
                        "field": "kind", "type": "nominal", "title": "Median gap",
                        "scale": {
                            "domain": ["nominal_median_gap_days", "effective_median_gap_days"],
                            "range": [NOMINAL_COLOR, EFFECTIVE_COLOR],
                        },
                        "legend": {
                            "labelExpr": "datum.label == 'nominal_median_gap_days' "
                            "? 'Nominal (all complete)' : 'Effective (usable)'"
                        },
                    },
                    "tooltip": [
                        {"field": "aoi_id", "type": "nominal", "title": "AOI"},
                        {"field": "kind", "type": "nominal", "title": "Series"},
                        {"field": "gap_days", "type": "quantitative", "format": ".2f",
                         "title": "Median gap (days)"},
                    ],
                },
            },
        ],
    }


def timeline_chart(outages: pd.DataFrame) -> Spec:
    """Ticks per datatake by status with shaded effective outages > 30 days."""
    records = [
        {
            "aoi_id": str(row.aoi_id),
            "gap_start": pd.Timestamp(row.gap_start).isoformat(),
            "gap_end": pd.Timestamp(row.gap_end).isoformat(),
            "gap_days": float(row.gap_days),
        }
        for row in outages.itertuples(index=False)
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
                        {"field": "gap_days", "type": "quantitative", "format": ".1f",
                         "title": f"Effective outage > {OUTAGE_THRESHOLD_DAYS:g} days (days)"},
                        {"field": "gap_start", "type": "temporal", "title": "From (UTC)",
                         "timeUnit": "utcyearmonthdatehoursminutes"},
                        {"field": "gap_end", "type": "temporal", "title": "To (UTC)",
                         "timeUnit": "utcyearmonthdatehoursminutes"},
                    ],
                },
            },
            {
                "mark": {"type": "tick", "thickness": 2, "size": 26},
                "encoding": {
                    "x": {"field": "observed_at", "type": "temporal",
                          "title": "Acquisition time (UTC, fractional days preserved)"},
                    "y": {
                        "field": "status", "type": "nominal", "sort": list(TIMELINE_STATUSES),
                        "title": None,
                        "axis": {"labelExpr": "datum.label == 'incomplete' "
                                 "? 'incomplete (excluded from metrics)' : datum.label"},
                    },
                    "color": {"field": "status", "type": "nominal", "scale": STATUS_SCALE,
                              "legend": None},
                    "tooltip": [
                        {"field": "datatake_id", "type": "nominal", "title": "Datatake"},
                        {"field": "observed_at", "type": "temporal", "title": "Observed (UTC)",
                         "timeUnit": "utcyearmonthdatehoursminutes"},
                        {"field": "status", "type": "nominal", "title": "Status"},
                        {"field": "clear_fraction", "type": "quantitative", "format": ".1%"},
                        {"field": "covered_fraction", "type": "quantitative", "format": ".1%"},
                        {"field": "catalog_cloud_cover", "type": "quantitative", "format": ".1f",
                         "title": "Catalog cloud cover (%)"},
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
                "mark": {"type": "point", "filled": True, "opacity": 0.75, "size": 55},
                "encoding": {
                    "x": {"field": "catalog_cloud_cover", "type": "quantitative",
                          "title": "Catalog cloud cover (%)", "scale": {"domain": [0, 100]}},
                    "y": {"field": "clear_fraction", "type": "quantitative",
                          "title": "AOI clear fraction (SCL, full-AOI denominator)",
                          "scale": {"domain": [0, 1]}, "axis": {"format": ".0%"}},
                    "color": {"field": "status", "type": "nominal", "title": "Pixel-derived",
                              "scale": {"domain": ["usable", "unusable"],
                                        "range": [USABLE_COLOR, UNUSABLE_COLOR]}},
                    "shape": {"field": "status", "type": "nominal", "title": "Pixel-derived",
                              "scale": {"domain": ["usable", "unusable"],
                                        "range": ["circle", "triangle-up"]}},
                    "tooltip": [
                        {"field": "aoi_id", "type": "nominal", "title": "AOI"},
                        {"field": "datatake_id", "type": "nominal", "title": "Datatake"},
                        {"field": "observed_at", "type": "temporal", "title": "Observed (UTC)",
                         "timeUnit": "utcyearmonthdatehoursminutes"},
                        {"field": "catalog_cloud_cover", "type": "quantitative", "format": ".1f",
                         "title": "Catalog cloud cover (%)"},
                        {"field": "clear_fraction", "type": "quantitative", "format": ".1%"},
                        {"field": "covered_fraction", "type": "quantitative", "format": ".1%"},
                        {"field": "status", "type": "nominal", "title": "Status"},
                    ],
                },
            },
            *_reference_rule("clear_fraction", min_clear, axis="y",
                             label=f"min_clear = {min_clear:.2f}"),
            *_reference_rule("catalog_cloud_cover", catalog_threshold, axis="x",
                             label=f"catalog threshold = {catalog_threshold}%"),
        ],
    }


def seasonal_chart() -> Spec:
    """Monthly P(wait ≤ 7) lines per AOI across all twelve start months."""
    return {
        "height": 320,
        "mark": {"type": "line", "point": True},
        "encoding": {
            "x": {"field": "month_name", "type": "ordinal", "sort": list(MONTH_NAMES),
                  "title": "Start month (month of t0)"},
            "y": {"field": "p_within_7d", "type": "quantitative", "title": "P(wait ≤ 7 days)",
                  "scale": {"domain": [0, 1]}, "axis": {"format": ".0%"}},
            "color": _aoi_color(),
            "tooltip": [
                {"field": "aoi_id", "type": "nominal", "title": "AOI"},
                {"field": "month_name", "type": "ordinal", "title": "Month"},
                {"field": "p_within_7d", "type": "quantitative", "format": ".1%",
                 "title": "P(within 7d)"},
                {"field": "n_days", "type": "quantitative", "title": "Start-day denominator"},
            ],
        },
    }
```

Note the `_reference_rule` text layer for `axis="y"` sets `"x": {"value": 0}` so the label sits at the left edge of the horizontal line; for `axis="x"` it sets `"y": {"value": 0}` (top). If the text overlaps data in browser QA, adjust `dx`/`dy` only.

- [ ] **Step 4: Run tests and gate** — `uv run pytest tests/test_app_charts.py -q && uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src/`

- [ ] **Step 5: Commit**

```bash
git add src/open_revisit/app_charts.py tests/test_app_charts.py
git commit -m "feat: add offline Vega-Lite chart specifications"
```

---

### Task 7: Compose the tabbed app and cover it with AppTest

**Files:**
- Modify: `app/streamlit_app.py`
- Modify: `tests/test_streamlit_app.py`

**Interfaces:**
- Consumes everything produced in Tasks 2–6.
- Produces: env var `OPEN_REVISIT_BASEMAP` (default `assets/natural_earth_europe.geojson`); widget keys `aoi_ids`, `period`, `every_days` (existing), `map_metric`, `timeline_aoi`, `catalog_threshold`, `sensitivity_enabled`; tabs `Overview`, `Reliability`, `Diagnostics`; subheaders (in order) "AOI summary and SLA", "Selected-city reliability map", "Nominal versus effective revisit", "Wait-time survival", "SLA curve across service intervals", "Monthly reliability heatmap", "Seasonal comparison", "min_clear threshold sensitivity", "Observation timeline", "Catalog versus AOI quality".
- Caption order is preserved: `app.caption[0]` = source caption, `app.caption[1]` = summary caption (existing test depends on it).

- [ ] **Step 1: Update `tests/test_streamlit_app.py`** (failing first)

Replace the fixture so it also writes `aois.parquet` and adds one unusable and one incomplete row, then replace/add tests:

```python
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
        "start": "2024-01-01", "end": "2024-03-31", "aois": ["alpha", "beta"],
        "data_dir": str(data_dir), "horizon_days": 60,
        "thresholds": {"min_clear": 0.8, "min_coverage": 0.95},
    }
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    config = load_config(config_path)
    rows = []
    for aoi_id, clear_values in {"alpha": (0.82, 0.92), "beta": (0.75, 0.85)}.items():
        for index, clear in enumerate(clear_values):
            rows.append(
                {
                    "aoi_id": aoi_id, "datatake_id": f"{aoi_id}-{index}",
                    "config_hash": config.config_hash(),
                    "observed_at": pd.Timestamp("2024-01-05T12:30:00Z")
                    + pd.Timedelta(days=index * 20),
                    "catalog_cloud_cover": 10.0 + 30.0 * index, "covered_fraction": 1.0,
                    "clear_fraction": clear, "usable": False, "complete": True,
                }
            )
    rows.append(
        {
            "aoi_id": "alpha", "datatake_id": "alpha-incomplete",
            "config_hash": config.config_hash(),
            "observed_at": pd.Timestamp("2024-02-20T09:00:00Z"),
            "catalog_cloud_cover": 0.0, "covered_fraction": 1.0, "clear_fraction": 1.0,
            "usable": True, "complete": False,
        }
    )
    pd.DataFrame(rows).to_parquet(data_dir / "observations.parquet", index=False)
    if with_aois:
        pd.DataFrame(
            [
                {"aoi_id": "alpha", "name": "Alpha", "country": "AA", "lat": 52.5, "lon": 13.4,
                 "utm_epsg": 32633, "area_km2": 400.0, "geometry": b"\x00"},
                {"aoi_id": "beta", "name": "Beta", "country": "BB", "lat": 69.6, "lon": 18.9,
                 "utm_epsg": 32634, "area_km2": 400.0, "geometry": b"\x00"},
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
    assert [slider.label for slider in app.slider] == [
        "min_clear", "Service interval W (days)", "Catalog cloud-cover threshold (%)",
    ]
    assert [tab.label for tab in app.tabs] == ["Overview", "Reliability", "Diagnostics"]
    assert len(app.dataframe) == 1
    assert len(_charts(app)) == 8  # survival, heatmap + map, dumbbell, SLA, seasonal, timeline, scatter
    assert app.toggle(key="sensitivity_enabled").value is False
    initial_summary = app.dataframe[0].value
    assert initial_summary["Usable observations"].tolist() == [2, 1]
    assert initial_summary["Complete observations"].tolist() == [2, 2]

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
    assert "Selected W = 5" in json.dumps(_charts(app))
    assert "min_clear = 0.90" in json.dumps(_charts(app))


def test_map_is_offline_and_metric_control_changes_values(tmp_path: Path, monkeypatch) -> None:
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
    assert "Longest effective outage" in caption and "days" in caption and "lower is better" in caption
    assert '"reverse": true' in [spec for spec in _charts(app) if "geoshape" in spec][0]


def test_diagnostics_controls_update_timeline_quality_and_sensitivity(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPEN_REVISIT_CONFIG", str(_app_fixture(tmp_path)))
    app = AppTest.from_file(APP).run(timeout=15)
    assert not app.exception
    assert app.selectbox(key="timeline_aoi").value == "alpha"
    timeline = _caption_with(app, "Timeline for")
    assert "alpha" in timeline and "3 datatakes" in timeline and "1 incomplete" in timeline

    app.selectbox(key="timeline_aoi").set_value("beta").run(timeout=15)
    assert not app.exception
    timeline = _caption_with(app, "Timeline for")
    assert "beta" in timeline and "2 datatakes" in timeline and "0 incomplete" in timeline

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
            {"start": "2024-01-01", "end": "2024-03-31", "aois": ["alpha"],
             "data_dir": str(tmp_path / "absent")}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPEN_REVISIT_CONFIG", str(config_path))
    app = AppTest.from_file(APP).run(timeout=15)

    assert not app.exception
    assert len(app.error) == 1
    assert "Setup required" in app.error[0].value
    assert "observations.parquet" in app.error[0].value


def test_missing_aois_or_basemap_degrades_to_map_warning(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPEN_REVISIT_CONFIG", str(_app_fixture(tmp_path, with_aois=False)))
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
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_streamlit_app.py --no-cov -q` → failures on slider labels/tabs/keys.

- [ ] **Step 3: Rewrite `app/streamlit_app.py`**

Keep `_load_cached`, `_metrics_cached`, `_load_context`, `_period_value`, `_render_survival`, `_render_heatmap`, `_render_summary` exactly as they are. Replace the imports and everything from `_render_summary` down with:

```python
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
def _load_aois_cached(path_text: str, source_size: int, source_mtime_ns: int) -> pd.DataFrame:
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
        observations, aoi_ids=aoi_ids, start=start, end=end, min_coverage=min_coverage,
        thresholds=thresholds, horizon_days=horizon_days, every_days=every_days,
    )


def _map_inputs(config: AppConfig) -> tuple[pd.DataFrame, dict[str, Any]]:
    aoi_path = config.data_dir / "aois.parquet"
    basemap_path = Path(os.environ.get(BASEMAP_ENV, str(DEFAULT_BASEMAP)))
    size, mtime_ns = aoi_signature(aoi_path)
    aois = _load_aois_cached(str(aoi_path), size, mtime_ns)
    size, mtime_ns = basemap_signature(basemap_path)
    basemap = _load_basemap_cached(str(basemap_path), size, mtime_ns)
    return aois, basemap


def _render_map(config: AppConfig, metrics: AppMetricTables, selection: Selection) -> None:
    st.subheader("Selected-city reliability map")
    metric = st.selectbox(
        "Colour markers by", MAP_METRICS,
        format_func=lambda value: MAP_METRIC_TITLES[value], key="map_metric",
    )
    try:
        aois, basemap = _map_inputs(config)
        points = map_points(metrics.summary, aois, metric=metric)
    except AppDataError as exc:
        st.warning(f"Map unavailable: {exc}")
        return
    spec = map_metric_spec(
        metric, every_days=selection.every_days,
        max_outage_days=float(metrics.summary["longest_outage_days"].max()),
    )
    better = "lower is better" if spec.lower_is_better else "higher is better"
    st.caption(
        f"{spec.title}; unit: {spec.unit}; colour domain {spec.domain[0]:g}–"
        f"{spec.domain[1]:g} ({better}). Thresholds: min_clear={selection.min_clear:.2f}, "
        f"min_coverage={selection.min_coverage:.2f}, W={selection.every_days} days. "
        "Offline Natural Earth outline from the committed asset; no tiles or web services."
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
    st.vega_lite_chart(dumbbell, dumbbell_chart(n_rows=len(dumbbell)), use_container_width=True)


def _render_sla_curve(metrics: AppMetricTables, selection: Selection) -> None:
    st.subheader("SLA curve across service intervals")
    st.caption(
        f"P(wait_days < W) for every W from 1 through {selection.horizon_days} days "
        f"(strict boundary); the dashed line marks the selected W={selection.every_days}. "
        "Denominator: all evaluated daily starts."
    )
    curve = sla_curve(metrics.waits, horizon_days=selection.horizon_days)
    st.vega_lite_chart(
        curve,
        sla_curve_chart(every_days=selection.every_days, horizon_days=selection.horizon_days),
        use_container_width=True,
    )


def _render_seasonal(metrics: AppMetricTables) -> None:
    st.subheader("Seasonal comparison")
    st.caption(
        "Monthly P(wait_days ≤ 7) per AOI; month is the month of t0, all twelve "
        "months are shown, and months without evaluated start days display 0.0."
    )
    st.vega_lite_chart(seasonal_comparison(metrics.monthly), seasonal_chart(), use_container_width=True)


def _render_sensitivity(
    observations: pd.DataFrame, selection: Selection
) -> None:
    st.subheader("min_clear threshold sensitivity")
    st.caption(
        "Recomputes usable = complete AND covered_fraction ≥ min_coverage AND "
        "clear_fraction ≥ min_clear for each grid value (0.00–1.00 in 0.05 steps "
        "plus the current slider value); persisted usable flags are ignored and "
        f"min_coverage={selection.min_coverage:.2f} stays enforced. Calculated on "
        "demand and cached."
    )
    if not st.toggle("Compute min_clear sensitivity", key="sensitivity_enabled"):
        st.info("Enable to calculate the grid for the selected AOIs (about 0.13 s per "
                "threshold for 20 AOIs).")
        return
    thresholds = threshold_grid(selection.min_clear)
    try:
        sensitivity = _sensitivity_cached(
            observations, selection.aoi_ids, selection.start, selection.end,
            selection.min_coverage, thresholds, selection.horizon_days, selection.every_days,
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
        f"({int(counts.get('usable', 0))} usable, {int(counts.get('unusable', 0))} unusable, "
        f"{int(counts.get('incomplete', 0))} incomplete and excluded from every metric); "
        f"one mark per (aoi_id, datatake_id, config_hash), never regrouped by date. "
        f"Shaded bands: effective gaps > {OUTAGE_THRESHOLD_DAYS:g} days "
        f"({len(timeline.outages)} outages)."
    )
    st.vega_lite_chart(timeline.marks, timeline_chart(timeline.outages), use_container_width=True)


def _render_quality(metrics: AppMetricTables, selection: Selection) -> None:
    st.subheader("Catalog versus AOI quality")
    catalog_threshold = int(
        st.slider(
            "Catalog cloud-cover threshold (%)", min_value=0, max_value=100,
            value=DEFAULT_CATALOG_THRESHOLD, step=5, key="catalog_threshold",
        )
    )
    scatter = quality_scatter(metrics.observations)
    counts = catalog_threshold_counts(metrics.observations, catalog_threshold=catalog_threshold)
    st.caption(
        "Complete observations only; incomplete datatakes are excluded. Horizontal "
        f"line: min_clear={selection.min_clear:.2f}; vertical line: catalog "
        f"threshold={catalog_threshold}%. Pooled over selected AOIs at that threshold: "
        f"precision {counts['precision']:.1%}, recall {counts['recall']:.1%} "
        f"(TP {counts['tp']}, FP {counts['fp']}, FN {counts['fn']}, TN {counts['tn']}). "
        "Catalog cloud cover is scene-level metadata and SCL is a per-pixel "
        "classifier; both are imperfect signals."
    )
    st.vega_lite_chart(
        scatter,
        quality_scatter_chart(min_clear=selection.min_clear, catalog_threshold=catalog_threshold),
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

    # ... sidebar block unchanged ...

    period = _period_value(period_raw)
    if period is None:
        st.warning("Select both a start date and an end date.")
        st.stop()
    start, end = period
    selection = Selection(
        aoi_ids=selected_aois, start=start, end=end, min_clear=float(min_clear),
        min_coverage=float(config.thresholds.min_coverage),
        horizon_days=config.horizon_days, every_days=int(every_days),
    )
    try:
        metrics = _metrics_cached(
            observations, selection.aoi_ids, selection.start, selection.end,
            selection.min_clear, selection.min_coverage, selection.horizon_days,
            selection.every_days,
        )
    except AppDataError as exc:
        st.warning(str(exc))
        st.stop()

    st.caption(  # unchanged source caption
        f"Source: {observation_path} · config {config.config_hash()} · "
        f"period {start.isoformat()} through {end.isoformat()} · "
        f"min_clear={min_clear:.2f} · min_coverage="
        f"{config.thresholds.min_coverage:.2f} · horizon={config.horizon_days} days · "
        "observations keyed by AOI + s2:datatake_id."
    )
    overview, reliability, diagnostics = st.tabs(["Overview", "Reliability", "Diagnostics"])
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
```

- [ ] **Step 4: Run the app tests, then the full gate**

Run: `uv run pytest tests/test_streamlit_app.py -q`, then `make check`.
Expected: all pass, coverage ≥ 85%. If a timestamp renders shifted in the browser later, add `"scale": {"type": "utc"}` to the temporal x encodings in `app_charts.py` — do not touch the data.

- [ ] **Step 5: Commit**

```bash
git add app/streamlit_app.py tests/test_streamlit_app.py
git commit -m "feat: organize app into overview, reliability, and diagnostics views"
```

---

### Task 8: Benchmark, docs, browser QA, and protected-artifact verification

**Files:**
- Modify: `scripts/benchmark_app.py`, `README.md` (Development section), `assets/README.md`
- Scratchpad only (never committed): hash lists, parity script, screenshots.

- [ ] **Step 1: Hash protected artifacts BEFORE any app run**

```bash
S=/private/tmp/claude-501/-Users-michi-Projects-research-open-revisit/*/scratchpad; mkdir -p "$S"
find data -maxdepth 1 -type f \( -name '*.parquet' -o -name '*.duckdb' \) | sort | xargs shasum -a 256 > "$S/hashes_before.txt"
find data/runs reports dbt/models dbt/macros dbt/tests dbt/dbt_project.yml -type f | sort | xargs shasum -a 256 >> "$S/hashes_before.txt"
wc -l "$S/hashes_before.txt"
```

- [ ] **Step 2: Extend the benchmark with a sensitivity timing**

Append to `scripts/benchmark_app.py::main` after the warm run:

```python
    started = perf_counter()
    app.toggle(key="sensitivity_enabled").set_value(True).run(timeout=60)
    sensitivity_seconds = perf_counter() - started
    if app.exception:
        raise RuntimeError(f"sensitivity render failed: {app.exception}")
    print(f"sensitivity_full_render_seconds={sensitivity_seconds:.6f}")
```

Run: `make benchmark-app` — record all three lines; cold must be < 3 s.

- [ ] **Step 3: Direct parity for Berlin and Tromsø** (scratchpad script, not committed)

```python
from datetime import date
from pathlib import Path
import pandas as pd
from open_revisit.app_analytics import revisit_dumbbell, sla_curve, threshold_grid, threshold_sensitivity, seasonal_comparison
from open_revisit.app_data import build_app_metrics, load_observations, select_observations
from open_revisit.config import load_config
from open_revisit.metrics import gap_table, monthly_reliability, service_level_success, survival_curve, wait_daily, within_probability

config = load_config(Path("config/default.yaml"))
obs = load_observations(config.data_dir / "observations.parquet", config_hash=config.config_hash())
aois = ("berlin", "tromso")
kw = dict(start=config.start, end=config.end, min_coverage=0.95, horizon_days=60)
m = build_app_metrics(obs, aoi_ids=aois, min_clear=0.80, every_days=7, **kw)
sel = select_observations(obs, aoi_ids=aois, start=config.start, end=config.end, min_clear=0.80, min_coverage=0.95)
curve = sla_curve(m.waits, horizon_days=60)
dumb = revisit_dumbbell(m.summary).set_index("aoi_id")
sens = threshold_sensitivity(obs, aoi_ids=aois, thresholds=threshold_grid(0.80), every_days=7, **kw)
for a in aois:
    c = sel[(sel.aoi_id == a) & sel.complete]; u = c[c.usable]
    w = wait_daily(pd.Series(u.observed_at), start=pd.Timestamp(config.start), end=pd.Timestamp(config.end), horizon_days=60)
    s = survival_curve(w, horizon_days=60)
    assert curve[(curve.aoi_id == a) & (curve.every_days == 7)].sla_success.iloc[0] == service_level_success(w, 7)
    assert dumb.loc[a, "effective_median_gap_days"] == gap_table(pd.Series(u.observed_at), kind="effective").gap_days.median()
    assert dumb.loc[a, "nominal_median_gap_days"] == gap_table(pd.Series(c.observed_at), kind="nominal").gap_days.median()
    row = sens[(sens.aoi_id == a) & (sens.min_clear == 0.8)].iloc[0]
    assert row.p_within_7d == within_probability(s, 7) and row.sla_success == service_level_success(w, 7)
    assert row.usable_rate == len(u) / len(c)
    print(a, dict(p7=within_probability(s, 7), sla7=service_level_success(w, 7), eff_med=dumb.loc[a, "effective_median_gap_days"], nom_med=dumb.loc[a, "nominal_median_gap_days"]))
print("parity OK")
```

Run with `uv run python "$S/parity.py"`; paste the printed numbers into the final report.

- [ ] **Step 4: Launch and QA in a browser**

```bash
lsof -nP -iTCP:8501 -sTCP:LISTEN || true   # reuse or stop an existing server cleanly
uv run streamlit run app/streamlit_app.py --server.headless true --server.port 8501 &
```

With the available browser automation (playwright MCP or the `browser-automation` skill): open `http://localhost:8501`, wait for the Overview tab, screenshot desktop (1440 px) and narrow (420 px via resize) for each of the three tabs; change map metric, W, timeline AOI, catalog threshold; enable sensitivity and confirm the three charts render; check the console for errors and the network log for any request other than `localhost:8501` (there must be none). Save screenshots under the scratchpad only. Stop the server you started.

- [ ] **Step 5: Hash protected artifacts AFTER and diff**

```bash
find data -maxdepth 1 -type f \( -name '*.parquet' -o -name '*.duckdb' \) | sort | xargs shasum -a 256 > "$S/hashes_after.txt"
find data/runs reports dbt/models dbt/macros dbt/tests dbt/dbt_project.yml -type f | sort | xargs shasum -a 256 >> "$S/hashes_after.txt"
diff "$S/hashes_before.txt" "$S/hashes_after.txt" && echo IDENTICAL
```

- [ ] **Step 6: Update docs**

README `## Development` — replace the app paragraph with:

```markdown
The app is organized into three tabs. *Overview* shows the AOI summary table,
an offline selected-city map (colour by P(within 7 days), SLA success, usable
rate, or longest outage) over the committed Natural Earth outline, and a
nominal-versus-effective median-gap dumbbell. *Reliability* shows the survival
curve, the SLA curve for every W from 1 through the horizon, the monthly
heatmap, and a seasonal monthly comparison. *Diagnostics* shows an on-demand
`min_clear` sensitivity analysis, a datatake-level observation timeline with
long-outage bands, and a catalog-versus-AOI quality scatter with a configurable
catalog threshold. Every number comes from `src/open_revisit/metrics.py`; the
app derives threshold-dependent usability in memory and does not run discovery,
read rasters, use the network, or modify pipeline outputs. Set
`OPEN_REVISIT_CONFIG` to use another repository-style YAML config and
`OPEN_REVISIT_BASEMAP` to point at another local GeoJSON outline.
```

`assets/README.md` — add one sentence: "The Streamlit app renders the same file inline through Vega-Lite; no tile or web service is contacted."

- [ ] **Step 7: Final gate and commit**

Run: `make check` (record the exact pytest summary line and TOTAL coverage), `git status --short` (only the intended files), `git diff --cached --name-only` after staging (no data, screenshots, `.streamlit`, dbt target/logs).

```bash
git add scripts/benchmark_app.py README.md assets/README.md
git commit -m "docs: describe expanded app analytics and benchmark sensitivity"
git status --short   # must be empty
git log --oneline origin/main..HEAD
```

Do not push.

---

## Self-review

- **Spec coverage:** map (T2, T3, T6, T7), SLA curve (T3, T6, T7), sensitivity (T4, T6, T7, benchmark T8), dumbbell (T3, T6, T7), timeline (T5, T6, T7), quality scatter (T5, T6, T7), seasonal (T5, T6, T7), UI organization + controls (T7), decision record (T1), docs (T8), hash/parity/browser/benchmark verification (T8), existing views preserved (T7 keeps `_render_summary`, `_render_survival`, `_render_heatmap` untouched and the existing AppTest assertions).
- **Type consistency:** `MapMetricSpec` fields (`field, title, unit, domain, value_format, lower_is_better`) used identically in T3/T6/T7; `TimelineTables(marks, outages)` in T5/T6/T7; `Selection` only in T7; `threshold_sensitivity` keyword signature identical in T4/T7/T8; widget keys `map_metric`, `timeline_aoi`, `catalog_threshold`, `sensitivity_enabled` identical in T7 tests and app and T8 benchmark.
- **Known judgement calls:** the quality scatter's pooled precision/recall caption reuses `catalog_filter_evaluation` (contract function) rather than counting points itself; the timeline shows one AOI at a time by design (20 AOIs × ~650 datatakes is unreadable); the sensitivity grid is 0.05 steps plus the slider value (22 points max) and is on-demand because it costs ≈ 2.7 s for 20 AOIs.
