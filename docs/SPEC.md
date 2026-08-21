# open-revisit — specification for the implementing agent

**Tagline:** From nominal revisit to useful observation. Open-source analytics for satellite image availability.

**Status:** v1.0, 2026-08-21. This document is the source of truth. Deviations are allowed only when recorded in `docs/DECISIONS.md` in the repo with a reason.

**Owner:** Michael Hartung. **Builder:** a coding agent working milestone by milestone (see `AGENT_BRIEF.md`).

---

## 1. Purpose

### 1.1 The question

Sentinel-2 nominally revisits every point on land every five days. An acquisition is not a useful observation: the area of interest (AOI) can be cloudy, in shadow, under snow, or only partly covered by the tile. `open-revisit` takes arbitrary AOIs and turns a public satellite catalog (STAC) plus the per-pixel scene classification layer (SCL) into customer-style service metrics:

1. **Effective revisit** of usable observations versus nominal revisit of all acquisitions.
2. **Wait-time distribution**: probability of obtaining a usable observation within N days of an arbitrary start day, as a survival curve, overall and per month.
3. **Service-level success rate** for a requirement of the form "at least one usable observation every W days".
4. **Longest outage** without a usable observation.
5. **Catalog-filter accuracy**: how often the scene-level `eo:cloud_cover` metadata (the filter almost everyone uses) keeps unusable observations and discards usable ones, measured against pixel-level AOI quality.

### 1.2 Why this shape

The project exists to give the owner hands-on geospatial data-engineering experience that maps onto constellation-service analytics (pipelines, derived datasets, service metrics, self-service tooling, data accuracy), not to produce a remote-sensing research result. The derived dataset, the metric definitions, the idempotent pipeline and the reproducibility matter more than the science. It is not marketed as novel research; published work on observation availability exists. The gap is the engineering and product layer.

### 1.3 Non-goals

- No machine learning, no cloud-mask model training. SCL is the quality source (limitations documented, see §11).
- No imagery download beyond the SCL windows needed, plus a handful of RGB chips for README figures.
- No Planet data, no proprietary catalogs, no authentication flows.
- No web service or API in the core. A Streamlit app is a stretch milestone.
- No notebooks as deliverables. Figures are produced by the CLI. Notebooks may exist for exploration but nothing depends on them.

---

## 2. Verified facts the design relies on (checked 2026-08-21)

- STAC API: Earth Search v1, `https://earth-search.aws.element84.com/v1`, collection `sentinel-2-l2a`. Public, no auth, temporal extent from 2015-06-27. Item assets include `scl` (COG, GeoTIFF, uint8, 20 m, nodata 0, `proj:shape` 5490×5490) and `visual` (RGB COG).
- SCL hrefs are public S3 objects, e.g. `https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/32/U/QD/2025/6/S2A_32UQD_20250626_0_L2A/SCL.tif`. HTTP range requests work (`Accept-Ranges: bytes`, tested 206). A full SCL tile is ~1.2 MB compressed. Volume is not a constraint; correctness of windowing is still required.
- Item properties observed: `datetime`, `platform` (`sentinel-2a`/`2b`/`2c`), `eo:cloud_cover`, `grid:code` (`MGRS-32UQD`), `proj:epsg` (or `proj:code`), `s2:datatake_id`, `s2:product_uri` (contains relative orbit `_R065_`), `s2:processing_baseline`, `s2:sequence` (reprocessing counter), `s2:generation_time`, `s2:nodata_pixel_percentage`, `s2:cloud_shadow_percentage`, `s2:medium_proba_clouds_percentage`, `s2:high_proba_clouds_percentage`, `s2:thin_cirrus_percentage`, `s2:snow_ice_percentage`, `s2:vegetation_percentage`, `s2:not_vegetated_percentage`, `s2:water_percentage`, `s2:unclassified_percentage`. `sat:relative_orbit` was absent; parse the orbit from `s2:product_uri` if needed.
- A 20 km AOI around Berlin intersects two MGRS tiles in two UTM zones (32UQD in EPSG:32632, 33UUU in EPSG:32633). Two different satellites can acquire the same AOI on the same day on different relative orbits with partial coverage (`s2:nodata_pixel_percentage` 37 %). Therefore: observations must be grouped by datatake, not by date, and tiles must be composited on a common grid before computing fractions (§6.3).

