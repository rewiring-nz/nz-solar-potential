"""Experimental execution of one admitted raster acquisition plan."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from src.raster_cache import RasterCache
from src.raster_disk_plan import DiskRequirements
from src.raster_prepare import (
    _planned_assets, ensure_planned_region, verify_planned_region,
)
from src.raster_processing import validate_processing
from src.raster_reservations import DiskReservations, QueueCleanupPolicy, reserve_with_queue_cleanup


@dataclass(frozen=True)
class RasterProcessing:
    resolution: float | None = None
    resampling: str = "average"


def _validate_raster_inputs(
    plan: dict,
    destinations: Mapping[tuple[str, str], Path],
    export: Callable[[dict, Path], None],
    *,
    processing: Mapping[tuple[str, str], RasterProcessing] | None = None,
) -> tuple[dict, dict, dict, dict]:
    if not isinstance(plan, dict) or not isinstance(plan.get("inputs"), list) \
            or not isinstance(plan.get("export_fallbacks"), list):
        raise ValueError("A batch acquisition plan with inputs and fallbacks is required")
    if not callable(export) or not isinstance(destinations, Mapping):
        raise ValueError("Destinations and an export callback are required")
    inputs = {}
    for item in plan["inputs"]:
        if not isinstance(item, dict):
            raise ValueError("Invalid planned input")
        key = (item.get("region"), item.get("role"))
        if (not all(isinstance(part, str) and part for part in key)
                or key[1] not in ("dsm", "imagery", "reference_imagery", "wide_dem")
                or key in inputs):
            raise ValueError("Invalid or duplicate planned input")
        inputs[key] = item
    if set(destinations) != set(inputs):
        raise ValueError("Destinations must match the planned inputs exactly")
    paths = {key: Path(path) for key, path in destinations.items()}
    if len({path.resolve() for path in paths.values()}) != len(paths):
        raise ValueError("Planned inputs must have distinct destinations")
    fallbacks = {}
    for fallback in plan["export_fallbacks"]:
        if not isinstance(fallback, dict):
            raise ValueError("Invalid planned export fallback")
        key = (fallback.get("region"), fallback.get("role"))
        if key in fallbacks or key not in inputs:
            raise ValueError("Invalid or duplicate export fallback")
        item = inputs[key]
        if (item.get("source") is not None
                or fallback.get("layer_id") != item.get("layer_id")
                or fallback.get("footprints_wgs84") != item.get("footprints_wgs84")):
            raise ValueError("Export fallback does not match planned input")
        fallbacks[key] = fallback
    if any((item.get("source") is None) != (key in fallbacks)
           for key, item in inputs.items()):
        raise ValueError("Every unmapped input must have a matching export fallback")
    if processing is None:
        processing = {}
    if not isinstance(processing, Mapping) or not set(processing) <= set(inputs) - set(fallbacks):
        raise ValueError("Processing options must refer to mapped inputs")
    processing = dict(processing)
    for options in processing.values():
        if not isinstance(options, RasterProcessing):
            raise ValueError("Invalid raster processing options")
        validate_processing(options.resolution, options.resampling)
    for key, item in inputs.items():
        if key in fallbacks:
            continue
        footprints = item.get("footprints_wgs84")
        if not isinstance(footprints, list) or not footprints:
            raise ValueError(f"Expected planned footprints for {key[0]} {key[1]}")
        for footprint in footprints:
            _planned_assets(plan, key[0], key[1], footprint)
        path = paths[key]
        sidecar = path.with_name(path.name + ".source.json")
        if path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink():
            options = processing.get(key, RasterProcessing())
            verify_planned_region(plan, key[0], key[1], path,
                                  res=options.resolution, resampling=options.resampling)
    return inputs, paths, fallbacks, processing


def _acquire_raster_inputs(plan: dict, cache: RasterCache, inputs: dict, paths: dict,
                           fallbacks: dict, processing: dict,
                           export: Callable[[dict, Path], None]) -> None:
    for key in inputs:
        path = paths[key]
        if key in fallbacks:
            export(fallbacks[key], path)
            if not path.is_file():
                raise RuntimeError(f"Export did not create its planned raster: {path}")
        else:
            options = processing.get(key, RasterProcessing())
            ensure_planned_region(cache, plan, key[0], key[1], path,
                                  res=options.resolution, resampling=options.resampling)


def prepare_admitted_mapped_inputs(
    plan: dict, cache: RasterCache, destinations: Mapping[tuple[str, str], Path],
    processing: Mapping[tuple[str, str], RasterProcessing],
    verify_admission: Callable[[], None],
) -> Callable[[tuple[str, str]], Path]:
    """Prepare a mapped-only executor under an independently held reservation.

    The caller owns the reservation for the whole consuming job, revalidates
    the current selections, and handles export fallbacks itself. The callback
    must reject a missing, changed, or orphaned reservation.
    """
    if not callable(verify_admission):
        raise ValueError("An active admission verifier is required")
    verify_admission()
    inputs, paths, fallbacks, options = _validate_raster_inputs(
        plan, destinations, lambda *_: None, processing=processing)
    if set(options) != set(inputs) - set(fallbacks):
        raise ValueError("Processing options must match the mapped inputs")

    def acquire(key: tuple[str, str]) -> Path:
        if key not in inputs or key in fallbacks:
            raise ValueError(f"Not a mapped raster input: {key}")
        verify_admission()
        selected = options[key]
        return ensure_planned_region(cache, plan, key[0], key[1], paths[key],
                                     res=selected.resolution, resampling=selected.resampling)

    return acquire


@contextmanager
def admitted_raster_inputs(
    plan: dict,
    cache: RasterCache,
    reservations: DiskReservations,
    requirements: DiskRequirements,
    destinations: Mapping[tuple[str, str], Path],
    export: Callable[[dict, Path], None],
    *,
    processing: Mapping[tuple[str, str], RasterProcessing] | None = None,
    cleanup_policy: QueueCleanupPolicy | None = None,
    upcoming_plans: list[dict] | None = None,
    consumers_stopped: Callable[[], bool] | None = None,
) -> Iterator[dict]:
    """Reserve disk, acquire planned inputs, and retain the reservation.

    The caller must remain in the context until the consuming job and its children
    have exited. Fallback exports are delegated to the caller, which must use
    the recorded layer and footprints. Bounds are supplied by the caller.
    """
    inputs, paths, fallbacks, processing = _validate_raster_inputs(
        plan, destinations, export, processing=processing)
    if cleanup_policy is None and upcoming_plans is not None:
        raise ValueError("Upcoming plans require a queue cleanup policy")
    admission = (reservations.reserve(lambda: cache.inspect_plan(plan), requirements,
                                      consumers_stopped=consumers_stopped)
                 if cleanup_policy is None else
                 reserve_with_queue_cleanup(reservations, cache, plan, requirements,
                                            upcoming_plans if upcoming_plans is not None else [],
                                            cleanup_policy, consumers_stopped=consumers_stopped))
    with admission as forecast:
        _acquire_raster_inputs(plan, cache, inputs, paths, fallbacks, processing, export)
        yield {"forecast": forecast, "destinations": paths}
