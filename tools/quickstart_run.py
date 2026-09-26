"""Run the user-facing quickstart with durable, numbered diagnostics.

Each step's number and label are shared by terminal output, step logs,
run.log, run.json, and report.md. The Markdown report is rewritten atomically
as the run progresses, so failures and interruptions leave a useful record.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import importlib
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from urllib.parse import quote, quote_plus
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RUNS = DATA / "quickstart_runs"

STEPS = [
    {
        "number": 1,
        "label": "Preflight: validate area and runtime",
        "inputs": "my_area.json; selected Python environment; LINZ_API_KEY presence",
        "outputs": "Run metadata; confirmed area name and WGS84 bbox",
    },
    {
        "number": 2,
        "label": "Fetch outlines, elevation, imagery, and point cloud",
        "inputs": "Area bbox; configured LINZ survey layers; LINZ API; point-cloud tile index/store",
        "outputs": "data/regions/<area>/building_outlines.geojson; dsm_mosaic.tif; optional imagery_mosaic.tif; data/dem_wide_mosaic.tif; data/pointcloud/ tiles",
    },
    {
        "number": 3,
        "label": "Vision precompute (optional)",
        "inputs": "Fetched outlines, imagery and point cloud; SAM, torch, segment-anything, roof_lines_v5.pt and roof_lines_v6.pt",
        "outputs": "data/selected_faces/<building_id>.json (some roofs may deliberately defer)",
    },
    {
        "number": 4,
        "label": "Build roof geometry and layout",
        "inputs": "Outlines, DSM, optional imagery/LiDAR, selected faces when available",
        "outputs": "data/regions/<area>/panel_layouts.geojson",
    },
    {
        "number": 5,
        "label": "Gate panel layouts",
        "inputs": "data/regions/<area>/panel_layouts.geojson; local LiDAR/DSM evidence",
        "outputs": "Updated data/regions/<area>/panel_layouts.geojson",
    },
    {
        "number": 6,
        "label": "Rerank panel layouts",
        "inputs": "data/regions/<area>/panel_layouts.geojson",
        "outputs": "Updated data/regions/<area>/panel_layouts.geojson",
    },
    {
        "number": 7,
        "label": "Derive building solar potential",
        "inputs": "Panel layouts and building outlines",
        "outputs": "data/regions/<area>/solar_potential.geojson",
    },
    {
        "number": 8,
        "label": "Render building verification report",
        "inputs": "Solar potential, panel layouts, optional aerial imagery",
        "outputs": "data/regions/<area>/quickstart_report.html",
    },
    {
        "number": 9,
        "label": "Patch roof confidence",
        "inputs": "Solar potential and gated panel layouts",
        "outputs": "Updated data/regions/<area>/solar_potential.geojson",
    },
    {
        "number": 10,
        "label": "Bake per-building horizons",
        "inputs": "Outlines, DSM, wide DEM and solar potential",
        "outputs": "solar_potential.geojson with building horizon fields",
    },
    {
        "number": 11,
        "label": "Register imagery alignment (optional)",
        "inputs": "Area imagery and LINZ Basemaps reference-photo tiles",
        "outputs": "data/regions/<area>/image_shift.json when measurable",
    },
    {
        "number": 12,
        "label": "Build per-pixel solar heatmap",
        "inputs": "Outlines, DSM, point cloud (when available), wide DEM and image shifts",
        "outputs": "data/regions/<area>/heatmap_raster.png and heatmap_raster.json",
    },
    {
        "number": 13,
        "label": "Add building addresses (optional)",
        "inputs": "Building polygons and LINZ NZ Addresses WFS",
        "outputs": "Address properties in solar_potential.geojson",
    },
    {
        "number": 14,
        "label": "Emit regional map tiles",
        "inputs": "Region solar/layout GeoJSON, heatmap, outlines, build keys",
        "outputs": "Run-isolated buildings/layout PMTiles, cells, detail, heatmap tiles and summary",
    },
    {
        "number": 15,
        "label": "Combine isolated map dataset",
        "inputs": "This run's regional tile artifacts only",
        "outputs": "data/quickstart_runs/<area>/<run-id>/map-data/data/ map contract",
    },
    {
        "number": 16,
        "label": "Build local 3D terrain tiles",
        "inputs": "Area DSM and wide DEM",
        "outputs": "Run-isolated map-data/data/terrain/ tiles (optional 3D view)",
    },
    {
        "number": 17,
        "label": "Validate map contract and prepare preview",
        "inputs": "Emitted PMTiles, supporting JSON/PNG assets and preview page assets",
        "outputs": "Contract-checked local map dataset and map-preview/preview.html",
    },
    {
        "number": 18,
        "label": "Start local map preview",
        "inputs": "Validated preview bundle and PMTiles byte-range server",
        "outputs": "Loopback preview URL and server PID/log",
    },
]


def _safe_area(area: str) -> str:
    """Return a path-safe area slug, or raise before using it in a path."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", area):
        raise ValueError("area name must be 1-64 letters, digits, '_' or '-', and start with a letter or digit")
    return area


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _md(value: Any) -> str:
    return (str(value).replace("|", "\\|").replace("\n", "<br>")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _linz_key_value() -> str:
    key = os.environ.get("LINZ_API_KEY", "").strip()
    if key:
        return key
    env_file = ROOT / ".env"
    if not env_file.exists():
        return ""
    try:
        lines = env_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        match = re.match(r"^\s*(?:export\s+)?LINZ_API_KEY\s*=\s*(.*?)\s*$", line)
        if match:
            key = match.group(1).strip()
            key = key.split(" #", 1)[0].rstrip()
            if len(key) >= 2 and key[0] == key[-1] and key[0] in "'\"":
                key = key[1:-1]
            if key and not key.startswith("#"):
                return key
    return ""


class QuickstartRun:
    def __init__(self, area: str, run_dir: Path):
        self.area = area
        self.run_dir = run_dir
        self.log_path = run_dir / "run.log"
        self.report_path = run_dir / "report.md"
        self.state_path = run_dir / "run.json"
        self.started = _now()
        self.started_clock = time.monotonic()
        self.records = [dict(s, status="PENDING", comment="Not run yet", command="", exit_code=None,
                             started=None, finished=None, seconds=None, log=None)
                        for s in STEPS]
        self.step_clocks: dict[int, float] = {}
        self.metadata: dict[str, Any] = {}
        self.secrets = {_linz_key_value()} - {""}
        self.overall = "RUNNING"
        run_dir.mkdir(parents=True, exist_ok=False)
        self.log_path.touch()
        self._save()

    def _write_log(self, text: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(text + "\n")

    def _save(self) -> None:
        failed = [s for s in self.records if s["status"] == "FAIL"]
        degraded = [s for s in self.records if s["status"] in {"DEGRADED", "SKIPPED", "INTERRUPTED"}]
        if failed:
            self.overall = "FAIL"
        elif degraded:
            self.overall = "DEGRADED"
        elif all(s["status"] == "PASS" for s in self.records):
            self.overall = "PASS"
        state = {
            "area": self.area,
            "run_id": self.run_dir.name,
            "started_utc": self.started,
            "updated_utc": _now(),
            "status": self.overall,
            "python": sys.executable,
            "metadata": self.metadata,
            "steps": self.records,
            "artifacts": {
                "report": str(self.report_path.relative_to(ROOT)),
                "log": str(self.log_path.relative_to(ROOT)),
            },
        }
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_path)
        self._write_report()

    def _write_report(self) -> None:
        rows = []
        for s in self.records:
            log = f"[step log]({s['log']})" if s.get("log") else "—"
            timing = (f"exit {s['exit_code']}, {s['seconds']}s"
                      if s.get("exit_code") is not None else "—")
            rows.append("| {number:02d} | {label} | {inputs} | {outputs} | **{status}** ({timing}) | {comment} | {log} |".format(
                number=s["number"], label=_md(s["label"]), inputs=_md(s["inputs"]),
                outputs=_md(s["outputs"]), status=_md(s["status"]),
                timing=_md(timing), comment=_md(s.get("comment", "")), log=log))
        failed = [s for s in self.records if s["status"] == "FAIL"]
        degraded = [s for s in self.records if s["status"] in {"DEGRADED", "SKIPPED", "INTERRUPTED"}]
        if failed:
            self.overall = "FAIL"
        elif degraded:
            self.overall = "DEGRADED"
        elif all(s["status"] == "PASS" for s in self.records):
            self.overall = "PASS"
        content = [
            f"# Quickstart run: {self.area}",
            "",
            f"- **Run ID:** `{self.run_dir.name}`",
            f"- **Started (UTC):** {self.started}",
            f"- **Status:** **{self.overall}**",
            f"- **Python:** `{sys.executable}`",
            f"- **Git commit:** `{self.metadata.get('git_commit', 'unknown')}`",
            f"- **WGS84 bbox:** `{self.metadata.get('bbox', 'not validated')}`",
            f"- **Survey overrides:** `{json.dumps(self.metadata.get('survey_overrides', {}), sort_keys=True)}`",
                        f"- **Survey selected:** `{self.metadata.get('survey_name', 'not resolved')}`",
                        *([f"- **Map preview:** [{self.metadata['map_preview_url']}]({self.metadata['map_preview_url']})"]
                            if self.metadata.get("map_preview_url") else []),
            f"- **Combined log:** [`run.log`](run.log)",
            f"- **Machine-readable record:** [`run.json`](run.json)",
            "",
            "Step numbers and labels below match the `[QS-NN]` prefixes in the terminal and logs.",
            "",
            "| Step | Description | Source data / inputs | Target data / outputs | Result | Comment / consequence | Log |",
            "|---:|---|---|---|---|---|---|",
            *rows,
            "",
            "## Debugging",
            "",
            "Open the matching `step-NN-*.log` for full stdout/stderr. Find the same `[QS-NN]` marker in `run.log` to see surrounding run context. A failed step stops dependent steps; later rows remain `NOT RUN` and explain why.",
            "",
            "## Map preview",
            "",
            (f"Map-ready local preview: [{self.metadata['map_preview_url']}]({self.metadata['map_preview_url']}). "
             "The preview uses this run's isolated dataset; it does not replace or publish the normal map data. "
             "Live parameter refitting still requires the separate `src/live_server.py` API and is not enabled by this static preview."
             if self.metadata.get("map_preview_url") else
             "The map-ready tile/preview steps have not completed. Earlier PASS statuses cover only the listed pipeline stages, not map readiness."),
            "",
        ]
        self.report_path.write_text("\n".join(content), encoding="utf-8")

    def event(self, number: int, text: str) -> None:
        line = f"[QS-{number:02d}] {text}"
        print(line, flush=True)
        self._write_log(line)

    def redact(self, text: str) -> str:
        for secret in self.secrets:
            for encoded in {secret, quote(secret, safe=""), quote_plus(secret)}:
                text = text.replace(encoded, "[REDACTED]")
        return text

    def begin(self, number: int, command: list[str] | None = None) -> dict[str, Any]:
        record = self.records[number - 1]
        record["status"] = "RUNNING"
        record["started"] = _now()
        self.step_clocks[number] = time.monotonic()
        record["command"] = self.redact(shlex.join(command)) if command else "internal validation"
        record["commands"] = [record["command"]]
        record["log"] = f"step-{number:02d}-{re.sub(r'[^a-z0-9]+', '-', record['label'].lower()).strip('-')}.log"
        self._save()
        self.event(number, f"START {record['label']}")
        self.event(number, f"COMMAND {record['command']}")
        with (self.run_dir / record["log"]).open("w", encoding="utf-8") as f:
            f.write(f"[QS-{number:02d}] {record['label']}\n[QS-{number:02d}] COMMAND {record['command']}\n")
        return record

    def finish(self, number: int, status: str, comment: str, exit_code: int | None) -> None:
        record = self.records[number - 1]
        clock = self.step_clocks.pop(number, time.monotonic())
        record.update(status=status, comment=comment, exit_code=exit_code,
                 finished=_now(), seconds=round(time.monotonic() - clock, 1))
        self.event(number, f"{status} {record['label']} (exit={exit_code}, {record['seconds']}s): {comment}")
        self._save()

    def run_command(self, number: int, command: list[str], *, env: dict[str, str] | None = None,
                    begin: bool = True) -> tuple[int, str]:
        if begin:
            record = self.begin(number, command)
        else:
            record = self.records[number - 1]
            safe_command = self.redact(shlex.join(command))
            record.setdefault("commands", []).append(safe_command)
            record["command"] = "; then ".join(record["commands"])
            self._save()
            command_line = f"[QS-{number:02d}] COMMAND {safe_command}"
            self.event(number, command_line.removeprefix(f"[QS-{number:02d}] "))
            with (self.run_dir / record["log"]).open("a", encoding="utf-8") as f:
                f.write(command_line + "\n")
        chunks: list[str] = []
        step_log = self.run_dir / record["log"]
        with step_log.open("a", encoding="utf-8") as f:
            try:
                proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1)
                assert proc.stdout is not None
                for raw in proc.stdout:
                    message = self.redact(raw.rstrip("\r\n"))
                    chunks.append(message)
                    tagged = f"[QS-{number:02d}] {message}"
                    print(tagged, flush=True)
                    self._write_log(tagged)
                    f.write(tagged + "\n")
                code = proc.wait()
            except KeyboardInterrupt:
                if "proc" in locals() and proc.poll() is None:
                    proc.send_signal(signal.SIGINT)
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                self.finish(number, "INTERRUPTED", "Interrupted by user; partial outputs may exist and require inspection before resuming.", None)
                for pending in self.records[number:]:
                    if pending["status"] == "PENDING":
                        pending["status"] = "NOT RUN"
                        pending["comment"] = f"Not run because step {number:02d} was interrupted."
                self._save()
                raise
            except OSError as exc:
                message = self.redact(f"could not start command: {type(exc).__name__}: {exc}")
                chunks.append(message)
                tagged = f"[QS-{number:02d}] {message}"
                print(tagged, flush=True)
                self._write_log(tagged)
                f.write(tagged + "\n")
                code = 127
        return code, "\n".join(chunks)


