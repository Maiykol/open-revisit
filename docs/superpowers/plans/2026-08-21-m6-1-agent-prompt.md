# Prompt — implement M6.1 Expanded visual analytics

Continue the open-revisit project in `/Users/michi/Projects/research/open-revisit`.

Implement the owner-approved M6 extension **M6.1 — Expanded visual analytics**.
This is not M7: do not begin cloud deployment or introduce cloud infrastructure.
Stop after M6.1.

## Read first, in full

1. `docs/superpowers/plans/2026-08-21-m6-1-visual-analytics.md` — the
   task-by-task implementation plan for this milestone. Execute it in order
   with `superpowers:subagent-driven-development` (preferred) or
   `superpowers:executing-plans`, using TDD exactly as the plan's steps lay out.
2. `AGENTS.md`, `docs/SPEC.md`, `docs/DECISIONS.md`, `docs/METRICS.md`,
   `README.md`, `dbt/README.md`
3. `src/open_revisit/metrics.py`, `src/open_revisit/app_data.py`,
   `app/streamlit_app.py`, `tests/test_app_data.py`, `tests/test_streamlit_app.py`

`docs/SPEC.md` remains the source of truth. `docs/METRICS.md` and
`src/open_revisit/metrics.py` are contracts; M6.1 must not alter the meaning of
any existing metric. Record the scope extension in `docs/DECISIONS.md` as
`M6.1-001` (plan Task 1).

## Repository state you should find

- Branch `main`, synchronized with origin at
  `a3014f3c717182f93220a299ccdc2cfab176ed5b`
  (`test: verify self-service app metrics`; before it
  `e5444e2d41fbce9986b2baca4f10eb06cc387d9c feat: add self-service reliability app`).
- The worktree is clean **except** for the untracked directory
  `docs/superpowers/plans/` holding the plan and this prompt. Plan Task 1
  commits those two files together with the decision record. Any other
  modification means stop and report.
- Last quality gate: `50 passed, 1 deselected, 15 warnings in 11.74s`,
  coverage 90.56%, Streamlit 1.49.1, full-data render cold 0.793810 s / warm
  0.042796 s.
- Full-run context: config hash
  `f33bae2b5ac9c19b740d210280ef6a5c5530032aec054366ebc2f4e943f5dab7`, 20 AOIs,
  2022-01-01 through 2025-12-31, min_coverage 0.95, default min_clear 0.80,
  horizon 60 days. `data/aois.parquet` has columns
  `aoi_id, name, country, lat, lon, utm_epsg, area_km2, geometry` (WKB; do not
  decode it — only `lat`/`lon` are needed). `data/observations.parquet` holds
  12,976 rows across three config hashes; 12 rows are `complete=false`.
- The app may already be listening on `localhost:8501`; check with
  `lsof -nP -iTCP:8501 -sTCP:LISTEN` and reuse or stop it cleanly.

## Checklist — print it before coding and keep it updated

- [ ] Offline selected-city reliability map
- [ ] SLA curve for W=1..60
- [ ] min_clear sensitivity visualization
- [ ] Nominal/effective revisit dumbbell
- [ ] Datatake observation timeline
- [ ] Catalog/AOI quality scatter plot
- [ ] Monthly seasonal comparison
- [ ] Existing controls and visualizations preserved
- [ ] Read-only/no-network behavior preserved
- [ ] Full-data startup remains under three seconds
- [ ] make check passes
- [ ] Focused conventional commits
- [ ] Worktree clean and nothing pushed

## Non-negotiable constraints (the plan's Global Constraints, summarized)

- Local, read-only, offline. No `st.map`, map tiles, Mapbox, online GeoJSON,
  geocoding, or any web service. Do not write Parquet, DuckDB, configuration,
  run records, dbt artifacts, or reports. No authentication, credentials,
  uploads, or GeoJSON workflow.
