"""Run the configured regional solar pipeline with durable diagnostics.

Each step's number and label are shared by terminal output,
run.log, and report.md. The Markdown report is rewritten atomically
as the run progresses, so failures and interruptions leave a useful record.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import io
import json
import os
import platform
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
from typing import TYPE_CHECKING, Any, Callable

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RUNS = DATA / "pipeline_runs"
CLOUD_RUNS = DATA / "cloud_quickstart_runs"
CLOUD_PIPELINE_STEP_OFFSET = 6

REPOSITORY = "https://github.com/rewiring-nz/nz-solar-potential.git"
FIREWALL_MARKER = "Managed by nz-solar-potential cloud quickstart"
if TYPE_CHECKING:
    from cloud_profile import CloudProfile

BOOTSTRAP = r"""set -Eeuo pipefail
sudo DEBIAN_FRONTEND=noninteractive apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  build-essential git libsqlite3-dev nano python3 python3-pip python3-venv zlib1g-dev
if ! command -v tippecanoe >/dev/null || ! command -v tile-join >/dev/null || ! command -v tippecanoe-decode >/dev/null; then
  work="$(mktemp -d)"
  git clone --depth 1 https://github.com/felt/tippecanoe.git "$work/tippecanoe"
  make -C "$work/tippecanoe" -j2
  sudo make -C "$work/tippecanoe" install
  rm -rf "$work"
fi
if [ ! -d "$HOME/nz-solar-potential/.git" ]; then
  git clone --depth 1 REPOSITORY_PLACEHOLDER "$HOME/nz-solar-potential"
else
  git -C "$HOME/nz-solar-potential" pull --ff-only
fi
cd "$HOME/nz-solar-potential"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
for tool in tippecanoe tile-join tippecanoe-decode; do command -v "$tool"; done
"""

STEPS = [
    {
        "number": 1,
        "label": "Preflight: validate area and runtime",
        "inputs": "config.py region; selected Python environment; LINZ_API_KEY presence",
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
        "outputs": "data/out/<area>/ tiles, cells, detail, heatmap tiles and summary",
    },
    {
        "number": 15,
        "label": "Combine map dataset",
        "inputs": "Regional tile artifacts",
        "outputs": "data/ map contract (PMTiles, cells, detail, heatmap, summary)",
    },
    {
        "number": 16,
        "label": "Build local 3D terrain tiles",
        "inputs": "Area DSM and wide DEM",
        "outputs": "data/terrain/ tiles (optional 3D view)",
    },
    {
        "number": 17,
        "label": "Validate map contract",
        "inputs": "Emitted PMTiles and supporting JSON/PNG assets in data/",
        "outputs": "Contract-checked local map dataset",
    },
    {
        "number": 18,
        "label": "Start local map preview",
        "inputs": "Validated map dataset and PMTiles byte-range server",
        "outputs": "Loopback preview URL and server PID/log",
    },
]


def _safe_area(area: str) -> str:
    """Return a path-safe area slug, or raise before using it in a path."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", area):
        raise ValueError("area name must be 1-64 letters, digits, '_' or '-', and start with a letter or digit")
    return area


def _python_executable(path: str) -> Path:
    return Path(path).expanduser().absolute()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _md(value: Any) -> str:
    return (str(value).replace("|", "\\|").replace("\n", "<br>")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _linz_key_value() -> str:
    """The key from the environment, else .env, else Secret Manager."""
    return _linz_key_local() or _linz_key_secret_manager()


def _linz_key_secret_manager() -> str:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from src.project_secrets import linz_api_key
        return linz_api_key() or ""
    except Exception:
        return ""


def _linz_key_local() -> str:
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


class PipelineRun:
    def __init__(self, area: str, run_dir: Path, *, step_offset: int = 0):
        self.area = area
        self.run_dir = run_dir
        self.step_offset = step_offset
        self.log_path = run_dir / "run.log"
        self.report_path = run_dir / "report.md"
        self.started = _now()
        self.started_clock = time.monotonic()
        self.records = [dict(s, status="PENDING", comment="Not run yet", command="", exit_code=None,
                             started=None, finished=None, seconds=None)
                        for s in STEPS]
        self.step_clocks: dict[int, float] = {}
        self.metadata: dict[str, Any] = {}
        self.secrets = {_linz_key_value()} - {""}
        self.overall = "RUNNING"
        run_dir.mkdir(parents=True, exist_ok=False)
        self.log_path.touch()
        self._write_report()

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
        self._write_report()

    def _write_report(self) -> None:
        rows = []
        for s in self.records:
            timing = (f"exit {s['exit_code']}, {s['seconds']}s"
                      if s.get("exit_code") is not None else "—")
            rows.append("| {number:02d} | {label} | {inputs} | {outputs} | **{status}** ({timing}) | {comment} |".format(
                number=s["number"] + self.step_offset, label=_md(s["label"]), inputs=_md(s["inputs"]),
                outputs=_md(s["outputs"]), status=_md(s["status"]),
                timing=_md(timing), comment=_md(s.get("comment", ""))))
        failed = [s for s in self.records if s["status"] == "FAIL"]
        degraded = [s for s in self.records if s["status"] in {"DEGRADED", "SKIPPED", "INTERRUPTED"}]
        if failed:
            self.overall = "FAIL"
        elif degraded:
            self.overall = "DEGRADED"
        elif all(s["status"] == "PASS" for s in self.records):
            self.overall = "PASS"
        content = [
            f"# Pipeline run: {self.area}",
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
            "",
            "Step numbers and labels below match the `[QS-NN]` prefixes in the terminal and `run.log`.",
            "",
            "| Step | Description | Source data / inputs | Target data / outputs | Result | Comment / consequence |",
            "|---:|---|---|---|---|---|",
            *rows,
            "",
            "## Debugging",
            "",
            "Find the matching `[QS-NN]` marker in `run.log` to see surrounding run context and full command stdout/stderr. A failed step stops dependent steps; later rows remain `NOT RUN` and explain why.",
            "",
            "## Map preview",
            "",
            (f"Map-ready local preview: [{self.metadata['map_preview_url']}]({self.metadata['map_preview_url']}). "
             "The preview serves directly from `data/`. "
             "Live parameter refitting still requires the separate `src/live_server.py` API and is not enabled by this static preview."
             if self.metadata.get("map_preview_url") else
             "The map-ready tile/preview steps have not completed. Earlier PASS statuses cover only the listed pipeline stages, not map readiness."),
            "",
        ]
        tmp = self.report_path.with_suffix(".md.tmp")
        tmp.write_text("\n".join(content), encoding="utf-8")
        os.replace(tmp, self.report_path)

    def event(self, number: int, text: str) -> None:
        line = f"[QS-{number + self.step_offset:02d}] {text}"
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
        self._save()
        self.event(number, f"START {record['label']}")
        self.event(number, f"COMMAND {record['command']}")
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
            self.event(number, f"COMMAND {safe_command}")
        chunks: list[str] = []
        try:
            proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1)
            assert proc.stdout is not None
            for raw in proc.stdout:
                message = self.redact(raw.rstrip("\r\n"))
                chunks.append(message)
                tagged = f"[QS-{number + self.step_offset:02d}] {message}"
                print(tagged, flush=True)
                self._write_log(tagged)
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
                    pending["comment"] = (
                        f"Not run because step {number + self.step_offset:02d} was interrupted."
                    )
            self._save()
            raise
        except OSError as exc:
            message = self.redact(f"could not start command: {type(exc).__name__}: {exc}")
            chunks.append(message)
            tagged = f"[QS-{number + self.step_offset:02d}] {message}"
            print(tagged, flush=True)
            self._write_log(tagged)
            code = 127
        return code, "\n".join(chunks)


QuickstartRun = PipelineRun


