# AGENTS.md — open-revisit

Source of truth: `docs/SPEC.md` (copied from the planning directory). Deviations go to `docs/DECISIONS.md` with a reason.

## Commands
- `uv sync` — install
- `make check` — ruff (lint + format --check), mypy --strict src/, pytest -m "not network"
- `make test-network` — the single live smoke test
- `open-revisit run --config config/dev.yaml` — dev pipeline (3 AOIs × 2024)
- `open-revisit report --config config/dev.yaml` — figures to reports/

## Rules
- Python 3.12, uv, src layout, Typer CLI, pydantic config, Parquet as system of record, DuckDB as query layer.
- No network access outside `stac.py` and `raster.py`. Tests never touch the network unless marked `network`.
- Every metric function's docstring states the unit and the denominator. Definitions live in `docs/METRICS.md` and must match `metrics.py`.
- Idempotency is a feature: re-running any stage without new input must produce zero new rows. Keys: scenes by `scene_id`; stats by `(aoi_id, scene_id, config_hash)`; observations by `(aoi_id, datatake_id, config_hash)`.
- Observations are grouped by `s2:datatake_id`, never by date. Fractions are computed on the per-AOI 20 m UTM analysis grid after compositing member scenes; never averaged per scene.
- Sort before writing Parquet; outputs must be byte-identical for identical inputs and config hash.
- Never commit `data/`, `.venv/`, or credentials. `reports/` is committed.
- Conventional commits, at least one per milestone.