- **Do not add fields to `AppConfig`** — `config_hash()` hashes every field and
  a new field would orphan the full-run data. The basemap path comes from the
  env var `OPEN_REVISIT_BASEMAP` (default repository-relative
  `assets/natural_earth_europe.geojson`); AOI metadata is
  `config.data_dir / "aois.parquet"`.
- `app/streamlit_app.py` stays UI composition only. Pure, typed preparation
  code lives in `src/open_revisit/app_analytics.py` (pandas) and
  `src/open_revisit/app_charts.py` (Vega-Lite dicts, no Streamlit import);
  loaders extend `src/open_revisit/app_data.py`. Every service number comes
  from `metrics.py`; never redefine waits, gaps, survival, SLA, or usability.
- The app and the new modules import nothing from discovery, STAC, raster,
  processing, report, metric_pipeline, or run_pipeline.
- Metric semantics that must remain unchanged: usability =
  `complete AND covered_fraction >= min_coverage AND clear_fraction >= min_clear`
  recomputed in memory (persisted `usable` never drives a result); incomplete
  observations excluded from every metric; wait = first usable observation at
  or after `t0`; inclusive horizon; survival `wait_days > n`; within-N
  `wait_days <= N`; SLA strict `wait_days < W`; outages `gap_days > 30`;
  fractional days preserved; observations keyed by
  `(aoi_id, datatake_id, config_hash)` and never regrouped by date; every
  zero denominator is finite `0.0`.
- Threshold sensitivity is on-demand (toggle, default off) and cached with a
  key that includes the observations, AOIs, period, min_coverage, threshold
  grid, horizon, and W. Measured cost: ≈ 0.13 s per threshold for 20 AOIs, so
  the 21-point grid would break the 3-second cold render if eager. The SLA
  curve (≈ 0.03 s) is computed eagerly.
- Tests use temporary Parquet fixtures only; never modify real data files.
  Reading the committed `assets/natural_earth_europe.geojson` in tests is fine.
- Conventional commits, one per plan task, on `main`. **Do not push.**

## Verification you must perform and report (plan Task 8)

- `make check` — report the exact pytest summary line and TOTAL coverage.
- `make benchmark-app` on the full dataset — report cold, warm, and the new
  sensitivity-enabled timing; cold must remain under three seconds.
- Direct Python parity for Berlin and Tromsø against `metrics.py` (the plan's
  scratchpad script) — paste the printed numbers.
- Launch the app, verify the primary flow, and inspect desktop (≈1440 px) and
  narrow (≈420 px) layouts of all three tabs with the available browser
  automation; confirm the browser network log shows only `localhost:8501`.
  Screenshots go to the session scratchpad only, never the repo.
- SHA-256 hash `data/*.parquet`, `data/open_revisit.duckdb`, `data/runs/*.json`,
  `reports/**`, `dbt/models/**`, `dbt/macros/**`, `dbt/tests/**`, and
  `dbt/dbt_project.yml` before any app run and again after all verification;
  prove they are byte-identical.
- Confirm nothing generated is staged (data, caches, screenshots,
  credentials, Parquet, DuckDB, rasters, `.streamlit`, dbt `target`/`logs`,
  local config) and that no pipeline, discovery, raster, report, production
  dbt, or STAC work ran.

## End-of-work report format

    ## Milestone M6.1 — Expanded visual analytics

    - Updated checklist with ticks
    - Exact pytest summary
    - Exact coverage
    - Streamlit version
    - Launch result
    - Cold, warm, and sensitivity full-data timings
    - Description of all seven visualizations
    - Controls and defaults (sidebar unchanged; map metric, timeline AOI,
      catalog threshold, sensitivity toggle)
    - Metric-contract verification
    - Direct Python parity results (Berlin, Tromsø)
    - AppTest and browser verification (desktop and narrow)
    - Read-only and offline-map verification
    - Protected-artifact hash verification
    - Deviations/decision record (M6.1-001 plus anything else)
    - Commit hashes and messages
    - Clean-worktree confirmation
    - Confirmation that nothing was pushed
    - Explicit statement that M7 was not started

Stop after M6.1. Do not begin M7.
