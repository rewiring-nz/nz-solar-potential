"""Verify a queue-held disk reservation in a consuming pipeline process."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from collections.abc import Mapping

from src.raster_job_bounds import validate_job_bounds
from src.raster_job_bounds import processing_for_snapshot
from src.raster_plan_handoff import RasterPlanError, optional_approved_raster_plan
from src.raster_reservations import DiskReservations

ADMISSION_ENV = "SOLAR_RASTER_ACTIVE_ADMISSION"


def admission_binding(bounds: dict) -> str:
    encoded = json.dumps(bounds, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def active_admission(environment: Mapping[str, str], *, region: str, workspace: Path,
                     plan_digest: str, include_imagery: bool, paid_referee: bool) -> dict:
    """Validate the immutable job bounds and an actively locked matching record.

    The registry protects cooperating jobs, not against a malicious local user.
    An orphaned record blocks future admission if its owner dies after this check.
    """
    try:
        handoff = json.loads(environment[ADMISSION_ENV])
    except (KeyError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("A queue-held raster admission is required") from exc
    if not isinstance(handoff, dict) or set(handoff) != {"bounds", "reservation_id"}:
        raise ValueError("Invalid raster admission handoff")
    bounds = validate_job_bounds(
        handoff["bounds"], region=region, workspace=workspace, plan_digest=plan_digest,
        include_imagery=include_imagery, paid_referee=paid_referee, frozen=True)
    registry = Path(bounds["cache"]["directory"]) / "reservations"
    DiskReservations(registry).verify_active(handoff["reservation_id"], admission_binding(bounds))
    return bounds


def approved_build_plan(environment: Mapping[str, str], *, region: str, workspace: Path,
                        include_imagery: bool, paid_referee: bool,
                        no_preview_server: bool) -> tuple[dict | None, dict | None]:
    """Resolve an approval; mapped plans additionally require a live bound reservation."""
    if not environment.get(ADMISSION_ENV):
        return optional_approved_raster_plan(environment, [region]), None
    if not no_preview_server:
        raise ValueError("Raster admission requires --no-preview-server")
    digest = environment.get("SOLAR_RASTER_PLAN_SHA256")
    if not digest:
        raise RasterPlanError("Raster admission requires an approved plan digest")
    bounds = active_admission(
        environment, region=region, workspace=workspace, plan_digest=digest,
        include_imagery=include_imagery, paid_referee=paid_referee)
    snapshot = optional_approved_raster_plan(environment, [region], allow_mapped=True)
    if snapshot is None:
        raise RasterPlanError("Raster admission requires an approved plan")
    processing_for_snapshot(bounds, snapshot, region)
    return snapshot, bounds
