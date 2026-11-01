"""Concise, machine-readable timings for dataset acquisition operations."""

from __future__ import annotations

import contextvars
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

_ACTIVE = contextvars.ContextVar("solar_dataset_timer", default=None)
OUTCOMES = {"downloaded", "reused_from_cache", "partially_resumed", "unavailable", "failed"}


def format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


class DatasetTimer:
    def __init__(self, dataset: str, source: str, area: str, outcome: str, path: Path | None):
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown dataset acquisition outcome: {outcome}")
        self.dataset, self.source, self.area = dataset, source, area
        self.outcome = outcome
        self.path = Path(path) if path else Path(
            os.environ.get("SOLAR_ACQUISITION_TIMINGS", "data/acquisition_timings.json"))
        self.started_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.started = time.monotonic()
        self.transfer_seconds = 0.0
        self.transfer_measured = False
        self.phase_seconds = {}

    def set_outcome(self, outcome: str) -> None:
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown dataset acquisition outcome: {outcome}")
        self.outcome = outcome

    def add_transfer(self, seconds: float) -> None:
        self.transfer_measured = True
        self.transfer_seconds += max(0.0, seconds)

    def add_phase(self, name: str, seconds: float) -> None:
        self.phase_seconds[name] = self.phase_seconds.get(name, 0.0) + max(0.0, seconds)

    def write(self, error: BaseException | None = None) -> None:
        elapsed = round(time.monotonic() - self.started, 3)
        record = {
            "dataset": self.dataset, "source": self.source, "area": self.area,
            "started_utc": self.started_utc, "outcome": self.outcome,
            "elapsed_seconds": elapsed, "elapsed_hms": format_duration(elapsed),
            "transfer_seconds": round(self.transfer_seconds, 3)
            if self.transfer_measured else None,
            "transfer_hms": format_duration(self.transfer_seconds)
            if self.transfer_measured else None,
            "phases": {name: {"elapsed_seconds": round(seconds, 3),
                              "elapsed_hms": format_duration(seconds)}
                       for name, seconds in self.phase_seconds.items()},
        }
        if error is not None:
            record["error_type"] = type(error).__name__
        existing = []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and isinstance(value.get("acquisitions"), list):
                existing = value["acquisitions"]
        except (OSError, ValueError):
            pass
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps({"schema": 1, "acquisitions": [*existing, record]},
                                            indent=1), encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        transfer = (f" transfer={record['transfer_hms']}"
                    if record["transfer_hms"] is not None else "")
        phases = "".join(
            f" {name}={phase_record['elapsed_hms']}"
            for name, phase_record in record["phases"].items()
            if phase_record["elapsed_seconds"] >= 60
        )
        print(f"[acquisition] {self.dataset} ({self.area}): {record['outcome']}; "
              f"total={record['elapsed_hms']}{transfer}{phases}", flush=True)


@contextmanager
def measure(dataset: str, source: str, area: str, outcome: str = "downloaded",
            path: Path | None = None):
    timer = DatasetTimer(dataset, source, area, outcome, path)
    token = _ACTIVE.set(timer)
    error = None
    try:
        yield timer
    except BaseException as exc:
        error = exc
        if timer.outcome != "unavailable":
            timer.set_outcome("failed")
        raise
    finally:
        _ACTIVE.reset(token)
        timer.write(error)


@contextmanager
def transfer():
    """Measure bytes-transfer work separately from export waiting and processing."""
    started = time.monotonic()
    try:
        yield
    finally:
        timer = _ACTIVE.get()
        if timer is not None:
            timer.add_transfer(time.monotonic() - started)


@contextmanager
def phase(name: str):
    """Record a material wait or local-preparation phase within acquisition time."""
    started = time.monotonic()
    try:
        yield
    finally:
        timer = _ACTIVE.get()
        if timer is not None:
            timer.add_phase(name, time.monotonic() - started)