def _key_available() -> bool:
    return bool(_linz_key_value())


def _preflight(area: str, py: Path) -> tuple[bool, str, dict[str, Any]]:
    config_path = ROOT / "my_area.json"
    if not config_path.is_file():
        return False, "my_area.json is missing; copy my_area.example.json and set name/bbox.", {}
    try:
        area_config = json.loads(config_path.read_text(encoding="utf-8"))
        name = str(area_config["name"]).strip()
        bbox = [float(v) for v in area_config["bbox"]]
        if name != area:
            raise ValueError(f"requested area {area!r} does not match my_area.json name {name!r}")
        if len(bbox) != 4:
            raise ValueError("bbox must contain [west, south, east, north]")
        west, south, east, north = bbox
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError("bbox is invalid or outside WGS84 coordinate limits")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return False, f"Invalid my_area.json: {exc}", {}
    if not py.is_file() or not os.access(py, os.X_OK):
        return False, f"Selected Python executable is unavailable: {py}", {"name": name, "bbox": bbox}
    if not _key_available():
        return False, "LINZ_API_KEY is not set in the environment or .env (value was not recorded).", {"name": name, "bbox": bbox}
    dependency_probe = subprocess.run(
        [str(py), "-c", "import matplotlib.colors"], cwd=ROOT,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if dependency_probe.returncode:
        return False, (
            f"Matplotlib is required for the map heatmap stage but cannot be imported by "
            f"the selected Python ({py}). Install project dependencies into this exact "
            f"environment with '{py} -m pip install -r requirements.txt', then verify "
            f"with '{py} -c \\\"import matplotlib.colors; print(matplotlib.__version__)\\\"'. "
            f"See docs/data-maintainers/troubleshooting.md#matplotlib-is-required-for-the-map-heatmap-stage."
        ), {"name": name, "bbox": bbox}
    missing_tools = [tool for tool in ("tippecanoe", "tile-join", "tippecanoe-decode")
                     if shutil.which(tool) is None]
    if missing_tools:
        return False, "Map-ready output requires Tippecanoe's commands on PATH: " + ", ".join(missing_tools) + ". Install the Tippecanoe package (macOS: brew bundle --file=Brewfile; Ubuntu/WSL: follow docs/data-maintainers/env-setup-ubuntu.md) and reopen the terminal before retrying.", {"name": name, "bbox": bbox}
    try:
        sys.path.insert(0, str(ROOT))
        with contextlib.redirect_stdout(io.StringIO()):
            config = importlib.import_module("config")
            from src.surveys import survey_for
            survey = survey_for(bbox, name)
        if not survey.get("dsm_layer"):
            raise ValueError("the selected survey has no DSM layer configured")
        if survey.get("pointcloud_bulk_url") and not survey.get("lidar_tile_index_layer"):
            raise ValueError("a point-cloud bulk store is configured without a tile-index layer")
        survey_meta = {k: survey.get(k) for k in (
            "name", "dsm_layer", "dem_layer", "imagery_layer",
            "reference_imagery_layer", "lidar_tile_index_layer",
            "pointcloud_bulk_url", "pointcloud_tile_year")}
    except Exception as exc:
        return False, f"No usable source survey for this bbox: {type(exc).__name__}: {exc}. Configure the survey coverage/layer IDs in my_area.json or config.SURVEYS before fetching.", {"name": name, "bbox": bbox}
    free_gb = shutil.disk_usage(DATA).free / 1e9 if DATA.exists() else shutil.disk_usage(ROOT).free / 1e9
    details = f"Area {name}; WGS84 bbox {bbox}; Python {sys.version.split()[0]}; free disk {free_gb:.1f} GB; LINZ key present (not recorded)."
    if free_gb < 10:
        details += f" WARNING: only {free_gb:.1f} GB free; documentation recommends about 10 GB."
    survey_keys = ("dsm_layer", "dem_layer", "imagery_layer", "lidar_tile_index_layer",
                   "pointcloud_bulk_url", "pointcloud_tile_year")
    overrides = {key: area_config[key] for key in survey_keys if key in area_config}
    return True, details + f" Selected survey: {survey.get('name')}.", {
        "name": name, "bbox": bbox, "free_disk_gb": round(free_gb, 1),
        "survey_overrides": overrides, "survey_name": survey.get("name"),
        "survey_layers": survey_meta,
    }


def _pointcloud_summary(area_dir: Path) -> tuple[int, int, bool]:
    listing = area_dir / "pointcloud_tiles.txt"
    if not listing.exists():
        return 0, 0, False
    try:
        names = [n.strip() for n in listing.read_text(encoding="utf-8", errors="replace").splitlines() if n.strip()]
    except OSError:
        return 0, 0, False
    pc_dir = DATA / "pointcloud"
    found = sum(1 for name in names if (pc_dir / name).exists() or (pc_dir / name.replace(".laz", ".copc.laz")).exists())
    return len(names), found, True


def _geojson_feature_count(path: Path) -> tuple[int | None, str | None]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"not readable JSON ({type(exc).__name__}: {exc})"
    if not isinstance(doc, dict) or doc.get("type") != "FeatureCollection" or not isinstance(doc.get("features"), list):
        return None, "not a GeoJSON FeatureCollection with a features array"
    return len(doc["features"]), None


def _evaluate_fetch(area_dir: Path, output: str) -> tuple[str, str]:
    required = [area_dir / "building_outlines.geojson", area_dir / "dsm_mosaic.tif", DATA / "dem_wide_mosaic.tif"]
    missing = [str(p.relative_to(ROOT)) for p in required if not p.is_file() or p.stat().st_size == 0]
    if missing:
        return "FAIL", "Required source output missing/empty: " + ", ".join(missing) + ". Geometry cannot be built reliably."
    outlines_count, outline_error = _geojson_feature_count(required[0])
    if outline_error:
        return "FAIL", f"Building outlines failed validation: {outline_error}."
    if outlines_count == 0:
        return "FAIL", "Fetch returned zero building outlines; there is no building set to estimate. Check the bbox and LINZ coverage."
    notes = []
    if not (area_dir / "imagery_mosaic.tif").is_file():
        notes.append("aerial imagery unavailable; vision precompute and image-based obstruction evidence will be unavailable")
    total, found, listed = _pointcloud_summary(area_dir)
    if not listed:
        notes.append("no point-cloud tile manifest; LiDAR coverage is unknown and the build may fall back to DSM-only")
    elif total == 0:
        notes.append("survey lists no point-cloud tiles; build will be DSM-only")
    elif found < total:
        notes.append(f"point-cloud coverage incomplete: {found}/{total} listed tiles found; missing tiles reduce LiDAR evidence")
    lower = output.lower()
    if "point cloud fetch failed" in lower or "tiles missing from the bulk store" in lower:
        if not notes:
            notes.append("fetcher reported a point-cloud warning; inspect step log")
    status = "DEGRADED" if notes else "PASS"
    comment = "; ".join(notes) + "." if notes else f"{outlines_count} building outlines validated; required rasters exist; point cloud {found}/{total} tiles present."
    return status, comment


def _prepare_preview_bundle(area: str, run: QuickstartRun, map_package: Path) -> Path:
    preview_dir = run.run_dir / "map-preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    for name in ("preview.html", "economics.js", "panel_editor.js"):
        shutil.copy2(ROOT / name, preview_dir / name)
    bbox = run.metadata["bbox"]
    center = [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]
    base_path = "/" + map_package.relative_to(ROOT).as_posix().rstrip("/") + "/"
    site = {
        "dataVersion": run.run_dir.name,
        "dataBase": base_path,
        "defaultView": {"center": center, "zoom": 15.5},
        "towns": [],
        "name": f"Quickstart: {area}",
    }
    (preview_dir / "site-config.js").write_text(
        "window.SITE = " + json.dumps(site, separators=(",", ":")) + ";\n",
        encoding="utf-8")
    return preview_dir / "preview.html"


def _free_port(preferred: int) -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        try:
            probe.bind(("127.0.0.1", preferred))
            return probe.getsockname()[1]
        except OSError:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]
    finally:
        probe.close()


