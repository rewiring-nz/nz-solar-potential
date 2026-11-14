"""Stage built regions: a frozen, checksummed local copy of data/out/<region>; never deploys.

A staged copy is what local releases are assembled from, so later rebuilds of
the same region cannot change a candidate. Each copy lives in
regional_staging/<staging id>/ as manifest.json plus artifacts/.

Staging locks only the region being copied (regional_staging/.locks/), not the
whole build workspace: other regions keep building. The copy is discarded if
the region's files change while it is read. Copies made before staging read
directly from data/out (schema 1, artifacts under snapshot/) remain readable.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.region_ids import file_slug, is_grid_selection_id, validate
from tools.artifact_common import ArtifactError, artifact_id, artifact_lock, inventory, now

SCHEMA = 2
LEGACY_SCHEMA = 1


def manifest_hash(directory: Path) -> str:
    return hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    if path.is_symlink():
        raise ArtifactError("Staging links are not supported", 409)
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ArtifactError("Staging manifest must be an object", 409)
    return document


class StagingStore:
    def __init__(self, data: Path, root: Path | None = None):
        self.data = Path(data).resolve()
        configured = Path(root) if root else self.data / "regional_staging"
        if configured.is_symlink():
            raise ArtifactError("Staging storage cannot use symlinks")
        self.root = configured.resolve()
        outputs = (self.data / "out").resolve()
        if self.root == outputs or self.root.is_relative_to(outputs) or outputs.is_relative_to(self.root):
            raise ArtifactError("Staging storage must be separate from emitted outputs")
        self.locks = self.root / ".locks"

    # ---- per-region lock -------------------------------------------------
    @contextmanager
    def region_lock(self, region: str):
        """Mark one region as being staged so the build queue leaves it alone."""
        self.locks.mkdir(parents=True, exist_ok=True)
        lock = self.locks / file_slug(region)
        try:
            lock.mkdir()
        except FileExistsError as exc:
            raise ArtifactError(f"{region} is already being staged; after a crash inspect {lock}", 409) from exc
        try:
            (lock / "owner.json").write_text(json.dumps(
                {"pid": os.getpid(), "region": region, "started_at": now()}), encoding="utf-8")
            yield
        finally:
            (lock / "owner.json").unlink(missing_ok=True)
            lock.rmdir()

    def staging_regions(self) -> set[str]:
        """Regions with a staging copy in progress (any local process)."""
        if not self.locks.is_dir():
            return set()
        out = set()
        for lock in self.locks.iterdir():
            try:
                region = json.loads((lock / "owner.json").read_text(encoding="utf-8")).get("region")
            except (OSError, ValueError):
                region = None
            # A lock without a readable owner is still a lock; report its slug.
            out.add(region or lock.name.replace("__", "/"))
        return out

    # ---- staging ---------------------------------------------------------
    def stage(self, region: str, identifier: str | None = None) -> dict:
        try:
            validate(region)
        except ValueError as exc:
            raise ArtifactError(str(exc)) from exc
        if is_grid_selection_id(region):
            raise ArtifactError("Grid cells are local build pieces; stage whole regions only")
        with self.region_lock(region), artifact_lock(self.data):
            return self._stage(region, artifact_id(identifier or uuid.uuid4().hex))

    def _stage(self, region: str, identifier: str) -> dict:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            for path in (self.root, *self.root.parents):
                if path.is_symlink():
                    raise ArtifactError("Staging storage cannot use symlinks")
            destination = self.root / identifier
            if destination.exists() or destination.is_symlink():
                raise ArtifactError("Staged copy already exists; it cannot be overwritten", 409)
            source = self.data / "out" / region
            if not source.is_dir() and not source.is_symlink():
                raise ArtifactError(f"{region} is not built: no emitted summary.json", 409)
            before = inventory(source)
            if not any(item["path"] == "summary.json" for item in before):
                raise ArtifactError("Region has no emitted summary.json; build it first", 409)
            partial = self.root / f".incomplete-{identifier}"
            try:
                partial.mkdir()
            except FileExistsError as exc:
                raise ArtifactError("An incomplete copy already reserves this staging ID", 409) from exc
            artifacts = partial / "artifacts"
            artifacts.mkdir()
            for item in before:
                target = artifacts / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / item["path"], target)
            if inventory(artifacts) != before or inventory(source) != before:
                raise ArtifactError("Region files changed while staging (is it being rebuilt?); "
                                    "copy not completed", 409)
            summary = json.loads((artifacts / "summary.json").read_text(encoding="utf-8"))
            if not isinstance(summary, dict) or summary.get("region", region) != region:
                raise ArtifactError("Emitted summary does not match the selected region", 409)
            manifest = {
                "schema_version": SCHEMA, "staging_id": identifier, "region_id": region,
                "staged_at": now(), "built_at": summary.get("built_at"),
                "producer_ref": summary.get("git"), "summary": summary,
                "artifacts": before, "scope": "emitted_region_only",
            }
            (partial / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            self._verify(partial, identifier)
            partial.rename(destination)
            return manifest
        except (OSError, ValueError) as exc:
            raise ArtifactError(f"Staging failed; no copy completed: {exc}", 409) from exc

    # ---- reading ---------------------------------------------------------
    def artifacts_path(self, identifier: str) -> Path:
        directory = self.root / artifact_id(identifier)
        legacy = directory / "snapshot" / "artifacts"
        return legacy if legacy.is_dir() and not (directory / "artifacts").exists() else directory / "artifacts"

    def _verify(self, directory: Path, identifier: str) -> dict:
        if directory.is_symlink():
            raise ArtifactError("Staging links are not supported", 409)
        manifest = _read(directory / "manifest.json")
        if manifest.get("staging_id") != identifier:
            raise ArtifactError("Staging identity does not match its directory", 409)
        if manifest.get("schema_version") == LEGACY_SCHEMA:
            return self._verify_legacy(directory, identifier, manifest)
        if manifest.get("schema_version") != SCHEMA:
            raise ArtifactError("Unsupported staging schema", 409)
        if inventory(directory / "artifacts") != manifest.get("artifacts"):
            raise ArtifactError("Staging verification failed: artifact inventory/checksums differ", 409)
        if sorted(p.name for p in directory.iterdir()) != ["artifacts", "manifest.json"]:
            raise ArtifactError("Unexpected staging files", 409)
        return manifest

    @staticmethod
    def _verify_legacy(directory: Path, identifier: str, manifest: dict) -> dict:
        """Schema 1: staging wrapped a copy of a separately captured snapshot."""
        if manifest.get("source_snapshot_id") != identifier:
            raise ArtifactError("Staging identity does not match its directory", 409)
        inner = directory / "snapshot"
        source = _read(inner / "manifest.json")
        if (source.get("schema_version") != 1 or source.get("snapshot_id") != identifier
                or manifest_hash(inner) != manifest.get("source_manifest_sha256")
                or source.get("region_id") != manifest.get("region_id")):
            raise ArtifactError("Staged source manifest differs from its recorded identity", 409)
        if inventory(inner / "artifacts") != source.get("artifacts"):
            raise ArtifactError("Staging verification failed: artifact inventory/checksums differ", 409)
        if (sorted(p.name for p in directory.iterdir()) != ["manifest.json", "snapshot"]
                or sorted(p.name for p in inner.iterdir()) != ["artifacts", "manifest.json"]):
            raise ArtifactError("Unexpected staging files", 409)
        return {**manifest, "artifacts": source["artifacts"], "summary": source.get("summary"),
                "producer_ref": source.get("producer_ref")}

    def verify(self, identifier: str) -> dict:
        identifier = artifact_id(identifier)
        try:
            return self._verify(self.root / identifier, identifier)
        except (OSError, ValueError) as exc:
            raise ArtifactError(f"Staging verification failed: {exc}", 409) from exc

    def list(self) -> list[dict]:
        if not self.root.exists():
            return []
        records = []
        for directory in sorted(self.root.iterdir()):
            if directory.name.startswith("."):
                continue
            try:
                artifact_id(directory.name)
                manifest = _read(directory / "manifest.json")
                records.append({key: manifest.get(key) for key in (
                    "staging_id", "region_id", "staged_at", "built_at", "producer_ref", "schema_version")})
            except (ArtifactError, OSError, ValueError) as exc:
                records.append({"staging_id": directory.name, "error": str(exc)})
        return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("stage", "verify", "list"))
    parser.add_argument("value", nargs="?", help="region id to stage, or staging ID to verify")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--staging-root", type=Path)
    args = parser.parse_args()
    try:
        store = StagingStore(args.data, args.staging_root)
        if args.action == "list":
            result = store.list()
        elif not args.value:
            raise ArtifactError("stage requires a region; verify requires a staging ID")
        else:
            result = store.stage(args.value) if args.action == "stage" else store.verify(args.value)
        print(json.dumps(result, indent=2))
        return 0
    except ArtifactError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
