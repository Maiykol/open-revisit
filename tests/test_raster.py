from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

from open_revisit.grid import AnalysisGrid
from open_revisit.raster import read_rgb_chip, read_scl_window


def _grid(
    *,
    epsg: int,
    transform: Affine,
    width: int = 10,
    height: int = 10,
) -> AnalysisGrid:
    return AnalysisGrid(
        crs=CRS.from_epsg(epsg),
        transform=transform,
        width=width,
        height=height,
        aoi_mask=np.ones((height, width), dtype=np.bool_),
    )


def _write_synthetic_cog(
    path: Path,
    values: np.ndarray,
    *,
    epsg: int,
    transform: Affine,
) -> None:
    with rasterio.open(
        path,
        "w",
        driver="COG",
        width=values.shape[1],
        height=values.shape[0],
        count=1,
        dtype="uint8",
        crs=f"EPSG:{epsg}",
        transform=transform,
        nodata=0,
        compress="DEFLATE",
        blocksize=128,
    ) as dataset:
        dataset.write(values, 1)


def test_windowed_read_has_exact_class_counts(tmp_path: Path) -> None:
    values = np.zeros((30, 30), dtype=np.uint8)
    target = np.repeat(np.arange(1, 11, dtype=np.uint8), 10).reshape(10, 10)
    values[10:20, 10:20] = target
    path = tmp_path / "classes.tif"
    _write_synthetic_cog(
        path,
        values,
        epsg=32633,
        transform=from_origin(499_800.0, 5_800_200.0, 20.0, 20.0),
    )
    grid = _grid(
        epsg=32633,
        transform=from_origin(500_000.0, 5_800_000.0, 20.0, 20.0),
    )

    result = read_scl_window(path, grid)

    assert result.source_window.width == 12
    assert result.source_window.height == 12
    assert result.source_window.width < values.shape[1]
    assert result.source_window.height < values.shape[0]
    counts = np.bincount(result.values[grid.aoi_mask], minlength=12)
    assert counts.tolist() == [0, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 0]


def test_reprojects_across_utm_zones_with_exact_counts(tmp_path: Path) -> None:
    grid_transform = from_origin(390_000.0, 5_830_000.0, 20.0, 20.0)
    grid = _grid(epsg=32633, transform=grid_transform)
    source_bounds = transform_bounds(
        grid.crs,
        CRS.from_epsg(32632),
        *grid.bounds,
        densify_pts=21,
    )
    left = np.floor(source_bounds[0] / 20.0) * 20.0 - 200.0
    top = np.ceil(source_bounds[3] / 20.0) * 20.0 + 200.0
    values = np.full((40, 40), 6, dtype=np.uint8)
    path = tmp_path / "zone32.tif"
    _write_synthetic_cog(
        path,
        values,
        epsg=32632,
        transform=from_origin(left, top, 20.0, 20.0),
    )

    result = read_scl_window(path, grid)

    assert result.source_crs == CRS.from_epsg(32632)
    counts = np.bincount(result.values[grid.aoi_mask], minlength=12)
    assert counts[6] == 100
    assert counts.sum() == 100


def test_nodata_stripes_survive_windowed_read(tmp_path: Path) -> None:
    values = np.full((10, 10), 4, dtype=np.uint8)
    values[:, :2] = 0
    path = tmp_path / "nodata.tif"
    transform_value = from_origin(500_000.0, 5_800_000.0, 20.0, 20.0)
    _write_synthetic_cog(
        path,
        values,
        epsg=32633,
        transform=transform_value,
    )
    grid = _grid(epsg=32633, transform=transform_value)

    result = read_scl_window(path, grid)

    counts = np.bincount(result.values[grid.aoi_mask], minlength=12)
    assert counts[0] == 20
    assert counts[4] == 80
    assert counts.sum() == grid.n_aoi_pixels


def test_rgb_chip_reads_three_bands_and_accounts_source_bytes(tmp_path: Path) -> None:
    path = tmp_path / "visual.tif"
    transform_value = from_origin(500_000.0, 5_800_000.0, 20.0, 20.0)
    source = np.zeros((3, 10, 10), dtype=np.uint8)
    source[0] = 20
    source[1] = 100
    source[2] = 220
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=10,
        height=10,
        count=3,
        dtype="uint8",
        crs="EPSG:32633",
        transform=transform_value,
    ) as dataset:
        dataset.write(source)
    grid = _grid(epsg=32633, transform=transform_value)

    result = read_rgb_chip(path, grid)

    assert result.values.shape == (10, 10, 3)
    assert result.values[5, 5].tolist() == [20, 100, 220]
    assert result.bytes_transferred == 300
    assert result.valid_mask is not None
    assert result.valid_mask.all()