SCL class codes (Sentinel-2 L2A): 0 NO_DATA, 1 SATURATED_OR_DEFECTIVE, 2 CAST_SHADOWS (called DARK_AREA_PIXELS before baseline 04.00), 3 CLOUD_SHADOWS, 4 VEGETATION, 5 NOT_VEGETATED, 6 WATER, 7 UNCLASSIFIED, 8 CLOUD_MEDIUM_PROBABILITY, 9 CLOUD_HIGH_PROBABILITY, 10 THIN_CIRRUS, 11 SNOW_ICE.

---

## 3. Architecture

### 3.1 Layers

```
                 ┌──────────────┐
 GeoJSON AOIs ──►│ 1. discover  │ STAC search per AOI → scenes (metadata only)
                 └──────┬───────┘
                        ▼
                 ┌──────────────┐
 SCL COGs ──────►│ 2. process   │ windowed reads → per-AOI composite per datatake → class counts
                 └──────┬───────┘
                        ▼
                 ┌──────────────┐
                 │ 3. metrics   │ observations → wait times, survival, gaps, SLA, monthly, filter eval
                 └──────┬───────┘
                        ▼
                 ┌──────────────┐
                 │ 4. report    │ figures + markdown tables for README; Streamlit (stretch)
                 └──────────────┘
```

Each layer reads the previous layer's Parquet output and writes its own. Layers are independently runnable and re-runnable. Layer 2 is the only one that touches rasters. Layers 3 and 4 are pure tabular work.

### 3.2 Storage

- All derived data lives under `data/` (gitignored) as Parquet, one file per table, partitioned by `aoi_id` where large.
- A DuckDB database `data/open_revisit.duckdb` exposes the Parquet files as views for ad-hoc SQL and for the dbt layer (M5). DuckDB is an access layer, not the system of record; Parquet is.
- AOIs are stored as GeoParquet (`data/aois.parquet`) and as source GeoJSON in the repo (`aois/*.geojson`).
- Rasters are not persisted by default. `--keep-rasters` persists per-observation composites as compressed GeoTIFF for selected AOIs (used for README example chips).

### 3.3 Execution model

Batch, incremental, idempotent. There is no scheduler in the core. Every stage is keyed so that re-running it produces the same output and only new inputs do new work:

- discover: watermark per `(aoi_id, collection)` = max `datetime` ingested. Re-runs search from `watermark − 7 days` (to catch late-published items) and upsert by `scene_id`.
- process: keyed by `(aoi_id, scene_id, config_hash)`. Already-processed keys are skipped unless `--force`.
- metrics and report: always recomputed from their inputs (cheap).

This is the event-style contract (one scene = one event, processed at most once per config) without an event bus. Moving discovery to a push trigger (e.g. Pub/Sub, STAC notifications) is a stretch item (§10, M7) and must not require changes to stages 2–4.

---

## 4. Architectural decision records

Each ADR: decision, alternatives, reason. The owner may override any of them before M0 starts; after that, changes go to `docs/DECISIONS.md`.

