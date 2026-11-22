"""Resolve regional fetch inputs into an experimental batch raster plan."""

from __future__ import annotations

import time
from collections.abc import Callable

from src.raster_acquisition_plan import _bounds, plan_batch
from src.raster_source_selection import LayerMapping, survey_requests
from src.region_ids import validate


def plan_regions(regions: list[str], *, mappings: list[LayerMapping],
                 include_imagery: bool = True,
                 bbox_for: Callable | None = None,
                 footprints_for: Callable | None = None,
                 survey_for: Callable | None = None,
                 get_json: Callable | None = None) -> dict:
    """Snapshot selected inputs and plan their native assets without downloading them.

    The region must already have enough local metadata to resolve its bbox and
    acquisition footprints. This is not a build admission or execution plan.
    """
    if not isinstance(regions, list) or not regions:
        raise ValueError("At least one region is required")
    for region in regions:
        validate(region)
    if len(set(regions)) != len(regions):
        raise ValueError("Each region may be planned only once")
    if type(include_imagery) is not bool:
        raise ValueError("include_imagery must be boolean")
    if bbox_for is None:
        from src.region_build import area_bbox_wgs84
        bbox_for = area_bbox_wgs84
    if footprints_for is None:
        from src.fetch_regions import acquisition_footprints
        footprints_for = acquisition_footprints
    if survey_for is None:
        from src.surveys import survey_for as resolve_survey
        survey_for = resolve_survey

    selections = []
    requests = []
    for region in regions:
        bbox = bbox_for(region)
        _bounds(bbox, f"{region} bounding box")
        footprints = footprints_for(region, bbox)
        survey = survey_for(bbox, region)
        if not isinstance(survey, dict):
            raise ValueError(f"Invalid survey selection for {region}")
        legacy = ["dsm_s3"] if survey.get("elevation") != "synthetic" else []
        if include_imagery:
            legacy.extend(("imagery_s3", "reference_imagery_s3"))
        configured = [field for field in legacy if survey.get(field)]
        if configured:
            raise ValueError(
                f"{region} selects legacy S3 inputs ({', '.join(configured)}); "
                "their source cannot yet be represented by this planner")
        selected = survey_requests(region, footprints, survey, mappings=mappings,
                                   include_imagery=include_imagery)
        requests.extend(selected["requests"])
        selections.append({"region": region, "bbox_wgs84": list(bbox),
                           "footprints_wgs84": [list(_bounds(value, f"{region} footprint"))
                                                 for value in footprints],
                           "survey_name": selected["survey_name"],
                           "inputs": selected["inputs"]})
    if requests:
        metadata_checked_at = time.time()
        plan = plan_batch(requests, **({"get_json": get_json} if get_json is not None else {}))
        if any(request.source is not None for request in requests):
            plan["metadata_checked_at"] = metadata_checked_at
    else:
        plan = None
    return {"regions": selections, "batch": plan}
