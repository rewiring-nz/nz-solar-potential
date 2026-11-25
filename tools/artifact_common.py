"""Shared helpers for local staging and release artifacts."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ID = re.compile(r"[a-f0-9]{32}")


class ArtifactError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def artifact_id(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ArtifactError("Invalid artifact ID")
    return value


@contextmanager
def artifact_lock(data: Path):
    """Serialize managed artifact writers and cleanup across local processes."""
    lock = Path(data) / ".artifact-lifecycle-lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ArtifactError("Artifact lifecycle is busy; after a crash inspect .artifact-lifecycle-lock", 409) from exc
    try:
        (lock / "owner.json").write_text(json.dumps({"pid": os.getpid(), "started_at": now()}))
        yield
    finally:
        (lock / "owner.json").unlink(missing_ok=True)
        lock.rmdir()


def inventory(directory: Path) -> list[dict[str, Any]]:
    """Reject links and special files, including links in directory ancestors."""
    for ancestor in (directory, *directory.parents):
        if ancestor.is_symlink():
            raise ArtifactError(f"Symlinks are not supported: {ancestor.name}")
    if not directory.is_dir():
        raise ArtifactError("Emitted artifact directory is missing", 404)
    entries = []
    for path in sorted(directory.rglob("*")):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ArtifactError(f"Symlinks are not supported: {path.name}")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ArtifactError(f"Not a regular artifact: {path.name}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        after = path.stat()
        if (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ArtifactError("Artifacts changed while being read; wait for the build to finish", 409)
        entries.append({"path": path.relative_to(directory).as_posix(),
                        "bytes": after.st_size, "sha256": digest.hexdigest()})
    return entries
