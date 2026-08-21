"""Per-AOI analysis grids and polygon masks."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from affine import Affine
from numpy.typing import NDArray
from pyproj import Transformer
from rasterio.crs import CRS
from rasterio.features import geometry_mask
from rasterio.transform import array_bounds, from_origin
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform


@dataclass(frozen=True, slots=True)
class AnalysisGrid:
    """A 20 m AOI grid, expressed in its centroid's UTM CRS."""

    crs: CRS
    transform: Affine
    width: int
    height: int
    aoi_mask: NDArray[np.bool_]

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("analysis grid dimensions must be positive")
        if self.aoi_mask.shape != self.shape:
            raise ValueError("AOI mask shape must match analysis grid")
        if self.aoi_mask.dtype != np.bool_:
            raise TypeError("AOI mask must have boolean dtype")
        if self.n_aoi_pixels == 0:
            raise ValueError("AOI must cover at least one analysis-grid pixel")

    @property
    def shape(self) -> tuple[int, int]:
        """Return raster shape as ``(height, width)``."""
        return (self.height, self.width)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """Return grid bounds as west, south, east, north in the grid CRS."""
        west, south, east, north = array_bounds(self.height, self.width, self.transform)
        return (float(west), float(south), float(east), float(north))

    @property
    def n_aoi_pixels(self) -> int:
        """Return the count of 20 m grid pixels inside the AOI polygon."""
        return int(np.count_nonzero(self.aoi_mask))


def build_analysis_grid(
    aoi_wgs84: BaseGeometry,
    *,
    utm_epsg: int,
    resolution: float = 20.0,
) -> AnalysisGrid:
    """Build an outward-snapped UTM grid and inside-AOI pixel mask."""
    if aoi_wgs84.is_empty or not aoi_wgs84.is_valid:
        raise ValueError("AOI geometry must be non-empty and valid")
    if resolution <= 0.0:
        raise ValueError("analysis-grid resolution must be positive")

    grid_crs = CRS.from_epsg(utm_epsg)
    to_utm = Transformer.from_crs(4326, utm_epsg, always_xy=True).transform
    aoi_utm = transform(to_utm, aoi_wgs84)
    min_x, min_y, max_x, max_y = aoi_utm.bounds
    west = math.floor(min_x / resolution) * resolution
    south = math.floor(min_y / resolution) * resolution
    east = math.ceil(max_x / resolution) * resolution
    north = math.ceil(max_y / resolution) * resolution
    width = round((east - west) / resolution)
    height = round((north - south) / resolution)
    grid_transform = from_origin(west, north, resolution, resolution)
    mask = geometry_mask(
        [mapping(aoi_utm)],
        out_shape=(height, width),
        transform=grid_transform,
        invert=True,
    )
    return AnalysisGrid(
        crs=grid_crs,
        transform=grid_transform,
        width=width,
        height=height,
        aoi_mask=mask,
    )
