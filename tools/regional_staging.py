"""Copy verified regional snapshots to independent local staging; never deploy."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.regional_snapshots import SnapshotError, SnapshotStore, artifact_lock, inventory, now, snapshot_id


def manifest_hash(directory: Path) -> str:
    return hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest()


class StagingStore:
    def __init__(self, snapshots: SnapshotStore, root: Path | None = None):
        self.snapshots = snapshots
        configured = Path(root) if root else snapshots.data / "regional_staging"
        if configured.is_symlink():
            raise SnapshotError("Staging storage cannot use symlinks")
        self.root = configured.resolve()
        for other in (snapshots.root.resolve(), (snapshots.data / "out").resolve()):
            if self.root == other or self.root.is_relative_to(other) or other.is_relative_to(self.root):
                raise SnapshotError("Staging storage must be separate from snapshots and emitted outputs")

    def stage(self, identifier: str) -> dict:
        with artifact_lock(self.snapshots.data):
            return self._stage(identifier)

    def _stage(self, identifier: str) -> dict:
        identifier = snapshot_id(identifier)
        try:
            original = self.snapshots.verify(identifier)
            source = self.snapshots.root / identifier
            before = inventory(source)
            source_hash = manifest_hash(source)
            self.root.mkdir(parents=True, exist_ok=True)
            for path in (self.root, *self.root.parents):
                if path.is_symlink():
                    raise SnapshotError("Staging storage cannot use symlinks")
            destination = self.root / identifier
            if destination.exists() or destination.is_symlink():
                existing = self.verify(identifier)
                if existing["source_manifest_sha256"] != source_hash:
                    raise SnapshotError("Snapshot metadata changed since staging; capture a new snapshot", 409)
                return existing
            partial = self.root / f".incomplete-{identifier}"
            try:
                partial.mkdir()
            except FileExistsError as exc:
                raise SnapshotError(
                    "This snapshot has an incomplete or running staging copy; inspect it before retrying", 409
                ) from exc
            # Preserve links rather than following them if an external writer races
            # the copy; verification below rejects them.
            shutil.copytree(source, partial / "snapshot", symlinks=True)
            if inventory(partial / "snapshot") != before or inventory(source) != before:
                raise SnapshotError("Snapshot changed during staging; copy not completed", 409)
            manifest = {
                "schema_version": 1, "staging_id": identifier, "source_snapshot_id": identifier,
                "source_manifest_sha256": source_hash, "region_id": original["region_id"],
                "staged_at": now(), "built_at": original.get("built_at"),
                "scope": "emitted_region_only",
            }
            (partial / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            self._verify(partial, identifier)
            partial.rename(destination)
            return manifest
        except (OSError, ValueError) as exc:
            raise SnapshotError(f"Staging failed; no copy completed: {exc}", 409) from exc

    def _verify(self, directory: Path, identifier: str) -> dict:
        if directory.is_symlink() or (directory / "manifest.json").is_symlink():
            raise SnapshotError("Staging links are not supported", 409)
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise SnapshotError("Unsupported staging schema", 409)
        if manifest.get("staging_id") != identifier or manifest.get("source_snapshot_id") != identifier:
            raise SnapshotError("Staging identity does not match its directory", 409)
        snapshot = self.snapshots.verify_directory(directory / "snapshot", identifier)
        if (manifest_hash(directory / "snapshot") != manifest.get("source_manifest_sha256")
                or snapshot.get("region_id") != manifest.get("region_id")):
            raise SnapshotError("Staged source manifest differs from its recorded identity", 409)
        if sorted(p.name for p in directory.iterdir()) != ["manifest.json", "snapshot"]:
            raise SnapshotError("Unexpected staging files", 409)
        return manifest

    def verify(self, identifier: str) -> dict:
        identifier = snapshot_id(identifier)
        try:
            return self._verify(self.root / identifier, identifier)
        except (OSError, ValueError) as exc:
            raise SnapshotError(f"Staging verification failed: {exc}", 409) from exc

    def list(self) -> list[dict]:
        if not self.root.exists():
            return []
        records = []
        for directory in sorted(self.root.iterdir()):
            if directory.name.startswith("."):
                continue
            try:
                snapshot_id(directory.name)
                if directory.is_symlink() or (directory / "manifest.json").is_symlink():
                    raise SnapshotError("Staging links are not supported")
                manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
                if not isinstance(manifest, dict):
                    raise SnapshotError("Staging manifest must be an object")
                records.append({key: manifest.get(key) for key in (
                    "staging_id", "source_snapshot_id", "region_id", "staged_at", "built_at")})
            except (SnapshotError, OSError, ValueError) as exc:
                records.append({"staging_id": directory.name, "error": str(exc)})
        return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stage", "verify", "list"))
    parser.add_argument("value", nargs="?")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--snapshot-root", type=Path)
    parser.add_argument("--staging-root", type=Path)
    args = parser.parse_args()
    try:
        store = StagingStore(SnapshotStore(args.data, args.snapshot_root), args.staging_root)
        if args.action == "list":
            result = store.list()
        elif not args.value:
            raise SnapshotError("stage and verify require a snapshot/staging ID")
        else:
            result = store.stage(args.value) if args.action == "stage" else store.verify(args.value)
        print(json.dumps(result, indent=2))
        return 0
    except SnapshotError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
