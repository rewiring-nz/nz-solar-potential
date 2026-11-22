"""Experimental subprocess boundary for cooperative raster reservations."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from collections.abc import Mapping, Sequence

from src.raster_cache import RasterCache
from src.raster_disk_plan import DiskRequirements
from src.raster_reservations import (
    DiskReservations, QueueCleanupPolicy, reserve_with_queue_cleanup,
)

_STOP_TIMEOUT_S = 10


def _process_group_stopped(group: int) -> bool:
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _stop_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=_STOP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        pass
    if not _process_group_stopped(process.pid):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def _validate_command(command: Sequence[str]) -> None:
    if os.name != "posix":
        raise RuntimeError("Raster job supervision requires POSIX process groups")
    if (not command or isinstance(command, (str, bytes))
            or any(not isinstance(part, str) or not part for part in command)):
        raise ValueError("A non-empty command argument list is required")


class _ProcessGroup:
    def __init__(self, *, ready: bool):
        self.ready = ready
        self.launch_attempted = False
        self.process: subprocess.Popen | None = None

    def all_group_consumers_stopped(self) -> bool:
        return (self.ready and (not self.launch_attempted or (
            self.process is not None and self.process.poll() is not None
            and _process_group_stopped(self.process.pid))))

    def run(self, command: Sequence[str], *, cwd: Path, env: Mapping[str, str]) -> int:
        if not self.ready or self.launch_attempted:
            raise RuntimeError("Process group is not ready for a new command")
        self.launch_attempted = True
        self.process = subprocess.Popen(command, cwd=cwd, env=dict(env), start_new_session=True)
        try:
            return self.process.wait()
        except BaseException:
            _stop_process_group(self.process)
            raise


def run_reserved_command(
    command: Sequence[str], *, cwd: Path, env: Mapping[str, str],
    reservations: DiskReservations, cache: RasterCache, plan: dict,
    requirements: DiskRequirements, upcoming_plans: list[dict],
    policy: QueueCleanupPolicy,
) -> int:
    """Run a command in its own POSIX group, retaining admission if it survives.

    All disk consumers must remain in this group; detached processes cannot be
    detected by this boundary. A surviving group leaves an orphan record that
    requires explicit recovery after its consumers have stopped.
    """
    _validate_command(command)
    group = _ProcessGroup(ready=True)
    with reserve_with_queue_cleanup(reservations, cache, plan, requirements,
                                    upcoming_plans, policy,
                                    consumers_stopped=group.all_group_consumers_stopped):
        return group.run(command, cwd=cwd, env=env)
