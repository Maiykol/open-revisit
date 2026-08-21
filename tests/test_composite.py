from __future__ import annotations

import numpy as np

from open_revisit.composite import (
    SceneLayer,
    class_counts,
    composite_scenes,
    summarize_composite,
)
from open_revisit.config import ClassGroups, Thresholds


def test_composite_uses_nodata_order_and_fills_only_empty_pixels() -> None:
    mask = np.ones((2, 4), dtype=np.bool_)
    first = SceneLayer(
        scene_id="lower-nodata",
        nodata_percentage=5.0,
        values=np.array([[4, 4, 0, 0], [6, 0, 5, 0]], dtype=np.uint8),
    )
    second = SceneLayer(
        scene_id="higher-nodata",
        nodata_percentage=20.0,
        values=np.array([[9, 5, 6, 0], [8, 4, 0, 11]], dtype=np.uint8),
    )

    composite = composite_scenes([second, first], mask)

    np.testing.assert_array_equal(
        composite,
        np.array([[4, 4, 6, 0], [6, 4, 5, 11]], dtype=np.uint8),
    )
    counts = class_counts(composite, mask)
    assert sum(counts) == mask.sum()
    assert counts[4] == 3
    assert counts[5] == 1
    assert counts[6] == 2
    assert counts[9] == 0
    assert counts[11] == 1
    assert counts[0] == 1


def test_composite_summary_enforces_fraction_invariants() -> None:
    mask = np.ones((2, 4), dtype=np.bool_)
    values = np.array([[4, 5, 6, 0], [8, 3, 7, 1]], dtype=np.uint8)

    summary = summarize_composite(
        values,
        mask,
        classes=ClassGroups(),
        thresholds=Thresholds(min_clear=0.3, min_coverage=0.8),
    )

    assert sum(summary.class_counts) == summary.n_aoi_pixels == 8
    assert summary.n_covered == 7
    assert summary.covered_fraction == 7 / 8
    assert summary.clear_fraction == 3 / 8
    assert summary.clear_fraction <= summary.covered_fraction <= 1.0
    assert summary.usable is True
