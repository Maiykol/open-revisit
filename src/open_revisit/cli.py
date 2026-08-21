"""Command-line interface for open-revisit."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated

import geopandas as gpd
import typer

from open_revisit import __version__
from open_revisit.aoi import build_aoi_files
from open_revisit.config import Thresholds, load_config
from open_revisit.discovery import AoiDiscoveryCounts, run_discovery
from open_revisit.logging import emit_event
from open_revisit.metric_pipeline import query_sla, run_metrics
from open_revisit.processing import AoiProcessCounts, run_processing
from open_revisit.report import run_report
from open_revisit.run_pipeline import execute_run

app = typer.Typer(
    name="open-revisit",
    help="Turn satellite acquisitions into useful-observation service metrics.",
    no_args_is_help=True,
)
aois_app = typer.Typer(help="Build and validate areas of interest.")
app.add_typer(aois_app, name="aois")


@dataclass(frozen=True, slots=True)
class CliState:
    """Global CLI output settings."""

    json_logs: bool = False


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def main(
    context: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
    json_logs: Annotated[
        bool,
        typer.Option("--json-logs", help="Emit structured log lines as JSON."),
    ] = False,
) -> None:
    """Run open-revisit commands."""
    context.obj = CliState(json_logs=json_logs)


def _state(context: typer.Context) -> CliState:
    state = context.obj
    if not isinstance(state, CliState):
        return CliState()
    return state


@aois_app.command("build")
def aois_build(
    context: typer.Context,
    centroids: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="CSV with aoi_id,name,country,lat,lon columns.",
        ),
    ],
    size_km: Annotated[
        float, typer.Option("--size-km", min=0.001, help="Square side length in km.")
    ] = 20.0,
    output_dir: Annotated[
        Path, typer.Option("--output-dir", help="Directory for per-AOI GeoJSON.")
    ] = Path("aois"),
    parquet_path: Annotated[
        Path, typer.Option("--parquet-path", help="Output GeoParquet table.")
    ] = Path("data/aois.parquet"),
) -> None:
    """Build equal-area square AOIs around centroid points."""
    records = build_aoi_files(centroids, output_dir, parquet_path, size_km=size_km)
    emit_event(
        "aoi.build",
        json_logs=_state(context).json_logs,
        input_rows=len(records),
        output_rows=len(records),
        size_km=size_km,
        parquet=str(parquet_path),
    )


def _date_override(value: str | None, *, option: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise typer.BadParameter("expected YYYY-MM-DD", param_hint=option) from error


@app.command("discover")
def discover(
    context: typer.Context,
    config_path: Annotated[
        Path,
        typer.Option(
            "--config",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Validated YAML configuration.",
        ),
    ] = Path("config/default.yaml"),
    aoi: Annotated[
        str | None, typer.Option("--aoi", help="Restrict discovery to one AOI id.")
    ] = None,
    start: Annotated[
        str | None, typer.Option("--start", help="Override start date (YYYY-MM-DD).")
    ] = None,
    end: Annotated[
        str | None, typer.Option("--end", help="Override end date (YYYY-MM-DD).")
    ] = None,
) -> None:
    """Discover Sentinel-2 scenes intersecting configured AOIs."""
    config = load_config(config_path)
    updates: dict[str, object] = {}
    if aoi is not None:
        updates["aoi_ids"] = (aoi,)
    start_value = _date_override(start, option="--start")
    end_value = _date_override(end, option="--end")
    if start_value is not None:
        updates["start"] = start_value
    if end_value is not None:
        updates["end"] = end_value
    if updates:
        config = config.model_copy(update=updates)
        config = type(config).model_validate(config.model_dump())

    aoi_path = config.data_dir / "aois.parquet"
    if not aoi_path.exists():
        raise FileNotFoundError(
            f"AOI table not found: {aoi_path}; run 'open-revisit aois build' first"
        )
    aois = gpd.read_parquet(aoi_path)
    state = _state(context)

    def log_aoi(counts: AoiDiscoveryCounts) -> None:
        emit_event(
            "discover.aoi",
            json_logs=state.json_logs,
            aoi_id=counts.aoi_id,
            items_fetched=counts.fetched,
            new=counts.new,
            superseded=counts.superseded,
            watermark=counts.watermark,
        )

    summary = run_discovery(config, aois, on_aoi_complete=log_aoi)
    emit_event(
        "discover.complete",
        json_logs=state.json_logs,
        scenes=summary.n_scenes,
        scene_aoi=summary.n_scene_aoi,
        scenes_superseded=summary.n_superseded,
        bytes_transferred=summary.bytes_transferred,
        config_hash=config.config_hash(),
    )


@app.command("process")
def process(
    context: typer.Context,
    config_path: Annotated[
        Path,
        typer.Option(
            "--config",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Validated YAML configuration.",
        ),
    ] = Path("config/default.yaml"),
    aoi: Annotated[
        str | None, typer.Option("--aoi", help="Restrict processing to one AOI id.")
    ] = None,
    workers: Annotated[
        int, typer.Option("--workers", min=1, help="Concurrent raster readers.")
    ] = 8,
    force: Annotated[
        bool, typer.Option("--force", help="Recompute already-processed keys.")
    ] = False,
    keep_rasters: Annotated[
        bool,
        typer.Option(
            "--keep-rasters", help="Persist compressed per-observation composites."
        ),
    ] = False,
) -> None:
    """Read SCL windows and derive per-scene and per-datatake observations."""
    config = load_config(config_path)
    if aoi is not None:
        config = config.model_copy(update={"aoi_ids": (aoi,)})
    aoi_path = config.data_dir / "aois.parquet"
    if not aoi_path.exists():
        raise FileNotFoundError(
            f"AOI table not found: {aoi_path}; run 'open-revisit aois build' first"
        )
    aois = gpd.read_parquet(aoi_path)
    state = _state(context)

    def log_aoi(counts: AoiProcessCounts) -> None:
        emit_event(
            "process.aoi",
            json_logs=state.json_logs,
            aoi_id=counts.aoi_id,
            scenes=counts.scenes,
            observations=counts.observations,
            usable=counts.usable,
            failed_scenes=counts.failed_scenes,
            skipped_scenes=counts.skipped_scenes,
            skipped_observations=counts.skipped_observations,
        )

    summary = run_processing(
        config,
        aois,
        workers=workers,
        force=force,
        keep_rasters=keep_rasters,
        on_aoi_complete=log_aoi,
    )
    emit_event(
        "process.complete",
        json_logs=state.json_logs,
        processed_scenes=summary.processed_scenes,
        processed_observations=summary.processed_observations,
        skipped_scenes=summary.skipped_scenes,
        skipped_observations=summary.skipped_observations,
        failed_scenes=summary.failed_scenes,
        bytes_transferred=summary.bytes_transferred,
        scene_stats=summary.n_scene_stats,
        observations=summary.n_observations,
        usable=summary.n_usable,
        config_hash=config.config_hash(),
    )


@app.command("metrics")
def metrics_command(
    context: typer.Context,
    config_path: Annotated[
        Path,
        typer.Option(
            "--config",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Validated YAML configuration.",
        ),
    ] = Path("config/default.yaml"),
) -> None:
    """Compute service metrics from the existing observation table."""
    config = load_config(config_path)
    summary = run_metrics(config)
    emit_event(
        "metrics.complete",
        json_logs=_state(context).json_logs,
        **summary.table_rows,
        config_hash=summary.config_hash,
    )


@app.command("report")
def report_command(
    context: typer.Context,
    config_path: Annotated[
        Path,
        typer.Option(
            "--config",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Config identifying existing Parquet report inputs.",
        ),
    ] = Path("config/default.yaml"),
    output_dir: Annotated[
        Path, typer.Option("--output-dir", help="Report artifact directory.")
    ] = Path("reports"),
) -> None:
    """Generate the seven case-study figures and summary table."""
    summary = run_report(load_config(config_path), output_dir=output_dir)
    emit_event(
        "report.complete",
        json_logs=_state(context).json_logs,
        artifacts=[str(path) for path in summary.artifacts],
        survival_aois=list(summary.survival_aois),
        rgb_examples=list(summary.rgb_examples),
        bytes_transferred=summary.bytes_transferred,
        config_hash=summary.config_hash,
    )


@app.command("run")
def run_command(
    context: typer.Context,
    config_path: Annotated[
        Path,
        typer.Option(
            "--config",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Validated YAML configuration.",
        ),
    ] = Path("config/default.yaml"),
    workers: Annotated[
        int, typer.Option("--workers", min=1, help="Concurrent raster readers.")
    ] = 8,
) -> None:
    """Run discover, process, metrics, and report with a durable run record."""
    summary = execute_run(load_config(config_path), workers=workers)
    emit_event(
        "run.complete",
        json_logs=_state(context).json_logs,
        record=str(summary.record_path),
        wall_clock_seconds=summary.wall_clock_seconds,
        bytes_transferred=summary.bytes_transferred,
        config_hash=summary.config_hash,
    )


@app.command("sla")
def sla(
    aoi: Annotated[str, typer.Option("--aoi", help="AOI id to query.")],
    every: Annotated[
        int,
        typer.Option("--every", min=1, help="Required observation interval in days."),
    ],
    config_path: Annotated[
        Path,
        typer.Option(
            "--config",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Config identifying the existing metric table rows.",
        ),
    ] = Path("config/default.yaml"),
    min_clear: Annotated[
        float | None,
        typer.Option(
            "--min-clear",
            min=0.0,
            max=1.0,
            help="Select rows for a config with this clear-fraction threshold.",
        ),
    ] = None,
    month: Annotated[
        int | None,
        typer.Option("--month", min=1, max=12, help="Restrict by month of t0."),
    ] = None,
) -> None:
    """Answer an SLA question using only an existing daily-wait metric table."""
    config = load_config(config_path)
    if min_clear is not None:
        config = config.model_copy(
            update={
                "thresholds": Thresholds(
                    min_clear=min_clear,
                    min_coverage=config.thresholds.min_coverage,
                )
            }
        )
    result = query_sla(
        config.data_dir,
        aoi_id=aoi,
        config_hash=config.config_hash(),
        every_days=every,
        month=month,
    )
    typer.echo(
        f"aoi_id={result.aoi_id} every_days={result.every_days} "
        f"month={result.month if result.month is not None else 'ALL'} "
        f"success_rate={result.success_rate:.12g} n_days={result.n_days} "
        f"config_hash={result.config_hash}"
    )