def _key_available() -> bool:
    return bool(_linz_key_value())


def _preflight(area: str, py: Path) -> tuple[bool, str, dict[str, Any]]:
    if not py.is_file() or not os.access(py, os.X_OK):
        return False, f"Selected Python executable is unavailable: {py}", {"name": area}
    if not _key_available():
        return False, "LINZ_API_KEY is not in the environment, .env or Secret Manager (value was not recorded).", {"name": area}
    dependency_probe = subprocess.run(
        [str(py), "-c", "import matplotlib.colors"], cwd=ROOT,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if dependency_probe.returncode:
        return False, (
            f"Matplotlib is required for the map heatmap stage but cannot be imported by "
            f"the selected Python ({py}). Install project dependencies into this exact "
            f"environment with '{py} -m pip install -r requirements.txt', then verify "
            f"with '{py} -c \"import matplotlib.colors; print(matplotlib.__version__)\"'. "
            f"See docs/data-maintainers/troubleshooting.md#matplotlib-is-required-for-the-map-heatmap-stage."
        ), {"name": area}
    missing_tools = [tool for tool in ("tippecanoe", "tile-join", "tippecanoe-decode")
                     if shutil.which(tool) is None]
    if missing_tools:
        return False, "Map-ready output requires Tippecanoe's commands on PATH: " + ", ".join(missing_tools) + ". Install the Tippecanoe package (macOS: brew bundle --file=Brewfile; Ubuntu/WSL: follow docs/data-maintainers/env-setup-ubuntu.md) and reopen the terminal before retrying.", {"name": area}
    try:
        sys.path.insert(0, str(ROOT))
        with contextlib.redirect_stdout(io.StringIO()):
            import config
            from src.surveys import survey_for
            if area not in config.REGIONS:
                raise KeyError(f"unknown configured region {area!r}; add its bbox to config.REGIONS")
            bbox = [float(v) for v in config.REGIONS[area]]
            survey = survey_for(bbox, area)
        if not survey.get("dsm_layer"):
            raise ValueError("the selected survey has no DSM layer configured")
        if survey.get("pointcloud_bulk_url") and not survey.get("lidar_tile_index_layer"):
            raise ValueError("a point-cloud bulk store is configured without a tile-index layer")
        survey_meta = {k: survey.get(k) for k in (
            "name", "dsm_layer", "dem_layer", "imagery_layer",
            "reference_imagery_layer", "lidar_tile_index_layer",
            "pointcloud_bulk_url", "pointcloud_tile_year")}
    except Exception as exc:
        return False, f"No usable configured region/survey: {type(exc).__name__}: {exc}. Configure the region bbox in config.REGIONS and source layers in config.SURVEYS.", {"name": area}
    free_gb = shutil.disk_usage(DATA).free / 1e9 if DATA.exists() else shutil.disk_usage(ROOT).free / 1e9
    details = f"Region {area}; WGS84 bbox {bbox}; Python {sys.version.split()[0]}; free disk {free_gb:.1f} GB; LINZ key present (not recorded)."
    if free_gb < 10:
        details += f" WARNING: only {free_gb:.1f} GB free; documentation recommends about 10 GB."
    return True, details + f" Selected survey: {survey.get('name')}.", {
        "name": area, "bbox": bbox, "free_disk_gb": round(free_gb, 1),
        "survey_overrides": {}, "survey_name": survey.get("name"),
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
            notes.append("fetcher reported a point-cloud warning; inspect run.log")
    status = "DEGRADED" if notes else "PASS"
    comment = "; ".join(notes) + "." if notes else f"{outlines_count} building outlines validated; required rasters exist; point cloud {found}/{total} tiles present."
    return status, comment


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


def _start_preview_server(area: str, run: PipelineRun, py: Path,
                          preferred_port: int,
                          open_browser: bool) -> tuple[bool, str]:
    port = _free_port(preferred_port)
    server_log = run.run_dir / "map-preview-server.log"
    pid_file = run.run_dir / "map-preview-server.pid"
    command = [str(py), "tools/pipeline_serve.py", "--root", str(ROOT), "--port", str(port)]
    record = run.begin(18, command)
    route = "/preview.html"
    bbox = run.metadata["bbox"]
    lat, lng = (bbox[1] + bbox[3]) / 2, (bbox[0] + bbox[2]) / 2
    url = f"http://127.0.0.1:{port}{route}?lat={lat:.7f}&lng={lng:.7f}&z=15.5"
    tile_path = "/data/buildings.pmtiles"
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


def _profile_options(profile: CloudProfile) -> list[str]:
    values = profile.values
    args = [
        f"--project={values['project']}",
        f"--zone={values['zone']}",
        f"--machine-type={values['machine_type']}",
        f"--network={values['network']}",
        f"--boot-disk-size={values['disk_gb']}GB",
        "--boot-disk-type=pd-balanced",
        "--boot-disk-auto-delete",
        f"--image-family={values['image_family']}",
        f"--image-project={values['image_project']}",
        f"--tags={_target_tag(profile)}",
        "--labels=purpose=solar-quickstart,created-by=nz-solar-potential,"
        f"cloud-profile={_profile_fingerprint(profile)}",
    ]
    if values.get("subnetwork"):
        args.append(f"--subnet={values['subnetwork']}")
    if values.get("service_account"):
        args.extend([
            f"--service-account={values['service_account']}",
            "--scopes=https://www.googleapis.com/auth/cloud-platform",
        ])
    else:
        args.append("--no-service-account")
    return args


def _cloud_run(
    args: list[str], *, check: bool = True, capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=ROOT,
            check=check,
            text=True,
            capture_output=capture,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "gcloud is not installed or is not on PATH; install the Google Cloud CLI first"
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(
            f"command failed with exit code {exc.returncode}: {' '.join(args)}"
            + (f"\n{detail}" if detail else "")
        ) from exc


class CloudQuickstartDeclined(RuntimeError):
    pass


class CloudQuickstartLog:
    def __init__(self) -> None:
        CLOUD_RUNS.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.path = CLOUD_RUNS / f"{stamp}.log"
        suffix = 1
        while self.path.exists():
            self.path = CLOUD_RUNS / f"{stamp}-{suffix:02d}.log"
            suffix += 1
        self.path.touch()
        header = f"Cloud quickstart started: {_now()}"
        print(header, flush=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(header + "\n")

    def event(self, number: int, text: str) -> None:
        line = f"[QS-{number:02d}] {text}"
        print(line, flush=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    def step(self, number: int, label: str, action: Callable[[], Any]) -> Any:
        self.event(number, f"START {label}")
        try:
            result = action()
        except CloudQuickstartDeclined as exc:
            self.event(number, f"DECLINED {label}: {exc}")
            raise
        except Exception as exc:
            self.event(number, f"FAIL {label}: {type(exc).__name__}: {exc}")
            raise
        detail = result if isinstance(result, str) and result else ""
        self.event(number, f"PASS {label}" + (f": {detail}" if detail else ""))
        return result

    def detail(self, number: int, text: str) -> None:
        self.event(number, text)


def _linux_environment() -> tuple[str, bool]:
    try:
        release = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace")
    except OSError:
        release = ""
    distro = next(
        (line.partition("=")[2].strip().strip('"').lower()
         for line in release.splitlines() if line.startswith("ID=")),
        "",
    )
    try:
        wsl = "microsoft" in Path("/proc/version").read_text(
            encoding="utf-8", errors="replace"
        ).lower()
    except OSError:
        wsl = False
    return distro, wsl


def _install_gcloud(log: CloudQuickstartLog | None, command: list[str], description: str) -> None:
    print(f"[QS-02] Installing Google Cloud CLI: {description}", flush=True)
    result = subprocess.run(
        command, cwd=ROOT, check=False, text=True, capture_output=True,
    )
    for output in (result.stdout, result.stderr):
        for line in output.splitlines():
            print(f"[QS-02] {line}", flush=True)
            if log:
                with log.path.open("a", encoding="utf-8") as stream:
                    stream.write(f"[QS-02] {line}\n")
    if result.returncode:
        raise RuntimeError(
            f"Google Cloud CLI installation failed (exit code {result.returncode}); "
            "review the installer output above, correct the reported problem, and retry."
        )


def _gcloud(log: CloudQuickstartLog | None = None) -> str:
    path = shutil.which("gcloud")
    if path:
        return path

    brew = shutil.which("brew") if platform.system() == "Darwin" else None
    distro, wsl = _linux_environment() if platform.system() == "Linux" else ("", False)
    apt = shutil.which("apt-get") if platform.system() == "Linux" else None
    if brew:
        installer = [brew, "install", "--cask", "gcloud-cli"]
        prompt = "Install it with Homebrew? (yes/NO) "
        description = "Homebrew: brew install --cask gcloud-cli"
        retry = "brew install --cask gcloud-cli"
    elif distro in {"ubuntu", "debian"} and apt:
        script = """set -euo pipefail
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -d -m 0755 /usr/share/keyrings
curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --dearmor --yes -o /usr/share/keyrings/cloud.google.gpg
echo 'deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main' | sudo tee /etc/apt/sources.list.d/google-cloud-sdk.list >/dev/null
sudo apt-get update
sudo apt-get install -y google-cloud-cli"""
        installer = ["bash", "-c", script]
        environment = "Ubuntu in WSL" if wsl and distro == "ubuntu" else distro.capitalize()
        prompt = f"Install it from Google's apt repository in {environment}? (yes/NO) "
        description = f"{environment}: configure Google's apt repository and install google-cloud-cli"
        retry = (
            "follow https://docs.cloud.google.com/sdk/docs/install-sdk#deb and install "
            "the google-cloud-cli package with apt"
        )
    else:
        if platform.system() == "Linux":
            context = f"Linux distribution {distro or '(unknown)'}"
            if wsl:
                context = f"WSL running {distro or 'an unsupported distribution'}"
        else:
            context = platform.system() or "this operating system"
        raise RuntimeError(
            f"gcloud is missing on {context}. Install the Google Cloud CLI using Google's "
            "instructions at https://cloud.google.com/sdk/docs/install-sdk, then rerun. "
            "The quickstart offers automatic installation only for macOS with Homebrew "
            "and Ubuntu/Debian with apt."
        )

    if not sys.stdin.isatty():
        raise RuntimeError(
            f"gcloud is missing and installation requires interactive confirmation. "
            f"Install it with `{retry}`, then rerun."
        )
    if log:
        log.detail(2, f"gcloud is missing; supported package manager available ({description}).")
    try:
        answer = input(f"[QS-02] Google Cloud CLI (gcloud) is missing. {prompt}")
    except EOFError:
        answer = ""
    if answer.strip().casefold() != "yes":
        raise CloudQuickstartDeclined(
            f"gcloud installation declined; no package was installed. To install later, run `{retry}`."
        )
    if log:
        log.detail(2, f"Installing Google Cloud CLI: {description}.")
    _install_gcloud(log, installer, description)
    path = shutil.which("gcloud")
    if not path:
        raise RuntimeError(
            "The installer completed, but gcloud is not on PATH. Start a new terminal, "
            "check the platform setup instructions, and retry."
        )
    return path


def _confirm_cloud_cost(
    profile: CloudProfile,
    action: str,
    log: CloudQuickstartLog | None = None,
) -> None:
    configured_estimate = profile.values.get("cost_estimate_nzd")
    configured_basis = profile.values.get("cost_estimate_basis")
    estimate = configured_estimate
    basis = configured_basis
    if not estimate or not basis:
        raise RuntimeError(
            "a cost estimate and assumptions are required before this cloud action. "
            "Set cost_estimate_nzd and cost_estimate_basis in the profile."
        )
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?", estimate):
        raise ValueError("cost estimate must be a non-negative NZD amount with up to two decimals")
    if not basis.strip() or len(basis) > 300:
        raise ValueError("cost estimate assumptions must be 1-300 characters")
    if not sys.stdin.isatty():
        raise RuntimeError(
            "interactive cost confirmation is required before this cloud action; "
            "no cloud action was started."
        )
    action_text = {
        "create": "create the configured VM and its boot disk",
        "bootstrap": "run setup commands on the configured VM",
        "ssh": "connect to and use the configured VM",
        "run": "run the selected region on the configured VM",
        "collect": "access the configured VM to copy run reports",
        "preview": "keep the configured VM in use while the preview tunnel is open",
    }[action]
    if log:
        log.detail(
            4,
            f"This action will {action_text}, which may result in financial costs. "
            f"Estimate: NZD ${estimate} ({basis}). This is not a spending cap; "
            "actual charges can be higher.",
        )
    try:
        answer = input("[QS-04] Continue? (yes/NO) ")
    except EOFError:
        answer = ""
    if answer.strip().casefold() != "yes":
        raise CloudQuickstartDeclined(
            "human cost confirmation declined; no cloud action was started."
        )


def _cloud_preflight(
    profile: CloudProfile,
    *,
    require_create_config: bool = False,
    gcloud: str | None = None,
) -> str:
    gcloud = gcloud or _gcloud()
    auth = _cloud_run(
        [gcloud, "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
        capture=True,
    )
    if not auth.stdout.strip():
        raise RuntimeError("gcloud has no active account; run `gcloud auth login` first")
    values = profile.values
    checks = [
        [gcloud, "compute", "zones", "describe", values["zone"],
         f"--project={values['project']}", "--format=value(name)"],
        [gcloud, "compute", "machine-types", "describe", values["machine_type"],
         f"--zone={values['zone']}", f"--project={values['project']}", "--format=value(name)"],
    ]
    for cmd in checks:
        _cloud_run(cmd, capture=True)
    if require_create_config and not values["ssh_source_ranges"]:
        raise RuntimeError(
            "profile must set ssh_source_ranges to your public IP as a CIDR (usually x.x.x.x/32) "
            "before creating a VM"
        )
    return (
        f"Profile {profile.name}; target {profile.target}; "
        f"{values['machine_type']} with {values['disk_gb']} GB pd-balanced disk; "
        "active Google account and configured zone/machine type verified."
    )


def _load_shared_cloud_config() -> dict[str, Any]:
    import config as project_config

    values = project_config.CLOUD_SHARED_BUILD
    required = ("project", "vm", "zone", "ssh_user", "regions", "ref")
    if not isinstance(values, dict) or any(
        not isinstance(values.get(key), str) for key in required
    ):
        raise ValueError(
            "config.CLOUD_SHARED_BUILD must define string values for: "
            + ", ".join(required)
        )
    values = dict(values)
    if not isinstance(values.get("preview_bucket"), str):
        raise ValueError("config.CLOUD_SHARED_BUILD.preview_bucket must be a string")
    if (
        isinstance(values.get("preview_retention_days"), bool)
        or not isinstance(values.get("preview_retention_days"), int)
        or values["preview_retention_days"] < 1
    ):
        raise ValueError(
            "config.CLOUD_SHARED_BUILD.preview_retention_days must be a positive integer"
        )
    if not re.fullmatch(
        r"gs://[a-z0-9][a-z0-9-]{1,61}[a-z0-9]",
        values["preview_bucket"] or "",
    ):
        raise ValueError("config.CLOUD_SHARED_BUILD.preview_bucket must be a valid gs:// bucket")
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", values["project"]):
        raise ValueError("CLOUD_SHARED_BUILD.project is not a valid Google Cloud project ID")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", values["vm"]):
        raise ValueError("CLOUD_SHARED_BUILD.vm must be a valid VM name")
    if not re.fullmatch(r"[a-z]+-[a-z]+[0-9]-[a-z]", values["zone"]):
        raise ValueError("CLOUD_SHARED_BUILD.zone must be a valid GCP zone")
    if not re.fullmatch(r"[a-z_][a-z0-9_ -]*", values["regions"]):
        raise ValueError("CLOUD_SHARED_BUILD.regions contains unsupported characters")
    if not values["ssh_user"] or not re.fullmatch(r"[a-z_][a-z0-9_-]*", values["ssh_user"]):
        raise ValueError("CLOUD_SHARED_BUILD.ssh_user must be a valid SSH username")
    if not values["ref"] or values["ref"].startswith("-") or "\n" in values["ref"]:
        raise ValueError("CLOUD_SHARED_BUILD.ref must be a non-empty Git ref")
    cost_model = values.get("preview_cost_model")
    cost_fields = (
        "vm_hourly_nzd", "vm_hours", "disk_monthly_per_gb_nzd", "disk_gb",
        "storage_gb", "storage_monthly_per_gb_nzd", "expected_download_gb",
        "egress_per_gb_nzd", "round_up_nzd",
    )
    if not isinstance(cost_model, dict) or any(
        not isinstance(cost_model.get(key), str) for key in cost_fields
    ):
        raise ValueError(
            "CLOUD_SHARED_BUILD.preview_cost_model must define string values for: "
            + ", ".join(cost_fields)
        )
    for key in cost_fields:
        value = cost_model[key]
        if not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,4})?", value):
            raise ValueError(f"CLOUD_SHARED_BUILD.preview_cost_model.{key} must be non-negative")
        try:
            amount = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(
                f"CLOUD_SHARED_BUILD.preview_cost_model.{key} is not a decimal amount"
            ) from exc
        if not amount.is_finite():
            raise ValueError(f"CLOUD_SHARED_BUILD.preview_cost_model.{key} must be finite")
    if Decimal(cost_model["vm_hours"]) <= 0 or Decimal(cost_model["round_up_nzd"]) <= 0:
        raise ValueError("preview cost model vm_hours and round_up_nzd must be positive")
    return {
        **{key: values[key] for key in required},
        "preview_bucket": values["preview_bucket"],
        "preview_retention_days": values["preview_retention_days"],
        "preview_cost_model": dict(cost_model),
    }


def _github_alias() -> str:
    """Return the login for the authenticated GitHub CLI account."""
    gh = shutil.which("gh")
    if not gh:
        raise RuntimeError(
            "GitHub CLI is required to publish a per-developer preview. Install it from "
            "https://cli.github.com/ and authenticate once with `gh auth login`."
        )
    try:
        result = subprocess.run(
            [gh, "api", "user", "--jq", ".login"],
            cwd=ROOT, check=True, text=True, capture_output=True, timeout=15,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "could not resolve the authenticated GitHub alias; run `gh auth login` "
            "and retry the cloud quickstart."
        ) from exc
    alias = result.stdout.strip()
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", alias):
        raise RuntimeError("GitHub CLI returned an invalid account login for the preview alias.")
    return alias.lower()


def _shared_cloud_preflight(config: dict[str, Any], gcloud: str) -> str:
    auth = _cloud_run(
        [gcloud, "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
        capture=True,
    )
    if not auth.stdout.strip():
        raise RuntimeError("gcloud has no active account; run `gcloud auth login` first")
    _cloud_run([
        gcloud, "compute", "zones", "describe", config["zone"],
        f"--project={config['project']}", "--format=value(name)",
    ], capture=True)
    result = _cloud_run([
        gcloud, "compute", "instances", "describe", config["vm"],
        f"--project={config['project']}", f"--zone={config['zone']}",
        "--format=value(status)",
    ], capture=True)
    status = result.stdout.strip()
    if status not in {"RUNNING", "TERMINATED", "STOPPED", "SUSPENDED"}:
        raise RuntimeError(
            f"could not confirm shared VM state (gcloud returned {status!r}); "
            "check project access and VM configuration."
        )
    return (
        f"Active Google account verified; target "
        f"{config['project']}/{config['zone']}/{config['vm']} is {status}."
    )


def _shared_vm_readiness(config: dict[str, Any], gcloud: str) -> str:
    result = _cloud_run([
        gcloud, "compute", "instances", "describe", config["vm"],
        f"--project={config['project']}", f"--zone={config['zone']}",
        "--format=value(status)",
    ], capture=True)
    status = result.stdout.strip()
    if status in {"TERMINATED", "STOPPED"}:
        return f"VM is {status}; the shared build workflow will start it after approval."
    if status != "RUNNING":
        raise RuntimeError(f"shared VM is {status or 'in an unknown state'}; no build was started.")
    ssh = _cloud_run([
        gcloud, "compute", "ssh", f"{config['ssh_user']}@{config['vm']}",
        f"--project={config['project']}", f"--zone={config['zone']}",
        "--tunnel-through-iap", "--command=printf CLOUD_QUICKSTART_SSH_OK",
    ], capture=True)
    if "CLOUD_QUICKSTART_SSH_OK" not in ssh.stdout:
        raise RuntimeError("IAP/SSH probe did not return its success marker.")
    return "Shared VM is RUNNING and IAP/SSH access is ready."


def _shared_preview_cost(config: dict[str, Any]) -> tuple[Decimal, str]:
    """Estimate VM, disk, preview storage, and expected public egress costs."""
    model = config["preview_cost_model"]
    vm_hours = Decimal(model["vm_hours"])
    disk_gb = Decimal(model["disk_gb"])
    storage_gb = Decimal(model["storage_gb"])
    download_gb = Decimal(model["expected_download_gb"])
    retention_days = Decimal(config["preview_retention_days"])
    vm_cost = Decimal(model["vm_hourly_nzd"]) * vm_hours
    disk_cost = (
        Decimal(model["disk_monthly_per_gb_nzd"]) * disk_gb * vm_hours / Decimal(720)
    )
    storage_cost = (
        Decimal(model["storage_monthly_per_gb_nzd"]) * storage_gb
        * retention_days / Decimal(30)
    )
    egress_cost = Decimal(model["egress_per_gb_nzd"]) * download_gb
    quantum = Decimal(model["round_up_nzd"])
    rounded_total = (
        ((vm_cost + disk_cost + storage_cost + egress_cost) / quantum)
        .to_integral_value(rounding=ROUND_CEILING) * quantum
    )
    basis = (
        f"compute NZD ${vm_cost:.2f} ({vm_hours:g} hours); "
        f"disk NZD ${disk_cost:.2f} ({disk_gb:g} GB for {vm_hours:g} hours); "
        f"preview storage NZD ${storage_cost:.2f} ({storage_gb:g} GB for "
        f"{retention_days:g} days); estimated public downloads NZD ${egress_cost:.2f} "
        f"({download_gb:g} GB). Unit prices and usage are planning assumptions."
    )
    return rounded_total, basis


def _confirm_shared_cloud_cost(
    config: dict[str, Any], log: CloudQuickstartLog,
) -> None:
    estimate, basis = _shared_preview_cost(config)
    estimate_text = f"{estimate:.2f}"
    log.detail(
        4,
        f"This action will build and gate region(s) {config['regions']} on the shared VM, "
        f"and publish a public preview. Calculated estimate: NZD ${estimate_text} "
        f"({basis}). Actual charges can be higher, especially if runtime or public "
        "downloads exceed these assumptions; this is not a spending cap.",
    )
    if not sys.stdin.isatty():
        raise RuntimeError(
            "interactive cost confirmation is required before shared cloud work; "
            "no cloud action was started."
        )
    try:
        answer = input("[QS-04] Continue? (yes/NO) ")
    except EOFError:
        answer = ""
    if answer.strip().casefold() != "yes":
        raise CloudQuickstartDeclined(
            "human cost confirmation declined; no cloud action was started."
        )


def _run_shared_cloud_build(
    config: dict[str, Any], region: str, log: CloudQuickstartLog,
) -> tuple[str, str | None]:
    command = [
        "bash", str(ROOT / "tools" / "remote_build.sh"),
        "--regions", region, "--ref", config["ref"],
        "--preview-user", config["preview_user"],
    ]
    env = os.environ.copy()
    env.update({
        "CLOUD_PROJECT": config["project"],
        "CLOUD_VM": config["vm"],
        "CLOUD_ZONE": config["zone"],
        "VM_USER": config["ssh_user"],
    })
    try:
        process = subprocess.Popen(
            command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1,
        )
    except OSError as exc:
        raise RuntimeError(f"could not start shared cloud build: {exc}") from exc
    assert process.stdout is not None
    preview_url: str | None = None
    for line in process.stdout:
        content = line.rstrip("\r\n")
        if content.startswith("PREVIEW_URL="):
            preview_url = content.partition("=")[2]
        log.detail(6, content)
    status = process.wait()
    if status:
        raise RuntimeError(
            f"shared build workflow exited with status {status}; inspect the numbered log."
        )
    expected_url = (
        f"https://storage.googleapis.com/{config['preview_bucket'][5:]}/devel/"
        f"{config['preview_user']}/preview.html"
    )
    if preview_url != expected_url:
        raise RuntimeError(
            "shared build completed without the expected developer preview URL; "
            "verify that the remote preview publisher succeeded."
        )
    return (
        "Shared build and gate completed; the developer preview was published.",
        preview_url,
    )


def _existing_instance(profile: CloudProfile) -> dict[str, Any] | None:
    result = _cloud_run([
        _gcloud(), "compute", "instances", "describe", profile.values["instance_name"],
        f"--project={profile.values['project']}", f"--zone={profile.values['zone']}",
        "--format=json",
    ], check=False, capture=True)
    if result.returncode:
        detail = (result.stderr or result.stdout or "").lower()
        if "not found" in detail and f"/instances/{profile.values['instance_name']}" in detail:
            return None
        raise RuntimeError(
            "could not inspect the selected VM; check project access and Compute Engine API status: "
            + (result.stderr or result.stdout or "gcloud returned an error").strip()
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("gcloud returned an invalid instance description") from exc


def _cloud_action_preflight(
    profile: CloudProfile, action: str, gcloud: str,
) -> str:
    if action == "create":
        if not profile.values["ssh_source_ranges"]:
            raise RuntimeError(
                "profile must set ssh_source_ranges to your public IP as a CIDR "
                "(usually x.x.x.x/32) before creating a VM"
            )
        if _existing_instance(profile):
            raise RuntimeError(
                f"VM {profile.target} already exists; refusing to adopt or replace it. "
                "Use `status` or `ssh`."
            )
        return f"No existing VM found at {profile.target}."
    if action not in {"bootstrap", "ssh", "run", "collect", "preview"}:
        return "No billable VM connection check is needed for this action."
    instance = _existing_instance(profile)
    if instance is None:
        raise RuntimeError(
            f"VM not found: {profile.target}. Create it first with `--cloud create`."
        )
    status = instance.get("status", "")
    if status != "RUNNING":
        raise RuntimeError(
            f"VM {profile.target} is {status or 'in an unknown state'}, not RUNNING. "
            "Start it and retry; this preflight did not start the VM."
        )
    output = _cloud_run([
        gcloud, "compute", "ssh", profile.values["instance_name"],
        f"--project={profile.values['project']}", f"--zone={profile.values['zone']}",
        "--command=printf CLOUD_QUICKSTART_SSH_OK",
    ], capture=True)
    if "CLOUD_QUICKSTART_SSH_OK" not in output.stdout:
        raise RuntimeError(
            "SSH connected without the expected success marker; inspect the gcloud output "
            "before retrying."
        )
    return f"VM is RUNNING and SSH access is ready at {profile.target}."


def _firewall_name(profile: CloudProfile) -> str:
    return f"{_target_tag(profile)}-ssh"


def _profile_fingerprint(profile: CloudProfile) -> str:
    return hashlib.sha256(profile.name.encode("utf-8")).hexdigest()[:16]


def _target_tag(profile: CloudProfile) -> str:
    suffix = hashlib.sha256(profile.name.encode("utf-8")).hexdigest()[:8]
    return f"solar-quickstart-{profile.name[:30]}-{suffix}"


def _firewall(profile: CloudProfile) -> dict[str, Any] | None:
    name = _firewall_name(profile)
    result = _cloud_run([
        _gcloud(), "compute", "firewall-rules", "describe", name,
        f"--project={profile.values['project']}", "--format=json",
    ], check=False, capture=True)
    if result.returncode:
        detail = (result.stderr or result.stdout or "").lower()
        if "not found" in detail and f"/firewalls/{name}" in detail:
            return None
        raise RuntimeError(
            "could not inspect the quickstart SSH firewall rule: "
            + (result.stderr or result.stdout or "gcloud returned an error").strip()
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("gcloud returned an invalid firewall rule description") from exc


def _firewall_matches_profile(profile: CloudProfile, current: dict[str, Any]) -> bool:
    values = profile.values
    allowed = current.get("allowed", [])
    return (
        current.get("description") == f"{FIREWALL_MARKER}: {profile.name}"
        and current.get("direction") == "INGRESS"
        and not current.get("disabled", False)
        and current.get("network", "").endswith(f"/{values['network']}")
        and set(current.get("sourceRanges", [])) == set(values["ssh_source_ranges"])
        and current.get("targetTags") == [_target_tag(profile)]
        and len(allowed) == 1
        and allowed[0].get("IPProtocol") == "tcp"
        and allowed[0].get("ports") == ["22"]
    )


def _ensure_firewall(profile: CloudProfile) -> None:
    current = _firewall(profile)
    values = profile.values
    if current:
        if not _firewall_matches_profile(profile, current):
            raise RuntimeError(
                f"firewall rule {_firewall_name(profile)!r} already exists but does not match "
                "this profile; refusing to change it"
            )
        return
    description = f"{FIREWALL_MARKER}: {profile.name}"
    _cloud_run([
        _gcloud(), "compute", "firewall-rules", "create", _firewall_name(profile),
        f"--project={values['project']}", f"--network={values['network']}",
        "--direction=INGRESS", "--action=ALLOW", "--rules=tcp:22",
        f"--source-ranges={','.join(values['ssh_source_ranges'])}",
        f"--target-tags={_target_tag(profile)}", f"--description={description}",
    ])


def _delete_owned_firewall(profile: CloudProfile) -> None:
    current = _firewall(profile)
    if current is None:
        return
    if not _firewall_matches_profile(profile, current):
        raise RuntimeError(
            f"not deleting firewall rule {_firewall_name(profile)!r}: its ownership marker or "
            "configuration differs from the selected profile"
        )
    _cloud_run([
        _gcloud(), "compute", "firewall-rules", "delete", _firewall_name(profile),
        f"--project={profile.values['project']}", "--quiet",
    ])


def _confirm_target(profile: CloudProfile, confirmation: str | None, verb: str) -> None:
    if confirmation != profile.target:
        raise RuntimeError(
            f"{verb} requires --confirm {profile.target!r}; review the project, zone, and VM first"
        )


def _create(profile: CloudProfile, confirmation: str | None) -> None:
    _confirm_target(profile, confirmation, "VM creation")
    if _existing_instance(profile):
        raise RuntimeError(
            f"VM {profile.target} already exists; refusing to adopt or replace it. Use `status` or `ssh`."
        )
    gcloud = _gcloud()
    ranges = profile.values["ssh_source_ranges"]
    print(
        f"Creating {profile.target} ({profile.values['machine_type']}, "
        f"{profile.values['disk_gb']} GB disk). This is billable.",
        flush=True,
    )
    _ensure_firewall(profile)
    _cloud_run([
        gcloud, "compute", "instances", "create", profile.values["instance_name"],
        *_profile_options(profile),
    ])
    print(
        "VM and profile-scoped SSH firewall rule created. SSH ingress is restricted to "
        f"{', '.join(ranges)}. Run `--cloud bootstrap` next."
    )


def _bootstrap(profile: CloudProfile) -> None:
    command = BOOTSTRAP.replace("REPOSITORY_PLACEHOLDER", REPOSITORY)
    _ssh(profile, command=command)
    print("Bootstrap complete. Add LINZ_API_KEY to the ignored VM-local .env before running.")


def _ssh(
    profile: CloudProfile,
    *,
    command: str | None = None,
    extra: list[str] | None = None,
) -> None:
    args = [
        _gcloud(), "compute", "ssh", profile.values["instance_name"],
        f"--project={profile.values['project']}", f"--zone={profile.values['zone']}",
    ]
    if command is not None:
        args.append(f"--command={command}")
    if extra:
        args.extend(["--", *extra])
    _cloud_run(args)


def _ssh_stream(profile: CloudProfile, command: str) -> tuple[int, str]:
    args = [
        _gcloud(), "compute", "ssh", profile.values["instance_name"],
        f"--project={profile.values['project']}", f"--zone={profile.values['zone']}",
        f"--command={command}",
    ]
    try:
        process = subprocess.Popen(
            args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", bufsize=1,
        )
    except OSError as exc:
        raise RuntimeError(f"could not start gcloud ssh: {type(exc).__name__}: {exc}") from exc
    run_id = ""
    assert process.stdout is not None
    try:
        for line in process.stdout:
            stripped = line.rstrip("\r\n")
            if stripped.startswith("CLOUD_RUN_ID="):
                run_id = stripped.partition("=")[2]
                continue
            print(line, end="", flush=True)
    except KeyboardInterrupt:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait()
        raise
    returncode = process.wait()
    if run_id and not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z(?:-[0-9]{2})?", run_id):
        raise RuntimeError(f"remote pipeline returned an invalid run ID: {run_id!r}")
    return returncode, run_id


def _collect_run(profile: CloudProfile, run_id: str) -> Path:
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z(?:-[0-9]{2})?", run_id):
        raise ValueError(f"invalid pipeline run ID: {run_id!r}")
    runs = ROOT / "data" / "pipeline_runs"
    local_run = runs / run_id
    if local_run.exists():
        raise RuntimeError(f"local pipeline run already exists; refusing to overwrite {local_run}")
    runs.mkdir(parents=True, exist_ok=True)
    _cloud_run([
        _gcloud(), "compute", "scp", "--recurse",
        f"--project={profile.values['project']}", f"--zone={profile.values['zone']}",
        f"{profile.values['instance_name']}:~/nz-solar-potential/data/pipeline_runs/{run_id}",
        str(runs),
    ])
    if not (local_run / "report.md").is_file() or not (local_run / "run.log").is_file():
        raise RuntimeError(
            f"remote run {run_id} was copied, but its report.md or run.log is missing"
        )
    return local_run


def _report_preview_port(report: Path) -> int | None:
    try:
        content = report.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"could not read collected pipeline report {report}: {exc}") from exc
    match = re.search(r"\*\*Map preview:\*\* \[http://127\.0\.0\.1:(\d+)/", content)
    return int(match.group(1)) if match else None


def _remote_pipeline_command(region: str, port: int) -> str:
    selected = _safe_area(region)
    if not 1024 <= port <= 65535:
        raise ValueError("preview port must be between 1024 and 65535")
    return (
        "set -u; cd \"$HOME/nz-solar-potential\" || exit 2; "
        "before=$(find data/pipeline_runs -mindepth 1 -maxdepth 1 -type d -printf '%f\\n' 2>/dev/null | sort); "
        f".venv/bin/python tools/pipeline.py {selected} --port {port} --no-open-browser "
        f"--step-offset {CLOUD_PIPELINE_STEP_OFFSET}; "
        "pipeline_status=$?; "
        "after=$(find data/pipeline_runs -mindepth 1 -maxdepth 1 -type d -printf '%f\\n' 2>/dev/null | sort); "
        "run_id=; "
        "for candidate in $after; do "
        "case \" $before \" in *\" $candidate \"*) ;; *) run_id=$candidate ;; esac; "
        "done; "
        "printf '\\nCLOUD_RUN_ID=%s\\n' \"$run_id\"; "
        "exit \"$pipeline_status\""
    )


def _run_pipeline(profile: CloudProfile, region: str, port: int) -> int:
    selected = _safe_area(region)
    print(f"Running configured region {selected!r} on {profile.target}.")
    returncode, run_id = _ssh_stream(profile, _remote_pipeline_command(selected, port))
    if not run_id:
        if returncode:
            raise RuntimeError(
                f"remote pipeline exited with status {returncode} before returning a run ID; "
                "inspect the VM's data/pipeline_runs and retry report collection"
            )
        raise RuntimeError("remote pipeline completed without returning a run ID")
    local_run = _collect_run(profile, run_id)
    print(f"Pipeline report: {local_run / 'report.md'}")
    print(f"Combined log: {local_run / 'run.log'}")
    actual_port = _report_preview_port(local_run / "report.md")
    if actual_port is not None:
        print(
            "When complete, start the SSH preview tunnel with "
            f"`bash quickstart.sh --mode cloud --profile {profile.name} "
            f"--cloud preview --port {actual_port}`."
        )
    return returncode


def _status(profile: CloudProfile) -> None:
    instance = _existing_instance(profile)
    if not instance:
        print(f"VM not found: {profile.target}")
        return
    print(f"VM: {instance.get('status', 'unknown')}  target={profile.target}")
    print(f"Machine type: {instance.get('machineType', '').rsplit('/', 1)[-1]}")
    for interface in instance.get("networkInterfaces", []):
        access_configs = interface.get("accessConfigs", [])
        if access_configs:
            print(f"External IP: {access_configs[0].get('natIP', 'unknown')}")
    if (
        instance.get("labels", {}).get("purpose") != "solar-quickstart"
        or instance.get("labels", {}).get("created-by") != "nz-solar-potential"
        or instance.get("labels", {}).get("cloud-profile") != _profile_fingerprint(profile)
    ):
        print("Notice: this VM is not labelled as created by the cloud quickstart.")


def _collect(profile: CloudProfile) -> None:
    destination = ROOT / "data" / "cloud_runs" / profile.name
    destination.mkdir(parents=True, exist_ok=True)
    _cloud_run([
        _gcloud(), "compute", "scp", "--recurse",
        f"--project={profile.values['project']}", f"--zone={profile.values['zone']}",
        f"{profile.values['instance_name']}:~/nz-solar-potential/data/pipeline_runs",
        str(destination),
    ])
    print(f"Copied remote pipeline reports and logs to {destination.relative_to(ROOT)}.")


def _delete(profile: CloudProfile, confirmation: str | None) -> None:
    _confirm_target(profile, confirmation, "VM deletion")
    instance = _existing_instance(profile)
    if instance is None:
        _delete_owned_firewall(profile)
        print(
            f"VM is already absent: {profile.target}; removed its owned SSH firewall rule if present."
        )
        return
    if (
        instance.get("labels", {}).get("purpose") != "solar-quickstart"
        or instance.get("labels", {}).get("created-by") != "nz-solar-potential"
        or instance.get("labels", {}).get("cloud-profile") != _profile_fingerprint(profile)
    ):
        raise RuntimeError(
            "refusing to delete this VM because it lacks the ownership labels for this profile"
        )
    print(f"Deleting {profile.target} and its auto-delete boot disk. This is destructive.", flush=True)
    _cloud_run([
        _gcloud(), "compute", "instances", "delete", profile.values["instance_name"],
        f"--project={profile.values['project']}", f"--zone={profile.values['zone']}", "--quiet",
    ])
    _delete_owned_firewall(profile)
    print(
        "VM deleted. The local clone, VM-local pipeline reports/data, and any non-auto-delete "
        "resources are not removed by this command."
    )


def _dispatch_cloud_action(
    profile: CloudProfile,
    action: str,
    region: str,
    port: int | None,
    confirmation: str | None,
) -> int:
    if action == "preflight":
        print("Preflight passed. It did not create resources or enable APIs.")
    elif action == "create":
        _create(profile, confirmation)
    elif action == "bootstrap":
        _bootstrap(profile)
    elif action == "ssh":
        _ssh(profile)
    elif action == "run":
        return _run_pipeline(profile, region, port or profile.values["preview_port"])
    elif action == "collect":
        _collect(profile)
    elif action == "preview":
        selected_port = port if port is not None else profile.values["preview_port"]
        if not 1024 <= selected_port <= 65535:
            raise ValueError("preview port must be between 1024 and 65535")
        _ssh(profile, extra=["-N", "-L", f"{selected_port}:127.0.0.1:{selected_port}"])
    elif action == "status":
        _status(profile)
    elif action == "delete":
        _delete(profile, confirmation)
    return 0


def _argument_parser(default_area: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the regional solar pipeline with persistent numbered logs.")
    parser.add_argument("region", nargs="?", default=None,
                        help=f"configured region to process (default: {default_area})")
    parser.add_argument("--mode", choices=("local", "cloud"), default="local",
                        help="where to run the pipeline (default: local)")
    parser.add_argument("--profile",
                        help="named GCP profile for a per-developer VM (omit to use shared VM defaults)")
    parser.add_argument("--profiles-file",
                        help="cloud profile JSON file (or SOLAR_CLOUD_PROFILES_FILE)")
    parser.add_argument(
        "--cloud",
        choices=("preflight", "build", "create", "bootstrap", "ssh", "run", "collect",
                 "preview", "status", "delete"),
        dest="cloud",
        help="cloud action (default: shared build and preview without --profile; run with --profile)",
    )
    parser.add_argument("--confirm",
                        help="exact project/zone/VM target required by create and delete")
    parser.add_argument("--step-offset", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--python", dest="python", default=sys.executable,
                        help="Python executable used for local pipeline subprocesses")
    parser.add_argument("--port", type=int, default=None,
                        help="preferred preview port (uses another free port if occupied)")
    parser.add_argument("--no-open-browser", action="store_true",
                        help="start the local preview server but do not open a browser tab")
    return parser


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from cloud_profile import load_profile

    import config
    parser = _argument_parser(config.PIPELINE_REGION)
    args = parser.parse_args()
    selected_profile_name = (
        args.profile or os.environ.get("SOLAR_CLOUD_PROFILE")
        if args.mode == "cloud" else args.profile
    )
    try:
        area = _safe_area(args.region or config.PIPELINE_REGION)
    except ValueError as exc:
        parser.error(str(exc))
    if args.mode == "cloud":
        action = args.cloud or ("build" if not selected_profile_name else "run")
        if selected_profile_name and action == "build":
            parser.error("--cloud build uses the shared VM and cannot be combined with --profile")
        try:
            log = CloudQuickstartLog()
        except OSError as exc:
            print(
                f"cloud quickstart error: could not create the preflight log: {exc}",
                file=sys.stderr,
            )
            return 2
        try:
            if args.confirm and action not in ("create", "delete"):
                parser.error("--confirm can only be used with cloud actions create or delete")
            chargeable_actions = {"create", "bootstrap", "ssh", "run", "collect", "preview"}

            if not selected_profile_name:
                if args.profiles_file:
                    parser.error("--profiles-file requires --profile or SOLAR_CLOUD_PROFILE")
                if action not in {"build", "preflight"}:
                    parser.error(
                        "--cloud create/bootstrap/ssh/run/collect/preview/status/delete "
                        "requires a named --profile; without a profile the shared VM is used."
                    )

                def check_shared_checkout_and_config() -> dict[str, Any]:
                    git = shutil.which("git")
                    if not git:
                        raise RuntimeError("Git is not installed or not on PATH.")
                    checkout = subprocess.run(
                        [git, "-C", str(ROOT), "rev-parse", "--show-toplevel"],
                        cwd=ROOT, check=True, text=True, capture_output=True, timeout=5,
                    ).stdout.strip()
                    if Path(checkout).resolve() != ROOT.resolve():
                        raise RuntimeError(f"Git checkout root {checkout!r} does not match {ROOT}.")
                    values = _load_shared_cloud_config()
                    regions = args.region or values["regions"]
                    for selected_region in regions.split():
                        _safe_area(selected_region)
                    values = dict(values)
                    values["regions"] = regions
                    if action == "build":
                        values["preview_user"] = _github_alias()
                    description = (
                        f"Git checkout valid; shared VM "
                        f"{values['project']}/{values['zone']}/{values['vm']}; "
                        f"regions {regions}; ref {values['ref']}"
                    )
                    if action == "build":
                        description += f"; preview alias {values['preview_user']}"
                    log.detail(1, description + ".")
                    return values

                shared = log.step(
                    1, "Check local checkout and shared cloud defaults",
                    check_shared_checkout_and_config,
                )
                gcloud = log.step(2, "Check or install Google Cloud CLI", lambda: _gcloud(log))
                log.step(
                    3,
                    "Check Google authentication and shared VM",
                    lambda: _shared_cloud_preflight(shared, gcloud),
                )

                def confirm_shared_cost() -> str:
                    if action == "build":
                        _confirm_shared_cloud_cost(shared, log)
                        return "Human approval received."
                    return "Preflight only; no cloud work or cost approval required."

                log.step(4, "Confirm estimated cloud cost", confirm_shared_cost)
                log.step(
                    5,
                    "Check shared VM readiness",
                    lambda: _shared_vm_readiness(shared, gcloud),
                )
                if action == "preflight":
                    result = log.step(
                        6, "Complete cloud preflight",
                        lambda: "No VM was started and no build was run.",
                    )
                else:
                    build_result = log.step(
                        6, "Build, gate, and publish developer preview",
                        lambda: _run_shared_cloud_build(shared, shared["regions"], log),
                    )
                    if build_result is None:
                        result = None
                        preview_url = None
                    else:
                        result, preview_url = build_result
                print(f"Cloud quickstart log: {log.path}", flush=True)
                if action == "build" and result is not None:
                    if not preview_url:
                        raise RuntimeError(
                            "shared build succeeded without returning a developer preview URL."
                        )
                    print(f"Developer preview: {preview_url}", flush=True)
                    webbrowser.open(preview_url)
                return 0 if result is not None else 1

            def check_checkout_and_profile() -> CloudProfile:
                git = shutil.which("git")
                if not git:
                    raise RuntimeError("Git is not installed or not on PATH.")
                checkout = subprocess.run(
                    [git, "-C", str(ROOT), "rev-parse", "--show-toplevel"],
                    cwd=ROOT, check=True, text=True, capture_output=True, timeout=5,
                ).stdout.strip()
                if Path(checkout).resolve() != ROOT.resolve():
                    raise RuntimeError(f"Git checkout root {checkout!r} does not match {ROOT}.")
                selected_profile = load_profile(selected_profile_name, args.profiles_file)
                if action in {"create", "delete"}:
                    _confirm_target(
                        selected_profile, args.confirm,
                        "VM creation" if action == "create" else "VM deletion",
                    )
                log.detail(1, f"Git checkout valid; profile {selected_profile.name} loaded.")
                return selected_profile

            profile = log.step(1, "Check local checkout and cloud profile", check_checkout_and_profile)
            gcloud = log.step(2, "Check or install Google Cloud CLI", lambda: _gcloud(log))
            log.step(
                3,
                "Check Google authentication and cloud target",
                lambda: _cloud_preflight(
                    profile,
                    require_create_config=action == "create",
                    gcloud=gcloud,
                ),
            )

            def confirm_cost() -> str:
                if action in chargeable_actions:
                    _confirm_cloud_cost(profile, action, log)
                    return "Human approval received."
                return "This action does not create or use a billable VM; no cost approval required."

            log.step(4, "Confirm estimated cloud cost", confirm_cost)
            log.step(
                5,
                "Check VM state and SSH readiness",
                lambda: _cloud_action_preflight(profile, action, gcloud),
            )
            result = log.step(
                6,
                f"Run cloud action: {action}",
                lambda: _dispatch_cloud_action(
                    profile, action, area, args.port, args.confirm,
                ),
            )
            print(f"Cloud quickstart log: {log.path}", flush=True)
            return result
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
            print(f"cloud quickstart error: {exc}\nDetails: {log.path}", file=sys.stderr)
            return 2
    if selected_profile_name or args.profiles_file or args.cloud or args.confirm:
        parser.error(
            "--profile, --profiles-file, --cloud, and --confirm require --mode cloud"
        )
    py = _python_executable(args.python)
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RUNS.mkdir(parents=True, exist_ok=True)
    run_dir = RUNS / run_id
    # Timestamp collisions are rare but should never overwrite evidence.
    suffix = 1
    while run_dir.exists():
        run_dir = RUNS / f"{run_id}-{suffix:02d}"
        suffix += 1
    if args.step_offset < 0:
        parser.error("--step-offset cannot be negative")
    run = PipelineRun(area, run_dir, step_offset=args.step_offset)
    area_dir = DATA / "regions" / area
    run.metadata = {"run_dir": str(run_dir.relative_to(ROOT))}
    region_out_root = DATA / "out"
    region_out = region_out_root / area
    region_out_root.mkdir(parents=True, exist_ok=True)
    map_data = DATA
    terrain_dir = DATA / "terrain"
    terrain_stage_dir = region_out_root / f".terrain-{run_id}"

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
        print(f"\nPipeline stopped. Report: {run.report_path}\nLogs: {run.run_dir}", flush=True)
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
        [str(py), "tools/pipeline._report.py", area],
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
         "--max-zoom", "17", "--out", str(terrain_stage_dir)],
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
                run.finish(number, "DEGRADED", f"Vision precompute failed (exit {code}); build will use any existing selected readings and fallback geometry. See run.log.", code)
            continue

        assert command is not None
        try:
            code, output = run.run_command(number, command, env=env)
        except KeyboardInterrupt:
            raise
        if number == 2 and code == 0:
            # Regional acquisition does not fetch the shared wide DEM. Fetch
            # or refresh it explicitly before validating the combined inputs.
            dem_command = [str(py), "src/fetch_dem_wide.py"]
            try:
                dem_code, dem_output = run.run_command(number, dem_command, env=env, begin=False)
            except KeyboardInterrupt:
                raise
            output = "\n".join(part for part in (output, dem_output) if part)
            code = dem_code
        if code != 0:
            if number in {11, 13, 16}:
                note = {
                    11: "Imagery alignment did not complete; the map can still display geometry at its LiDAR coordinates.",
                    13: "Address enrichment did not complete; buildings remain clickable by building ID.",
                    16: "3D terrain tiles are unavailable; the 2D map remains complete.",
                }[number]
                run.finish(number, "DEGRADED", f"Optional command exited {code}. {note} Inspect run.log.", code)
                continue
            run.finish(number, "FAIL", f"Command exited {code}; dependent outputs were not run. See run.log.", code)
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
                16: terrain_stage_dir / "meta.json",
            }[number]
            if number == 11 and not expected.is_file():
                run.finish(number, "SKIPPED", "No image_shift.json was produced (for example, the survey has no reference imagery); map geometry will render at its source coordinates.", 0)
                continue
            if number in {11, 16} and (not expected.is_file() or expected.stat().st_size == 0):
                run.finish(number, "DEGRADED", f"Optional output missing/empty: {expected.relative_to(ROOT)}. The 2D solar map remains available.", 0)
                continue
            if not expected.is_file() or expected.stat().st_size == 0:
                run.finish(number, "FAIL", f"Command returned success but expected output is missing/empty: {expected.relative_to(ROOT)}. See run.log.", 0)
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
                run.finish(number, "PASS", f"Emitted regional tiles and support data under {region_out.relative_to(ROOT)}.", 0)
            elif number == 15:
                required = ("buildings.pmtiles", "panel_layouts.pmtiles", "building_cells.pmtiles", "assumptions.json",
                            "addresses.json", "building_detail/index.json", "heatmap_tiles/meta.json",
                            "seasonal_curves/index.json", "build_summary.json")
                missing = [rel for rel in required
                           if not (map_data / rel).is_file() or (map_data / rel).stat().st_size == 0]
                if missing:
                    run.finish(number, "FAIL", "Combine omitted required map files: " + ", ".join(missing), 0)
                    failed_at = number
                    break
                if not (map_data / "markup_lines.geojson").exists():
                    (map_data / "markup_lines.geojson").write_text(
                        json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")
                run.finish(number, "PASS", f"Combined region into map data at {map_data.relative_to(ROOT)}.", 0)
            elif number == 16:
                terrain_tiles = list(terrain_stage_dir.rglob("*.png"))
                if terrain_dir.exists():
                    shutil.rmtree(terrain_dir)
                os.replace(terrain_stage_dir, terrain_dir)
                run.finish(number, "PASS" if terrain_tiles else "DEGRADED",
                           f"Wrote {len(terrain_tiles)} 3D terrain tiles." if terrain_tiles else "Terrain metadata exists but no DSM tiles were emitted; 3D view is unavailable.", 0)
            else:
                run.finish(number, "PASS", f"Command succeeded; verified non-empty {expected.relative_to(ROOT)}{feature_note}.", 0)

    if failed_at is None:
        validator = [str(py), "tools/validate_quickstart_map.py",
                     "--region-out", str(region_out), "--map-data", str(map_data)]
        run.begin(17, validator)
        try:
            code, output = run.run_command(17, validator, env=env, begin=False)
        except KeyboardInterrupt:
            raise
        if code != 0:
            run.finish(17, "FAIL", "Map output contract validation failed. Review run.log before opening this dataset.", code)
            failed_at = 17
        else:
            run.finish(17, "PASS", output.strip() or "Map contract validated.", 0)

    if failed_at is None:
        ok, url = _start_preview_server(area, run, py,
                                        args.port or 8765, not args.no_open_browser)
        if not ok:
            failed_at = 18

    if failed_at is not None:
        for s in run.records[failed_at:]:
            if s["status"] == "PENDING":
                s["status"] = "NOT RUN"
                s["comment"] = f"Not run because step {failed_at:02d} failed; dependent outputs are unavailable."
        run._save()
    print(f"\nPipeline report: {run.report_path}\nCombined log: {run.log_path}", flush=True)
    if run.metadata.get("map_preview_url"):
        print(f"Map preview: {run.metadata['map_preview_url']}", flush=True)
    return 1 if failed_at is not None else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Pipeline interrupted; inspect the latest data/pipeline_runs/<run-id>/report.md", file=sys.stderr)
        raise SystemExit(130)
