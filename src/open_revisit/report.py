"""M4 report orchestration from persisted Parquet inputs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np

from open_revisit.config import AppConfig
from open_revisit.grid import AnalysisGrid, build_analysis_grid
from open_revisit.raster import RgbChipRead, read_rgb_chip
from open_revisit.report_data import (
    load_report_tables,
    render_summary_table,
    select_rgb_examples,
    select_survival_aois,
    service_level_frame,
)
from open_revisit.report_figures import (
    plot_catalog_filter,
    plot_europe_map,
    plot_monthly_heatmap,
    plot_revisit_dumbbell,
    plot_rgb_examples,
    plot_service_level,
    plot_survival_curves,
)

RgbReader = Callable[[str | Path, AnalysisGrid], RgbChipRead]
DEFAULT_COASTLINE_PATH = (
    Path(__file__).resolve().parents[2] / "assets" / "natural_earth_europe.geojson"
)


@dataclass(frozen=True, slots=True)
class ReportSummary:
    """Artifacts and deterministic selections produced by one report run."""

    config_hash: str
    artifacts: tuple[Path, ...]
    survival_aois: tuple[str, ...]
    rgb_examples: tuple[str, ...]
    bytes_transferred: int


def _names(aois: gpd.GeoDataFrame) -> dict[str, str]:
    return {str(row.aoi_id): str(row.name) for row in aois.itertuples(index=False)}


def run_report(
    config: AppConfig,
    *,
    output_dir: Path = Path("reports"),
    coastline_path: Path = DEFAULT_COASTLINE_PATH,
    rgb_reader: RgbReader | None = None,
) -> ReportSummary:
    """Generate all seven figures and the Markdown summary table."""
    if not coastline_path.exists():
        raise FileNotFoundError(f"offline coastline asset not found: {coastline_path}")
    tables = load_report_tables(config)
    names = _names(tables.aois)
    figure_dir = output_dir / "figures"
    table_dir = output_dir / "tables"
    selected_survival = select_survival_aois(tables.summary)
    service = service_level_frame(tables.wait_daily)
    examples = select_rgb_examples(tables.observations)
    reader = read_rgb_chip if rgb_reader is None else rgb_reader
    aoi_lookup = {
        str(row.aoi_id): (row.geometry, int(row.utm_epsg))
        for row in tables.aois.itertuples(index=False)
    }
    chips: dict[str, np.ndarray] = {}
    rgb_bytes = 0
    for row in examples.itertuples(index=False):
        geometry, utm_epsg = aoi_lookup[str(row.aoi_id)]
        grid = build_analysis_grid(geometry, utm_epsg=utm_epsg)
        linked_ids = tables.scene_aoi.loc[
            tables.scene_aoi["aoi_id"] == row.aoi_id, "scene_id"
        ]
        members = tables.scenes.loc[
            tables.scenes["scene_id"].isin(linked_ids)
            & tables.scenes["datatake_id"].eq(row.datatake_id)
            & tables.scenes["visual_href"].notna()
        ].copy()
        members["_nodata"] = members["s2_nodata_pct"].fillna(float("inf"))
        members = members.sort_values(["_nodata", "scene_id"], kind="stable")
        if members.empty:
            raise ValueError("selected RGB example has no linked visual assets")
        composite = np.zeros((grid.height, grid.width, 3), dtype=np.uint8)
        filled = np.zeros(grid.shape, dtype=np.bool_)
        for member in members.itertuples(index=False):
            chip = reader(str(member.visual_href), grid)
            valid = (
                chip.valid_mask
                if chip.valid_mask is not None
                else np.any(chip.values != 0, axis=2)
            )
            fill = valid & ~filled
            composite[fill] = chip.values[fill]
            filled |= valid
            rgb_bytes += chip.bytes_transferred
        chips[str(row.case)] = composite

    artifacts = (
        plot_revisit_dumbbell(
            tables.summary, names, figure_dir / "01_revisit_dumbbell.png"
        ),
        plot_europe_map(
            tables.summary,
            tables.aois,
            names,
            coastline_path,
            figure_dir / "02_europe_map.png",
        ),
        plot_monthly_heatmap(
            tables.monthly, names, figure_dir / "03_monthly_reliability.png"
        ),
        plot_survival_curves(
            tables.survival,
            selected_survival,
            names,
            figure_dir / "04_wait_survival.png",
        ),
        plot_catalog_filter(
            tables.observations,
            tables.catalog_filter,
            min_clear=config.thresholds.min_clear,
            path=figure_dir / "05_catalog_filter.png",
        ),
        plot_service_level(service, names, figure_dir / "06_service_level.png"),
        plot_rgb_examples(examples, chips, names, figure_dir / "07_rgb_examples.png"),
    )
    table_dir.mkdir(parents=True, exist_ok=True)
    table_path = table_dir / "aoi_summary.md"
    table_path.write_text(render_summary_table(tables.summary, names), encoding="utf-8")
    return ReportSummary(
        config_hash=config.config_hash(),
        artifacts=(*artifacts, table_path),
        survival_aois=selected_survival,
        rgb_examples=tuple(str(value) for value in examples["datatake_id"]),
        bytes_transferred=rgb_bytes,
    )
