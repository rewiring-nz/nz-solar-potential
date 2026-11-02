"""PROJ-using calculations for the panel, run in a short-lived child process.

The panel server must never load PROJ itself: once pyproj opens proj.db, PROJ
registers a fork handler, and on macOS the child of a fork from the
multithreaded server crashes in it ("Python quit unexpectedly"). Every later
git, gcloud or pipeline launch from the panel would then fail. Running these
calculations here keeps PROJ out of the server process.

Usage (JSON on stdout):
    python -m tools.pipeline_webui.geo_worker km2 '<[w, s, e, n]>'
    python -m tools.pipeline_webui.geo_worker size <locality-id>   # LINZ_API_KEY in env
    python -m tools.pipeline_webui.geo_worker catalog <data-dir>
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT_SECONDS = 300
STAGES = ("starting", "loading layer catalogue", "loading localities", "resolving locality",
          "fetching WFS outlines", "transforming outline bounds", "calculating area", "complete")
STAGE_PREFIX = "area-worker-stage: "


def _stage(name: str) -> None:
    print(STAGE_PREFIX + name, file=sys.stderr, flush=True)


def _last_stage(stderr: str) -> str:
    stages = [line[len(STAGE_PREFIX):] for line in stderr.splitlines()
              if line.startswith(STAGE_PREFIX)]
    return next((name for name in reversed(stages) if name in STAGES), "starting")


def _km2(bbox: list[float]) -> dict:
    _stage("calculating area")
    from src.localities import bbox_km2
    return {"bbox_km2": bbox_km2(bbox)}


def _size(region: str) -> dict:
    _stage("loading localities")
    from src import localities as L
    key = os.environ.get("LINZ_API_KEY", "")
    _stage("resolving locality")
    props = L.resolve(key, region)
    _stage("fetching WFS outlines")
    outlines = L.fetch_outlines(key, props)
    n = len(outlines["features"])
    if not n:
        raise LookupError(f"{props.get('name')} has no building outlines; choose another area")
    _stage("transforming outline bounds")
    bbox = L.outlines_bbox_wgs84(outlines)
    _stage("calculating area")
    return {"buildings": n, "bbox": bbox, "bbox_km2": round(L.bbox_km2(bbox), 3)}


def run(args: list[str], env: dict | None = None, python: str = sys.executable) -> dict:
    """Run one calculation in a child process; raise LookupError with its message on failure."""
    proc = subprocess.run(
        [python, "-m", "tools.pipeline_webui.geo_worker", *args], cwd=ROOT,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
        env={**os.environ, **(env or {})}, check=False)
    stage = _last_stage(proc.stderr)
    if proc.returncode:
        detail = f"exit {proc.returncode}"
        if proc.returncode < 0:
            try:
                detail += f", {signal.Signals(-proc.returncode).name}"
            except ValueError:
                pass
        # Never forward raw stdout/stderr: requests exceptions can contain the keyed URL.
        logging.getLogger(__name__).warning("Local area worker failed (%s); last stage: %s",
                                           detail, stage)
        if proc.returncode < 0:
            raise LookupError(f"area calculation failed ({detail}); last stage: {stage}. "
                              "The local helper crashed; see local estimate troubleshooting.")
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        raise LookupError(f"area calculation failed (exit {proc.returncode})") from None
    if "error" in out:
        raise LookupError(out["error"])
    return out


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(ROOT))
    _stage("starting")
    try:
        if len(argv) == 2 and argv[0] == "km2":
            out = _km2(json.loads(argv[1]))
        elif len(argv) == 2 and argv[0] == "size":
            out = _size(argv[1])
        elif len(argv) == 2 and argv[0] == "catalog":
            _stage("loading layer catalogue")
            from src.map_layer_catalog import build_local_catalog
            out = build_local_catalog(Path(argv[1]))
        else:
            out = {"error": "usage: geo_worker km2 <bbox-json> | size <region> | catalog <data-dir>"}
    except LookupError as exc:
        out = {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - other failures can carry a keyed URL: type only
        out = {"error": f"area lookup failed ({type(exc).__name__})"}
    if "error" not in out:
        _stage("complete")
    print(json.dumps(out))
    return 1 if "error" in out else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
