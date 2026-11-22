"""Experimental supervised command consuming an approved regional raster plan."""

from __future__ import annotations

import hmac
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path

from src.raster_cache import RasterCache
from src.raster_disk_plan import DiskRequirements
from src.raster_execution import RasterProcessing
from src.raster_job_supervisor import _ProcessGroup, _validate_command
from src.raster_plan_handoff import plan_digest
from src.raster_regional_execution import admitted_regional_rasters
from src.raster_reservations import DiskReservations, QueueCleanupPolicy
from src.raster_source_selection import LayerMapping


def run_regional_command(
    snapshot: dict, approved_digest: str, regions: list[str], mappings: list[LayerMapping] | None,
    command: Sequence[str], *, cwd: Path, env: Mapping[str, str], regions_dir: Path,
    api_key: str, include_imagery: bool, cache: RasterCache, reservations: DiskReservations,
    requirements: DiskRequirements, processing: Mapping[tuple[str, str], RasterProcessing],
    cleanup_policy: QueueCleanupPolicy | None = None, upcoming_plans: list[dict] | None = None,
) -> dict:
    """Acquire and run within one reservation, with a fail-closed lifetime check.

    All acquisition and command descendants must be confined by the caller:
    a process group cannot detect a descendant that leaves the group. Bounds
    must cover the entire command, including all outputs and temporary files.
    """
    _validate_command(command)
    if not isinstance(approved_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", approved_digest):
        raise ValueError("An approved raster plan digest is required")
    snapshot = deepcopy(snapshot)
    if not hmac.compare_digest(plan_digest(snapshot), approved_digest):
        raise ValueError("Approved raster plan changed; estimate again")
    group = _ProcessGroup(ready=False)
    with admitted_regional_rasters(
        snapshot, regions, mappings, regions_dir=regions_dir, api_key=api_key,
        include_imagery=include_imagery, cache=cache, reservations=reservations,
        requirements=requirements, processing=processing, cleanup_policy=cleanup_policy,
        upcoming_plans=upcoming_plans, consumers_stopped=group.all_group_consumers_stopped,
    ) as result:
        group.ready = True
        exit_code = group.run(command, cwd=cwd, env=env)
    return {**result, "exit_code": exit_code}
