"""Validated configuration loading and hashing."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClassGroups(BaseModel):
    """SCL class groups used when deriving observation fractions."""

    model_config = ConfigDict(frozen=True)

    clear: tuple[int, ...] = (4, 5, 6)
    cloud: tuple[int, ...] = (8, 9, 10)
    shadow: tuple[int, ...] = (2, 3)
    snow: tuple[int, ...] = (11,)
    unclassified: tuple[int, ...] = (7,)
    defective: tuple[int, ...] = (1,)
    nodata: tuple[int, ...] = (0,)


class Thresholds(BaseModel):
    """Usability thresholds expressed as fractions in the closed unit interval."""

    model_config = ConfigDict(frozen=True)

    min_clear: float = Field(default=0.80, ge=0.0, le=1.0)
    min_coverage: float = Field(default=0.95, ge=0.0, le=1.0)


class AppConfig(BaseModel):
    """Resolved configuration shared by pipeline stages."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    stac_url: str = "https://earth-search.aws.element84.com/v1"
    collection: str = "sentinel-2-l2a"
    start: date
    end: date
    aoi_ids: tuple[str, ...] = Field(alias="aois")
    data_dir: Path = Path("data")
    stac_fixture: Path | None = None
    late_overlap_days: int = Field(default=7, ge=0)
    max_aoi_km2: float = Field(default=2500.0, gt=0.0)
    horizon_days: int = Field(default=60, gt=0)
    classes: ClassGroups = Field(default_factory=ClassGroups)
    thresholds: Thresholds = Field(default_factory=Thresholds)

    @model_validator(mode="after")
    def validate_period_and_classes(self) -> AppConfig:
        """Reject inverted periods, duplicate AOIs, and overlapping SCL classes."""
        if self.end < self.start:
            raise ValueError("end must be on or after start")
        if len(self.aoi_ids) != len(set(self.aoi_ids)):
            raise ValueError("AOI ids must be unique")
        grouped_classes = [
            *self.classes.clear,
            *self.classes.cloud,
            *self.classes.shadow,
            *self.classes.snow,
            *self.classes.unclassified,
            *self.classes.defective,
            *self.classes.nodata,
        ]
        if len(grouped_classes) != len(set(grouped_classes)):
            raise ValueError("SCL classes must occur in exactly one class group")
        if set(grouped_classes) != set(range(12)):
            raise ValueError("SCL class groups must cover integer classes 0 through 11")
        return self

    def config_hash(self) -> str:
        """Return a stable SHA-256 hash of the fully resolved configuration."""
        payload = self.model_dump(mode="json", by_alias=True)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_config(path: Path) -> AppConfig:
    """Load and validate a YAML configuration file."""
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"configuration must be a YAML mapping: {path}")
    return AppConfig.model_validate(raw)