| # | Decision | Alternatives considered | Reason |
|---|---|---|---|
| ADR-1 | Data source: Earth Search v1 (`sentinel-2-l2a`) | Copernicus Data Space STAC; Microsoft Planetary Computer | Public, no auth, COGs, stable; CDSE downloads need credentials; MPC needs token signing |
| ADR-2 | Quality source: SCL band only | s2cloudless probabilities; OmniCloudMask; Fmask | SCL is in the same COG set, no ML dependency, good enough for availability statistics; limitations documented and measurable |
| ADR-3 | Observation = all scenes of one `s2:datatake_id` intersecting the AOI | Group by date; group by scene | Same-day passes of two satellites on different orbits are distinct observations; one pass split over tile boundaries is one observation |
| ADR-4 | Fractions computed on a per-AOI analysis grid (20 m, UTM zone of AOI centroid), scenes composited onto it | Per-scene fractions weighted by coverage | Tiles overlap and can sit in different UTM zones; compositing avoids double counting and is the honest way to handle CRS |
| ADR-5 | AOIs standardised as 20 km × 20 km squares around a centroid, built in the local UTM zone | Administrative boundaries; bounding boxes in WGS84 | Equal area makes AOIs comparable; squares in UTM are true squares; arbitrary polygons remain supported by the same code path |
| ADR-6 | Python 3.12, `uv` for env and lockfile, `pyproject.toml`, src layout | poetry; conda | `uv` is installed on the owner's machine and is fast; lockfile gives reproducibility |
| ADR-7 | Core libs: `pystac-client`, `rasterio`, `shapely`, `pyproj`, `geopandas`, `numpy`, `pandas`, `pyarrow`, `duckdb`, `typer`, `pydantic` (config), `matplotlib` | `rioxarray`/`xarray`/`dask`; `odc-stac`; `stackstac` | Windows are small; a bare rasterio read plus numpy is simpler to test and to explain than a lazy array stack |
| ADR-8 | Metrics implemented in Python first (M3), ported to dbt-duckdb SQL models in M5 with a parity test | dbt-only from the start | Working product early; dbt is a listed skill for the target role, so it is added as a transformation layer once the reference implementation exists |
| ADR-9 | Tests never touch the network in CI. STAC responses are recorded fixtures; rasters are synthetic COGs written in the test | Live tests in CI | Determinism; a single `@pytest.mark.network` smoke test exists and runs locally on demand |
| ADR-10 | Parquet as system of record, DuckDB as query layer | PostGIS; SQLite | No server, GeoParquet for geometry, DuckDB spatial for ad-hoc queries, dbt-duckdb adapter exists |
| ADR-11 | CLI with Typer; config via YAML validated by pydantic; every run writes its resolved config and a `config_hash` | argparse + env vars | Reproducible runs; the hash keys the processed table so threshold changes are tracked, not overwritten |
| ADR-12 | Concurrency: thread pool for raster reads (I/O bound), `--workers` default 8 | multiprocessing; async | rasterio releases the GIL during I/O; simple |
| ADR-13 | Licence MIT | Apache-2.0 | Simplest for a portfolio project |
| ADR-14 | Repo name and package: `open-revisit` / `open_revisit` (PyPI and GitHub names free as of 2026-08-21) | — | — |

---

## 5. Data model

All timestamps UTC. All fractions in [0, 1]. Geometry columns are WKB in EPSG:4326 unless stated.

### 5.1 `aois` (GeoParquet)

| column | type | notes |
|---|---|---|
| aoi_id | string PK | slug, e.g. `berlin` |
| name | string | |
| country | string | ISO-3166-1 alpha-2 |
| lat, lon | float | centroid |
| utm_epsg | int | 326xx zone of the centroid |
| area_km2 | float | |
| geometry | WKB | polygon, EPSG:4326 |

### 5.2 `scenes` (global, one row per STAC item)

| column | type | notes |
|---|---|---|
| scene_id | string PK | STAC item id |
| collection | string | `sentinel-2-l2a` |
| datetime | timestamp | |
| platform | string | `sentinel-2a` etc. |
| datatake_id | string | `s2:datatake_id` |
| relative_orbit | int | parsed from `s2:product_uri` `_R(\d{3})_`, nullable |
| mgrs_tile | string | from `grid:code` without `MGRS-` |
| epsg | int | |
| processing_baseline | string | |
| sequence | int | `s2:sequence` |
| generation_time | timestamp | |
| eo_cloud_cover | float | percent as published |
| s2_nodata_pct, s2_cloud_shadow_pct, s2_medium_cloud_pct, s2_high_cloud_pct, s2_cirrus_pct, s2_snow_pct, s2_unclassified_pct | float | percent as published |
| scl_href | string | |
| visual_href | string | |
| geometry | WKB | item footprint |
| ingested_at | timestamp | |

Deduplication rule: for identical `(mgrs_tile, datatake_id)`, keep the row with the highest `sequence`, ties broken by latest `generation_time`. Superseded ids are recorded in `scenes_superseded` (scene_id, superseded_by) so a re-run does not re-process them.

