"""Asynchronous local staging and release operations for the authenticated web panel."""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path

from tools.artifact_common import ArtifactError, artifact_id, now
from tools.regional_staging import StagingStore
from tools.local_releases import AssemblyError, LocalReleases
from tools.cloud_staging import CloudStaging
from tools.pipeline_webui.common import read_json, write_json_atomic, web_dir


class Artifacts:
    def __init__(self, data: Path, staging_root: Path | None = None,
                 release_root: Path | None = None, frontend: Path | None = None,
                 python: str | None = None, cloud_staging: CloudStaging | None = None):
        self.staging = StagingStore(data, staging_root)
        self.cloud_staging = cloud_staging
        options = {}
        if frontend is not None:
            options["frontend"] = frontend
        if python is not None:
            options["python"] = python
        self.releases = LocalReleases(self.staging, release_root, **options)
        self.operations = web_dir(data) / "artifact_operations"
        self.lock = threading.Lock()
        self.busy = False
        self.current: dict | None = None
        for path in self.operations.glob("*.json"):
            record = read_json(path)
            if isinstance(record, dict) and record.get("status") == "running":
                try:
                    os.kill(int(record["pid"]), 0)
                except (KeyError, TypeError, ValueError, ProcessLookupError):
                    record.update(status="interrupted",
                                  finished_at=now(),
                                  error="Panel stopped during operation; check the artifact lifecycle lock before retrying.")
                    write_json_atomic(path, record)
                except PermissionError:
                    pass  # Another process owns the operation.

    def staging_regions(self) -> set[str]:
        """Regions being staged now, including one whose copy has not yet locked it."""
        with self.lock:
            current = self.current
        pending = {current["value"]} if current and current["action"] == "stage" else set()
        return pending | self.staging.staging_regions()

    def get(self, identifier: str) -> dict:
        record = read_json(self.operations / f"{artifact_id(identifier)}.json")
        if not isinstance(record, dict):
            raise ArtifactError("Unknown operation", 404)
        return record

    def cloud_operations(self) -> list[dict]:
        records = []
        for path in self.operations.glob("*.json"):
            record = read_json(path)
            if isinstance(record, dict) and record.get("action") == "cloud_submit":
                records.append(record)
        records.sort(key=lambda record: str(record.get("started_at") or ""), reverse=True)
        return records[:50]

    def start(self, action: str, value: str | dict) -> dict:
        if action not in {"stage", "verify_staged", "release_create",
                          "release_verify", "release_compare", "release_approve", "release_activate",
                          "release_rollback", "release_policy", "release_cleanup", "release_publish",
                          "cloud_submit"}:
            raise ArtifactError("Unknown operation")
        if (action.startswith("release_") or action == "cloud_submit") and not isinstance(value, dict):
            raise ArtifactError("Operation request must be an object")
        if action == "cloud_submit" and (value.get("cloud_cost_acknowledged") is not True
                                          or self.cloud_staging is None
                                          or not isinstance(value.get("quote_id"), str)
                                          or not isinstance(value.get("cost_nzd"), str)):
            raise ArtifactError("Cloud submission needs an explicit cost approval and configured storage", 400)
        with self.lock:
            if self.busy:
                raise ArtifactError("Another staging or release operation is running", 409)
            identifier = uuid.uuid4().hex
            record = {"operation_id": identifier, "action": action, "value": value,
                      "status": "running", "started_at": now(), "pid": os.getpid()}
            write_json_atomic(self.operations / f"{identifier}.json", record)
            self.busy = True
            self.current = record
            threading.Thread(target=self._run, args=(record,), daemon=True).start()
            return dict(record)

    def _run(self, record: dict) -> None:
        try:
            action, value = record["action"], record["value"]
            if action.startswith("release_"):
                self._release_operation(record, action, value)
                return
            if action == "cloud_submit":
                def progress(phase: str, **state) -> None:
                    record.update(status="running", phase=phase, progress_at=now(), **state)
                    write_json_atomic(self.operations / f"{record['operation_id']}.json", record)

                result = self.cloud_staging.submit(value["staging_ids"], progress,
                                                   value["quote_id"], value["cost_nzd"])
                record.update(status="succeeded", phase="complete", result=result)
                return
            manifest = self.staging.stage(value) if action == "stage" else self.staging.verify(value)
            record.update(status="succeeded", staging_id=manifest["staging_id"],
                          region_id=manifest.get("region_id"),
                          artifact_count=len(manifest["artifacts"]),
                          bytes=sum(item["bytes"] for item in manifest["artifacts"]))
        except (ArtifactError, OSError, ValueError) as exc:
            record.update(status="failed", error=str(exc))
            if isinstance(exc, AssemblyError):
                record["failed_release_id"] = exc.release_id
        except Exception as exc:
            record.update(status="failed", error=f"Unexpected operation error ({type(exc).__name__})")
        finally:
            record["finished_at"] = now()
            try:
                write_json_atomic(self.operations / f"{record['operation_id']}.json", record)
            finally:
                with self.lock:
                    self.busy = False
                    self.current = None
                # Staged and live state feed the tree and map colours.
                from tools.pipeline_webui import region_status
                region_status.invalidate()

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
                raise ArtifactError("apply must be a boolean")
            result = store.cleanup(value.get("apply", False))
        else:
            identifier = artifact_id(value.get("release_id"))
            if action == "release_verify":
                manifest = store.verify(identifier)
                result = {"release_id": identifier, "verified": True,
                          "artifacts": len(manifest["site_inventory"])}
            elif action == "release_compare":
                result = store.compare(identifier)
            elif action in {"release_approve", "release_publish"}:
                reason = value.get("reason", "")
                if not isinstance(reason, str):
                    raise ArtifactError("Review reason must be text")
                method = store.publish if action == "release_publish" else store.approve
                result = method(identifier, value.get("generation"), reason)
            else:
                result = store.activate(identifier, value.get("generation"))
        if action == "release_create":
            result = {"release_id": result["release_id"], "region_builds": result["region_builds"],
                      "warnings": result["warnings"]}
        record.update(status="succeeded", result=result)
