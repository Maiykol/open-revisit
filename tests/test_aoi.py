import csv
from pathlib import Path

import geopandas as gpd
import pytest
from pyproj import Transformer
from shapely import transform

from open_revisit.aoi import (
    AoiRecord,
    build_aoi_files,
    build_square,
    read_aoi_geojson,
    utm_epsg_for_point,
    write_aoi_geojson,
)


def test_square_has_expected_area_and_utm_zone() -> None:
    geometry, utm_epsg = build_square(lat=52.52, lon=13.405, size_km=20.0)
    to_utm = Transformer.from_crs(4326, utm_epsg, always_xy=True)
    area_km2 = transform(geometry, to_utm.transform, interleaved=False).area / 1e6

    assert utm_epsg == 32633
    assert area_km2 == pytest.approx(400.0, rel=0.005)


@pytest.mark.parametrize(
    ("lat", "lon", "expected"),
    [(52.52, 13.405, 32633), (69.6492, 18.9553, 32634), (-33.9, 18.4, 32734)],
)
def test_utm_epsg_for_point(lat: float, lon: float, expected: int) -> None:
    assert utm_epsg_for_point(lat=lat, lon=lon) == expected


def test_geojson_round_trip(tmp_path: Path) -> None:
    geometry, utm_epsg = build_square(lat=52.52, lon=13.405, size_km=20.0)
    record = AoiRecord(
        aoi_id="berlin",
        name="Berlin",
        country="DE",
        lat=52.52,
        lon=13.405,
        utm_epsg=utm_epsg,
        area_km2=400.0,
        geometry=geometry,
    )
    path = tmp_path / "berlin.geojson"

    write_aoi_geojson(record, path)
    restored = read_aoi_geojson(path)

    assert restored.aoi_id == record.aoi_id
    assert restored.utm_epsg == record.utm_epsg
    assert restored.geometry.equals_exact(record.geometry, tolerance=1e-12)


def test_build_aoi_files_writes_geojson_and_geoparquet(tmp_path: Path) -> None:
    csv_path = tmp_path / "centroids.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["aoi_id", "name", "country", "lat", "lon"]
        )
        writer.writeheader()
        writer.writerow(
            {
                "aoi_id": "berlin",
                "name": "Berlin",
                "country": "DE",
                "lat": "52.52",
                "lon": "13.405",
            }
        )
    output_dir = tmp_path / "aois"
    parquet_path = tmp_path / "data" / "aois.parquet"

    records = build_aoi_files(csv_path, output_dir, parquet_path, size_km=20.0)
    frame = gpd.read_parquet(parquet_path)

    assert len(records) == 1
    assert (output_dir / "berlin.geojson").exists()
    assert frame.loc[0, "aoi_id"] == "berlin"
    assert frame.crs is not None and frame.crs.to_epsg() == 4326
    assert frame.loc[0, "area_km2"] == pytest.approx(400.0, rel=0.005)
