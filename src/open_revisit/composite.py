"""Pure SCL compositing, class counting, and observation fractions."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from open_revisit.config import ClassGroups, Thresholds


@dataclass(frozen=True, slots=True)
class SceneLayer:
    """A scene reprojected onto one AOI's common analysis grid."""

    scene_id: str
    nodata_percentage: float | None
    values: NDArray[np.uint8]


@dataclass(frozen=True, slots=True)
class CompositeSummary:
    """Pixel counts and full-AOI-denominator fractions for one datatake."""

    n_aoi_pixels: int
    n_covered: int
    class_counts: tuple[int, ...]
    covered_fraction: float
    clear_fraction: float
    cloud_fraction: float
    shadow_fraction: float
    snow_fraction: float
    unclassified_fraction: float
    defective_fraction: float
    usable: bool


def _check_values(values: NDArray[np.uint8], mask: NDArray[np.bool_]) -> None:
    if values.shape != mask.shape:
        raise ValueError("SCL array and AOI mask shapes differ")
    if mask.dtype != np.bool_:
        raise TypeError("AOI mask must have boolean dtype")
    if not np.any(mask):
        raise ValueError("AOI mask contains no pixels")
    selected = values[mask]
    if np.any(selected > 11):
        raise ValueError("SCL values must be integer classes 0 through 11")


def class_counts(values: NDArray[np.uint8], mask: NDArray[np.bool_]) -> tuple[int, ...]:
    """Count SCL classes 0-11 over all AOI pixels, including nodata."""
    _check_values(values, mask)
    counts = np.bincount(values[mask], minlength=12)
    return tuple(int(value) for value in counts[:12])


def composite_scenes(
    scenes: list[SceneLayer], mask: NDArray[np.bool_]
) -> NDArray[np.uint8]:
    """Fill nodata by ascending scene nodata percentage without double counting."""
    if not scenes:
        raise ValueError("an observation must contain at least one scene")
    ordered = sorted(
        scenes,
        key=lambda scene: (
            float("inf")
            if scene.nodata_percentage is None
            else scene.nodata_percentage,
            scene.scene_id,
        ),
    )
    first_shape = ordered[0].values.shape
    if first_shape != mask.shape:
        raise ValueError("scene and AOI mask shapes differ")
    composite = np.zeros(first_shape, dtype=np.uint8)
    for scene in ordered:
        _check_values(scene.values, mask)
        empty = (composite == 0) & mask
        composite[empty] = scene.values[empty]
    return composite


def _fraction(counts: tuple[int, ...], classes: tuple[int, ...], total: int) -> float:
    return sum(counts[class_code] for class_code in classes) / total


def summarize_composite(
    values: NDArray[np.uint8],
    mask: NDArray[np.bool_],
    *,
    classes: ClassGroups,
    thresholds: Thresholds,
) -> CompositeSummary:
    """Derive class fractions using all AOI pixels as the denominator."""
    counts = class_counts(values, mask)
    n_aoi_pixels = int(np.count_nonzero(mask))
    if sum(counts) != n_aoi_pixels:
        raise AssertionError("class counts must sum to n_aoi_pixels")
    n_covered = n_aoi_pixels - counts[0]
    covered_fraction = n_covered / n_aoi_pixels
    clear_fraction = _fraction(counts, classes.clear, n_aoi_pixels)
    if covered_fraction > 1.0:
        raise AssertionError("covered_fraction must not exceed one")
    if clear_fraction > covered_fraction:
        raise AssertionError("clear_fraction must not exceed covered_fraction")
    return CompositeSummary(
        n_aoi_pixels=n_aoi_pixels,
        n_covered=n_covered,
        class_counts=counts,
        covered_fraction=covered_fraction,
        clear_fraction=clear_fraction,
        cloud_fraction=_fraction(counts, classes.cloud, n_aoi_pixels),
        shadow_fraction=_fraction(counts, classes.shadow, n_aoi_pixels),
        snow_fraction=_fraction(counts, classes.snow, n_aoi_pixels),
        unclassified_fraction=_fraction(counts, classes.unclassified, n_aoi_pixels),
        defective_fraction=_fraction(counts, classes.defective, n_aoi_pixels),
        usable=(
            covered_fraction >= thresholds.min_coverage
            and clear_fraction >= thresholds.min_clear
        ),
    )
