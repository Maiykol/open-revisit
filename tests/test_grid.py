from __future__ import annotations

import numpy as np
from pyproj import Transformer
from shapely.geometry import box
from shapely.ops import transform

from open_revisit.grid import build_analysis_grid


def test_analysis_grid_snaps_outward_and_masks_exact_aoi_pixels() -> None:
    aoi_utm = box(500_003.0, 5_799_997.0, 500_197.0, 5_800_203.0)
    to_wgs84 = Transformer.from_crs(32633, 4326, always_xy=True).transform
    aoi_wgs84 = transform(to_wgs84, aoi_utm)

    grid = build_analysis_grid(aoi_wgs84, utm_epsg=32633, resolution=20.0)

    assert grid.bounds == (500_000.0, 5_799_980.0, 500_200.0, 5_800_220.0)
    assert grid.shape == (12, 10)
    assert grid.aoi_mask.dtype == np.bool_
    assert grid.n_aoi_pixels == 100
