"""Asynchronous local snapshot operations for the authenticated web panel."""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

from tools.regional_snapshots import SnapshotError, SnapshotStore, now, snapshot_id
from tools.regional_staging import StagingStore
from tools.pipeline_webui.common import read_json, write_json_atomic, web_dir


class Snapshots:
    def __init__(self, data: Path, root: Path | None = None, staging_root: Path | None = None):
        self.store = SnapshotStore(data, root)
        self.staging = StagingStore(self.store, staging_root)
        self.operations = web_dir(data) / "snapshot_operations"
        self.lock = threading.Lock()
        self.busy = False
        for path in self.operations.glob("*.json"):
            record = read_json(path)
            if isinstance(record, dict) and record.get("status") == "running":
                try:
                    os.kill(int(record["pid"]), 0)
                except (KeyError, ValueError, ProcessLookupError):
                    record.update(status="interrupted",
                                  error="Panel stopped during operation; inspect any workspace lock before retrying.")
                    write_json_atomic(path, record)
                except PermissionError:
                    pass  # Another process owns the operation.

    def get(self, identifier: str) -> dict:
        record = read_json(self.operations / f"{snapshot_id(identifier)}.json")
        if not isinstance(record, dict):
            raise SnapshotError("Unknown snapshot operation", 404)
        return record

    def start(self, action: str, value: str) -> dict:
        if action not in {"capture", "verify", "stage", "verify_staged"}:
            raise SnapshotError("Unknown snapshot operation")
        with self.lock:
            if self.busy:
                raise SnapshotError("Another snapshot operation is running", 409)
            identifier = uuid.uuid4().hex
            record = {"operation_id": identifier, "action": action, "value": value,
                      "status": "running", "started_at": now(), "pid": os.getpid()}
            write_json_atomic(self.operations / f"{identifier}.json", record)
            self.busy = True
            threading.Thread(target=self._run, args=(record,), daemon=True).start()
            return dict(record)

    def _run(self, record: dict) -> None:
        try:
            action, value = record["action"], record["value"]
            if action in {"stage", "verify_staged"}:
                staged = self.staging.stage(value) if action == "stage" else self.staging.verify(value)
                manifest = json.loads((self.staging.root / staged["staging_id"] / "snapshot"
                                       / "manifest.json").read_text(encoding="utf-8"))
                record["staging_id"] = staged["staging_id"]
            else:
                manifest = self.store.capture(value) if action == "capture" else self.store.verify(value)
            record.update(status="succeeded", snapshot_id=manifest["snapshot_id"],
                          artifact_count=len(manifest["artifacts"]),
                          bytes=sum(item["bytes"] for item in manifest["artifacts"]))
        except (SnapshotError, OSError, ValueError) as exc:
            record.update(status="failed", error=str(exc))
        finally:
            record["finished_at"] = now()
            try:
                write_json_atomic(self.operations / f"{record['operation_id']}.json", record)
            finally:
                with self.lock:
                    self.busy = False
