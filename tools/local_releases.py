"""Local-only release assembly, review, activation, rollback and bounded cleanup."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import uuid
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.output_contract import CONTRACT_VERSION, SUMMARY_REQUIRED
from src.region_ids import is_grid_selection_id, validate
from tools.artifact_common import ArtifactError, artifact_id, artifact_lock, inventory, now
from tools.regional_staging import StagingStore, manifest_hash
from tools.site_assets import FRONTEND_FILES

POLICY_KEYS = ("rollback_hours", "candidate_hours", "staging_hours",
               "incomplete_hours", "grace_hours")
DEFAULT_POLICY = {"rollback_hours": 720, "candidate_hours": 720,
                  "staging_hours": 720, "incomplete_hours": 168, "grace_hours": 72}
CORE_FILES = ("buildings.pmtiles", "panel_layouts.pmtiles", "building_cells.pmtiles",
              "build_summary.json", "assumptions.json", "seasonal_curves.json")


class AssemblyError(ArtifactError):
    def __init__(self, release_id: str, log: Path, code: int):
        super().__init__(f"Assembly failed (exit {code}); inspect {log}", 409)
        self.release_id = release_id


def read_document(path: Path) -> dict:
    if path.is_symlink():
        raise ArtifactError(f"Refusing linked metadata: {path.name}", 409)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ArtifactError(f"Cannot read {path.name}: {exc}", 409) from exc
    if not isinstance(value, dict):
        raise ArtifactError(f"{path.name} must be an object", 409)
    return value


def atomic_document(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}-{uuid.uuid4().hex}")
    try:
        temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def after_hours(timestamp: str, hours: float) -> str:
    return (parse_time(timestamp) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def parse_time(timestamp: str) -> datetime:
    try:
        value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if value.tzinfo is None:
            raise ValueError("Timezone required")
        return value
    except (AttributeError, TypeError, ValueError) as exc:
        raise ArtifactError(f"Invalid lifecycle timestamp: {timestamp!r}", 409) from exc


def expired(timestamp: str, current: str) -> bool:
    return parse_time(timestamp) <= parse_time(current)


def staged_id(build: dict):
    return build.get("staging_id") or build.get("snapshot_id")


def staged_hash(build: dict):
    return build.get("manifest_sha256") or build.get("source_manifest_sha256")


class LocalReleases:
    def __init__(self, staging: StagingStore, root: Path | None = None,
                 frontend: Path = ROOT, python: str = sys.executable,
                 assembler: Callable | None = None):
        self.staging = staging
        configured = root or staging.data / "local_releases"
        if Path(configured).is_symlink():
            raise ArtifactError("Release root cannot be a symlink")
        self.root = Path(configured).resolve()
        for other in (staging.root, staging.data / "out"):
            other = other.resolve()
            if self.root == other or self.root.is_relative_to(other) or other.is_relative_to(self.root):
                raise ArtifactError("Release storage must not overlap staging or outputs")
        self.frontend = Path(frontend).resolve()
        self.python = python
        self.assembler = assembler or self._assemble

    @contextmanager
    def locked(self):
        with artifact_lock(self.staging.data):
            with self._release_lock():
                yield

    @contextmanager
    def _release_lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        if any(path.is_symlink() for path in (self.root, *self.root.parents)):
            raise ArtifactError("Release root cannot contain symlinks", 409)
        lock = self.root / ".lifecycle-lock"
        try:
            lock.mkdir()
        except FileExistsError as exc:
            raise ArtifactError("Local release operation is busy; after a crash inspect .lifecycle-lock", 409) from exc
        try:
            (lock / "owner.json").write_text(json.dumps({"pid": os.getpid(), "started_at": now()}))
            yield
        finally:
            (lock / "owner.json").unlink(missing_ok=True)
            lock.rmdir()

    def state(self) -> dict:
        path = self.root / "state.json"
        if not path.exists():
            return {"schema_version": 1, "generation": 0, "active": None, "previous": None,
                    "rollback_until": None, "policy": dict(DEFAULT_POLICY), "approvals": {}, "history": [],
                    "purge_marks": {}}
        state = read_document(path)
        if (state.get("schema_version") != 1 or type(state.get("generation")) is not int or
                state["generation"] < 0 or
                any(not isinstance(state.get(k), dict) for k in ("approvals", "purge_marks")) or
                not isinstance(state.get("history"), list) or
                not {"active", "previous", "rollback_until", "policy"} <= state.keys()):
            raise ArtifactError("Invalid or unsupported local release state schema", 409)
        for key in ("active", "previous"):
            if state[key] is not None:
                artifact_id(state[key])
        if state["rollback_until"] is not None:
            parse_time(state["rollback_until"])
        for item in state["history"]:
            if not isinstance(item, dict) or not {"at", "active", "previous"} <= item.keys():
                raise ArtifactError("Invalid local release history", 409)
            parse_time(item["at"])
        for item in state["approvals"].values():
            if (not isinstance(item, dict) or type(item.get("generation")) is not int or
                    not isinstance(item.get("manifest_sha256"), str)):
                raise ArtifactError("Invalid local release approval", 409)
        for value in state["purge_marks"].values():
            parse_time(value)
        if state["policy"] is None:
            state["policy"] = dict(DEFAULT_POLICY)
        if state["policy"] is not None:
            # Policies saved before snapshots were folded into staging carry
            # a snapshot window; it no longer governs anything.
            if isinstance(state["policy"], dict):
                state["policy"].pop("snapshot_hours", None)
            self._validate_policy(state["policy"])
        return state

    def _save(self, state: dict) -> None:
        atomic_document(self.root / "state.json", state)

    def path(self, identifier: str) -> Path:
        return self.root / "releases" / artifact_id(identifier)

    def manifest(self, identifier: str) -> dict:
        directory = self.path(identifier)
        if any(p.is_symlink() for p in (directory, *directory.parents)):
            raise ArtifactError("Release links are not supported", 409)
        document = read_document(directory / "manifest.json")
        if (document.get("schema_version") != 1 or document.get("release_id") != identifier or
                not isinstance(document.get("created_at"), str) or
                type(document.get("baseline_generation")) is not int or
                "parent_release_id" not in document or
                any(not isinstance(document.get(k), dict) for k in ("region_builds", "summaries")) or
                any(not isinstance(document.get(k), list) for k in
                    ("site_inventory", "region_inventory", "warnings"))):
            raise ArtifactError("Invalid release identity/schema", 409)
        if not document["region_builds"] or set(document["region_builds"]) != set(document["summaries"]):
            raise ArtifactError("Invalid release region mapping", 409)
        for region, build in document["region_builds"].items():
            validate(region)
            # Releases made before snapshots were folded into staging record
            # snapshot_id/source_manifest_sha256; both name the staged copy.
            if (not isinstance(build, dict) or not isinstance(staged_hash(build), str) or
                    not isinstance(document["summaries"][region], dict)):
                raise ArtifactError("Invalid release regional metadata", 409)
            artifact_id(staged_id(build))
        parse_time(document["created_at"])
        if document["parent_release_id"] is not None:
            artifact_id(document["parent_release_id"])
        for key in ("site_inventory", "region_inventory"):
            if any(not isinstance(item, dict) or
                   not {"path", "bytes", "sha256"} <= item.keys() or
                   not isinstance(item["path"], str) or
                   Path(item["path"]).is_absolute() or
                   any(part in (".", "..") for part in item["path"].split("/")) or
                   type(item["bytes"]) is not int or item["bytes"] < 0 or
                   not isinstance(item["sha256"], str)
                   for item in document[key]):
                raise ArtifactError("Invalid release inventory", 409)
        return document

    def verify(self, identifier: str) -> dict:
        manifest = self.manifest(identifier)
        directory = self.path(identifier)
        if inventory(directory / "site") != manifest["site_inventory"]:
            raise ArtifactError("Release site integrity failed", 409)
        if inventory(directory / "regions") != manifest["region_inventory"]:
            raise ArtifactError("Release regional integrity failed", 409)
        for region, summary in manifest["summaries"].items():
            if self._regional_summary(directory / "regions" / region, region) != summary:
                raise ArtifactError("Release summary differs from regional input", 409)
        if set(p.name for p in directory.iterdir()) != {"manifest.json", "site", "regions", "assembly.log"}:
            raise ArtifactError("Unexpected release files", 409)
        return manifest

    def list(self) -> dict:
        state = self.state()
        releases = []
        for path in sorted((self.root / "releases").glob("*")):
            if path.name.startswith("."):
                continue
            manifest = self.manifest(path.name)
            releases.append({key: manifest[key] for key in
                             ("release_id", "created_at", "parent_release_id", "region_builds", "warnings")})
        releases.sort(key=lambda item: (item["created_at"], item["release_id"]))
        return {"state": state, "releases": releases}

    def incomplete_log(self, identifier: str) -> dict:
        path = self.root / "releases" / f".incomplete-{artifact_id(identifier)}" / "assembly.log"
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ArtifactError("Refusing linked assembly log", 409)
        if not path.exists():
            raise ArtifactError("Incomplete assembly log is unavailable or has expired", 404)
        if not stat.S_ISREG(path.stat().st_mode):
            raise ArtifactError("Assembly log is not a regular file", 409)
        size, limit = path.stat().st_size, 64 * 1024
        with path.open("rb") as stream:
            stream.seek(max(0, size - limit))
            text = stream.read(limit).decode("utf-8", errors="replace")
        return {"release_id": identifier, "text": text, "truncated": size > limit}

    def _regional_summary(self, artifacts: Path, region: str) -> dict:
        summary = read_document(artifacts / "summary.json")
        missing = [key for key in SUMMARY_REQUIRED if key not in summary]
        if missing or summary.get("region") != region or summary.get("contract") != CONTRACT_VERSION:
            raise ArtifactError(f"{region}: missing summary fields {missing} or incompatible region/contract", 409)
        if is_grid_selection_id(region):
            raise ArtifactError("Partial selections are preview-only and cannot replace complete regions", 409)
        for name in ("buildings.pmtiles", "panel_layouts.pmtiles", "cells.json",
                     "addresses.json", "sun.json", "solar_potential.geojson"):
            if not (artifacts / name).is_file():
                raise ArtifactError(f"{region}: required artifact {name} is missing", 409)
        for name in ("n", "n_est", "panel_count", "kwp", "kwh"):
            value = summary[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ArtifactError(f"{region}: invalid {name}", 409)
        for name in ("ladder", "by_type", "assumptions", "model", "workings"):
            if not isinstance(summary[name], dict):
                raise ArtifactError(f"{region}: invalid {name}", 409)
        if len(summary["ladder"]) != summary["n"] or summary["n_est"] > summary["n"]:
            raise ArtifactError(f"{region}: inconsistent building coverage", 409)
        centroid = summary["centroid"]
        if (not isinstance(centroid, list) or len(centroid) != 2 or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in centroid) or
                not -180 <= centroid[0] <= 180 or not -90 <= centroid[1] <= 90):
            raise ArtifactError(f"{region}: invalid centroid", 409)
        return summary

    def _assemble(self, regions: list[str], inputs: Path, destination: Path, log: Path) -> None:
        with log.open("w", encoding="utf-8") as output:
            process = subprocess.run(
                [self.python, str(ROOT / "src/combine_regions.py"), "--regions", *regions,
                 "--out-root", str(inputs), "--dest", str(destination),
                 "--skip-markup-lines", "--frozen-inputs"],
                cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, check=False)
        if process.returncode:
            raise AssemblyError(artifact_id(log.parent.name.removeprefix(".incomplete-")), log, process.returncode)

    @staticmethod
    def _assembly_ref() -> dict:
        return {"source_sha256": {
            path.relative_to(ROOT).as_posix(): digest(path)
            for path in [Path(__file__), ROOT / "config.py", *sorted((ROOT / "src").glob("*.py"))]}}

    def _site(self, site: Path, identifier: str, summaries: dict) -> dict:
        before = {}
        for name in (*FRONTEND_FILES, "site-config.js"):
            source = self.frontend / name
            if not source.is_file() or source.is_symlink():
                raise ArtifactError(f"Required frontend asset unavailable: {name}", 409)
            before[name] = digest(source)
            shutil.copyfile(source, site / name)
        settings = (site / "site-config.js").read_text(encoding="utf-8")
        lon = sum(s["centroid"][0] for s in summaries.values()) / len(summaries)
        lat = sum(s["centroid"][1] for s in summaries.values()) / len(summaries)
        for pattern, replacement in (
            (r'(dataBase:\s*)"[^"]*"', r'\1"./"'),
            (r'(dataVersion:\s*)"[^"]*"', lambda m: f'{m[1]}"{identifier}"'),
            (r'defaultView:\s*\{[^}]*\}', f"defaultView: {{ center: [{lon}, {lat}], zoom: 13 }}"),
        ):
            settings, count = re.subn(pattern, replacement, settings)
            if count != 1:
                raise ArtifactError("Cannot create isolated site configuration", 409)
        settings += '\nObject.assign(window.SITE, { terrainAvailable: false, markupAvailable: false });\n'
        (site / "site-config.js").write_text(settings, encoding="utf-8")
        if any(digest(self.frontend / name) != checksum for name, checksum in before.items()):
            raise ArtifactError("Frontend changed during assembly; retry with idle source files", 409)
        return before

    def create(self, replacements: list[str], expected_generation: int) -> dict:
        if not isinstance(replacements, list) or not replacements:
            raise ArtifactError("Select at least one staged region")
        with self.locked():
            state = self.state()
            self._expect(state, expected_generation)
            base = self.verify(state["active"]) if state["active"] else None
            builds = dict(base["region_builds"]) if base else {}
            source_paths = {region: self.path(base["release_id"]) / "regions" / region
                            for region in builds} if base else {}
            selected = set()
            for identifier in replacements:
                staged = self.staging.verify(identifier)
                region = validate(staged["region_id"])
                if region in selected:
                    raise ArtifactError(f"Choose only one staged version of {region}", 409)
                selected.add(region)
                builds[region] = {"staging_id": identifier,
                                  "manifest_sha256": manifest_hash(self.staging.root / identifier)}
                source_paths[region] = self.staging.artifacts_path(identifier)
            identifier = uuid.uuid4().hex
            partial = self.root / "releases" / f".incomplete-{identifier}"
            partial.mkdir(parents=True)
            regions, site = partial / "regions", partial / "site"
            regions.mkdir()
            (site / "data").mkdir(parents=True)
            summaries, assumptions, owned = {}, None, set()
            if any(a != b and b.startswith(a + "/") for a in builds for b in builds):
                raise ArtifactError("Overlapping parent/child region namespaces cannot be assembled", 409)
            for region, source in sorted(source_paths.items()):
                before = inventory(source)
                summary = self._regional_summary(source, region)
                if assumptions is not None and assumptions != summary["assumptions"]:
                    raise ArtifactError("Mixed PV assumptions cannot be represented by the combined frontend", 409)
                assumptions = summary["assumptions"]
                ladder = summary["ladder"]
                if not isinstance(ladder, dict):
                    raise ArtifactError(f"{region}: invalid building ladder", 409)
                overlap = owned.intersection(ladder)
                if overlap:
                    raise ArtifactError(f"Overlapping building ownership across regions: {next(iter(overlap))}", 409)
                owned.update(ladder)
                destination = regions / region
                shutil.copytree(source, destination, symlinks=True)
                if inventory(destination) != before or inventory(source) != before:
                    raise ArtifactError("Regional inputs changed during assembly", 409)
                summaries[region] = summary
            assembly_ref = self._assembly_ref()
            self.assembler(sorted(builds), regions, site / "data", partial / "assembly.log")
            if self._assembly_ref() != assembly_ref:
                raise ArtifactError("Assembly code/config changed during creation; retry with idle source files", 409)
            for name in CORE_FILES:
                if not (site / "data" / name).is_file():
                    raise ArtifactError(f"Combined output is missing {name}", 409)
            if inventory(regions) != sorted([
                dict(item, path=f"{region}/{item['path']}")
                for region in sorted(builds) for item in inventory(source_paths[region])
            ], key=lambda item: Path(item["path"]).parts):
                raise ArtifactError("Assembly modified regional inputs", 409)
            frontend_ref = self._site(site, identifier, summaries)
            manifest = {
                "schema_version": 1, "release_id": identifier, "created_at": now(),
                "parent_release_id": state["active"], "baseline_generation": state["generation"],
                "region_builds": builds, "summaries": summaries, "output_contract_version": CONTRACT_VERSION,
                "frontend_source_sha256": frontend_ref,
                "assembly_ref": assembly_ref,
                "warnings": ["Terrain and terrain-ground are not captured by regional staging; 3D terrain is unavailable.",
                             "Shared seasonal curves and cells are assembled using the recorded local producer."],
                "site_inventory": inventory(site), "region_inventory": inventory(regions),
            }
            atomic_document(partial / "manifest.json", manifest)
            partial.rename(self.path(identifier))
            self.verify(identifier)
            return manifest

    @staticmethod
    def _expect(state: dict, expected_generation: int) -> None:
        if type(expected_generation) is not int or expected_generation != state["generation"]:
            raise ArtifactError("Active local release changed; refresh, compare and create a new candidate", 409)

    def compare(self, identifier: str) -> dict:
        candidate = self.verify(identifier)
        state = self.state()
        base = self.verify(state["active"]) if state["active"] else None
        rows = []

        def metric(region, field, previous, proposed, status, reason):
            rows.append(dict(region=region, field=field, previous=previous, proposed=proposed,
                             status=status, reason=reason))

        for region, summary in candidate["summaries"].items():
            old = base["summaries"].get(region) if base else None
            metric(region, "region_membership", "existing" if old else None, "complete region", "pass",
                   "New coverage" if not old else "Region preserved or replaced; omission is not removal")
            for field in ("n", "n_est", "panel_count", "kwh", "built_at", "git", "elevation", "model",
                          "assumptions", "contract"):
                previous, proposed = old.get(field) if old else None, summary.get(field)
                status, reason = "pass", "Recorded; an increase does not imply improved quality"
                if proposed is None or proposed == "" or proposed == "unknown" or proposed == {}:
                    status, reason = "unknown", "Not recorded; requires explicit review"
                elif old and previous != proposed:
                    status, reason = "warning", "Changed; inspect regional output"
                    if field in ("n", "n_est") and proposed < (previous or 0):
                        status, reason = "fail", "Building coverage decreased; automatic approval blocked"
                metric(region, field, previous, proposed, status, reason)
            for field in ("sources", "coverage", "boundary_version"):
                proposed = summary.get(field)
                status = ("unknown" if not proposed or proposed == "unknown" else
                          "warning" if old and old.get(field) != proposed else "pass")
                reason = "Source/boundary completeness must be reviewed when absent or changed"
                if field == "coverage":
                    complete = proposed.get("complete") if isinstance(proposed, dict) else None
                    if type(complete) is not bool:
                        status, reason = "unknown", "coverage.complete must explicitly record true or false"
                    elif not complete:
                        status, reason = "fail", "Source coverage is explicitly incomplete; automatic approval blocked"
                metric(region, field, old.get(field) if old else None, proposed, status, reason)
        stale = (candidate["baseline_generation"] != state["generation"] or
                 candidate["parent_release_id"] != state["active"])
        return {"policy_version": 1, "release_id": identifier, "generation": state["generation"],
                "baseline_release_id": state["active"], "stale": stale, "metrics": rows,
                "automatic_eligible": not stale and all(r["status"] == "pass" for r in rows),
                "warnings": candidate["warnings"]}

    def approve(self, identifier: str, expected_generation: int, reason: str = "") -> dict:
        if not isinstance(reason, str):
            raise ArtifactError("Review reason must be text")
        with self.locked():
            state = self.state()
            self._expect(state, expected_generation)
            comparison = self.compare(identifier)
            if comparison["stale"]:
                raise ArtifactError("Candidate baseline is stale; create a new candidate from current local deployment", 409)
            if not comparison["automatic_eligible"] and (not isinstance(reason, str) or not reason.strip()):
                raise ArtifactError("Warnings/unknowns require a recorded manual review reason", 409)
            decision = {"release_id": identifier, "generation": state["generation"], "at": now(),
                        "manifest_sha256": digest(self.path(identifier) / "manifest.json"),
                        "policy_version": 1, "actor": "local-maintainer" if reason.strip() else "automatic-policy",
                        "reason": reason.strip(), "comparison": comparison}
            state["approvals"][identifier] = decision
            self._save(state)
            return decision

    @staticmethod
    def _validate_policy(policy: dict) -> None:
        if not isinstance(policy, dict) or set(policy) != set(POLICY_KEYS):
            raise ArtifactError("Provide every retention policy field explicitly")
        for key, value in policy.items():
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
                raise ArtifactError(f"{key} must be a positive number of hours")
            try:
                after_hours(now(), value)
            except OverflowError as exc:
                raise ArtifactError(f"{key} exceeds the supported date range") from exc

    def set_policy(self, policy: dict) -> dict:
        self._validate_policy(policy)
        with self.locked():
            state = self.state()
            state["policy"] = dict(policy)
            state["purge_marks"] = {}
            self._save(state)
        return policy

    def activate(self, identifier: str, expected_generation: int) -> dict:
        with self.locked():
            state = self.state()
            self._expect(state, expected_generation)
            if not state["policy"]:
                raise ArtifactError("Configure explicit retention/rollback windows before activation", 409)
            manifest = self.verify(identifier)
            decision = state["approvals"].get(identifier)
            if (not decision or decision["generation"] != state["generation"] or
                    decision["manifest_sha256"] != digest(self.path(identifier) / "manifest.json") or
                    manifest["parent_release_id"] != state["active"]):
                raise ArtifactError("Review and approve this exact candidate against the active release first", 409)
            return self._switch(state, identifier, "activate")

    def publish(self, identifier: str, expected_generation: int, reason: str = "") -> dict:
        """Auto-review eligible candidates; switching still checks the exact baseline/hash."""
        self.approve(identifier, expected_generation, reason)
        return self.activate(identifier, expected_generation)

    def _switch(self, state: dict, identifier: str, action: str) -> dict:
        timestamp = now()
        previous = state["active"]
        deadline = (state["rollback_until"] if action == "rollback" else
                    after_hours(timestamp, state["policy"]["rollback_hours"]) if previous else None)
        state.update(active=identifier, previous=previous, generation=state["generation"] + 1,
                     rollback_until=deadline)
        state["history"].append({"action": action, "previous": previous, "active": identifier,
                                 "generation": state["generation"], "at": timestamp})
        self._save(state)
        return state

    def rollback(self, expected_generation: int) -> dict:
        with self.locked():
            state = self.state()
            self._expect(state, expected_generation)
            if not state["previous"] or not state["rollback_until"] or expired(state["rollback_until"], now()):
                raise ArtifactError("No unexpired rollback release is available", 409)
            self.verify(state["previous"])
            return self._switch(state, state["previous"], "rollback")

    def cleanup(self, apply: bool = False) -> dict:
        if type(apply) is not bool:
            raise ArtifactError("apply must be a boolean")
        with self.locked():
            state, current = self.state(), now()
            policy = state["policy"]
            if not policy:
                raise ArtifactError("Cleanup disabled: configure all retention windows explicitly", 409)
            for root in (self.root / "releases", self.staging.root):
                if any(path.is_symlink() for path in (root, *root.parents)):
                    raise ArtifactError("Cleanup refuses linked storage roots", 409)
            protected = {state["active"]}
            if state["previous"] and state["rollback_until"] and not expired(state["rollback_until"], current):
                protected.add(state["previous"])
            candidates = []
            referenced = set()
            for path in sorted((self.root / "releases").glob("*")):
                if path.name.startswith(".incomplete-"):
                    continue
                manifest = self.manifest(path.name)
                last_use = max([manifest["created_at"]] + [e["at"] for e in state["history"]
                                if path.name in (e["active"], e["previous"])], key=parse_time)
                if path.name not in protected and expired(after_hours(last_use, policy["candidate_hours"]), current):
                    candidates.append(("release", path.name, path))
                else:
                    referenced.update(staged_id(v) for v in manifest["region_builds"].values())
            referenced.update(self.staging.current_ids())
            for path in sorted(self.staging.root.glob("*")):
                if path.name.startswith(".") or path.name == "current.json":
                    continue
                artifact_id(path.name)
                if path.is_symlink():
                    raise ArtifactError("Cleanup refuses linked directories", 409)
                manifest = read_document(path / "manifest.json")
                if manifest.get("staging_id") != path.name or manifest.get("schema_version") not in (1, 2):
                    raise ArtifactError("Cleanup refuses unknown artifact identity/schema", 409)
                if path.name not in referenced and expired(
                        after_hours(manifest.get("staged_at"), policy["staging_hours"]), current):
                    candidates.append(("staging", path.name, path))
            for kind, root in (("release", self.root / "releases"), ("staging", self.staging.root)):
                for path in root.glob(".incomplete-*"):
                    artifact_id(path.name.removeprefix(".incomplete-"))
                    if path.is_symlink():
                        raise ArtifactError("Cleanup refuses linked incomplete directories", 409)
                    modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
                    if expired(after_hours(modified, policy["incomplete_hours"]), current):
                        candidates.append((f"incomplete-{kind}", path.name, path))
            deleted, eligible = [], []
            marks = state["purge_marks"]
            keys = {f"{kind}:{identifier}" for kind, identifier, _ in candidates}
            state["purge_marks"] = {key: value for key, value in marks.items() if key in keys}
            for kind, identifier, path in candidates:
                key = f"{kind}:{identifier}"
                mark = state["purge_marks"].setdefault(key, current)
                ready = expired(after_hours(mark, policy["grace_hours"]), current)
                eligible.append({"kind": kind, "id": identifier, "purge_after": after_hours(mark, policy["grace_hours"]),
                                 "ready": ready})
                if apply and ready:
                    # Delete only a named managed child; never a root or a link.
                    if path.is_symlink() or path.parent not in {
                            self.root / "releases", self.staging.root}:
                        raise ArtifactError("Unsafe cleanup target", 409)
                    record = {"artifact": key, "purged_at": current}
                    if not kind.startswith("incomplete-"):
                        manifest = read_document(path / "manifest.json")
                        record["metadata"] = {k: v for k, v in manifest.items()
                                              if k not in ("summary", "summaries", "site_inventory",
                                                           "region_inventory", "artifacts")}
                    atomic_document(self.root / "purged" / f"{kind}-{identifier}.json", record)
                    shutil.rmtree(path)
                    state["history"].append({"action": "purge", "previous": None, "active": None,
                                             "artifact": key, "at": current, "generation": state["generation"]})
                    state["purge_marks"].pop(key, None)
                    deleted.append(key)
            self._save(state)
            return {"eligible": eligible, "deleted": deleted,
                    "note": "Explicit policy and grace period applied; active releases are protected."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("list", "create", "verify", "compare", "approve", "activate", "publish",
                                          "rollback", "policy", "cleanup"))
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--generation", type=int, default=0)
    parser.add_argument("--reason", default="")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--staging-root", type=Path)
    parser.add_argument("--release-root", type=Path)
    parser.add_argument("--policy-json", help="JSON object with all retention windows in hours")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        store = LocalReleases(StagingStore(args.data, args.staging_root), args.release_root)
        if args.action == "list":
            result = store.list()
        elif args.action == "create":
            result = store.create(args.ids, args.generation)
        elif args.action == "rollback":
            result = store.rollback(args.generation)
        elif args.action == "policy":
            result = store.set_policy(json.loads(args.policy_json or "{}"))
        elif args.action == "cleanup":
            result = store.cleanup(args.apply)
        else:
            if len(args.ids) != 1:
                raise ArtifactError("Select exactly one release ID")
            method = getattr(store, args.action)
            result = (method(args.ids[0], args.generation, args.reason) if args.action in {"approve", "publish"} else
                      method(args.ids[0], args.generation) if args.action == "activate" else method(args.ids[0]))
        print(json.dumps(result, indent=2))
        return 0
    except (ArtifactError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
