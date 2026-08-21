"""Windowed SCL reads and reprojection onto an AOI analysis grid."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from numpy.typing import NDArray
from rasterio.crs import CRS
from rasterio.io import DatasetReader
from rasterio.warp import Resampling, reproject, transform_bounds
from rasterio.windows import Window, from_bounds
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from open_revisit.grid import AnalysisGrid

_GDAL_ENV = {
    "AWS_NO_SIGN_REQUEST": "YES",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_CONNECTTIMEOUT": "10",
    "GDAL_HTTP_TIMEOUT": "60",
    "GDAL_HTTP_MAX_RETRY": "0",
    "VSI_CACHE": "TRUE",
}


@dataclass(frozen=True, slots=True)
class SclWindowRead:
    """One windowed SCL array reprojected to an analysis grid."""

    values: NDArray[np.uint8]
    source_window: Window
    source_crs: CRS


def _source_window(dataset: DatasetReader, grid: AnalysisGrid) -> Window:
    if dataset.crs is None:
        raise ValueError("SCL raster has no CRS")
    source_bounds = transform_bounds(
        grid.crs,
        dataset.crs,
        *grid.bounds,
        densify_pts=21,
    )
    raw = from_bounds(*source_bounds, transform=dataset.transform)
    col_start = max(0, math.floor(raw.col_off) - 1)
    row_start = max(0, math.floor(raw.row_off) - 1)
    col_stop = min(dataset.width, math.ceil(raw.col_off + raw.width) + 1)
    row_stop = min(dataset.height, math.ceil(raw.row_off + raw.height) + 1)
    if col_start >= col_stop or row_start >= row_stop:
        raise ValueError("AOI analysis grid does not overlap the SCL raster")
    return Window(
        col_off=col_start,
        row_off=row_start,
        width=col_stop - col_start,
        height=row_stop - row_start,
    )


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential_jitter(initial=1, max=30),
    reraise=True,
)
def read_scl_window(href: str | Path, grid: AnalysisGrid) -> SclWindowRead:
    """Read only the source window needed and reproject it to ``grid``."""
    with rasterio.Env(**_GDAL_ENV), rasterio.open(str(href)) as dataset:
        if dataset.count < 1:
            raise ValueError("SCL raster has no bands")
        if dataset.crs is None:
            raise ValueError("SCL raster has no CRS")
        if dataset.transform.is_identity:
            raise ValueError("SCL raster has an identity geotransform")
        source_window = _source_window(dataset, grid)
        source = dataset.read(1, window=source_window)
        destination = np.zeros(grid.shape, dtype=np.uint8)
        source_transform = dataset.window_transform(source_window)
        if source_transform.is_identity:
            raise ValueError("SCL source window has an identity geotransform")
        reproject(
            source=source,
            destination=destination,
            src_transform=source_transform,
            src_crs=dataset.crs,
            src_nodata=0,
            dst_transform=grid.transform,
            dst_crs=grid.crs,
            dst_nodata=0,
            resampling=Resampling.nearest,
            init_dest_nodata=True,
        )
        return SclWindowRead(
            values=destination,
            source_window=source_window,
            source_crs=dataset.crs,
        )


def write_composite(path: Path, values: NDArray[np.uint8], grid: AnalysisGrid) -> None:
    """Persist one compressed analysis-grid composite GeoTIFF."""
    if values.shape != grid.shape:
        raise ValueError("composite shape must match analysis grid")
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=grid.width,
        height=grid.height,
        count=1,
        dtype="uint8",
        crs=grid.crs,
        transform=grid.transform,
        nodata=0,
        compress="DEFLATE",
        predictor=1,
    ) as dataset:
        dataset.write(values, 1)
