# Decisions and deviations

This log records any implementation deviation from [`SPEC.md`](SPEC.md), along
with its reason and consequences. There were no deviations in M0.

## M1-001 — Partition long STAC searches into 90-day intervals

- **Decision:** Split each requested AOI period into contiguous, non-overlapping
  intervals of at most 90 days, paginate every interval with the specified
  `limit=500`, merge by `scene_id`, and then apply the specified deduplication.
- **Spec alternative:** §6.2 describes one search call over the entire requested
  period for each AOI.
- **Reason:** On 2026-08-21, Earth Search returned HTTP 502 for the Berlin 2024
  query after all five retries, both with `+00:00` and `Z` timestamps. A bounded
  one-day Berlin request returned HTTP 200 and the expected fields/assets.
- **Consequence:** The logical query and outputs are unchanged, but initial
  discovery makes more bounded metadata requests. Watermark-overlap reruns
  normally cover one interval per AOI.

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
