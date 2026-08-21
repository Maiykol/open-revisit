import json
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import pandas as pd

from open_revisit.stac import (
    deduplicate_scenes,
    item_to_scene_row,
    search_intervals,
    search_start,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "stac" / "berlin_items.json"


def _items() -> list[dict[str, Any]]:
    payload: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return payload["features"]


def test_item_maps_to_expected_scene_row() -> None:
    row = item_to_scene_row(_items()[0], ingested_at=datetime(2026, 8, 21, tzinfo=UTC))

    assert row["scene_id"] == "S2A_32UQD_20250626_0_L2A"
    assert row["collection"] == "sentinel-2-l2a"
    assert row["datatake_id"] == "GS2A_20250626T101701_052286_N05.11"
    assert row["relative_orbit"] == 65
    assert row["mgrs_tile"] == "32UQD"
    assert row["epsg"] == 32632
    assert row["sequence"] == 0
    assert row["eo_cloud_cover"] == 98.219323
    assert row["scl_href"].endswith("/SCL.tif")
    assert row["visual_href"].endswith("/TCI.tif")
    assert isinstance(row["geometry"], bytes)
    assert row["datetime"] == pd.Timestamp("2025-06-26T10:26:27.025000Z")


def test_dedupe_keeps_highest_sequence_and_records_superseded_id() -> None:
    ingested_at = datetime(2026, 8, 21, tzinfo=UTC)
    frame = pd.DataFrame(
        [item_to_scene_row(item, ingested_at=ingested_at) for item in _items()]
    )

    kept, superseded = deduplicate_scenes(frame)

    assert set(kept["scene_id"]) == {
        "S2A_32UQD_20250626_2_L2A",
        "S2A_33UUU_20250626_0_L2A",
    }
    assert superseded.to_dict("records") == [
        {
            "scene_id": "S2A_32UQD_20250626_0_L2A",
            "superseded_by": "S2A_32UQD_20250626_2_L2A",
        }
    ]


def test_dedupe_breaks_sequence_tie_by_latest_generation_time() -> None:
    ingested_at = datetime(2026, 8, 21, tzinfo=UTC)
    first = item_to_scene_row(_items()[0], ingested_at=ingested_at)
    second = dict(first)
    second["scene_id"] = "later-generation"
    second["generation_time"] = pd.Timestamp("2025-07-02T12:00:00Z")
    frame = pd.DataFrame([first, second])

    kept, superseded = deduplicate_scenes(frame)

    assert kept["scene_id"].tolist() == ["later-generation"]
    assert superseded.loc[0, "superseded_by"] == "later-generation"


def test_search_start_uses_seven_day_watermark_overlap() -> None:
    configured_start = datetime(2024, 1, 1, tzinfo=UTC)

    assert search_start(configured_start, None) == configured_start
    assert search_start(configured_start, datetime(2024, 2, 1, tzinfo=UTC)) == datetime(
        2024, 1, 25, tzinfo=UTC
    )
    assert (
        search_start(configured_start, datetime(2024, 1, 3, tzinfo=UTC))
        == configured_start
    )


def test_search_intervals_are_contiguous_and_cover_the_period() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2024, 12, 31, 23, 59, 59, tzinfo=UTC)

    intervals = search_intervals(start, end, chunk_days=90)

    assert len(intervals) == 5
    assert intervals[0][0] == start
    assert intervals[-1][1] == end
    assert all(
        current_end + pd.Timedelta(seconds=1) == next_start
        for (_, current_end), (next_start, _) in pairwise(intervals)
    )
    assert all(
        interval_end - interval_start <= pd.Timedelta(days=90)
        for interval_start, interval_end in intervals
    )
