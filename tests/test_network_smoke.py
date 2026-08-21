from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from open_revisit.aoi import read_aoi_geojson
from open_revisit.composite import class_counts
from open_revisit.config import AppConfig
from open_revisit.grid import build_analysis_grid
from open_revisit.raster import read_scl_window
from open_revisit.stac import fetch_items


@pytest.mark.network
def test_live_berlin_discovery_and_scl_window() -> None:
    aoi = read_aoi_geojson(Path("aois/berlin.geojson"))
    config = AppConfig.model_validate(
        {
            "start": "2025-06-26",
            "end": "2025-06-26",
            "aois": ["berlin"],
        }
    )
    items = fetch_items(
        config,
        aoi.geometry,
        datetime(2025, 6, 26, tzinfo=UTC),
        datetime(2025, 6, 26, 23, 59, 59, tzinfo=UTC),
    )
    assert items
    scl_href = items[0]["assets"]["scl"]["href"]
    grid = build_analysis_grid(aoi.geometry, utm_epsg=aoi.utm_epsg)

    result = read_scl_window(scl_href, grid)
    counts = class_counts(result.values, grid.aoi_mask)

    assert result.values.shape == grid.shape
    assert sum(counts) == grid.n_aoi_pixels
    assert np.count_nonzero(result.values[grid.aoi_mask]) > 0
