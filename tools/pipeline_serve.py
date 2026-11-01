"""Serve the repository locally with byte-range support required by PMTiles.

The server binds to loopback only. It uses the standard-library static file
handler for path normalization and adds single-range responses for tile files.
"""

from __future__ import annotations

import argparse
import http.server
import ipaddress
import json
import re
import sys
import urllib.parse
from pathlib import Path


LOCAL_CATALOG_PATH = "/data/local_map_layers.json"


class RangeHandler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # The local preview computes the cache catalogue on request; it is never a
    # file in data/, so no publish or copy allowlist can pick it up.
    local_catalog = True

    def end_headers(self) -> None:
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def _refused(self) -> bool:
        """Dotfiles and dot-directories (.env holds the LINZ key, .git the
        history) are never served, and only a localhost Host header is
        answered, so a page elsewhere cannot rebind its name onto this port."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        parts = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path).split("/")
        if host not in ("127.0.0.1", "localhost", "::1") or any(p.startswith(".") for p in parts if p):
            self.send_error(404)
            return True
        return False

    def do_HEAD(self) -> None:
        if not self._refused():
            super().do_HEAD()

    def _send_local_catalog(self) -> None:
        try:
            loopback = ipaddress.ip_address(self.client_address[0]).is_loopback
        except (IndexError, ValueError):
            loopback = False
        if not loopback:
            self.send_error(404)
            return
        try:
            from src.map_layer_catalog import build_local_catalog
            catalogue = build_local_catalog(Path(self.directory) / "data")
            status = 200
        except Exception as exc:  # noqa: BLE001 - report the failure without paths
            catalogue = {"version": 1, "layers": [],
                         "warnings": [f"local catalogue failed ({type(exc).__name__})"],
                         "availability": {"source": "local", "local_cache": False}}
            status = 500
        body = json.dumps(catalogue).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self._refused():
            return
        if self.local_catalog and urllib.parse.urlsplit(self.path).path == LOCAL_CATALOG_PATH:
            self._send_local_catalog()
            return
        range_header = self.headers.get("Range")
        if not range_header:
            return super().do_GET()
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if not match:
            self.send_error(416, "Only a single byte range is supported")
            return
        path = Path(self.translate_path(self.path))
        if not path.is_file():
            return super().do_GET()
        size = path.stat().st_size
        first, last = match.groups()
        if not first and not last:
            self.send_error(416, "Empty byte range")
            return
        if first:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
        else:
            length = int(last)
            start = max(0, size - length)
            end = size - 1
        if start >= size or end < start:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        length = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        with path.open("rb") as stream:
            stream.seek(start)
            remaining = length
            while remaining:
                chunk = stream.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True,
                        help="directory to serve; the quickstart uses the repository root")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")
    # Imports also work when invoked directly as tools/pipeline_serve.py.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.render_docs import render_docs
    render_docs(root)
    handler = lambda *a, **kw: RangeHandler(*a, directory=str(root), **kw)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    server.daemon_threads = True
    print(f"Serving {root} at http://127.0.0.1:{args.port}/ (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