### 5.3 `scene_aoi` (link table, one row per scene × AOI it intersects)

| column | type |
|---|---|
| aoi_id, scene_id | PK |
| footprint_overlap_fraction | float, fraction of AOI area inside the item footprint (vector, pre-raster) |

### 5.4 `scene_aoi_stats` (raster result per scene × AOI × config)

| column | type | notes |
|---|---|---|
| aoi_id, scene_id, config_hash | PK | |
| n_aoi_pixels | int | pixels of the analysis grid inside the AOI polygon |
| n_covered | int | AOI pixels with SCL ≠ 0 from this scene |
| count_class_0 … count_class_11 | int | per-class counts over AOI pixels from this scene |
| read_ok | bool | |
| error | string | nullable |
| processed_at | timestamp | |

### 5.5 `observations` (one row per AOI × datatake × config)

| column | type | notes |
|---|---|---|
| aoi_id, datatake_id, config_hash | PK | |
| observed_at | timestamp | min `datetime` of member scenes |
| platform | string | |
| relative_orbit | int | |
| n_scenes | int | |
| primary_scene_id | string | member scene with the largest `n_covered` |
| catalog_cloud_cover | float | `eo_cloud_cover` of the primary scene |
| catalog_cloud_cover_wmean | float | coverage-weighted mean over member scenes |
| n_aoi_pixels | int | |
| covered_fraction | float | composite non-nodata pixels / n_aoi_pixels |
| clear_fraction | float | composite pixels in `clear` classes / n_aoi_pixels (uncovered pixels count as not clear) |
| cloud_fraction, shadow_fraction, snow_fraction, unclassified_fraction, defective_fraction | float | same denominator |
| usable | bool | `covered_fraction ≥ min_coverage AND clear_fraction ≥ min_clear` |
| complete | bool | false if any member scene has `read_ok = false`; incomplete observations are excluded from metrics |

### 5.6 Metric tables (outputs of stage 3)

- `aoi_wait_daily`: `(aoi_id, config_hash, t0 date, wait_days float, censored bool)`.
- `aoi_survival`: `(aoi_id, config_hash, n_days int 0..horizon, p_waiting float)`.
- `aoi_monthly`: `(aoi_id, config_hash, month 1..12, p_within_5d, p_within_7d, p_within_14d, n_days)`.
- `aoi_gaps`: `(aoi_id, config_hash, kind ∈ {nominal, effective}, gap_start, gap_end, gap_days)`.
- `aoi_summary`: one row per `(aoi_id, config_hash)` with the headline numbers listed in §6.5.
- `catalog_filter_eval`: `(aoi_id | 'ALL', config_hash, threshold int, tp, fp, fn, tn, precision, recall, f1, kept_unusable_rate, discarded_usable_rate)`.
- `ingest_state`: `(aoi_id, collection, watermark timestamp, last_run_at)`.

---

## 6. Processing rules

### 6.1 AOI construction

`open-revisit aois build aois/centroids.csv` reads `aoi_id,name,country,lat,lon`, builds a 20 km × 20 km square centred on the point in the centroid's UTM zone, reprojects to EPSG:4326 and writes one GeoJSON per AOI plus `data/aois.parquet`. Arbitrary user polygons are accepted by `--aoi file.geojson`; polygons larger than `max_aoi_km2` (default 2 500) are rejected with a clear error.

Default centroid set (20 AOIs, strongly different climates): Hamburg, Berlin, Munich, London, Dublin, Amsterdam, Paris, Marseille, Madrid, Lisbon, Rome, Athens, Zürich, Innsbruck, Warsaw, Copenhagen, Stockholm, Oslo, Tromsø, Reykjavík. Coordinates are city centres; the agent fills them in from a reliable source and records the source.

### 6.2 Discovery (stage 1)