def _start_preview_server(area: str, run: QuickstartRun, py: Path,
                          map_package: Path, preferred_port: int,
                          open_browser: bool) -> tuple[bool, str]:
    port = _free_port(preferred_port)
    server_log = run.run_dir / "map-preview-server.log"
    pid_file = run.run_dir / "map-preview-server.pid"
    command = [str(py), "tools/quickstart_serve.py", "--root", str(ROOT), "--port", str(port)]
    record = run.begin(18, command)
    preview_path = run.run_dir / "map-preview" / "preview.html"
    route = "/" + preview_path.relative_to(ROOT).as_posix()
    bbox = run.metadata["bbox"]
    lat, lng = (bbox[1] + bbox[3]) / 2, (bbox[0] + bbox[2]) / 2
    url = f"http://127.0.0.1:{port}{route}?lat={lat:.7f}&lng={lng:.7f}&z=15.5"
    tile_path = "/" + (map_package / "data" / "buildings.pmtiles").relative_to(ROOT).as_posix()
    try:
        popen_options: dict[str, Any] = {}
        if os.name == "nt":
            popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_options["start_new_session"] = True
        with server_log.open("ab") as log_file:
            process = subprocess.Popen(command, cwd=ROOT, stdout=log_file,
                                       stderr=subprocess.STDOUT, **popen_options)
        pid_file.write_text(f"{process.pid}\n", encoding="ascii")
        deadline = time.monotonic() + 10
        last_error = "server did not become ready"
        while time.monotonic() < deadline:
            if process.poll() is not None:
                last_error = f"server exited with code {process.returncode}"
                break
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    if response.status != 200:
                        raise RuntimeError(f"preview page returned HTTP {response.status}")
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}{tile_path}", headers={"Range": "bytes=0-31"})
                with urllib.request.urlopen(request, timeout=2) as response:
                    content_range = response.headers.get("Content-Range", "")
                    if response.status != 206 or not content_range.startswith("bytes 0-31/"):
                        raise RuntimeError(f"PMTiles byte-range probe returned HTTP {response.status}, {content_range!r}")
                    if len(response.read()) != 32:
                        raise RuntimeError("PMTiles byte-range probe returned the wrong payload length")
                run.metadata.update({"map_preview_url": url,
                                     "map_preview_server_pid": process.pid,
                                     "map_preview_server_log": str(server_log.relative_to(ROOT))})
                run._save()
                if open_browser:
                    with contextlib.suppress(Exception):
                        webbrowser.open(url, new=2)
                run.finish(18, "PASS", f"Local preview serves HTTP 200 and PMTiles byte ranges (206). Open {url}. Stop the local server with PID {process.pid} when finished; details: {server_log.relative_to(ROOT)}.", 0)
                return True, url
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(0.2)
        process.terminate()
        process.wait(timeout=5)
        run.finish(18, "FAIL", f"Could not verify local preview: {last_error}. See {server_log.relative_to(ROOT)}.", 1)
        return False, ""
    except Exception as exc:
        run.finish(18, "FAIL", f"Could not start local map server: {type(exc).__name__}: {exc}.", 1)
        return False, ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Run quickstart with persistent numbered logs and Markdown report.")
    parser.add_argument("area", help="must match my_area.json name")
    parser.add_argument("--python", dest="python", default=sys.executable,
                        help="Python executable used for pipeline subprocesses (defaults to this interpreter)")
    parser.add_argument("--port", type=int, default=8765,
                        help="preferred local preview port (uses another free port if occupied)")
    parser.add_argument("--no-open-browser", action="store_true",
                        help="start the local preview server but do not open a browser tab")
    args = parser.parse_args()
    try:
        area = _safe_area(args.area)
    except ValueError as exc:
        parser.error(str(exc))
    py = Path(args.python).expanduser().resolve()
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = RUNS / area
    base.mkdir(parents=True, exist_ok=True)
    run_dir = base / run_id
    # Timestamp collisions are rare but should never overwrite evidence.
    suffix = 1
    while run_dir.exists():
        run_dir = base / f"{run_id}-{suffix:02d}"
        suffix += 1
    run = QuickstartRun(area, run_dir)
    area_dir = DATA / "regions" / area
    run.metadata = {"run_dir": str(run_dir.relative_to(ROOT))}
    region_out_root = run_dir / "region-out"
    region_out = region_out_root / area
    map_package = run_dir / "map-data"
    map_data = map_package / "data"
    map_preview = run_dir / "map-preview"
    map_data.mkdir(parents=True, exist_ok=True)

    run.begin(1)
    ok, details, area_meta = _preflight(area, py)
    run.metadata.update(area_meta)
    try:
        run.metadata["git_commit"] = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, check=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        run.metadata["git_commit"] = "unknown"
    run.finish(1, "PASS" if ok else "FAIL", details, 0 if ok else 2)
    if not ok:
        for s in run.records[1:]:
            s["status"] = "NOT RUN"
            s["comment"] = "Not run because preflight failed."
        run._save()
        print(f"\nQuickstart stopped. Report: {run.report_path}\nLogs: {run.run_dir}", flush=True)
        return 2

    env = os.environ.copy()
    env["SOLAR_SELECTED_FACES"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    commands = [
        [str(py), "src/fetch_regions.py", area],
        None,  # optional vision step, decided from the fetched inputs
        [str(py), "src/run_stage.py", "build_layout_geojson", area],
        [str(py), "src/run_stage.py", "gate_panels", area],
        [str(py), "src/run_stage.py", "rerank_layouts", area],
        [str(py), "src/run_stage.py", "derive_solar_potential", area],
        [str(py), "tools/quickstart_report.py", area],
        [str(py), "src/run_stage.py", "patch_roof_confidence", area],
        [str(py), "src/run_stage.py", "bake_building_horizons", area],
        [str(py), "src/run_stage.py", "register_imagery", area],
        [str(py), "src/run_stage.py", "build_heatmap_raster", area],
        [str(py), "src/run_stage.py", "add_addresses", area],
        [str(py), "src/emit_region.py", area, "--out", str(region_out_root)],
        [str(py), "src/combine_regions.py", "--regions", area,
         "--out-root", str(region_out_root), "--dest", str(map_data),
         "--skip-markup-lines"],
        [str(py), "tools/build_terrain_tiles.py", area, "--min-zoom", "11",
         "--max-zoom", "17", "--out", str(map_data / "terrain")],
    ]
    failed_at: int | None = None
    for number, command in enumerate(commands, start=2):
        record = run.records[number - 1]
        if number == 3:
            (DATA / "selected_faces").mkdir(parents=True, exist_ok=True)
            vision_files = [DATA / "sam_vit_b.pth", DATA / "models/roof_lines_v5.pt", DATA / "models/roof_lines_v6.pt"]
            line_models_ready = all(p.is_file() for p in vision_files[1:])
            has_imagery = (area_dir / "imagery_mosaic.tif").is_file()
            modules_probe = subprocess.run([str(py), "-c", "import torch, segment_anything"], cwd=ROOT,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) \
                if line_models_ready and has_imagery else None
            if not vision_files[0].is_file() and modules_probe is not None and modules_probe.returncode == 0:
                curl = shutil.which("curl")
                if curl:
                    partial = DATA / "sam_vit_b.pth.part"
                    code, _ = run.run_command(number, [curl, "--fail", "--location", "--retry", "3",
                                                        "--retry-all-errors", "--output", str(partial),
                                                        "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth"], env=env)
                    if code == 0 and partial.is_file() and partial.stat().st_size >= 300_000_000:
                        os.replace(partial, vision_files[0])
                        run.event(number, "Validated and installed the SAM checkpoint (size threshold passed).")
                    else:
                        partial.unlink(missing_ok=True)
                        run.finish(number, "DEGRADED", "SAM checkpoint download failed or was truncated; vision precompute skipped and geometry will use available readings/fallbacks.", code)
                        continue
            absent = [str(p.relative_to(ROOT)) for p in vision_files if not p.is_file()]
            modules_ok = modules_probe is not None and modules_probe.returncode == 0
            if absent or not modules_ok or not has_imagery:
                run.begin(number)
                why = []
                if absent:
                    why.append("missing checkpoints: " + ", ".join(absent))
                if not modules_ok:
                    why.append("torch/segment-anything unavailable or imagery missing")
                run.finish(number, "SKIPPED", "; ".join(why) + ". Geometry stage will use available selected readings and its normal fallback paths.", None)
                continue
            vision_command = [str(py), "tools/predict_faces.py", "--region", area]
            try:
                code, output = run.run_command(number, vision_command, env=env,
                                               begin=not bool(record.get("started")))
            except KeyboardInterrupt:
                raise
            if code == 0:
                selected = DATA / "selected_faces"
                count = sum(1 for p in selected.glob("*.json")) if selected.exists() else 0
                run.finish(number, "PASS", f"Vision precompute completed; {count} selected-face files currently exist (including any from earlier runs).", code)
            else:
                run.finish(number, "DEGRADED", f"Vision precompute failed (exit {code}); build will use any existing selected readings and fallback geometry. See step log.", code)
            continue

        assert command is not None
        try:
            code, output = run.run_command(number, command, env=env)
        except KeyboardInterrupt:
            raise
        if code != 0:
            if number in {11, 13, 16}:
                note = {
                    11: "Imagery alignment did not complete; the map can still display geometry at its LiDAR coordinates.",
                    13: "Address enrichment did not complete; buildings remain clickable by building ID.",
                    16: "3D terrain tiles are unavailable; the 2D map remains complete.",
                }[number]
                run.finish(number, "DEGRADED", f"Optional command exited {code}. {note} Inspect the step log.", code)
                continue
            run.finish(number, "FAIL", f"Command exited {code}; dependent outputs were not run. See step log.", code)
            failed_at = number
            break
        if number == 2:
            status, comment = _evaluate_fetch(area_dir, output)
            if status == "FAIL":
                run.finish(number, status, comment, code)
                failed_at = number
                break
            run.finish(number, status, comment, code)
        else:
            expected = {
                4: area_dir / "panel_layouts.geojson",
                5: area_dir / "panel_layouts.geojson",
                6: area_dir / "panel_layouts.geojson",
                7: area_dir / "solar_potential.geojson",
                8: area_dir / "quickstart_report.html",
                9: area_dir / "solar_potential.geojson",
                10: area_dir / "solar_potential.geojson",
                11: area_dir / "image_shift.json",
                12: area_dir / "heatmap_raster.png",
                13: area_dir / "solar_potential.geojson",
                14: region_out / "summary.json",
                15: map_data / "buildings.pmtiles",
                16: map_data / "terrain" / "meta.json",
            }[number]
            if number == 11 and not expected.is_file():
                run.finish(number, "SKIPPED", "No image_shift.json was produced (for example, the survey has no reference imagery); map geometry will render at its source coordinates.", 0)
                continue
            if number in {11, 16} and (not expected.is_file() or expected.stat().st_size == 0):
                run.finish(number, "DEGRADED", f"Optional output missing/empty: {expected.relative_to(ROOT)}. The 2D solar map remains available.", 0)
                continue
            if not expected.is_file() or expected.stat().st_size == 0:
                run.finish(number, "FAIL", f"Command returned success but expected output is missing/empty: {expected.relative_to(ROOT)}. See step log.", 0)
                failed_at = number
                break
            if number in {4, 5, 6, 7, 9, 10, 13}:
                feature_count, validation_error = _geojson_feature_count(expected)
                if validation_error:
                    run.finish(number, "FAIL", f"Expected GeoJSON is invalid: {validation_error} in {expected.relative_to(ROOT)}.", 0)
                    failed_at = number
                    break
                if feature_count == 0:
                    if number == 7:
                        run.finish(number, "FAIL", f"Solar potential output has zero building features: {expected.relative_to(ROOT)}.", 0)
                        failed_at = number
                        break
                    run.finish(number, "DEGRADED", f"Output is valid GeoJSON but contains zero layout features: {expected.relative_to(ROOT)}. No roof geometry is available to inspect.", 0)
                    continue
                feature_note = f"; {feature_count} GeoJSON features"
            else:
                feature_note = ""
            if number == 8:
                rendered = re.search(r"\((\d+) buildings rendered\)", output)
                n_rendered = int(rendered.group(1)) if rendered else None
                comment = f"Wrote {expected.relative_to(ROOT)}; {n_rendered} buildings rendered." if n_rendered is not None else f"Wrote {expected.relative_to(ROOT)}; inspect the HTML report for coverage."
                if n_rendered == 0:
                    run.finish(number, "DEGRADED", comment + " No image cards rendered; verify imagery/facet availability.", 0)
                else:
                    run.finish(number, "PASS", comment, 0)
            elif number == 10:
                doc = json.loads(expected.read_text(encoding="utf-8"))
                n_horizon = sum("horizon_b64" in f.get("properties", {})
                                for f in doc["features"])
                run.finish(number, "PASS" if n_horizon else "DEGRADED",
                           f"Horizon profiles available for {n_horizon}/{feature_count} buildings; map detail can display horizon data where present.", 0)
            elif number == 11:
                shifts = json.loads(expected.read_text(encoding="utf-8"))
                run.finish(number, "PASS" if shifts else "SKIPPED",
                           f"Image alignment shifts recorded for {len(shifts)} buildings." if shifts else "No image shifts were measurable; rendering remains in LiDAR coordinates.", 0)
            elif number == 12:
                sidecar = area_dir / "heatmap_raster.json"
                if not sidecar.is_file() or sidecar.stat().st_size == 0:
                    run.finish(number, "FAIL", "Heatmap sidecar is missing/empty; the emitted heatmap cannot be georeferenced.", 0)
                    failed_at = number
                    break
                rendered = re.search(r"(\d+)/(\d+) buildings rendered", output)
                n_rendered = int(rendered.group(1)) if rendered else None
                total_buildings = int(rendered.group(2)) if rendered else None
                comment = (f"Rendered LiDAR solar heat for {n_rendered}/{total_buildings} buildings; "
                           f"wrote {expected.stat().st_size / 1e6:.1f} MB plus georeferencing sidecar.") \
                    if rendered else f"Wrote {expected.stat().st_size / 1e6:.1f} MB plus georeferencing sidecar; rendered count was not reported."
                run.finish(number, "DEGRADED" if n_rendered == 0 else "PASS", comment +
                           (" No roofs had usable point-cloud coverage; the map's roof heat layer will be empty." if n_rendered == 0 else ""), 0)
            elif number == 14:
                for rel in ("buildings.pmtiles", "panel_layouts.pmtiles", "cells.json", "summary.json"):
                    artifact = region_out / rel
                    if not artifact.is_file() or artifact.stat().st_size == 0:
                        run.finish(number, "FAIL", f"Regional emission omitted required map artifact: {artifact.relative_to(ROOT)}.", 0)
                        failed_at = number
                        break
                if failed_at is not None:
                    break
                run.finish(number, "PASS", f"Emitted this run's tiles and support data under {region_out.relative_to(ROOT)}.", 0)
            elif number == 15:
                required = ("panel_layouts.pmtiles", "building_cells.pmtiles", "assumptions.json",
                            "addresses.json", "building_detail/index.json", "heatmap_tiles/meta.json",
                            "seasonal_curves/index.json", "build_summary.json")
                missing = [rel for rel in required
                           if not (map_data / rel).is_file() or (map_data / rel).stat().st_size == 0]
                if missing:
                    run.finish(number, "FAIL", "Isolated combine omitted required map files: " + ", ".join(missing), 0)
                    failed_at = number
                    break
                (map_data / "markup_lines.geojson").write_text(
                    json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")
                run.finish(number, "PASS", f"Combined one region into isolated map data at {map_data.relative_to(ROOT)}; repository-wide map data was not modified.", 0)
            elif number == 16:
                terrain_tiles = list((map_data / "terrain").rglob("*.png"))
                run.finish(number, "PASS" if terrain_tiles else "DEGRADED",
                           f"Wrote {len(terrain_tiles)} 3D terrain tiles." if terrain_tiles else "Terrain metadata exists but no DSM tiles were emitted; 3D view is unavailable.", 0)
            else:
                run.finish(number, "PASS", f"Command succeeded; verified non-empty {expected.relative_to(ROOT)}{feature_note}.", 0)

    if failed_at is None:
        validator = [str(py), "tools/validate_quickstart_map.py",
                     "--region-out", str(region_out), "--map-data", str(map_data)]
        run.begin(17, ["prepare isolated preview bundle", "then", *validator])
        try:
            _prepare_preview_bundle(area, run, map_package)
        except Exception as exc:
            run.finish(17, "FAIL", f"Could not prepare local preview assets: {type(exc).__name__}: {exc}.", 1)
            failed_at = 17
        else:
            try:
                code, output = run.run_command(17, validator, env=env, begin=False)
            except KeyboardInterrupt:
                raise
            if code != 0:
                run.finish(17, "FAIL", "Map output contract validation failed. Review the step log before opening this dataset.", code)
                failed_at = 17
            else:
                run.finish(17, "PASS", output.strip() or "Isolated preview bundle and map contract validated.", 0)

    if failed_at is None:
        ok, url = _start_preview_server(area, run, py, map_package,
                                        args.port, not args.no_open_browser)
        if not ok:
            failed_at = 18

    if failed_at is not None:
        for s in run.records[failed_at:]:
            if s["status"] == "PENDING":
                s["status"] = "NOT RUN"
                s["comment"] = f"Not run because step {failed_at:02d} failed; dependent outputs are unavailable."
        run._save()
    print(f"\nQuickstart report: {run.report_path}\nCombined log: {run.log_path}\nRun artifacts: {run.run_dir}", flush=True)
    if run.metadata.get("map_preview_url"):
        print(f"Map preview: {run.metadata['map_preview_url']}", flush=True)
    return 1 if failed_at is not None else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Quickstart interrupted; inspect the latest data/quickstart_runs/<area>/<run-id>/report.md", file=sys.stderr)
        raise SystemExit(130)
