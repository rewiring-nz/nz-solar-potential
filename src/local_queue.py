"""Loopback task coordination with persistent JSON state and no cloud dependencies."""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

TERMINAL = frozenset({"succeeded", "failed", "interrupted"})


class QueueError(RuntimeError):
    pass


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_task(task: Any) -> dict[str, Any]:
    if not isinstance(task, dict) or set(task) != {
        "id", "region", "workspace", "python", "options", "provenance",
    }:
        raise QueueError("Task must contain id, region, workspace, python, options and provenance.")
    for key in ("id", "region"):
        if not isinstance(task[key], str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", task[key]):
            raise QueueError(f"Invalid task {key}.")
    for key in ("workspace", "python"):
        if not isinstance(task[key], str) or not Path(task[key]).is_absolute():
            raise QueueError(f"Task {key} must be an absolute path.")
    task = dict(task, workspace=str(Path(task["workspace"]).resolve()))
    options = task["options"]
    if not isinstance(options, dict) or set(options) != {"port", "no_open_browser", "step_offset"}:
        raise QueueError("Invalid pipeline options.")
    port = options["port"]
    if port is not None and (type(port) is not int or not 1024 <= port <= 65535):
        raise QueueError("Preview port must be between 1024 and 65535.")
    if type(options["no_open_browser"]) is not bool:
        raise QueueError("no_open_browser must be boolean.")
    if type(options["step_offset"]) is not int or options["step_offset"] < 0:
        raise QueueError("step_offset must be a non-negative integer.")
    provenance = task["provenance"]
    if not isinstance(provenance, dict) or set(provenance) != {"commit", "files"}:
        raise QueueError("Invalid task provenance.")
    if not isinstance(provenance["commit"], str) or not isinstance(provenance["files"], dict):
        raise QueueError("Invalid task provenance values.")
    # Copy through JSON so callers cannot mutate the persisted task afterward.
    return json.loads(json.dumps(task))


class QueueState:
    """One coordinator owns updates; no automatic lease takeover or retry."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.persistence_error: str | None = None
        if path.exists():
            self.document = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(self.document, dict) or self.document.get("version") != 1 or not isinstance(self.document.get("tasks"), list):
                raise QueueError(f"Unsupported queue state: {path}")
            identifiers: set[str] = set()
            for record in self.document["tasks"]:
                if not isinstance(record, dict) or not {"task", "status", "attempt", "worker"} <= record.keys():
                    raise QueueError(f"Invalid task record in {path}")
                record["task"] = validate_task(record["task"])
                if not isinstance(record["status"], str) or record["status"] not in {"pending", "running", *TERMINAL}:
                    raise QueueError(f"Invalid task status in {path}")
                if record["task"]["id"] in identifiers:
                    raise QueueError(f"Duplicate task id in {path}")
                identifiers.add(record["task"]["id"])
                if record["status"] == "running":
                    record.update(status="interrupted", error="Coordinator stopped during execution; inspect the old worker before resubmitting.")
        else:
            self.document = {"version": 1, "tasks": []}
        self._save()

    def _save(self) -> None:
        try:
            atomic_json(self.path, self.document)
        except OSError as exc:
            self.persistence_error = str(exc)
            raise

    def dispatch(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if self.persistence_error:
                raise QueueError(f"Queue storage failed; stop and inspect before restarting: {self.persistence_error}")
            if operation == "status":
                return json.loads(json.dumps(self.document))
            if operation == "submit":
                task = validate_task(payload.get("task"))
                if any(r["task"]["id"] == task["id"] for r in self.document["tasks"]):
                    raise QueueError(f"Duplicate task id: {task['id']}; use a new id for a deliberate rerun.")
                self.document["tasks"].append({"task": task, "status": "pending", "attempt": None, "worker": None})
                self._save()
                return {"id": task["id"]}
            if operation == "claim":
                worker = payload.get("worker")
                if not isinstance(worker, str) or not worker or len(worker) > 128:
                    raise QueueError("A worker id is required.")
                active = {r["task"]["workspace"] for r in self.document["tasks"] if r["status"] == "running"}
                for record in self.document["tasks"]:
                    if record["status"] == "pending" and record["task"]["workspace"] not in active:
                        record.update(status="running", worker=worker, attempt=uuid.uuid4().hex)
                        self._save()
                        return json.loads(json.dumps(record))
                return {"task": None, "finished": all(r["status"] in TERMINAL for r in self.document["tasks"])}
            if operation not in {"heartbeat", "complete", "fail"}:
                raise QueueError(f"Unknown queue operation: {operation}")
            record = next((r for r in self.document["tasks"] if r["task"]["id"] == payload.get("id")), None)
            if record is None:
                raise QueueError("Unknown task.")
            if record["status"] != "running" or record["attempt"] != payload.get("attempt") or record["worker"] != payload.get("worker"):
                raise QueueError("Task acknowledgement rejected: worker/attempt no longer owns this running task.")
            if operation == "heartbeat":
                record["heartbeat"] = time.time()
            elif operation == "complete":
                record.update(status="succeeded", exit_code=0)
            else:
                error = payload.get("error")
                if not isinstance(error, str) or not error:
                    raise QueueError("A failed task requires an error.")
                record.update(status="failed", error=error[:4000])
            self._save()
            return {"status": record["status"]}

    def interrupt(self, reason: str) -> None:
        with self.lock:
            for record in self.document["tasks"]:
                if record["status"] not in TERMINAL:
                    record.update(status="interrupted", error=reason)
            self._save()


class Coordinator:
    def __init__(self, directory: Path, port: int = 0):
        if type(port) is not int or not 0 <= port <= 65535:
            raise QueueError("Coordinator port must be between 0 and 65535 (0 selects a free port).")
        self.directory = directory.resolve()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.ownership = self.directory / "coordinator.lock"
        try:
            self.ownership.mkdir()
        except FileExistsError as exc:
            raise QueueError(
                f"Queue already owned: {self.ownership}. If its coordinator crashed, stop its workers "
                "and inspect owner.json before manually removing this lock directory."
            ) from exc
        self.thread: threading.Thread | None = None
        self.server: ThreadingHTTPServer | None = None
        try:
            atomic_json(self.ownership / "owner.json", {"pid": os.getpid()})
            self.state = QueueState(self.directory / "state.json")
            self.token = secrets.token_urlsafe(32)
            coordinator = self

            class Handler(BaseHTTPRequestHandler):
                def setup(self) -> None:
                    super().setup()
                    self.connection.settimeout(5)

                def log_message(self, format: str, *args: Any) -> None:
                    pass

                def do_POST(self) -> None:
                    authorization = self.headers.get("Authorization", "")
                    host = self.headers.get("Host", "").split(":")[0]
                    if self.headers.get("Origin") or host not in {"localhost", "127.0.0.1"} or not hmac.compare_digest(authorization, f"Bearer {coordinator.token}"):
                        self._reply(403, {"error": "Local queue authorization required."})
                        return
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        if not 0 < length <= 65536:
                            raise QueueError("Request body must be between 1 and 65536 bytes.")
                        payload = json.loads(self.rfile.read(length))
                        if not isinstance(payload, dict):
                            raise QueueError("Request body must be an object.")
                        result = coordinator.state.dispatch(self.path.removeprefix("/"), payload)
                    except (ValueError, QueueError) as exc:
                        self._reply(409, {"error": str(exc)})
                    except OSError as exc:
                        self._reply(500, {"error": f"Queue persistence failed: {exc}"})
                    else:
                        self._reply(200, result)

                def _reply(self, status: int, value: Any) -> None:
                    content = json.dumps(value).encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)

            self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            self.server.timeout = 1
            self.server.daemon_threads = True
            descriptor = self.directory / ".connection.json"
            descriptor.touch(mode=0o600, exist_ok=True)
            atomic_json(descriptor, {"url": f"http://127.0.0.1:{self.server.server_port}", "token": self.token})
            descriptor.chmod(0o600)
        except BaseException:
            if self.server is not None:
                self.server.server_close()
            self._release()
            raise

    def _release(self) -> None:
        (self.directory / ".connection.json").unlink(missing_ok=True)
        (self.ownership / "owner.json").unlink(missing_ok=True)
        self.ownership.rmdir()

    def __enter__(self) -> Coordinator:
        assert self.server is not None
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        assert self.server is not None
        if self.thread is not None:
            self.server.shutdown()
            self.thread.join()
        self.server.server_close()
        try:
            self.state.interrupt("Coordinator stopped; pending/running work was not completed.")
        finally:
            self._release()


class QueueClient:
    def __init__(self, directory: Path):
        descriptor = json.loads((directory / ".connection.json").read_text(encoding="utf-8"))
        from urllib.parse import urlsplit
        url = urlsplit(descriptor["url"])
        if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port or url.path:
            raise QueueError("Queue endpoint must be loopback HTTP.")
        self.url = descriptor["url"]
        self.token = descriptor["token"]
        self.opener = build_opener(ProxyHandler({}))

    def call(self, operation: str, **payload: Any) -> dict[str, Any]:
        request = Request(self.url + "/" + operation, data=json.dumps(payload).encode("utf-8"),
                          headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=10) as response:
                return json.load(response)
        except HTTPError as exc:
            with contextlib.closing(exc):
                message = json.load(exc).get("error", str(exc))
            raise QueueError(message) from exc
        except (URLError, TimeoutError) as exc:
            raise QueueError(f"Local coordinator unavailable: {exc}") from exc