For each AOI: `pystac_client.Client.open(EARTH_SEARCH_V1).search(collections=["sentinel-2-l2a"], intersects=aoi_geometry, datetime=f"{start}/{end}", limit=500)`; iterate all pages; map items to `scenes` rows; compute `footprint_overlap_fraction` with shapely on the WGS84 footprint (good enough for a link table); upsert; dedupe (§5.2); update watermark. Requests are retried with exponential backoff (`tenacity`), max 5 attempts. STAC responses for the test fixture are recorded once (3 items covering the Berlin two-tile case, including one reprocessed duplicate) and committed under `tests/fixtures/stac/`.

### 6.3 Raster processing (stage 2)

Per AOI, build the analysis grid once: CRS = `utm_epsg`, resolution 20 m, bounds = AOI bounds in that CRS snapped outward to multiples of 20 m; `aoi_mask = rasterio.features.geometry_mask(aoi_in_utm, out_shape, transform, invert=True)`. `n_aoi_pixels = aoi_mask.sum()`.

Per scene intersecting the AOI:
1. Open `scl_href` with rasterio (`AWS_NO_SIGN_REQUEST=YES`, `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR`, `CPL_VSIL_CURL_ALLOWED_EXTENSIONS=.tif`, `GDAL_HTTP_MERGE_CONSECUTIVE_RANGES=YES`, `VSI_CACHE=TRUE`).
2. Compute the window covering the analysis-grid bounds transformed into the scene CRS (`rasterio.warp.transform_bounds`), padded by one pixel, clipped to the raster.
3. Read the window; `rasterio.warp.reproject` it onto the analysis grid with `Resampling.nearest`, `src_nodata=0`, `dst_nodata=0`. When scene CRS equals grid CRS and grids are aligned this is an identity copy.
4. Store per-class counts over `aoi_mask` in `scene_aoi_stats`. Nothing else is kept.

Per observation (datatake): composite the member scenes onto the grid in order of ascending `s2_nodata_pct`, filling only pixels that are still 0. Compute the class counts of the composite over `aoi_mask` and derive the `observations` row. Implementation note: the composite is built from the reprojected arrays held in memory for that datatake; do not reread. Class mapping (configurable, defaults):

```yaml
classes:
  clear: [4, 5, 6]
  cloud: [8, 9, 10]
  shadow: [2, 3]
  snow: [11]
  unclassified: [7]
  defective: [1]
  nodata: [0]
thresholds:
  min_clear: 0.80
  min_coverage: 0.95
```

Unclassified pixels are not clear by default (conservative). Snow is not clear by default; a second preset `snow_ok` treats 11 as clear for users who want winter mapping. Errors in a single scene (HTTP 5xx after retries, corrupt tile) are recorded with `read_ok=false` and do not abort the run; an observation with any failed member scene is flagged `complete=false` and excluded from metrics.

### 6.4 Metrics (stage 3)

Let `U` be the sorted `observed_at` of usable, complete observations for an AOI; `A` the same for all complete observations. Period `[start, end]`, horizon `H` = 60 days (config).

- **Nominal gaps**: differences between consecutive elements of `A`. **Effective gaps**: consecutive elements of `U`. Report median, P90, max, count of gaps > 30 days. Both go to `aoi_gaps`.
- **Wait time**: for every calendar day `t0` from `start` to `end − H`: `wait = min{u ∈ U : u ≥ t0} − t0` in fractional days; if none within `H`, `wait = H` and `censored = true`. Evaluating only `t0 ≤ end − H` removes end-of-period bias.
- **Survival**: `S(n) = P(wait > n)` for integer `n` in `0..H` over all evaluated `t0`.
- **P(within N)**: `1 − S(N)` for N ∈ {3, 5, 7, 14, 30}.
- **Service-level success rate** for "one usable observation every W days": defined as `P(wait < W)` over all start days. Note in the README that this equals the fraction of rolling W-day windows that contain a usable observation.
- **Monthly reliability**: `P(wait ≤ N | month(t0) = m)` for N ∈ {5, 7, 14}.
- **Catalog-filter evaluation**: over observations, for thresholds `t` in 0, 5, …, 100: predicted usable iff `catalog_cloud_cover ≤ t`; actual = `usable`. TP/FP/FN/TN, precision, recall, F1, `kept_unusable_rate = FP / (TP + FP)`, `discarded_usable_rate = FN / (TP + FN)`. Computed per AOI and pooled (`aoi_id = 'ALL'`). The README headline uses t = 20.

