"""Local control server for the pipeline web panel (issue #12).

Serves the repository like tools/pipeline_serve.py (loopback only, byte
ranges for PMTiles, no dotfiles) and adds a JSON API under /api/. Every API
call except /api/health needs the per-launch token printed at start-up, sent
as the X-Pipeline-Token header (EventSource streams send it as ?token=,
because browsers cannot set headers there). POSTs must be JSON from this
server's own origin, so another page in the browser cannot start a build.

Three targets: local (this checkout), developer (shared VM, published to the
preview bucket under devel/<alias>/) and published (shared VM, then the deploy
gate, then the live site). Viewing developer and published regions reads
public bucket JSON; starting a cloud job needs a typed yes plus the estimate's
cloud cost echoed back, so a job can only start against the cost shown.
"""

from __future__ import annotations

import argparse
import hmac
import http.server
import json
import secrets
import signal
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Callable

from src import map_layer_catalog as MLC
from tools.pipeline_serve import RangeHandler
from tools.render_docs import render_docs
from tools.pipeline_webui import cloud as C
from tools.pipeline_webui import estimate as E
from tools.pipeline_webui import geo_worker
from tools.pipeline_webui import locality_proxy as LP
from tools.pipeline_webui import region_catalog as RC
from tools.pipeline_webui import remote_catalog as RM
from tools.pipeline_webui.common import DATA, ROOT, git_short
from tools.pipeline_webui.jobs import JobError, JobManager, TERMINAL

DEFAULT_PORT = 8766
MAX_BODY = 64 * 1024
STREAM_POLL_S = 0.5
STREAM_MAX_S = 6 * 3600


