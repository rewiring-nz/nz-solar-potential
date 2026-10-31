"""Shared build VM helpers for the web panel's developer and published targets.

Developer runs build on the shared VM and publish to
``gs://<preview_bucket>/devel/<alias>/``; published runs go through
``tools/remote_build.sh --publish`` and its deploy gate. Status checks here are
free (``gcloud compute instances describe`` and local git); nothing in this
module starts or uses a billable VM. Commands are built here and run by
``jobs.JobManager`` only after the typed ``yes`` approval of a shown cost.
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import threading
import time
from decimal import Decimal
from typing import Callable

from src.region_ids import is_locality_id, validate
from tools.pipeline_webui.common import ROOT

CLOUD_TARGETS = ("developer", "published")
VM_STATUS_TTL = 30
MIN_VM_HOURS = 8
MAX_VM_HOURS = 16          # the spot VM stops itself 16 hours after starting
HOURS_MARGIN = 1.5
COST_WARNING = ("This is an estimate, not a spending cap; actual charges can be higher, "
                "especially if runtime or public downloads exceed these assumptions.")

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=60,
                          stdin=subprocess.DEVNULL, check=False)


def _pipeline_module():
    from tools import pipeline
    return pipeline


class CloudError(Exception):
    pass


class Cloud:
    """Configuration, VM status, preview alias and cost for the shared VM."""

    def __init__(self, runner: Runner | None = None, config: dict | None = None,
                 alias_lookup: Callable[[], str] | None = None,
                 gcloud: str | None = None, now: Callable[[], float] = time.time):
        self._runner = runner or _default_runner
        self._config = config
        self._alias_lookup = alias_lookup
        self._gcloud = gcloud
        self._now = now
        self._lock = threading.Lock()
        self._describe_lock = threading.Lock()
        self._status: tuple[float, dict] | None = None
        self._alias: str | None = None

    def config(self) -> dict:
        if self._config is None:
            self._config = _pipeline_module()._load_shared_cloud_config()
        return self._config

    def gcloud(self) -> str | None:
        return self._gcloud or shutil.which("gcloud")

    def alias(self) -> str:
        with self._lock:
            if self._alias is None:
                lookup = self._alias_lookup or _pipeline_module()._github_alias
                self._alias = lookup()
            return self._alias

    def preview_url(self, alias: str) -> str:
        bucket = self.config()["preview_bucket"].removeprefix("gs://")
        return f"https://storage.googleapis.com/{bucket}/devel/{alias}/preview.html"

    def vm_status(self, refresh: bool = False) -> dict:
        """Return {status, checked_at, error}. Free: describe never starts the VM."""
        asked_at = self._now()
        with self._lock:
            if self._status and not refresh and asked_at - self._status[0] < VM_STATUS_TTL:
                return self._status[1]
        # One describe at a time: requests that waited reuse the answer that just arrived.
        with self._describe_lock:
            with self._lock:
                if self._status and self._status[0] > asked_at:
                    return self._status[1]
            return self._describe()

    def _describe(self) -> dict:
        cfg = self.config()
        out = {"project": cfg["project"], "vm": cfg["vm"], "zone": cfg["zone"],
               "status": "UNKNOWN", "error": None, "checked_at": self._now()}
        gcloud = self.gcloud()
        if not gcloud:
            out["error"] = "gcloud is not installed or not on PATH"
        else:
            try:
                result = self._runner([
                    gcloud, "compute", "instances", "describe", cfg["vm"],
                    f"--project={cfg['project']}", f"--zone={cfg['zone']}",
                    "--format=value(status)"])
            except (OSError, subprocess.TimeoutExpired) as exc:
                out["error"] = type(exc).__name__
            else:
                if result.returncode == 0 and result.stdout.strip():
                    out["status"] = result.stdout.strip()
                else:
                    lines = [ln.strip() for ln in (result.stderr or "").splitlines() if ln.strip()]
                    detail = f": {lines[-1][:300]}" if lines else ""
                    out["error"] = ("gcloud could not describe the VM (check `gcloud auth login`)"
                                    + detail)
        with self._lock:
            self._status = (self._now(), out)
        return out

    def vm_hours(self, estimated_seconds: float | None) -> int:
        if not estimated_seconds:
            return MIN_VM_HOURS
        hours = math.ceil(estimated_seconds / 3600 * HOURS_MARGIN)
        return max(MIN_VM_HOURS, min(MAX_VM_HOURS, hours))

    def cost(self, target: str, estimated_seconds: float | None) -> dict:
        cfg = dict(self.config())
        model = dict(cfg["preview_cost_model"])
        hours = self.vm_hours(estimated_seconds)
        model["vm_hours"] = str(hours)
        cfg["preview_cost_model"] = model
        amount, basis = _pipeline_module()._shared_preview_cost(cfg)
        basis = (f"{basis} VM hours: {hours} (estimated build time x {HOURS_MARGIN}, "
                 f"between {MIN_VM_HOURS} and {MAX_VM_HOURS}; the spot VM stops itself "
                 f"after {MAX_VM_HOURS} hours).")
        if target == "published":
            basis += (" A published build writes to the production tiles bucket instead "
                      "of the preview bucket; storage there is of similar size.")
        text = (f"Estimated Google Cloud cost: up to NZD {amount} for this "
                f"{'production publish' if target == 'published' else 'developer preview'} "
                f"on {cfg['project']}/{cfg['zone']}/{cfg['vm']}. {COST_WARNING}")
        return {"cost_nzd": str(amount), "cost_basis": basis, "cost_text": text,
                "vm_hours": hours}

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return self._runner(["git", *args])

    def publish_blockers(self, fetch: bool = True) -> list[str]:
        """Reasons the local checkout cannot drive a production publish.

        remote_build.sh --publish pulls main, commits site-config.js and pushes
        main from this checkout, so it must be on main, clean for site-config.js
        and carry no unpushed commits.
        """
        blockers = []
        if not self.gcloud():
            blockers.append("gcloud is not installed or not on PATH.")
        branch = self.git("rev-parse", "--abbrev-ref", "HEAD")
        if branch.returncode != 0 or branch.stdout.strip() != "main":
            blockers.append("This checkout is not on main; publishing commits and pushes main.")
        status = self.git("status", "--porcelain", "--", "site-config.js")
        if status.returncode != 0 or status.stdout.strip():
            blockers.append("site-config.js has local changes; publishing rewrites and commits it.")
        if fetch:
            self.git("fetch", "--quiet", "origin", "main")
        ahead = self.git("rev-list", "--count", "origin/main..HEAD")
        if ahead.returncode != 0:
            blockers.append("Could not compare with origin/main.")
        elif ahead.stdout.strip() not in ("", "0"):
            blockers.append(f"This checkout has {ahead.stdout.strip()} unpushed commit(s); "
                            "publishing would push them unreviewed. Push or reset them first.")
        return blockers

    def developer_blockers(self) -> tuple[list[str], str | None]:
        blockers = []
        if not self.gcloud():
            blockers.append("gcloud is not installed or not on PATH.")
        alias = None
        try:
            alias = self.alias()
        except Exception as exc:  # noqa: BLE001 - shown to the operator as a blocker
            blockers.append(f"Could not determine the GitHub preview alias: {exc}")
        return blockers, alias

    def env(self) -> dict:
        cfg = self.config()
        return {"CLOUD_PROJECT": cfg["project"], "CLOUD_VM": cfg["vm"],
                "CLOUD_ZONE": cfg["zone"], "VM_USER": cfg["ssh_user"]}


def cloud_command(target: str, python: str, regions: list[str], paid: bool) -> list[str]:
    """The command a cloud job runs. Never adds --reviewed (that stays on the CLI)."""
    if target not in CLOUD_TARGETS:
        raise ValueError(f"not a cloud target: {target}")
    if not regions:
        raise ValueError("no regions selected")
    for region in regions:
        validate(region)
    referee = "--paid-referee" if paid else "--no-paid-referee"
    joined = " ".join(regions)
    if target == "developer":
        return [python, "tools/pipeline.py", joined, "--mode", "cloud", "--cloud", "build",
                "--yes", "--no-open-browser", referee]
    cmd = ["bash", "tools/remote_build.sh", "--regions", joined, "--publish"]
    if any(is_locality_id(r) for r in regions):
        cmd.append("--fetch")
    cmd.append(referee)
    return cmd


def cloud_env(cloud: Cloud) -> dict:
    env = dict(os.environ)
    env.update(cloud.env())
    return env


def parse_cloud_line(line: str, record: dict) -> None:
    """Record preview URL, gate result and published version from build output."""
    text = line.strip()
    if text.startswith("PREVIEW_URL="):
        record["preview_url"] = text.split("=", 1)[1].strip()
    elif text.startswith("Developer preview: "):
        record.setdefault("preview_url", text.split(": ", 1)[1].strip())
    elif text.startswith("GATE_RESULT="):
        record["gate_result"] = text.split("=", 1)[1].strip()
    elif "GATE FAILED" in text:
        record["gate_result"] = "failed"
    elif text.startswith("PUSHED (data v"):
        record["published_version"] = text.removeprefix("PUSHED (data v").rstrip(")").strip()


__all__ = ["CLOUD_TARGETS", "Cloud", "CloudError", "cloud_command", "cloud_env",
           "parse_cloud_line", "COST_WARNING"]