### 6.5 `aoi_summary` columns

`n_observations, n_usable, usable_rate, nominal_median_gap_days, effective_median_gap_days, effective_p90_gap_days, longest_outage_days, n_outages_over_30d, p_within_3d, p_within_5d, p_within_7d, p_within_14d, p_within_30d, best_month, worst_month, p_within_7d_best_month, p_within_7d_worst_month, catalog_filter_precision_t20, catalog_filter_recall_t20`.

### 6.6 Report (stage 4)

`open-revisit report` writes to `reports/figures/` and `reports/tables/`:

1. Dumbbell chart per AOI: nominal vs effective median revisit, reference line at 5 days.
2. Map of Europe: AOI points coloured by effective median revisit (Natural Earth coastlines via `geodatasets` or a committed low-res GeoJSON; no live basemap).
3. Heatmap month × AOI: `P(within 7 days)`.
4. Survival curves for 6 AOIs chosen to span the range (auto-selected: best, worst, and 4 quantile picks).
5. Catalog filter: scatter `catalog_cloud_cover` vs `clear_fraction` with the four quadrants at t = 20 and `min_clear`, plus precision/recall vs threshold.
6. Service-level curve: success rate vs W for W in 1..30, all AOIs as thin lines, median bold.
7. Two example chips (RGB from `visual` asset, cropped to AOI): one catalog-says-clear-but-AOI-cloudy, one catalog-says-cloudy-but-AOI-clear, auto-selected as the most extreme cases.

Plus `reports/tables/aoi_summary.md` (markdown table) which the README embeds.

---

## 7. Repository layout

```
open-revisit/
├── AGENTS.md                  # agent operating rules (copied from AGENT_BRIEF.md §3)
├── README.md                  # problem, method, results, figures, how to run, limitations
├── LICENSE                    # MIT
├── pyproject.toml             # uv-managed; [project.scripts] open-revisit = "open_revisit.cli:app"
├── uv.lock
├── Makefile                   # make check (ruff+mypy+pytest), make run-dev, make report
├── Dockerfile                 # python:3.12-slim + GDAL deps, runs the CLI
├── .github/workflows/ci.yml   # uv sync, ruff, mypy, pytest -m "not network"
├── config/
│   ├── default.yaml
│   ├── dev.yaml               # 3 AOIs × 1 year for fast iteration
│   └── test.yaml              # fixture STAC + synthetic rasters, used by test_cli.py
├── aois/
│   ├── centroids.csv
│   └── *.geojson
├── src/open_revisit/
│   ├── __init__.py
│   ├── cli.py                 # typer app: aois, discover, process, metrics, report, run, sla
│   ├── config.py              # pydantic models, config_hash()
│   ├── aoi.py                 # square construction, UTM zone, validation, GeoParquet I/O
│   ├── stac.py                # search, item→row mapping, dedupe, watermark
│   ├── grid.py                # analysis grid + AOI mask
│   ├── raster.py              # windowed SCL read + reproject onto grid
│   ├── composite.py           # datatake composite + class counts
│   ├── metrics.py             # gaps, wait, survival, monthly, SLA, filter eval (pure pandas/numpy)
│   ├── store.py               # Parquet read/write, upsert, DuckDB views
│   ├── report.py              # figures and tables
│   └── logging.py
├── dbt/                       # M5: dbt-duckdb project (models mirror metrics.py)
├── app/streamlit_app.py       # M6
├── tests/
│   ├── fixtures/stac/*.json
│   ├── test_aoi.py test_stac.py test_grid.py test_raster.py test_composite.py test_metrics.py test_cli.py
│   └── test_network_smoke.py  # @pytest.mark.network
├── docs/
│   ├── DECISIONS.md           # deviations from SPEC with reasons
│   └── METRICS.md             # the §6.4 definitions, kept in sync
└── reports/                   # generated; figures committed for the README, tables too
```

Module contract: each module exposes pure functions with typed signatures and no hidden global state; `cli.py` is the only place that wires config, I/O and logging together. `metrics.py` must work on a plain `pandas.DataFrame` of observations so it can be unit-tested with hand-made timelines.

