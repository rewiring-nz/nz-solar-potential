"""Experimental, read-only peak disk forecast for an inspected raster plan.

This is not a reservation: another process can consume space after the check.
Callers must supply conservative bounds for preparation, outputs and exports.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.raster_cache import _key


@dataclass(frozen=True)
class DiskAllocation:
    name: str
    directory: Path
    bytes: int


@dataclass(frozen=True)
class DiskRequirements:
    cache_dir: Path
    work_dir: Path
    output_dir: Path
    mosaic_bytes: int
    scratch_bytes: int
    output_bytes: int
    min_free_bytes: int
    unknown_export_bytes: int | None = None
    extra_allocations: tuple[DiskAllocation, ...] = ()


def _disk_probe(path: Path) -> tuple[int, int]:
    if not path.is_dir():
        raise ValueError(f"Disk planning directory does not exist: {path}")
    return os.stat(path).st_dev, shutil.disk_usage(path).free


def forecast_disk(plan: dict, requirements: DiskRequirements, *,
                  probe: Callable[[Path], tuple[int, int]] = _disk_probe) -> dict:
    """Conservatively sum simultaneous new allocations by actual filesystem."""
    assets = plan.get("assets")
    cache = plan.get("cache")
    fallbacks = plan.get("export_fallbacks")
    if not isinstance(assets, list) or not isinstance(cache, dict) or not isinstance(fallbacks, list):
        raise ValueError("An inspected batch raster plan with export fallbacks is required")
    cache_directory = cache.get("directory")
    if (not isinstance(cache_directory, str) or not Path(cache_directory).is_absolute()
            or Path(cache_directory).resolve() != Path(requirements.cache_dir).resolve()):
        raise ValueError("Inspected cache directory does not match disk requirements")
    for label, value in (
        ("mosaic_bytes", requirements.mosaic_bytes),
        ("scratch_bytes", requirements.scratch_bytes),
        ("output_bytes", requirements.output_bytes),
        ("min_free_bytes", requirements.min_free_bytes),
        ("unknown_asset_estimate_bytes", cache.get("unknown_asset_estimate_bytes")),
        ("cache usage_bytes", cache.get("usage_bytes")),
        ("cache budget_bytes", cache.get("budget_bytes")),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{label} must be a non-negative integer byte count")
    unknown_asset_bytes = cache["unknown_asset_estimate_bytes"]
    if unknown_asset_bytes == 0:
        raise ValueError("Unknown asset estimate must be positive")
    if cache["budget_bytes"] == 0:
        raise ValueError("Cache budget must be positive")
    export_bytes = requirements.unknown_export_bytes
    if export_bytes is not None and (
        isinstance(export_bytes, bool) or not isinstance(export_bytes, int) or export_bytes <= 0
    ):
        raise ValueError("Unknown export estimate must be a positive integer byte count")
    if fallbacks and export_bytes is None:
        raise ValueError("Each exports fallback requires an explicit positive size estimate")

    transfer_bytes = 0
    seen = set()
    unknown_count = 0
    for asset in assets:
        key = _key(asset)
        if key in seen:
            raise ValueError("Inspected plan contains duplicate raster assets")
        seen.add(key)
        state = asset.get("cache_state")
        if state not in ("cached", "missing", "superseded", "invalid", "unversioned"):
            raise ValueError(f"Raster asset requires inspection or recovery: {key}")
        size = asset.get("size_bytes")
        if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size <= 0):
            raise ValueError(f"Invalid raster asset size: {key}")
        if state != "cached":
            if size is None:
                unknown_count += 1
                transfer_bytes += unknown_asset_bytes
            else:
                transfer_bytes += size

    allocations = [
        ("cache_transfer", Path(requirements.cache_dir), transfer_bytes),
        ("exports", Path(requirements.work_dir), len(fallbacks) * (export_bytes or 0)),
        ("mosaics", Path(requirements.work_dir), requirements.mosaic_bytes),
        ("scratch", Path(requirements.work_dir), requirements.scratch_bytes),
        ("outputs", Path(requirements.output_dir), requirements.output_bytes),
    ]
    if not isinstance(requirements.extra_allocations, tuple):
        raise ValueError("Additional disk allocations must be a tuple")
    names = {name for name, _, _ in allocations}
    for allocation in requirements.extra_allocations:
        if (not isinstance(allocation, DiskAllocation)
                or not isinstance(allocation.name, str) or not allocation.name.strip()
                or allocation.name in names
                or type(allocation.bytes) is not int or allocation.bytes < 0
                or not isinstance(allocation.directory, (str, Path))
                or not str(allocation.directory)):
            raise ValueError("Invalid or duplicate additional disk allocation")
        names.add(allocation.name)
        allocations.append((allocation.name, Path(allocation.directory), allocation.bytes))
    filesystems: dict[int, dict] = {}
    for category, path, size in allocations:
        device, free = probe(path)
        if (isinstance(device, bool) or not isinstance(device, int)
                or isinstance(free, bool) or not isinstance(free, int) or free < 0):
            raise ValueError(f"Invalid filesystem probe for {path}")
        if device not in filesystems:
            filesystems[device] = {"device": device, "free_bytes": free,
                                   "paths": [], "allocations": {}}
        item = filesystems[device]
        # A concurrent change during probing must not increase the estimate of free space.
        item["free_bytes"] = min(item["free_bytes"], free)
        if str(path) not in item["paths"]:
            item["paths"].append(str(path))
        item["allocations"][category] = size
    for item in filesystems.values():
        item["allocation_bytes"] = sum(item["allocations"].values())
        item["reserve_bytes"] = requirements.min_free_bytes
        item["shortfall_bytes"] = max(0, item["allocation_bytes"] + item["reserve_bytes"]
                                      - item["free_bytes"])
    result = list(filesystems.values())
    # Existing replacement payloads are conservatively retained in this bound.
    budget_shortfall = max(0, cache["usage_bytes"] + transfer_bytes - cache["budget_bytes"])
    return {
        "filesystems": result,
        "cache_budget_shortfall_bytes": budget_shortfall,
        "sufficient_at_check": (budget_shortfall == 0
                                and all(item["shortfall_bytes"] == 0 for item in result)),
        "unknown_asset_count": unknown_count,
        "unknown_export_count": len(fallbacks),
        "reservation": False,
    }