class App:
    """Everything a request handler needs, injectable for tests."""

    def __init__(self, token: str, data: Path = DATA, root: Path = ROOT, python: str = sys.executable,
                 api_key_fn: Callable[[], str] | None = None, http_get: Callable | None = None,
                 estimates: E.EstimateStore | None = None, jobs: JobManager | None = None,
                 cloud: C.Cloud | None = None, remote: RM.RemoteCatalog | None = None):
        self.token = token
        self.data = Path(data)
        self.root = Path(root)
        self.python = python
        if api_key_fn is None:
            from tools.pipeline import _linz_key_value
            api_key_fn = _linz_key_value
        self.api_key_fn = api_key_fn
        self.http_get = http_get
        self.cloud = cloud or C.Cloud()
        self.remote = remote or RM.RemoteCatalog()
        self.estimates = estimates or E.EstimateStore(self.data, E.linz_size_lookup(api_key_fn),
                                                      cloud=self.cloud)
        self.jobs = jobs or JobManager(self.data, self.root, python,
                                       cloud_builder=C.cloud_command,
                                       cloud_env=lambda: C.cloud_env(self.cloud))
        self.git = git_short(self.root)

    def api_key(self) -> str:
        key = self.api_key_fn()
        if not key:
            raise LP.LocalityError("LINZ_API_KEY is not set (environment, .env or Secret Manager)", 503)
        return key


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class WebHandler(RangeHandler):
    app: App  # set on the subclass made by make_server

    quiet = False
    # Local caches are listed only through the token-checked /api/layers here.
    local_catalog = False

    def log_message(self, fmt, *args):
        if self.quiet:
            return
        # Query strings can carry the token (?token= on streams); log paths only.
        if args and isinstance(args[0], str):
            args = (args[0].split("?", 1)[0],) + args[1:]
        super().log_message(fmt, *args)

    # ---- plumbing ------------------------------------------------------
    def _send_json(self, status: int, obj) -> None:
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        if status >= 400:
            # A refused POST's body may be unread; close rather than parse it as a request.
            self.close_connection = True
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self, query: dict) -> bool:
        sent = self.headers.get("X-Pipeline-Token") or (query.get("token") or [""])[0]
        return hmac.compare_digest(sent.encode(), self.app.token.encode())

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host") or ""
        return origin in (f"http://{host}",)

    def _body(self) -> dict:
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise HttpError(415, "send application/json")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise HttpError(400, "bad Content-Length") from None
        if length > MAX_BODY:
            raise HttpError(413, "request body too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            raise HttpError(400, "body is not JSON") from None
        if not isinstance(body, dict):
            raise HttpError(400, "body must be a JSON object")
        return body

    def _api(self, method: str) -> None:
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        parts = [urllib.parse.unquote(p) for p in url.path.split("/")[2:] if p]
        try:
            if parts == ["health"] and method == "GET":
                return self._send_json(200, self._health())
            if not self._authorised(query):
                raise HttpError(403, "missing or wrong token; open the link printed by tools/pipeline_web.py")
            if method == "POST" and not self._same_origin():
                raise HttpError(403, "cross-origin request refused")
            body = self._body() if method == "POST" else {}
            status, payload = self._route(method, parts, query, body)
            if payload is not None:
                self._send_json(status, payload)
        except HttpError as exc:
            self._send_json(exc.status, {"error": str(exc)})
        except (E.EstimateError, LP.LocalityError, JobError) as exc:
            self._send_json(exc.status, {"error": str(exc)})
        except Exception as exc:  # never echo text that could carry a keyed URL
            self._send_json(500, {"error": f"server error ({type(exc).__name__})"})

    # ---- routes --------------------------------------------------------
    def _health(self) -> dict:
        jobs = self.app.jobs
        return {"ok": True, "git": self.app.git, "active_job": jobs.active,
                "active_jobs": jobs.active_jobs(),
                "workspace_lock": jobs.workspace_owner() is not None and jobs.active is None,
                "linz_key": bool(self.app.api_key_fn()), "max_regions": E.MAX_REGIONS}

    def _route(self, method: str, parts: list[str], query: dict, body: dict):
        app = self.app
        head = parts[0] if parts else ""
        if head == "layers" and method == "GET" and len(parts) == 1:
            source = (query.get("source") or ["local"])[0]
            if source == "local":
                return 200, geo_worker.run(["catalog", str(app.data)])
            if source in {"developer", "published"}:
                return 200, MLC.unavailable_catalog(source)
            raise HttpError(400, "source must be local, developer or published")
        if head == "regions" and method == "GET":
            source = (query.get("source") or ["local"])[0]
            if source != "local":
                if len(parts) != 1 or source not in RM.SOURCES:
                    raise HttpError(400, "source must be local, developer or published")
                return 200, app.remote.get(source, refresh=(query.get("refresh") or [""])[0] == "1")
            if len(parts) == 1:
                return 200, RC.catalog(app.data)
            detail = RC.region_detail("/".join(parts[1:]), app.data)
            if detail is None:
                raise HttpError(404, "unknown region")
            return 200, detail
        if head == "localities" and method == "GET":
            if parts[1:] == ["search"]:
                return 200, {"results": LP.search((query.get("q") or [""])[0], app.api_key(),
                                                  app.http_get)}
            if len(parts) == 1:
                bbox = LP.parse_bbox((query.get("bbox") or [""])[0])
                return 200, LP.localities_in_bbox(bbox, app.api_key(), app.data, app.http_get)
        if head == "estimate":
            if method == "POST" and len(parts) == 1:
                estimate_id = app.estimates.start(body.get("regions"), body.get("target", "local"))
                return 202, {"estimate_id": estimate_id, "status": "pending"}
            if method == "GET" and len(parts) == 2:
                return 200, app.estimates.get(parts[1])
        if head == "queue" and method == "GET" and len(parts) == 1:
            return 200, {"lanes": app.jobs.queue()}
        if parts == ["cloud", "status"] and method == "GET":
            # gcloud describe only: free, and never starts the VM.
            status = app.cloud.vm_status(refresh=(query.get("refresh") or [""])[0] == "1")
            cfg = app.cloud.config()
            return 200, {**status, "ref": cfg["ref"],
                         "preview_bucket": cfg["preview_bucket"],
                         "busy_with": app.jobs.active_jobs().get("cloud")}
        if head == "jobs":
            if len(parts) == 1 and method == "GET":
                return 200, {"jobs": app.jobs.list(), "active_job": app.jobs.active,
                             "active_jobs": app.jobs.active_jobs()}
            if len(parts) == 1 and method == "POST":
                return 202, self._start_job(body)
            if len(parts) == 2 and method == "GET":
                return 200, app.jobs.view(parts[1])
            if len(parts) == 3 and parts[2] == "log" and method == "GET":
                try:
                    offset = int((query.get("offset") or ["0"])[0])
                except ValueError:
                    raise HttpError(400, "offset must be an integer") from None
                return 200, app.jobs.log(parts[1], offset)
            if len(parts) == 3 and parts[2] == "stream" and method == "GET":
                self._stream(parts[1])
                return 200, None
            if len(parts) == 3 and parts[2] == "cancel" and method == "POST":
                app.jobs.cancel(parts[1])
                return 202, {"job_id": parts[1], "status": "cancelling"}
        raise HttpError(404, "no such API route")

    def _start_job(self, body: dict) -> dict:
        if str(body.get("confirm", "")).strip().lower() != "yes":
            raise HttpError(400, "type yes to confirm the estimate")
        record = self.app.estimates.usable(str(body.get("estimate_id") or ""))
        target = record.get("target", "local")
        if body.get("target", target) != target:
            raise HttpError(409, "the estimate was made for another target; estimate again")
        if target != "local":
            cloud = record.get("cloud") or {}
            if not cloud.get("cost_nzd") or str(body.get("cost_nzd")) != str(cloud["cost_nzd"]):
                raise HttpError(400, "confirm the cloud cost shown in the estimate")
        paid = body.get("paid_referee", False)
        if not isinstance(paid, bool):
            raise HttpError(400, "paid_referee must be true or false")
        if target == "published":
            # The checkout can change after the estimate; remote_build.sh commits and pushes main.
            blockers = self.app.cloud.publish_blockers()
            if blockers:
                raise HttpError(409, "cannot publish: " + " ".join(blockers))
        job_id = self.app.jobs.start(record, paid_referee=paid)
        RC.invalidate()
        return {"job_id": job_id, "status": "queued"}

    def _stream(self, job_id: str) -> None:
        """Server-sent events: `log` chunks, then `status` when anything changes."""
        jobs = self.app.jobs
        jobs.load(job_id)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            offset = int(self.headers.get("Last-Event-ID") or 0)
        except ValueError:
            offset = 0
        last_status = None
        deadline = time.monotonic() + STREAM_MAX_S
        try:
            while time.monotonic() < deadline:
                chunk = jobs.log(job_id, offset)
                if chunk["text"]:
                    offset = chunk["next_offset"]
                    data = "\n".join("data: " + line for line in chunk["text"].split("\n"))
                    self.wfile.write(f"id: {offset}\nevent: log\n{data}\n\n".encode("utf-8"))
                view = jobs.view(job_id)
                summary = json.dumps({k: view.get(k) for k in
                                      ("status", "progress", "eta_s", "current_index", "runs",
                                       "final_combine", "waiting", "queue_position",
                                       "preview_url", "gate_result", "published_version")},
                                     default=str)
                if summary != last_status:
                    last_status = summary
                    self.wfile.write(f"event: status\ndata: {summary}\n\n".encode("utf-8"))
                self.wfile.flush()
                if chunk["eof"] and view.get("status") in TERMINAL:
                    self.wfile.write(b"event: end\ndata: {}\n\n")
                    self.wfile.flush()
                    RC.invalidate()
                    return
                if not chunk["text"]:
                    time.sleep(STREAM_POLL_S)
        except (BrokenPipeError, ConnectionResetError):
            return

    # ---- verbs ---------------------------------------------------------
    def do_GET(self) -> None:
        if urllib.parse.urlsplit(self.path).path.startswith("/api/"):
            if not self._refused():
                self._api("GET")
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self._refused():
            return
        if urllib.parse.urlsplit(self.path).path.startswith("/api/"):
            self._api("POST")
            return
        self.send_error(405)


def make_server(app: App, port: int = DEFAULT_PORT,
                quiet: bool = False) -> http.server.ThreadingHTTPServer:
    handler_cls = type("BoundWebHandler", (WebHandler,), {"app": app, "quiet": quiet})
    handler = lambda *a, **kw: handler_cls(*a, directory=str(app.root), **kw)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server


def port_owner(port: int) -> tuple[int, str] | None:
    """(pid, command line) of the process listening on a TCP port, if lsof can tell."""
    import subprocess
    try:
        found = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                               capture_output=True, text=True, timeout=10, check=False)
        pid = int(found.stdout.split()[0])
        command = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
    return pid, command.stdout.strip()