---

## 8. CLI

```
open-revisit aois build aois/centroids.csv [--size-km 20]
open-revisit discover  --config config/default.yaml [--aoi berlin] [--start 2022-01-01 --end 2025-12-31]
open-revisit process   --config config/default.yaml [--aoi berlin] [--workers 8] [--force] [--keep-rasters]
open-revisit metrics   --config config/default.yaml
open-revisit report    --config config/default.yaml
open-revisit run       --config config/default.yaml        # all four stages
open-revisit sla       --aoi berlin --every 7 [--min-clear 0.8] [--month 12]   # prints success rate from existing tables
```

Every command logs structured lines (JSON when `--json-logs`) with counts in/out per stage and exits non-zero on any unhandled error. `run` writes `data/runs/<timestamp>_<config_hash>.json` with the resolved config, versions and stage counts.

---

## 9. Testing strategy

- **Unit, no network (CI):**
  - `aoi`: square has area 400 km² ± 0.5 %, lies in the expected UTM zone, round-trips through GeoJSON.
  - `stac`: fixture items map to the expected rows; dedupe keeps the highest sequence; watermark logic with overlap window.
  - `grid`/`raster`: write a synthetic SCL COG with `rasterio` in a temp dir (known class pattern, known CRS), read a window through the same code path, assert exact class counts; one case with a different UTM zone than the grid to exercise reprojection; one case with nodata stripes.
  - `composite`: two synthetic scenes with overlapping coverage and nodata; assert no double counting and the fill order.
  - `metrics`: hand-built observation timelines with known answers, e.g. usable at days 0, 5, 40 over a 60-day period with H = 20 → exact wait times, survival values, SLA(7), longest outage 35; censoring behaviour; monthly conditioning; filter-eval confusion matrix on a 6-row table.
  - `cli`: `run` on `config/test.yaml` with the fixture STAC and synthetic rasters produces all tables with expected row counts.
- **Network smoke (local only):** `pytest -m network` discovers one day over Berlin and reads one SCL window; asserts shape and that class counts sum to `n_aoi_pixels`.
- **Parity (M5):** dbt models over the dev dataset equal `metrics.py` output within 1e-9.
- Coverage target 85 % on `src/open_revisit`, measured in CI, not enforced as a gate until M4.

---

## 10. Milestones and definitions of done

Work strictly in order. A milestone is done only when every DoD line is true and `make check` passes. Commit at least once per milestone (conventional commits). Dev iterations use `config/dev.yaml` (Berlin, Athens, Tromsø × 2024). The full run (20 AOIs × 2022–2025) happens once in M4.

**M0 — Scaffold**
- Repo initialised, MIT licence, `uv` project with pinned deps and lockfile, src layout, Typer CLI with `--version`.
- `make check` runs ruff (lint + format check), mypy (strict on `src/`), pytest; all green on an empty test.
- GitHub Actions CI runs `make check` on push; Dockerfile builds and `docker run open-revisit --version` works.
- `AGENTS.md`, `docs/DECISIONS.md`, `docs/METRICS.md` (copied from §6.4) exist.

**M1 — AOIs and discovery**
- `aois build` produces 20 GeoJSON files and `aois.parquet`; centroid source recorded in `aois/README.md`.
- `discover` fills `scenes`, `scene_aoi`, `scenes_superseded`, `ingest_state` for the dev config; re-running immediately makes zero new rows; dedupe test passes on the fixture.
- Log output states per AOI: items fetched, new, superseded, watermark.

**M2 — Raster processing**
- `process` fills `scene_aoi_stats` and `observations` for the dev config, 8 workers, no unhandled exceptions; failed scenes recorded, not fatal.
- Synthetic-COG tests for window read, reprojection across UTM zones, nodata, composite fill order pass.
- Network smoke test passes locally.
- Invariants checked in code and tests: class counts sum to `n_aoi_pixels`; `covered_fraction ≤ 1`; `clear_fraction ≤ covered_fraction`; each observation has `n_scenes ≥ 1`.
- Re-running `process` does nothing; `--force` recomputes; changing `min_clear` changes `config_hash` and produces new rows without touching old ones.

