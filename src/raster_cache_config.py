"""Explicit, filesystem-sized configuration for the experimental raster cache."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from src.raster_cache import CachePolicy, RasterCache
from src.raster_reservations import QueueCleanupPolicy

GIB = 1024 ** 3
DAY = 24 * 60 * 60


@dataclass(frozen=True)
class RasterCacheConfig:
    directory: Path
    max_bytes: int
    min_free_bytes: int
    unknown_asset_bytes: int
    high_water_bytes: int
    target_bytes: int
    max_unused_age_s: int
    metadata_refresh_s: int
    partial_expiry_s: int
    queue_lookahead: int
    near_distance_m: int

    def __post_init__(self):
        names = ("max_bytes", "min_free_bytes", "unknown_asset_bytes", "high_water_bytes",
                 "target_bytes", "max_unused_age_s", "metadata_refresh_s", "partial_expiry_s",
                 "queue_lookahead", "near_distance_m")
        for name in names:
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if (self.max_bytes == 0 or self.unknown_asset_bytes == 0
                or self.metadata_refresh_s == 0 or self.queue_lookahead == 0):
            raise ValueError("Cache budget, unknown asset bound, refresh interval and lookahead must be positive")
        if not 0 <= self.target_bytes < self.high_water_bytes <= self.max_bytes:
            raise ValueError("Cache thresholds must satisfy target < high water <= maximum")

    def cache(self) -> RasterCache:
        return RasterCache(self.directory, CachePolicy(
            self.max_bytes, self.min_free_bytes, self.unknown_asset_bytes))

    def cleanup_policy(self) -> QueueCleanupPolicy:
        return QueueCleanupPolicy(self.queue_lookahead, self.near_distance_m,
                                  self.max_unused_age_s, self.partial_expiry_s,
                                  self.high_water_bytes, self.target_bytes)

    def describe(self, sources: dict[str, str]) -> dict:
        values = {name: getattr(self, name) for name in self.__dataclass_fields__}
        values["directory"] = str(self.directory)
        return {"values": values, "sources": sources}


def _cache_config(raw: dict, path: Path) -> tuple[RasterCacheConfig, dict[str, str]]:
    fields = set(RasterCacheConfig.__dataclass_fields__)
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
        raise ValueError("Raster cache configuration must be a version 1 object")
    if set(raw) - fields - {"version"}:
        raise ValueError("Unknown raster cache configuration fields: "
                         + ", ".join(sorted(set(raw) - fields - {"version"})))
    directory = raw.get("directory", "raster_cache")
    if not isinstance(directory, str) or not directory.strip():
        raise ValueError("Cache directory must be a non-empty path")
    directory = Path(directory).expanduser()
    if not directory.is_absolute():
        directory = path.parent / directory
    directory = directory.resolve()
    parent = directory
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if not parent.is_dir():
        raise ValueError(f"Cache directory or existing parent is not a directory: {parent}")
    total = shutil.disk_usage(parent).total
    default_max = min(50 * GIB, total // 10)
    maximum = raw.get("max_bytes", default_max)
    if type(maximum) is not int or maximum <= 0:
        raise ValueError("max_bytes must be a positive integer")
    defaults = {
        "directory": directory,
        "max_bytes": default_max,
        "min_free_bytes": min(10 * GIB, total // 10),
        "unknown_asset_bytes": min(GIB, maximum),
        "high_water_bytes": maximum * 9 // 10,
        "target_bytes": maximum * 7 // 10,
        "max_unused_age_s": 90 * DAY,
        "metadata_refresh_s": DAY,
        "partial_expiry_s": DAY,
        "queue_lookahead": 3,
        "near_distance_m": 5000,
    }
    values = {name: (directory if name == "directory" else raw.get(name, default))
              for name, default in defaults.items()}
    sources = {name: (f"file:{path}" if name in raw else "default")
               for name in defaults}
    return RasterCacheConfig(**values), sources


def load_cache_config(path: Path) -> tuple[RasterCacheConfig, dict[str, str]]:
    """Load overrides and derive defaults from the configured cache filesystem."""
    path = Path(path).resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read raster cache configuration {path}: {exc}") from exc
    return _cache_config(raw, path)


def display_cache_config(data: Path) -> tuple[RasterCacheConfig, dict[str, str]]:
    """Use the configured cache policy, or the local display-only default."""
    import os

    policy = os.environ.get("SOLAR_RASTER_CACHE_POLICY")
    if policy:
        return load_cache_config(Path(policy))
    directory = os.environ.get("SOLAR_RASTER_CACHE_DIR")
    raw: dict = {"version": 1}
    if directory:
        raw["directory"] = str(Path(directory).expanduser().resolve())
    return _cache_config(raw, Path(data).resolve() / "raster-cache-policy.json")
