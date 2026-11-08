"""PROJ-using calculations for the panel, run in a short-lived child process.

The panel server must never load PROJ itself: once pyproj opens proj.db, PROJ
registers a fork handler, and on macOS the child of a fork from the
multithreaded server crashes in it ("Python quit unexpectedly"). Every later
git, gcloud or pipeline launch from the panel would then fail. Running these
calculations here keeps PROJ out of the server process.

Usage (JSON on stdout):
    python tools/pipeline_webui/geo_worker.py km2 '<[w, s, e, n]>'
    python tools/pipeline_webui/geo_worker.py size <locality-id>   # LINZ_API_KEY in env
    python tools/pipeline_webui/geo_worker.py catalog <data-dir>
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT_SECONDS = 300


def _km2(bbox: list[float]) -> dict:
    from src.localities import bbox_km2
    return {"bbox_km2": bbox_km2(bbox)}


def _size(region: str) -> dict:
    from src import localities as L
    key = os.environ.get("LINZ_API_KEY", "")
    props = L.resolve(key, region)
    outlines = L.fetch_outlines(key, props)
    n = len(outlines["features"])
    if not n:
        raise LookupError(f"{props.get('name')} has no building outlines; choose another area")
    bbox = L.outlines_bbox_wgs84(outlines)
    return {"buildings": n, "bbox": bbox, "bbox_km2": round(L.bbox_km2(bbox), 3)}


def _selection(request_path: str, data: str) -> dict:
    from src.grid_selection import materialise
    result = materialise(Path(request_path), Path(data), os.environ.get("LINZ_API_KEY", ""))
    from src.localities import bbox_km2
    footprints = result.get("acquisition_footprints") or [result["bbox"]]
    selection_count = len(result["grid_ids"])
    return {"region": result["selection_id"], "buildings": result["buildings"],
            "bbox": result["bbox"], "bbox_km2": round(bbox_km2(result["bbox"]), 3),
            "acquisition_footprints": footprints,
            "acquisition_km2": round(sum(bbox_km2(bbox) for bbox in footprints), 3),
            "selection_count": selection_count}


def _catalog(data: str) -> dict:
    from src.map_layer_catalog import build_local_catalog
    return build_local_catalog(Path(data))


def run(args: list[str], env: dict | None = None, python: str = sys.executable,
        timeout: int = TIMEOUT_SECONDS) -> dict:
    """Run one calculation in a child process; raise LookupError with its message on failure."""
    try:
        # On macOS, launching a child from this threaded server must use
        # posix_spawn: PROJ's registered fork handler can segfault in SQLite.
        # Direct script invocation removes the need to change the child's cwd.
        command = [python, str(ROOT / "tools" / "pipeline_webui" / "geo_worker.py"), *args]
        proc = subprocess.run(
            command,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, **(env or {})}, check=False,
            # Python-created descriptors are non-inheritable; this lets CPython
            # use posix_spawn on macOS instead of forking a PROJ-loaded process.
            close_fds=sys.platform != "darwin")
    except subprocess.TimeoutExpired:
        raise LookupError("area calculation timed out") from None
    except OSError as exc:
        raise LookupError(f"area calculation could not start ({type(exc).__name__})") from None
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        if proc.returncode < 0:
            detail = f"terminated by signal {-proc.returncode}"
        else:
            detail = f"exited with status {proc.returncode}"
        raise LookupError(f"area calculation worker returned no result ({detail})") from None
    if "error" in out:
        raise LookupError(out["error"])
    return out


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(ROOT))
    try:
        if len(argv) == 2 and argv[0] == "km2":
            out = _km2(json.loads(argv[1]))
        elif len(argv) == 2 and argv[0] == "size":
            out = _size(argv[1])
        elif len(argv) == 3 and argv[0] == "selection":
            out = _selection(argv[1], argv[2])
        elif len(argv) == 2 and argv[0] == "catalog":
            out = _catalog(argv[1])
        else:
            out = {"error": "usage: geo_worker km2 <bbox-json> | size <region> | "
                            "selection <request-json> <data-dir> | catalog <data-dir>"}
    except LookupError as exc:
        out = {"error": str(exc)}
    except Exception as exc:
        out = {"error": f"area lookup failed ({type(exc).__name__})"}
    print(json.dumps(out))
    return 1 if "error" in out else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