**M3 — Metrics**
- `metrics` writes all §5.6 tables; `metrics.py` functions are pure and covered by the hand-computed tests in §9.
- `sla` command answers the example question from existing tables in under a second.
- `docs/METRICS.md` matches the implementation line by line and contains the §9 example timeline with its expected numbers; a test parses that example from the doc and compares it with `metrics.py` output.

**M4 — Full case study, report, README**
- Full run completed for 20 AOIs × 2022-01-01..2025-12-31 with the default config; `data/runs/*.json` committed (without data) as the record; wall-clock and bytes transferred logged.
- All seven figures and the summary table generated by `report` and committed under `reports/`.
- README contains: the question, method summary with the grouping/compositing explanation, the headline numbers, all figures, the catalog-filter finding, how to run in three commands, limitations (§11), and a clear "not novel research" statement with references to prior work on observation availability.
- Coverage ≥ 85 %.

**M5 — dbt layer**
- `dbt/` project with the dbt-duckdb adapter; sources = Parquet tables; models reproduce `aoi_gaps`, `aoi_wait_daily`, `aoi_survival`, `aoi_monthly`, `aoi_summary`, `catalog_filter_eval`; dbt tests (not null, unique keys, accepted ranges).
- Parity test against `metrics.py` passes on the dev dataset; `make dbt` runs build + test.

**M6 — Self-service app (stretch)**
- Streamlit app: pick AOI(s), period, `min_clear`, W; shows survival curve, monthly heatmap, summary numbers, and the SLA answer; reads Parquet/DuckDB only, no raster work; starts in under 3 seconds on the full dataset.
- Optional: upload a GeoJSON and run discover+process for it with a progress bar (bounded to 1 year, ≤ 2 500 km²).

**M7 — Cloud deployment (optional, costs money, only on the owner's explicit go)**
- Cloud Run job running `open-revisit run` on a schedule via Cloud Scheduler, Parquet to GCS, marts loaded to BigQuery, Looker Studio dashboard over BigQuery; a Pub/Sub-triggered variant of `discover` that processes one pushed STAC item id. Terraform for the resources. Nothing in M0–M6 may depend on M7.

**Project DoD (what the owner must be able to say afterwards):** explain the data model and why observations are grouped by datatake; explain the CRS and tile-overlap handling; quote the effective-vs-nominal result and the catalog-filter error rates from the real run; show the idempotent incremental design; run the whole thing from a clean clone with three commands.

---

## 11. Known pitfalls and limitations (document in README)

- SCL misses thin clouds and haze, over-detects clouds on bright surfaces (snow, sand, urban roofs) and misclassifies some water/shadow. Results are availability statistics under the SCL definition of "clear", not ground truth. The `catalog_filter_eval` compares two imperfect signals; say so.
- High-latitude AOIs (Tromsø, Reykjavík) have far more than the nominal 5-day revisit because of orbit overlap, and polar night reduces usable optical data to near zero in winter; both are features of the result, not bugs.
- Processing baseline changes over time alter SCL behaviour slightly; the period starts in 2022 (baseline ≥ 04.00) to limit this, and `processing_baseline` is stored for stratified checks.
- Late-published or reprocessed items: handled by the watermark overlap and the dedupe rule; mention it.
- Earth Search occasionally returns items with missing `s2:*` fields; all such fields are nullable.
- Fractions use the full AOI as denominator, so partial tile coverage lowers `clear_fraction` by design; `min_coverage` exists to separate "cloudy" from "not covered".

---

## 12. Engineering standards

- Type hints everywhere; `mypy --strict` on `src/`. Ruff for lint and format.
- No network access outside `stac.py` and `raster.py`; both wrap calls with retries and timeouts.
- No silent failures: every skipped or failed unit is counted and logged; counts appear in the run record.
- Deterministic outputs: same inputs and config hash produce byte-identical Parquet (sort before write).
- No data, caches or secrets in git; `data/` and `.venv/` ignored; `reports/` committed.
- Docstrings state units and denominators for every metric function.
- Keep functions small; a module over ~400 lines is a signal to split.
