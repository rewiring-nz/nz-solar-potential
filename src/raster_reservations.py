"""Experimental cooperative disk reservations for raster build forecasts.

All cooperating jobs must use the same local registry and hold the reservation
until their entire build and its child processes have stopped. This does not
allocate blocks or protect against unrelated filesystem users.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from src.raster_cache import RasterCache, _key
from src.raster_disk_plan import DiskRequirements, _disk_probe, forecast_disk


class DiskReservationError(RuntimeError):
    """A forecast cannot safely be admitted or a registry is inconsistent."""


class DiskCapacityError(DiskReservationError):
    """A reservation exceeds the available cache budget or filesystem capacity."""

    def __init__(self, cache_shortfall: int, filesystem_shortfalls: dict[int, int],
                 cache_device: int, cache_usage: int):
        self.cache_shortfall = cache_shortfall
        self.filesystem_shortfalls = filesystem_shortfalls
        self.cache_device = cache_device
        self.cache_usage = cache_usage
        reasons = ([f"cache budget: {cache_shortfall} bytes"] if cache_shortfall else [])
        reasons.extend(f"device {device}: {amount} bytes"
                       for device, amount in filesystem_shortfalls.items())
        super().__init__("Insufficient disk reservation capacity: " + "; ".join(reasons))


@dataclass(frozen=True)
class QueueCleanupPolicy:
    lookahead: int
    near_distance_m: float
    max_unused_age_s: float
    partial_expiry_s: float
    high_water_bytes: int | None = None
    target_bytes: int | None = None

    def __post_init__(self):
        if type(self.lookahead) is not int or self.lookahead < 1:
            raise ValueError("Cleanup lookahead must include the current plan")
        for value in (self.near_distance_m, self.max_unused_age_s, self.partial_expiry_s):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError("Cleanup distances and ages must be non-negative finite numbers")
        if self.high_water_bytes is not None or self.target_bytes is not None:
            if (type(self.high_water_bytes) is not int or type(self.target_bytes) is not int
                    or not 0 <= self.target_bytes < self.high_water_bytes):
                raise ValueError("Cleanup target must be non-negative and below high water")


class DiskReservations:
    def __init__(self, directory: Path, *,
                 probe: Callable[[Path], tuple[int, int]] = _disk_probe):
        self.directory = Path(directory)
        self.probe = probe

    @contextmanager
    def _guard(self) -> Iterator[None]:
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / "registry.lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _active(self) -> list[dict]:
        """Under the registry lock, refuse records whose owner has disappeared."""
        active = []
        for path in self.directory.iterdir():
            if not re.fullmatch(r"[a-f0-9]{32}\.json", path.name):
                continue
            if path.is_symlink() or not path.is_file():
                raise DiskReservationError(f"Invalid reservation record: {path}")
            with path.open("r+b") as stream:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    try:
                        record = json.load(stream)
                    except (ValueError, UnicodeError) as exc:
                        raise DiskReservationError(f"Unreadable active reservation: {path}") from exc
                    self._validate_record(record, path)
                    active.append(record)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                    raise DiskReservationError(
                        f"Orphaned disk reservation {path}; its owner may have left "
                        "child processes running. Stop all consumers before explicit recovery.")
        return active

    def recover_orphan(self, reservation_id: str, *, consumers_stopped: bool) -> None:
        """Remove one unlocked record only after its consumers have been stopped."""
        if not isinstance(reservation_id, str) or not re.fullmatch(r"[a-f0-9]{32}", reservation_id):
            raise ValueError("Reservation ID must be 32 lowercase hexadecimal characters")
        if consumers_stopped is not True:
            raise ValueError("Recovery requires confirmation that all consumers have stopped")
        if not self.directory.is_dir():
            raise DiskReservationError(f"Reservation registry does not exist: {self.directory}")
        with self._guard():
            path = self.directory / f"{reservation_id}.json"
            if path.is_symlink() or not path.is_file():
                raise DiskReservationError(f"Reservation record not found or invalid: {path}")
            with path.open("r+b") as stream:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise DiskReservationError(f"Reservation is still active: {path}") from exc
                try:
                    path.unlink()
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def verify_active(self, reservation_id: str, binding: str) -> None:
        """Verify that a particular bound reservation is still held by its owner."""
        if not isinstance(reservation_id, str) or not re.fullmatch(r"[a-f0-9]{32}", reservation_id):
            raise ValueError("Reservation ID must be 32 lowercase hexadecimal characters")
        if not isinstance(binding, str) or not re.fullmatch(r"[a-f0-9]{64}", binding):
            raise ValueError("Reservation binding must be a SHA-256 digest")
        if not self.directory.is_dir():
            raise DiskReservationError(f"Reservation registry does not exist: {self.directory}")
        with self._guard():
            path = self.directory / f"{reservation_id}.json"
            if path.is_symlink() or not path.is_file():
                raise DiskReservationError(f"Reservation record not found or invalid: {path}")
            with path.open("r+b") as stream:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    try:
                        record = json.load(stream)
                    except (ValueError, UnicodeError) as exc:
                        raise DiskReservationError(f"Unreadable active reservation: {path}") from exc
                    self._validate_record(record, path)
                    if record.get("binding") != binding:
                        raise DiskReservationError("Reservation does not match the approved job")
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                    raise DiskReservationError(f"Reservation is not active: {path}")

    @staticmethod
    def _validate_record(record: object, path: Path) -> None:
        def nonnegative(value: object) -> bool:
            return type(value) is int and value >= 0

        if not isinstance(record, dict) or record.get("version") != 1:
            raise DiskReservationError(f"Invalid active reservation: {path}")
        if "exclusive" in record and type(record["exclusive"]) is not bool:
            raise DiskReservationError(f"Invalid reservation exclusivity: {path}")
        filesystems = record.get("filesystems")
        if not isinstance(filesystems, list) or not filesystems:
            raise DiskReservationError(f"Invalid reservation filesystems: {path}")
        devices = set()
        for entry in filesystems:
            if (not isinstance(entry, dict) or not nonnegative(entry.get("device"))
                    or not nonnegative(entry.get("allocation_bytes"))
                    or not nonnegative(entry.get("reserve_bytes"))
                    or entry["device"] in devices):
                raise DiskReservationError(f"Invalid reservation filesystem: {path}")
            devices.add(entry["device"])
        if (not isinstance(record.get("cache_dir"), str)
                or not Path(record["cache_dir"]).is_absolute()
                or not nonnegative(record.get("cache_budget_bytes"))
                or record["cache_budget_bytes"] == 0):
            raise DiskReservationError(f"Invalid reservation cache budget: {path}")
        if not nonnegative(record.get("cache_device")) or record["cache_device"] not in devices:
            raise DiskReservationError(f"Invalid reservation cache device: {path}")
        assets = record.get("assets")
        if not isinstance(assets, list):
            raise DiskReservationError(f"Invalid reservation assets: {path}")
        keys = set()
        for asset in assets:
            if (not isinstance(asset, dict)
                    or not isinstance(asset.get("key"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", asset["key"])
                    or not isinstance(asset.get("version"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", asset["version"])
                    or not nonnegative(asset.get("transfer_bytes"))
                    or ("unversioned" in asset and type(asset["unversioned"]) is not bool)
                    or ("repeated_use" in asset and type(asset["repeated_use"]) is not bool)
                    or asset["key"] in keys):
                raise DiskReservationError(f"Invalid reservation asset: {path}")
            keys.add(asset["key"])
        cache_allocation = next(entry["allocation_bytes"] for entry in filesystems
                                if entry["device"] == record["cache_device"])
        if sum(asset["transfer_bytes"] for asset in assets) > cache_allocation:
            raise DiskReservationError(f"Reservation transfers exceed filesystem allocation: {path}")

    @contextmanager
    def reserve(self, load_inspected: Callable[[], dict],
                requirements: DiskRequirements, *,
                consumers_stopped: Callable[[], bool] | None = None,
                binding: str | None = None,
                exclusive: bool = False) -> Iterator[dict]:
        """Check fresh metadata and reserve estimated bytes for cooperating jobs."""
        if consumers_stopped is not None and not callable(consumers_stopped):
            raise ValueError("Consumer lifetime check must be callable")
        if type(exclusive) is not bool:
            raise ValueError("Reservation exclusivity must be a boolean")
        if binding is not None and (
            not isinstance(binding, str) or not re.fullmatch(r"[a-f0-9]{64}", binding)
        ):
            raise ValueError("Reservation binding must be a SHA-256 digest")
        stream = None
        path = None
        with self._guard():
            active = self._active()
            if active and (exclusive or any(record.get("exclusive") for record in active)):
                raise DiskReservationError(
                    "Another raster operation is active in this cache registry; wait for it to finish.")
            plan = load_inspected()
            if not isinstance(plan, dict) or not isinstance(plan.get("assets"), list):
                raise ValueError("An inspected batch raster plan with assets is required")
            # Inspection is not a lease: cached assets can be evicted before use.
            reservation_plan = {
                **plan,
                "assets": [{**asset, "cache_state": "missing"}
                           if asset.get("cache_state") == "cached" else asset
                           for asset in plan["assets"]],
            }
            forecast = forecast_disk(reservation_plan, requirements, probe=self.probe)
            cache_dir = str(Path(requirements.cache_dir).resolve())
            cache = plan["cache"]
            cached_recovery_bytes = sum(
                asset["size_bytes"] or cache["unknown_asset_estimate_bytes"]
                for asset in plan["assets"] if asset["cache_state"] == "cached")
            cache_filesystem = next(item for item in forecast["filesystems"]
                                    if "cache_transfer" in item["allocations"])
            assets = []
            for asset in plan["assets"]:
                version = json.dumps([asset.get(field) for field in
                                      ("url", "revision", "checksum", "size_bytes")],
                                     sort_keys=True, separators=(",", ":"), allow_nan=False)
                transfer = asset["size_bytes"] or cache["unknown_asset_estimate_bytes"]
                revision = asset.get("revision")
                unversioned = (asset.get("checksum") is None
                               and (not isinstance(revision, str) or not revision.strip()))
                planned_uses = asset.get("uses", [])
                if (not isinstance(planned_uses, list)
                        or any(not isinstance(use, dict)
                               or not isinstance(use.get("region"), str) or not use["region"]
                               or not isinstance(use.get("role"), str) or not use["role"]
                               for use in planned_uses)):
                    raise ValueError("Invalid raster asset uses")
                repeated_use = len({(use["region"], use["role"]) for use in planned_uses}) > 1
                assets.append({"key": _key(asset),
                               "version": hashlib.sha256(version.encode()).hexdigest(),
                               "transfer_bytes": transfer, "unversioned": unversioned,
                               "repeated_use": repeated_use})
            same_cache = [record for record in active if record["cache_dir"] == cache_dir]
            if any(record["cache_device"] != cache_filesystem["device"] for record in same_cache):
                raise DiskReservationError("Cache filesystem changed during an active reservation")
            shared: dict[str, dict] = {}
            uses: dict[str, int] = {}
            unversioned_keys = set()
            repeated_keys = set()
            for asset in [entry for record in same_cache for entry in record["assets"]] + assets:
                uses[asset["key"]] = uses.get(asset["key"], 0) + 1
                if asset.get("unversioned", True):
                    unversioned_keys.add(asset["key"])
                if asset.get("repeated_use", True):
                    repeated_keys.add(asset["key"])
                previous = shared.get(asset["key"])
                if previous is not None and previous["version"] != asset["version"]:
                    raise DiskReservationError(
                        f"Active jobs require conflicting versions of raster asset {asset['key']}")
                if previous is None or asset["transfer_bytes"] > previous["transfer_bytes"]:
                    shared[asset["key"]] = asset
            budget = min([cache["budget_bytes"]] +
                         [record["cache_budget_bytes"] for record in same_cache])
            shared_transfer = sum(item["transfer_bytes"] for item in shared.values())
            # An unversioned hit is re-downloaded. Repeated leases can require
            # a committed copy and a replacement partial simultaneously.
            replacement_headroom = sum(
                item["transfer_bytes"] for key, item in shared.items()
                if (uses[key] > 1 or key in repeated_keys) and key in unversioned_keys)
            shared_transfer += replacement_headroom
            cache_shortfall = max(0, cache["usage_bytes"] + shared_transfer - budget)
            shortfalls = {}
            for filesystem in forecast["filesystems"]:
                peers = [(record, entry) for record in active for entry in record["filesystems"]
                         if entry["device"] == filesystem["device"]]
                outstanding = sum(entry["allocation_bytes"] for _, entry in peers)
                # Replace summed transfers for this cache with its distinct asset union.
                active_transfer = sum(
                    sum(asset["transfer_bytes"] for asset in record["assets"])
                    for record, _ in peers if record["cache_dir"] == cache_dir
                    and record["cache_device"] == filesystem["device"])
                new_transfer = (sum(asset["transfer_bytes"] for asset in assets)
                                if filesystem["device"] == cache_filesystem["device"] else 0)
                additional = (shared_transfer - active_transfer - new_transfer
                              if filesystem["device"] == cache_filesystem["device"] else 0)
                reserve = max([filesystem["reserve_bytes"]] +
                              [entry["reserve_bytes"] for _, entry in peers])
                shortfall = max(0, outstanding + filesystem["allocation_bytes"] + additional + reserve
                                - filesystem["free_bytes"])
                if shortfall:
                    shortfalls[filesystem["device"]] = shortfall
            if cache_shortfall or shortfalls:
                raise DiskCapacityError(cache_shortfall, shortfalls, cache_filesystem["device"],
                                        cache["usage_bytes"])
            record = {
                "version": 1,
                "owner_pid": os.getpid(),
                "cache_dir": cache_dir,
                "cache_device": cache_filesystem["device"],
                "cache_budget_bytes": budget,
                "assets": assets,
                "filesystems": [
                    {key: item[key] for key in ("device", "allocation_bytes", "reserve_bytes")}
                    for item in forecast["filesystems"]
                ],
            }
            if binding is not None:
                record["binding"] = binding
            if exclusive:
                record["exclusive"] = True
            path = self.directory / f"{uuid.uuid4().hex}.json"
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            stream = os.fdopen(fd, "w+", encoding="utf-8")
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                json.dump(record, stream, sort_keys=True, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                stream.close()
                path.unlink(missing_ok=True)
                raise
        try:
            yield {**forecast, "reservation": True, "reservation_id": path.stem,
                   "cooperative_only": True,
                   "cached_recovery_bytes": cached_recovery_bytes,
                   "shared_unversioned_replacement_bytes": replacement_headroom}
        finally:
            try:
                try:
                    safe = consumers_stopped is None or consumers_stopped() is True
                except Exception as exc:
                    raise DiskReservationError(
                        f"Cannot verify consumers stopped; reservation {path} retained for recovery") from exc
                if not safe:
                    raise DiskReservationError(
                        f"Consumers may still be running; reservation {path} retained for recovery")
                with self._guard():
                    path.unlink()
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                stream.close()


@contextmanager
def reserve_with_queue_cleanup(
    reservations: DiskReservations,
    cache: RasterCache,
    plan: dict,
    requirements: DiskRequirements,
    upcoming_plans: list[dict],
    policy: QueueCleanupPolicy,
    *,
    consumers_stopped: Callable[[], bool] | None = None,
    binding: str | None = None,
) -> Iterator[dict]:
    """Attempt admission, reclaim eligible cache entries, and recheck admission.

    The current plan is the first retention preference; upcoming plans are
    bounded by the remaining lookahead. Each admission attempt reinspects the
    cache and serializes against the same cooperative reservation registry.
    """
    if not isinstance(policy, QueueCleanupPolicy):
        raise ValueError("A queue cleanup policy is required")
    if not isinstance(upcoming_plans, list):
        raise ValueError("Upcoming plans must be an ordered list")
    if Path(cache.directory).resolve() != Path(requirements.cache_dir).resolve():
        raise ValueError("Cache directory does not match disk requirements")
    if policy.high_water_bytes is not None and policy.high_water_bytes > cache.policy.max_bytes:
        raise ValueError("Cleanup high water cannot exceed the cache budget")
    empty_plan = (plan.get("inputs") == [] and plan.get("assets") == []
                  and plan.get("export_fallbacks") == [])
    queue_plans = ([] if empty_plan else [plan]) + upcoming_plans[:policy.lookahead - 1]
    results = {"partials": None, "assets": None, "proactive": None}
    with ExitStack() as stack:
        def admit():
            return stack.enter_context(reservations.reserve(
                lambda: cache.inspect_plan(plan), requirements,
                consumers_stopped=consumers_stopped, binding=binding))

        try:
            forecast = admit()
        except DiskCapacityError as first:
            if any(device != first.cache_device for device in first.filesystem_shortfalls):
                raise
            cache._queue_cleanup_context(
                queue_plans, lookahead=policy.lookahead,
                near_distance_m=policy.near_distance_m, max_unused_age_s=policy.max_unused_age_s)
            results["partials"] = cache.cleanup_partials(policy.partial_expiry_s)
            try:
                forecast = admit()
            except DiskCapacityError as after_partials:
                if any(device != after_partials.cache_device
                       for device in after_partials.filesystem_shortfalls):
                    raise
                reclaim = max(after_partials.cache_shortfall,
                              after_partials.filesystem_shortfalls.get(after_partials.cache_device, 0))
                if reclaim > after_partials.cache_usage:
                    raise DiskReservationError(
                        f"{after_partials}; reclaiming the entire cache cannot make this job fit") \
                        from after_partials
                target = max(0, after_partials.cache_usage - reclaim)
                results["assets"] = cache.cleanup_for_queue(
                    target, queue_plans, lookahead=policy.lookahead,
                    near_distance_m=policy.near_distance_m, max_unused_age_s=policy.max_unused_age_s)
                try:
                    forecast = admit()
                except DiskCapacityError as final:
                    raise DiskReservationError(
                        f"{final}; cleanup reclaimed {sum(entry['bytes'] for entry in results['partials']['removed'])} "
                        f"partial bytes and {sum(entry['bytes'] for entry in results['assets']['removed'])} "
                        "asset bytes; active leases remain protected") from final
        if policy.high_water_bytes is not None:
            inspected = cache.inspect_plan(plan)
            if inspected["cache"]["usage_bytes"] >= policy.high_water_bytes:
                cache._queue_cleanup_context(
                    queue_plans, lookahead=policy.lookahead,
                    near_distance_m=policy.near_distance_m, max_unused_age_s=policy.max_unused_age_s)
                if results["partials"] is None:
                    results["partials"] = cache.cleanup_partials(policy.partial_expiry_s)
                results["proactive"] = cache.cleanup_for_queue(
                    policy.target_bytes, queue_plans, lookahead=policy.lookahead,
                    near_distance_m=policy.near_distance_m, max_unused_age_s=policy.max_unused_age_s)
        yield {**forecast, "cleanup": results}
