"""Experimental regional adapter for an approved raster acquisition snapshot."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from src.fetch_regions import (
    acquisition_footprints, fetch_raster_footprints, validate_approved_plan,
)
from src.raster_cache import RasterCache
from src.raster_disk_plan import DiskRequirements
from src.raster_execution import RasterProcessing, admitted_raster_inputs
from src.raster_plan_handoff import plan_for_regions
from src.raster_reservations import DiskReservations, QueueCleanupPolicy
from src.raster_source_selection import LayerMapping, mappings_from_snapshot, survey_requests
from src.region_build import area_bbox_wgs84
from src.region_ids import validate
from src.surveys import survey_for


def _device(path: Path) -> int:
    return path.stat().st_dev


@contextmanager
def admitted_regional_rasters(
    snapshot: dict,
    regions: list[str],
    mappings: list[LayerMapping] | None,
    *,
    regions_dir: Path,
    api_key: str,
    include_imagery: bool,
    cache: RasterCache,
    reservations: DiskReservations,
    requirements: DiskRequirements,
    processing: Mapping[tuple[str, str], RasterProcessing],
    cleanup_policy: QueueCleanupPolicy | None = None,
    upcoming_plans: list[dict] | None = None,
    consumers_stopped: Callable[[], bool] | None = None,
) -> Iterator[dict]:
    """Revalidate and acquire an approved snapshot while the caller holds admission.

    The caller must keep this context open throughout the consuming job and
    its descendants' lifetimes. It must independently verify snapshot approval
    and supply defensible bounds for the whole job. If mappings is None, use
    the explicit assertions in that verified snapshot.
    """
    if not isinstance(regions, list) or not regions:
        raise ValueError("At least one region is required")
    for region in regions:
        validate(region)
    if type(include_imagery) is not bool:
        raise ValueError("include_imagery must be boolean")
    if not isinstance(processing, Mapping):
        raise ValueError("Explicit processing options are required for mapped inputs")
    selected_plan = plan_for_regions(snapshot, regions)
    if mappings is None:
        mappings = mappings_from_snapshot(selected_plan, regions)
    batch = selected_plan["batch"] or {"inputs": [], "assets": [], "export_fallbacks": []}
    if batch["export_fallbacks"] and (not isinstance(api_key, str) or not api_key):
        raise ValueError("A LINZ API key is required for regional exports")
    root = Path(regions_dir).resolve()
    work_device = _device(Path(requirements.work_dir))
    destinations = {}
    mapped = set()
    for region in regions:
        bbox = area_bbox_wgs84(region)
        footprints = acquisition_footprints(region, bbox, root)
        survey = survey_for(bbox, region)
        legacy = ["dsm_s3"] if survey.get("elevation") != "synthetic" else []
        if include_imagery:
            legacy.extend(("imagery_s3", "reference_imagery_s3"))
        if any(survey.get(field) for field in legacy):
            raise ValueError(f"{region} selects legacy S3 raster inputs")
        selected = survey_requests(region, footprints, survey, mappings=mappings,
                                   include_imagery=include_imagery)
        validate_approved_plan(selected_plan, region, footprints, selected)
        directory = (root / region).resolve()
        if not directory.is_relative_to(root) or not directory.is_dir():
            raise ValueError(f"Region directory is missing or outside the regions root: {region}")
        if _device(directory) != work_device:
            raise ValueError(f"Region directory is not on the reserved work filesystem: {region}")
        for item in selected["requests"]:
            key = (region, item.role)
            destinations[key] = directory / f"{item.role}_mosaic.tif"
            if item.source is not None:
                mapped.add(key)
    if set(processing) != mapped:
        raise ValueError("Explicit processing options must match every mapped regional input")

    def export(fallback: dict, destination: Path) -> None:
        role = fallback["role"]
        fetch_raster_footprints(fallback["footprints_wgs84"], api_key,
                                fallback["layer_id"], role, destination.parent,
                                "grid" if role == "dsm" else "raster")

    with admitted_raster_inputs(
        batch, cache, reservations, requirements, destinations, export,
        processing=processing, cleanup_policy=cleanup_policy,
        upcoming_plans=upcoming_plans, consumers_stopped=consumers_stopped,
    ) as result:
        yield result
