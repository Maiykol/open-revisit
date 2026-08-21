"""AOI construction, validation, and GeoJSON/GeoParquet I/O."""

from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
from pyproj import Transformer
from shapely.geometry import Polygon, box, mapping, shape
from shapely.ops import transform

AOI_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True, slots=True)
class AoiRecord:
    """A WGS84 AOI and its identifying metadata."""

    aoi_id: str
    name: str
    country: str
    lat: float
    lon: float
    utm_epsg: int
    area_km2: float
    geometry: Polygon


def utm_epsg_for_point(*, lat: float, lon: float) -> int:
    """Return the local WGS84 UTM EPSG code for a latitude/longitude point."""
    if not -80.0 <= lat <= 84.0:
        raise ValueError("UTM is defined only between 80°S and 84°N")
    if not -180.0 <= lon <= 180.0:
        raise ValueError("longitude must be between -180 and 180 degrees")
    zone = min(60, math.floor((lon + 180.0) / 6.0) + 1)
    return (32600 if lat >= 0.0 else 32700) + zone


def build_square(*, lat: float, lon: float, size_km: float) -> tuple[Polygon, int]:
    """Build a true square in local UTM and return it in WGS84."""
    if size_km <= 0.0:
        raise ValueError("size_km must be positive")
    utm_epsg = utm_epsg_for_point(lat=lat, lon=lon)
    to_utm = Transformer.from_crs(4326, utm_epsg, always_xy=True)
    to_wgs84 = Transformer.from_crs(utm_epsg, 4326, always_xy=True)
    centre_x, centre_y = to_utm.transform(lon, lat)
    half_size_m = size_km * 500.0
    square_utm = box(
        centre_x - half_size_m,
        centre_y - half_size_m,
        centre_x + half_size_m,
        centre_y + half_size_m,
    )
    geometry = transform(to_wgs84.transform, square_utm)
    if not isinstance(geometry, Polygon):
        raise TypeError("square reprojection did not produce a polygon")
    return geometry, utm_epsg


def _record_properties(record: AoiRecord) -> dict[str, str | int | float]:
    values = asdict(record)
    values.pop("geometry")
    return values


def write_aoi_geojson(record: AoiRecord, path: Path) -> None:
    """Write one AOI as a deterministic WGS84 GeoJSON feature."""
    feature = {
        "type": "Feature",
        "properties": _record_properties(record),
        "geometry": mapping(record.geometry),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(feature, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def read_aoi_geojson(path: Path) -> AoiRecord:
    """Read an AOI written by :func:`write_aoi_geojson`."""
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("type") != "Feature":
        raise ValueError(f"expected one GeoJSON Feature: {path}")
    properties = payload.get("properties")
    geometry_value = payload.get("geometry")
    if not isinstance(properties, dict) or not isinstance(geometry_value, dict):
        raise ValueError(f"invalid GeoJSON feature: {path}")
    geometry = shape(geometry_value)
    if not isinstance(geometry, Polygon):
        raise ValueError(f"AOI geometry must be a Polygon: {path}")
    return AoiRecord(
        aoi_id=str(properties["aoi_id"]),
        name=str(properties["name"]),
        country=str(properties["country"]),
        lat=float(properties["lat"]),
        lon=float(properties["lon"]),
        utm_epsg=int(properties["utm_epsg"]),
        area_km2=float(properties["area_km2"]),
        geometry=geometry,
    )


def _record_from_csv_row(row: dict[str, str], size_km: float) -> AoiRecord:
    required = {"aoi_id", "name", "country", "lat", "lon"}
    missing = required - row.keys()
    if missing:
        raise ValueError(f"centroid row is missing columns: {sorted(missing)}")
    aoi_id = row["aoi_id"]
    if AOI_ID_PATTERN.fullmatch(aoi_id) is None:
        raise ValueError(f"invalid AOI id: {aoi_id!r}")
    lat = float(row["lat"])
    lon = float(row["lon"])
    geometry, utm_epsg = build_square(lat=lat, lon=lon, size_km=size_km)
    to_utm = Transformer.from_crs(4326, utm_epsg, always_xy=True)
    area_km2 = transform(to_utm.transform, geometry).area / 1_000_000.0
    return AoiRecord(
        aoi_id=aoi_id,
        name=row["name"],
        country=row["country"],
        lat=lat,
        lon=lon,
        utm_epsg=utm_epsg,
        area_km2=area_km2,
        geometry=geometry,
    )


def build_aoi_files(
    csv_path: Path,
    output_dir: Path,
    parquet_path: Path,
    *,
    size_km: float = 20.0,
) -> list[AoiRecord]:
    """Build equal-area AOIs from centroids and write GeoJSON plus GeoParquet."""
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        records = [_record_from_csv_row(row, size_km) for row in reader]
    if not records:
        raise ValueError("centroid CSV contains no AOIs")
    aoi_ids = [record.aoi_id for record in records]
    if len(aoi_ids) != len(set(aoi_ids)):
        raise ValueError("centroid CSV contains duplicate AOI ids")

    sorted_records = sorted(records, key=lambda record: record.aoi_id)
    for record in sorted_records:
        write_aoi_geojson(record, output_dir / f"{record.aoi_id}.geojson")

    frame = gpd.GeoDataFrame(
        [
            {
                **_record_properties(record),
                "geometry": record.geometry,
            }
            for record in sorted_records
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(parquet_path, index=False, compression="zstd")
    return sorted_records
