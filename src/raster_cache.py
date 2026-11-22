"""Experimental, explicitly leased native-raster storage.

The cache's own transfer checks do not reserve space for a whole build; the
opt-in local queue holds a separate cooperative whole-job reservation.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class RasterCacheError(RuntimeError):
    pass


class RasterTransferError(RasterCacheError):
    """A raster could not be retrieved after bounded network retries."""


@dataclass(frozen=True)
class CachePolicy:
    max_bytes: int
    min_free_bytes: int
    unknown_asset_bytes: int

    def __post_init__(self):
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0
               for v in (self.max_bytes, self.min_free_bytes, self.unknown_asset_bytes)):
            raise ValueError("Cache policy values must be non-negative integer byte counts")
        if self.max_bytes == 0 or self.unknown_asset_bytes == 0:
            raise ValueError("Cache budget and unknown-asset estimate must be positive")


def _key(asset: dict) -> str:
    keys = ("collection_url", "collection_id", "item_id", "asset_key")
    if any(not isinstance(asset.get(k), str) or not asset[k] for k in keys):
        raise ValueError("Asset must have collection URL, collection ID, item ID and asset key")
    return hashlib.sha256(json.dumps([asset[k] for k in keys], ensure_ascii=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _checksum(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"1220[0-9a-fA-F]{64}", value):
        raise RasterCacheError("Unsupported STAC checksum; expected SHA-256 multihash")
    return value[4:].lower()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_raster(path: Path) -> None:
    import rasterio
    from rasterio.windows import Window

    try:
        with rasterio.open(path) as dataset:
            if dataset.driver != "GTiff" or dataset.crs is None or dataset.count < 1:
                raise RasterCacheError(f"Raster lacks a GeoTIFF driver, CRS or band: {path.name}")
            for row in range(0, dataset.height, 1024):
                for col in range(0, dataset.width, 1024):
                    window = Window(col, row, min(1024, dataset.width - col),
                                    min(1024, dataset.height - row))
                    for band in dataset.indexes:
                        dataset.read(band, window=window)
                        dataset.read_masks(band, window=window)
    except rasterio.errors.RasterioError as exc:
        raise RasterCacheError(f"Raster cannot be read: {path.name}: {exc}") from exc


class RasterCache:
    def __init__(self, directory: Path, policy: CachePolicy):
        self.directory = Path(directory)
        self.policy = policy

    def _prepare(self) -> None:
        for name in ("assets", "metadata", "locks", "partial"):
            (self.directory / name).mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _lock(self, name: str, operation: int) -> Iterator[object]:
        with (self.directory / "locks" / name).open("a+b") as lock:
            fcntl.flock(lock.fileno(), operation)
            try:
                yield lock
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _record(self, key: str) -> dict | None:
        path = self.directory / "metadata" / f"{key}.json"
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RasterCacheError(f"Unreadable cache metadata: {path}") from exc
        if not isinstance(record, dict):
            raise RasterCacheError(f"Invalid cache metadata: {path}")
        return record

    def _is_hit(self, asset: dict, key: str, target: Path, *,
                require_version: bool = True) -> bool:
        record = self._record(key)
        if record is None or not target.is_file():
            return False
        revision = asset.get("revision")
        if (require_version and asset.get("checksum") is None
                and (not isinstance(revision, str) or not revision.strip())):
            return False
        if (record.get("url") != asset.get("url")
                or record.get("revision") != asset.get("revision")
                or record.get("source_checksum") != asset.get("checksum")
                or (asset.get("size_bytes") is not None
                    and record.get("size_bytes") != asset["size_bytes"])):
            return False
        if target.stat().st_size != record.get("size_bytes"):
            return False
        digest = _sha256(target)
        return digest == record.get("sha256") and (
            _checksum(asset.get("checksum")) in (None, digest))

    def _recover_replacement(self, key: str, target: Path) -> None:
        backup = self.directory / "partial" / f"{key}.previous"
        if not backup.exists():
            return
        record = self._record(key)
        if record is None or not isinstance(record.get("sha256"), str):
            raise RasterCacheError(f"Cannot recover interrupted raster replacement {key}: metadata missing")

        def matches(path: Path) -> bool:
            return (path.is_file() and path.stat().st_size == record.get("size_bytes")
                    and _sha256(path) == record["sha256"])

        if matches(target):
            backup.unlink()
        elif matches(backup):
            os.replace(backup, target)
        else:
            raise RasterCacheError(
                f"Cannot recover interrupted raster replacement {key}: neither payload matches metadata")

    def _write_record(self, key: str, record: dict) -> None:
        target = self.directory / "metadata" / f"{key}.json"
        fd, name = tempfile.mkstemp(prefix=f"{key}-", dir=self.directory / "partial")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, target)
        finally:
            Path(name).unlink(missing_ok=True)

    def _usage(self) -> int:
        return sum(path.stat().st_size for path in (self.directory / "assets").glob("*.tif")
                   if path.is_file())

    def _inventory(self) -> dict[str, int]:
        """Classify logical payload bytes under the global lock, without eviction."""
        totals = {"leased_bytes": 0, "reclaimable_bytes": 0, "unmanaged_bytes": 0}
        for path in (self.directory / "assets").glob("*.tif"):
            if not path.is_file():
                continue
            size = path.stat().st_size
            key = path.stem
            if path.is_symlink() or not re.fullmatch(r"[a-f0-9]{64}", key):
                totals["unmanaged_bytes"] += size
                continue
            if (self.directory / "partial" / f"{key}.previous").exists():
                totals["unmanaged_bytes"] += size
                continue
            record = self._record(key)
            if record is None:
                totals["unmanaged_bytes"] += size
                continue
            last_used = record.get("last_used")
            if (isinstance(last_used, bool) or not isinstance(last_used, (int, float))
                    or not math.isfinite(last_used) or last_used < 0):
                raise RasterCacheError(f"Cache entry has no valid last-used time: {key}")
            try:
                with self._lock(f"{key}.lock", fcntl.LOCK_EX | fcntl.LOCK_NB):
                    totals["reclaimable_bytes"] += size
            except BlockingIOError:
                totals["leased_bytes"] += size
        return totals

    def inspect_assets(self, assets: list[dict]) -> list[dict]:
        """Validate selected assets without scanning the rest of the cache."""
        if not isinstance(assets, list):
            raise ValueError("A raster plan must contain an assets list")
        self._prepare()
        entries = []
        for asset in assets:
            key = _key(asset)
            target = self.directory / "assets" / f"{key}.tif"
            with self._lock(f"{key}.lock", fcntl.LOCK_SH):
                with self._lock("global.lock", fcntl.LOCK_EX):
                    record = self._record(key)
                    if (self.directory / "partial" / f"{key}.previous").exists():
                        state = "recovery_required"
                    elif record is None or not target.is_file():
                        state = "missing"
                    elif self._is_hit(asset, key, target):
                        state = "cached"
                    elif self._is_hit(asset, key, target, require_version=False):
                        state = "unversioned"
                    elif (record.get("revision") != asset.get("revision")
                          or record.get("source_checksum") != asset.get("checksum")
                          or record.get("url") != asset.get("url")):
                        state = "superseded"
                    else:
                        state = "invalid"
                    entries.append({**asset, "cache_key": key, "cache_state": state})
        return entries

    def inspect_plan(self, plan: dict) -> dict:
        """Report cache state and inventory for a STAC plan without transfers."""
        entries = self.inspect_assets(plan.get("assets"))
        with self._lock("global.lock", fcntl.LOCK_EX):
            usage = self._usage()
            inventory = self._inventory()
        missing = [asset for asset in entries if asset["cache_state"] != "cached"]
        return {
            **plan, "assets": entries,
            "cache": {
                "directory": str(self.directory.resolve()),
                "usage_bytes": usage, "budget_bytes": self.policy.max_bytes,
                "min_free_bytes": self.policy.min_free_bytes,
                "cached_count": len(entries) - len(missing),
                "missing_count": len(missing),
                "known_missing_bytes": sum(item["size_bytes"] or 0 for item in missing),
                "unknown_missing_count": sum(item["size_bytes"] is None for item in missing),
                "unknown_asset_estimate_bytes": self.policy.unknown_asset_bytes,
                **inventory,
            },
        }

    def cleanup(self, target_bytes: int, *, preferred_keys: frozenset[str] = frozenset()) -> dict:
        """Reclaim only registered, unlocked assets, retaining preferred keys last."""
        if isinstance(target_bytes, bool) or not isinstance(target_bytes, int) or target_bytes < 0:
            raise ValueError("Cleanup target must be a non-negative byte count")
        self._prepare()
        removed = []
        with self._lock("global.lock", fcntl.LOCK_EX):
            candidates = []
            for path in (self.directory / "assets").glob("*.tif"):
                key = path.stem
                if not re.fullmatch(r"[a-f0-9]{64}", key) or path.is_symlink() or not path.is_file():
                    continue
                if (self.directory / "partial" / f"{key}.previous").exists():
                    continue
                record = self._record(key)
                if record is None:
                    continue
                last_used = record.get("last_used")
                if isinstance(last_used, bool) or not isinstance(last_used, (int, float)):
                    raise RasterCacheError(f"Cache entry has no valid last-used time: {key}")
                candidates.append((key in preferred_keys, last_used, key, path))
            for _, _, key, path in sorted(candidates):
                if self._usage() <= target_bytes:
                    break
                try:
                    with self._lock(f"{key}.lock", fcntl.LOCK_EX | fcntl.LOCK_NB):
                        size = path.stat().st_size
                        path.unlink()
                        (self.directory / "metadata" / f"{key}.json").unlink()
                        removed.append({"key": key, "bytes": size})
                except BlockingIOError:
                    continue
            remaining = self._usage()
        return {"removed": removed, "remaining_bytes": remaining,
                "target_reached": remaining <= target_bytes}

    @staticmethod
    def _queue_cleanup_context(plans: list[dict], *, lookahead: int, near_distance_m: float,
                               max_unused_age_s: float, now: float | None = None):
        from pyproj import Transformer
        from shapely.geometry import box
        from shapely.ops import unary_union
        from src.raster_acquisition_plan import _bounds

        if isinstance(lookahead, bool) or not isinstance(lookahead, int) or lookahead < 0:
            raise ValueError("Queue lookahead must be a non-negative integer")
        if not isinstance(plans, list):
            raise ValueError("Queue plans must be an ordered list")
        now = time.time() if now is None else now
        for value, label in ((near_distance_m, "Near distance"),
                             (max_unused_age_s, "Maximum unused age"),
                             (now, "Current time")):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError(f"{label} must be a non-negative finite number")

        transform = Transformer.from_crs("EPSG:4326", "EPSG:2193", always_xy=True)

        def projected(bounds, label):
            extent = transform.transform_bounds(*_bounds(bounds, label), densify_pts=21)
            if not all(math.isfinite(value) for value in extent):
                raise ValueError(f"{label} cannot be projected to EPSG:2193")
            return box(*extent)

        queued: dict[str, dict] = {}
        footprints = []
        for plan in plans[:lookahead]:
            if (not isinstance(plan, dict) or not isinstance(plan.get("assets"), list)
                    or not isinstance(plan.get("inputs"), list) or not plan["inputs"]):
                raise ValueError("Each upcoming batch plan needs assets and input footprints")
            for entry in plan["inputs"]:
                if not isinstance(entry, dict) or not isinstance(entry.get("footprints_wgs84"), list) \
                        or not entry["footprints_wgs84"]:
                    raise ValueError("Each upcoming input needs acquisition footprints")
                footprints.extend(projected(bounds, "Queue footprint")
                                  for bounds in entry["footprints_wgs84"])
            for asset in plan["assets"]:
                key = _key(asset)
                previous = queued.get(key)
                if previous is not None and any(
                    previous.get(field) != asset.get(field)
                    for field in ("url", "revision", "checksum", "size_bytes")
                ):
                    raise RasterCacheError(f"Conflicting queued versions for raster asset {key}")
                queued[key] = asset
        working_area = unary_union(footprints) if footprints else None
        return now, queued, working_area, projected

    def cleanup_for_queue(self, target_bytes: int, plans: list[dict], *,
                          lookahead: int, near_distance_m: float,
                          max_unused_age_s: float, now: float | None = None) -> dict:
        """Experimental bounded queue retention; plans are snapshots, not pins."""
        if isinstance(target_bytes, bool) or not isinstance(target_bytes, int) or target_bytes < 0:
            raise ValueError("Cleanup target must be a non-negative byte count")
        now, queued, working_area, projected = self._queue_cleanup_context(
            plans, lookahead=lookahead, near_distance_m=near_distance_m,
            max_unused_age_s=max_unused_age_s, now=now)
        self._prepare()
        removed = []
        with self._lock("global.lock", fcntl.LOCK_EX):
            candidates = []
            for path in (self.directory / "assets").glob("*.tif"):
                key = path.stem
                if not re.fullmatch(r"[a-f0-9]{64}", key) or path.is_symlink() or not path.is_file():
                    continue
                if (self.directory / "partial" / f"{key}.previous").exists():
                    continue
                record = self._record(key)
                if record is None:
                    continue
                last_used = record.get("last_used")
                if (isinstance(last_used, bool) or not isinstance(last_used, (int, float))
                        or not math.isfinite(last_used) or last_used < 0):
                    raise RasterCacheError(f"Cache entry has no valid last-used time: {key}")
                upcoming = queued.get(key)
                superseded = upcoming is not None and (
                    record.get("url") != upcoming.get("url")
                    or record.get("revision") != upcoming.get("revision")
                    or record.get("source_checksum") != upcoming.get("checksum")
                )
                bounds = record.get("bbox_wgs84")
                if working_area is None:
                    distance = math.inf
                elif bounds is None:
                    distance = None
                else:
                    distance = projected(bounds, f"Cache entry {key} footprint").distance(working_area)
                expired_outside = (upcoming is None and distance is not None
                                   and distance > near_distance_m
                                   and now - last_used >= max_unused_age_s)
                rank = 0 if superseded else 1 if expired_outside else 3 if upcoming else 2
                # Unknown spatial metadata is kept ahead of other non-queued entries.
                spatial_rank = 1 if distance is None else 0
                farthest_first = -distance if distance is not None else 0
                candidates.append((rank, spatial_rank, farthest_first, last_used, key, path))
            for _, _, _, _, key, path in sorted(candidates):
                if self._usage() <= target_bytes:
                    break
                try:
                    with self._lock(f"{key}.lock", fcntl.LOCK_EX | fcntl.LOCK_NB):
                        size = path.stat().st_size
                        path.unlink()
                        (self.directory / "metadata" / f"{key}.json").unlink()
                        removed.append({"key": key, "bytes": size})
                except BlockingIOError:
                    continue
            remaining = self._usage()
        return {"removed": removed, "remaining_bytes": remaining,
                "target_reached": remaining <= target_bytes,
                "considered_plans": min(len(plans), lookahead)}

    def cleanup_partials(self, older_than_seconds: float, *, now: float | None = None) -> dict:
        """Remove expired transfer partials only when their asset lock is free."""
        if (isinstance(older_than_seconds, bool)
                or not isinstance(older_than_seconds, (int, float))
                or not 0 <= older_than_seconds < float("inf")):
            raise ValueError("Partial expiry must be a non-negative finite duration")
        now = time.time() if now is None else now
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not 0 <= now < float("inf"):
            raise ValueError("Current time must be a non-negative finite timestamp")
        self._prepare()
        removed = []
        with self._lock("global.lock", fcntl.LOCK_EX):
            for path in (self.directory / "partial").iterdir():
                match = re.fullmatch(r"([a-f0-9]{64})-[a-zA-Z0-9_]+\.part", path.name)
                if match is None or path.is_symlink() or not path.is_file():
                    continue
                if now - path.stat().st_mtime < older_than_seconds:
                    continue
                try:
                    with self._lock(f"{match.group(1)}.lock", fcntl.LOCK_EX | fcntl.LOCK_NB):
                        size = path.stat().st_size
                        path.unlink()
                        removed.append({"name": path.name, "bytes": size})
                except BlockingIOError:
                    continue
        return {"removed": removed}

    def _download(self, asset: dict, key: str, target: Path) -> None:
        url = asset.get("url")
        size = asset.get("size_bytes")
        if not isinstance(url, str) or not url:
            raise ValueError("Asset URL is required")
        if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size <= 0):
            raise ValueError("Asset size must be a positive integer or unknown")
        expected_digest = _checksum(asset.get("checksum"))
        estimate = size if size is not None else self.policy.unknown_asset_bytes
        old_size = target.stat().st_size if target.is_file() else 0
        if self._usage() - old_size + estimate > self.policy.max_bytes:
            raise RasterCacheError("Raster cache budget cannot accommodate this asset")
        if shutil.disk_usage(self.directory).free - estimate < self.policy.min_free_bytes:
            raise RasterCacheError("Insufficient free disk space for raster transfer and reserve")

        last_error: Exception | None = None
        for attempt in range(3):
            fd, name = tempfile.mkstemp(prefix=f"{key}-", suffix=".part",
                                        dir=self.directory / "partial")
            partial = Path(name)
            try:
                digest = hashlib.sha256()
                count = 0
                with os.fdopen(fd, "wb") as stream, urllib.request.urlopen(url, timeout=60) as response:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > estimate:
                            raise RasterCacheError(
                                "Raster exceeds its reserved transfer size; increase the unknown-size estimate")
                        if shutil.disk_usage(self.directory).free - len(chunk) < self.policy.min_free_bytes:
                            raise RasterCacheError("Disk reserve reached during raster transfer")
                        stream.write(chunk)
                        digest.update(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                actual_digest = digest.hexdigest()
                if count == 0 or (size is not None and count != size):
                    raise RasterCacheError(f"Truncated or empty raster transfer: expected {size}, got {count}")
                if expected_digest is not None and expected_digest != actual_digest:
                    raise RasterCacheError("Raster checksum does not match STAC metadata")
                _validate_raster(partial)
                backup = self.directory / "partial" / f"{key}.previous"
                if target.is_file():
                    os.link(target, backup)
                try:
                    os.replace(partial, target)
                    self._write_record(key, {
                        "url": url, "revision": asset.get("revision"),
                        "source_checksum": asset.get("checksum"), "size_bytes": count,
                        "sha256": actual_digest, "last_used": time.time(),
                        "collection_id": asset["collection_id"], "item_id": asset["item_id"],
                        "asset_key": asset["asset_key"], "bbox_wgs84": asset.get("bbox_wgs84"),
                    })
                except OSError:
                    if backup.exists():
                        os.replace(backup, target)
                    else:
                        target.unlink(missing_ok=True)
                    raise
                else:
                    backup.unlink(missing_ok=True)
                return
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.1 * (attempt + 1))
            except OSError as exc:
                if exc.errno == errno.ENOSPC:
                    raise RasterCacheError("Disk full during raster cache transfer") from exc
                raise
            finally:
                partial.unlink(missing_ok=True)
        raise RasterTransferError(f"Raster transfer failed after 3 attempts: {last_error}") from last_error

    @contextmanager
    def lease(self, asset: dict) -> Iterator[Path]:
        """Hold an OS-backed shared lease while using a validated cached asset."""
        key = _key(asset)
        self._prepare()
        target = self.directory / "assets" / f"{key}.tif"
        with ExitStack() as lease:
            lock = lease.enter_context(self._lock(f"{key}.lock", fcntl.LOCK_SH))
            with self._lock("global.lock", fcntl.LOCK_EX):
                needs_write = ((self.directory / "partial" / f"{key}.previous").exists()
                               or not self._is_hit(asset, key, target))
            if needs_write:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                with self._lock("global.lock", fcntl.LOCK_EX):
                    self._recover_replacement(key, target)
                    if not self._is_hit(asset, key, target):
                        self._download(asset, key, target)
                    fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            with self._lock("global.lock", fcntl.LOCK_EX):
                record = self._record(key)
                if record is None:
                    raise RasterCacheError("Cache metadata missing after raster transfer")
                self._write_record(key, {**record, "last_used": time.time()})
            yield target

    @contextmanager
    def lease_many(self, assets: list[dict]) -> Iterator[dict[str, Path]]:
        """Lease a deduplicated asset set in stable order until the consumer exits."""
        selected: dict[str, dict] = {}
        for asset in assets:
            key = _key(asset)
            if key in selected:
                previous = selected[key]
                if any(previous.get(field) != asset.get(field)
                       for field in ("url", "revision", "checksum", "size_bytes")):
                    raise RasterCacheError(f"Conflicting source versions for raster asset {key}")
            else:
                selected[key] = asset
        with ExitStack() as stack:
            paths = {key: stack.enter_context(self.lease(selected[key]))
                     for key in sorted(selected)}
            yield paths
