"""Recheck stale approved STAC plans without changing their selected sources."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable

from src.raster_acquisition_plan import (
    RasterRequest, RasterSource, _read_json, plan_batch, resolve_asset_sizes,
)

_ASSET_FIELDS = (
    "collection_url", "collection_id", "item_id", "asset_key", "url", "bbox_wgs84",
    "size_bytes", "checksum", "revision", "datetime", "start_datetime", "end_datetime",
    "native_crs", "resolution", "license", "providers", "regions", "uses",
)


def verify_due_metadata(plan: dict, refresh_s: int, *,
                        get_json: Callable[[str], dict] = _read_json,
                        now: float | None = None) -> bool:
    """Recheck a due plan, returning whether STAC was queried.

    An overdue check does not invalidate cached payloads. A changed upstream
    plan must be re-estimated and approved instead of being silently substituted.
    """
    if type(refresh_s) is not int or refresh_s <= 0:
        raise ValueError("Metadata refresh interval must be a positive integer")
    now = time.time() if now is None else now
    if isinstance(now, bool) or not isinstance(now, (float, int)) or not math.isfinite(now):
        raise ValueError("Invalid metadata refresh time")
    inputs = plan.get("inputs")
    if not isinstance(inputs, list):
        raise ValueError("Invalid raster plan inputs")
    mapped = [item for item in inputs if item.get("source") is not None]
    if not mapped:
        return False
    checked = plan.get("metadata_checked_at")
    if checked is not None and (
        isinstance(checked, bool) or not isinstance(checked, (int, float))
        or not math.isfinite(checked) or checked < 0 or checked > now
    ):
        raise ValueError("Invalid raster metadata check time")
    if checked is not None and now - checked < refresh_s:
        return False
    requests = []
    for item in mapped:
        source = item["source"]
        requests.append(RasterRequest(
            item["region"], item["role"],
            tuple(tuple(footprint) for footprint in item["footprints_wgs84"]),
            RasterSource(source["collection_url"], source["asset_key"],
                         source["expected_collection_id"], source.get("index_layer")),
            item["layer_id"], survey_name=item.get("survey_name"),
            mapping_evidence=item.get("mapping_evidence"),
            allow_partial=item.get("allow_partial", False)))
    refreshed = plan_batch(requests, get_json=get_json)
    observed = {(item["collection_url"], item["item_id"], item["asset_key"])
                for item in plan["assets"] if item.get("size_source") == "http-head"}
    if observed:
        resolve_asset_sizes([item for item in refreshed["assets"]
                             if (item["collection_url"], item["item_id"], item["asset_key"]) in observed])

    def assets(value: dict) -> list:
        return [{field: asset[field] for field in _ASSET_FIELDS} for asset in value["assets"]]

    # Round-trip normalizes tuples from discovery and lists in persisted JSON.
    before = json.dumps(assets(plan), sort_keys=True, allow_nan=False)
    after = json.dumps(assets(refreshed), sort_keys=True, allow_nan=False)
    if before != after:
        raise ValueError("Raster source metadata changed; estimate and approve a new plan")
    if plan.get("capture_coverage", []) != refreshed.get("capture_coverage", []):
        raise ValueError("Raster capture coverage changed; estimate and approve a new plan")
    if plan.get("capture_surveys", []) != refreshed.get("capture_surveys", []):
        raise ValueError("Raster contributing surveys changed; estimate and approve a new plan")
    return True
