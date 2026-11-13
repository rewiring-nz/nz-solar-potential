"""Verified local copies of emitted regions; no build, publication or cleanup."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.region_ids import validate

_ID = re.compile(r"[a-f0-9]{32}")


class SnapshotError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def snapshot_id(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise SnapshotError("Invalid snapshot ID")
    return value


def inventory(directory: Path) -> list[dict[str, Any]]:
    """Reject links and special files, including links in directory ancestors."""
    for ancestor in (directory, *directory.parents):
        if ancestor.is_symlink():
            raise SnapshotError(f"Symlinks are not supported: {ancestor.name}")
    if not directory.is_dir():
        raise SnapshotError("Emitted artifact directory is missing", 404)
    entries = []
    for path in sorted(directory.rglob("*")):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise SnapshotError(f"Symlinks are not supported: {path.name}")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise SnapshotError(f"Not a regular artifact: {path.name}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        after = path.stat()
        if (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise SnapshotError("Artifacts changed while being read; wait for the build to finish", 409)
        entries.append({"path": path.relative_to(directory).as_posix(),
                        "bytes": after.st_size, "sha256": digest.hexdigest()})
    return entries


class SnapshotStore:
    def __init__(self, data: Path, root: Path | None = None):
        self.data = Path(data).resolve()
        if root and Path(root).is_symlink():
            raise SnapshotError("Snapshot storage cannot use symlinks")
        self.root = Path(root).resolve() if root else self.data / "regional_snapshots"
        outputs = (self.data / "out").resolve()
        resolved = self.root.resolve()
        if resolved == outputs or resolved.is_relative_to(outputs) or outputs.is_relative_to(resolved):
            raise SnapshotError("Snapshot storage must be separate from emitted outputs")

    def capture(self, region: str, identifier: str | None = None) -> dict:
        try:
            validate(region)
        except ValueError as exc:
            raise SnapshotError(str(exc)) from exc
        identifier = snapshot_id(identifier or uuid.uuid4().hex)
        self.root.mkdir(parents=True, exist_ok=True)
        for path in (self.root, *self.root.parents):
            if path.is_symlink():
                raise SnapshotError("Snapshot storage cannot use symlinks")
        destination = self.root / identifier
        if destination.exists():
            raise SnapshotError("Snapshot already exists; it cannot be overwritten", 409)
        lock = self.data / "pipeline-workspace.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        try:
            lock.mkdir()
        except FileExistsError as exc:
            raise SnapshotError("Workspace is busy; wait for the local build or snapshot to finish", 409) from exc
        try:
            (lock / "owner.json").write_text(json.dumps({
                "pid": os.getpid(), "task": f"snapshot-{identifier}", "operation": "snapshot",
            }), encoding="utf-8")
            source = self.data / "out" / region
            before = inventory(source)
            if not any(item["path"] == "summary.json" for item in before):
                raise SnapshotError("Region has no emitted summary.json; build it first", 409)
            partial = self.root / f".incomplete-{identifier}"
            try:
                partial.mkdir()
            except FileExistsError as exc:
                raise SnapshotError("An incomplete capture already reserves this snapshot ID", 409) from exc
            artifacts = partial / "artifacts"
            artifacts.mkdir()
            for item in before:
                target = artifacts / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / item["path"], target)
            if inventory(artifacts) != before or inventory(source) != before:
                raise SnapshotError("Source changed during capture; snapshot not completed", 409)
            summary = json.loads((artifacts / "summary.json").read_text(encoding="utf-8"))
            if not isinstance(summary, dict) or summary.get("region", region) != region:
                raise SnapshotError("Emitted summary does not match the selected region", 409)
            manifest = {
                "schema_version": 1, "snapshot_id": identifier, "region_id": region,
                "created_at": now(), "built_at": summary.get("built_at"),
                "producer_ref": summary.get("git"),
                "summary": summary, "artifacts": before,
                "scope": "emitted_region_only",
            }
            (partial / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            self._verify(partial, identifier)
            partial.rename(destination)
            return manifest
        except (OSError, ValueError) as exc:
            raise SnapshotError(f"Capture failed; no snapshot completed: {exc}", 409) from exc
        finally:
            (lock / "owner.json").unlink(missing_ok=True)
            lock.rmdir()

    def _verify(self, directory: Path, identifier: str) -> dict:
        if directory.is_symlink() or (directory / "manifest.json").is_symlink():
            raise SnapshotError("Snapshot links are not supported")
        try:
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SnapshotError("Snapshot manifest is missing or invalid", 409) from exc
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise SnapshotError("Unsupported snapshot schema", 409)
        if manifest.get("snapshot_id") != identifier:
            raise SnapshotError("Snapshot identity does not match its directory", 409)
        if inventory(directory / "artifacts") != manifest.get("artifacts"):
            raise SnapshotError("Snapshot verification failed: artifact inventory/checksums differ", 409)
        if sorted(p.name for p in directory.iterdir()) != ["artifacts", "manifest.json"]:
            raise SnapshotError("Snapshot verification failed: unexpected snapshot files", 409)
        return manifest

    def verify_directory(self, directory: Path, identifier: str) -> dict:
        """Verify an independent copy without requiring the original snapshot."""
        return self._verify(directory, snapshot_id(identifier))

    def verify(self, identifier: str) -> dict:
        identifier = snapshot_id(identifier)
        try:
            return self._verify(self.root / identifier, identifier)
        except OSError as exc:
            raise SnapshotError(f"Snapshot verification failed: {exc}", 409) from exc

    def list(self) -> list[dict]:
        if not self.root.exists():
            return []
        records = []
        for directory in sorted(self.root.iterdir()):
            if not _ID.fullmatch(directory.name):
                continue
            try:
                if directory.is_symlink() or (directory / "manifest.json").is_symlink():
                    raise ValueError("Snapshot links are not supported")
                manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
                if not isinstance(manifest, dict):
                    raise ValueError("Manifest must be an object")
                records.append({key: manifest.get(key) for key in (
                    "snapshot_id", "region_id", "created_at", "built_at", "schema_version")})
            except (OSError, ValueError) as exc:
                records.append({"snapshot_id": directory.name, "error": str(exc)})
        return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("capture", "verify", "list"))
    parser.add_argument("value", nargs="?")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--snapshot-root", type=Path)
    args = parser.parse_args()
    try:
        store = SnapshotStore(args.data, args.snapshot_root)
        if args.action == "list":
            result = store.list()
        elif not args.value:
            raise SnapshotError("capture requires a region; verify requires a snapshot ID")
        else:
            result = store.capture(args.value) if args.action == "capture" else store.verify(args.value)
        print(json.dumps(result, indent=2))
        return 0
    except SnapshotError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
