"""Asynchronous local snapshot operations for the authenticated web panel."""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

from tools.regional_snapshots import SnapshotError, SnapshotStore, now, snapshot_id
from tools.regional_staging import StagingStore
from tools.local_releases import AssemblyError, LocalReleases
from tools.pipeline_webui.common import read_json, write_json_atomic, web_dir


class Snapshots:
    def __init__(self, data: Path, root: Path | None = None, staging_root: Path | None = None,
                 release_root: Path | None = None, frontend: Path | None = None,
                 python: str | None = None):
        self.store = SnapshotStore(data, root)
        self.staging = StagingStore(self.store, staging_root)
        options = {}
        if frontend is not None:
            options["frontend"] = frontend
        if python is not None:
            options["python"] = python
        self.releases = LocalReleases(self.staging, release_root, **options)
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

    def start(self, action: str, value: str | dict) -> dict:
        if action not in {"capture", "verify", "stage", "verify_staged", "release_create",
                          "release_verify", "release_compare", "release_approve", "release_activate",
                          "release_rollback", "release_policy", "release_cleanup", "release_publish"}:
            raise SnapshotError("Unknown snapshot operation")
        if action.startswith("release_") and not isinstance(value, dict):
            raise SnapshotError("Release request must be an object")
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
            if action.startswith("release_"):
                self._release_operation(record, action, value)
                return
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
            if isinstance(exc, AssemblyError):
                record["failed_release_id"] = exc.release_id
        finally:
            record["finished_at"] = now()
            try:
                write_json_atomic(self.operations / f"{record['operation_id']}.json", record)
            finally:
                with self.lock:
                    self.busy = False

    def _release_operation(self, record: dict, action: str, value: dict) -> None:
        store = self.releases
        if action == "release_create":
            result = store.create(value.get("staging_ids"), value.get("generation"))
        elif action == "release_rollback":
            result = store.rollback(value.get("generation"))
        elif action == "release_policy":
            result = store.set_policy(value.get("policy"))
        elif action == "release_cleanup":
            if type(value.get("apply", False)) is not bool:
                raise SnapshotError("apply must be a boolean")
            result = store.cleanup(value.get("apply", False))
        else:
            identifier = snapshot_id(value.get("release_id"))
            if action == "release_verify":
                manifest = store.verify(identifier)
                result = {"release_id": identifier, "verified": True,
                          "artifacts": len(manifest["site_inventory"])}
            elif action == "release_compare":
                result = store.compare(identifier)
            elif action in {"release_approve", "release_publish"}:
                reason = value.get("reason", "")
                if not isinstance(reason, str):
                    raise SnapshotError("Review reason must be text")
                method = store.publish if action == "release_publish" else store.approve
                result = method(identifier, value.get("generation"), reason)
            else:
                result = store.activate(identifier, value.get("generation"))
        if action == "release_create":
            result = {"release_id": result["release_id"], "region_builds": result["region_builds"],
                      "warnings": result["warnings"]}
        record.update(status="succeeded", result=result)