def port_in_use_message(port: int, exc: OSError, owner_fn=port_owner) -> str:
    lines = [f"Cannot listen on 127.0.0.1:{port} ({exc.strerror})."]
    owner = owner_fn(port) if port else None
    if owner:
        pid, command = owner
        if "pipeline_web" in command:
            lines += [f"An earlier pipeline panel is still running (process {pid}).",
                      "Stop it with Ctrl+C in its terminal, or from here with:",
                      f"  kill {pid}",
                      "Either way it cancels its running builds before it exits."]
        else:
            lines.append(f"Process {pid} holds the port: {command or 'unknown command'}")
    lines.append(f"Or start this panel on another port: --port {port + 1} (or --port 0 for any free port).")
    return "\n".join(lines)


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"loopback port (default {DEFAULT_PORT}; 0 picks a free one)")
    parser.add_argument("--python", default=sys.executable,
                        help="interpreter for pipeline runs (default: this one)")
    parser.add_argument("--no-open-browser", action="store_true",
                        help="print the link instead of opening it")
    args = parser.parse_args(argv)
    render_docs(ROOT)
    app = App(secrets.token_urlsafe(24), python=args.python)
    try:
        server = make_server(app, args.port)
    except OSError as exc:
        print(port_in_use_message(args.port, exc), file=sys.stderr)
        return 1
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/pipeline.html#token={app.token}"
    print(f"Pipeline web panel: {url}", flush=True)
    print("Anyone with this link can start builds on this computer and on the shared VM.",
          "Ctrl+C stops the panel; running builds are cancelled first and queued ones dropped.",
          flush=True)
    if not args.no_open_browser:
        webbrowser.open(url)
    # A panel started in the background ignores SIGINT; make `kill` and Ctrl+C both
    # take the shutdown path below, which cancels running builds.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, _interrupt)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        for job_id in [j for j in app.jobs.active_jobs().values() if j]:
            print(f"Cancelling job {job_id} ...", flush=True)
            try:
                app.jobs.cancel(job_id)
            except JobError:
                pass
        if any(app.jobs.active_jobs().values()):
            app.jobs.wait(app.jobs.cancel_grace_s + 20)
    return 0
