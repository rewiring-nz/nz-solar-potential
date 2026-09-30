"""Push the combined, served data set to the tiles bucket, under a version.

    python tools/publish_served.py 42              # -> gs://rewiring-solar-tiles/v42/data/
    python tools/publish_served.py 42 --public     # ...and grant public read (idempotent)
    python tools/publish_served.py 42 --keep 3     # ...and delete all but the newest 3 versions
    python tools/publish_served.py --list          # what the bucket holds

Then in site-config.js:  dataBase: "https://storage.googleapis.com/rewiring-solar-tiles/v42/",
and dataVersion: "42". The page prefixes every data URL with dataBase
(preview.html, DATA_BASE), so nothing else changes. tools/deploy_from_vm.sh
does all of this.

WHY A VERSION FOLDER. Tiles are fetched by byte range and cached by the
browser; replacing a file in place under a URL the page already holds is how
a client ends up reading the header of one build and the tiles of another.
A new version is a new folder, never changed after upload -- so its files are
served with a year-long cache -- and rolling back is pointing site-config.js
at an older folder. --keep holds the newest N for exactly that.

WHY A SEPARATE BUCKET. gs://rewiring-solar-data holds models and build inputs
and must stay private. gs://rewiring-solar-tiles holds only what the page
serves, has CORS for Range requests, and is the one that is made public.

WHY NOT GITHUB. The repository reached 2.3 GB with 42 copies of the panel
tiles in its history, and the panel tiles alone passed GitHub's 50 MB warning
(58 MB at v42; 100 MB is refused). Pages caps a site at 1 GB. The code and the
page stay on GitHub; the data lives here.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BUCKET = "gs://rewiring-solar-tiles"
PUBLIC_BASE = "https://storage.googleapis.com/rewiring-solar-tiles"
SERVED_FILES = ["panel_layouts.pmtiles", "buildings.pmtiles", "building_cells.pmtiles",
                "addresses.json", "assumptions.json", "seasonal_curves.json",
                "build_summary.json", "markup_lines.geojson"]
SERVED_DIRS = ["building_detail", "heatmap_tiles", "addresses", "seasonal_curves", "summaries"]
# The 3D terrain tiles do not change between builds: copied inside the bucket
# from the previous version when it has them, uploaded only the first time.
STATIC_DIRS = ["terrain"]
CACHE = "public, max-age=31536000, immutable"


def _run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def versions():
    """Version numbers present in the bucket, ascending."""
    r = subprocess.run(["gcloud", "storage", "ls", f"{BUCKET}/"], capture_output=True, text=True)
    return sorted(int(m.group(1)) for m in re.finditer(r"/v(\d+)/", r.stdout))


def publish(version):
    base = f"{BUCKET}/v{version}/data"
    missing = [f for f in ("panel_layouts.pmtiles", "buildings.pmtiles", "building_cells.pmtiles")
               if not (DATA / f).exists()]
    if missing:
        raise SystemExit(f"not a combined build: missing {missing} -- run src/combine_regions.py first")
    for f in SERVED_FILES:
        p = DATA / f
        if p.exists():
            _run(["gcloud", "storage", "cp", "-q", f"--cache-control={CACHE}", str(p), f"{base}/{f}"])
    for d in SERVED_DIRS:
        p = DATA / d
        if p.is_dir():
            _run(["gcloud", "storage", "rsync", "-r", "-q", f"--cache-control={CACHE}",
                  str(p), f"{base}/{d}"])
    older = [v for v in versions() if v < int(version)]
    for d in STATIC_DIRS:
        if older:
            _run(["gcloud", "storage", "rsync", "-r", "-q",
                  f"{BUCKET}/v{older[-1]}/data/{d}", f"{base}/{d}"])
        elif (DATA / d).is_dir():
            _run(["gcloud", "storage", "rsync", "-r", "-q", f"--cache-control={CACHE}",
                  str(DATA / d), f"{base}/{d}"])
    return f"{PUBLIC_BASE}/v{version}/"


def prune(keep, protect=()):
    """Delete all but the newest `keep` versions, never one in `protect`."""
    vs = versions()
    for v in vs[:-keep] if keep > 0 else []:
        if v in protect:
            continue
        _run(["gcloud", "storage", "rm", "-r", "-q", f"{BUCKET}/v{v}/"])
        print(f"  deleted v{v}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version", nargs="?")
    ap.add_argument("--public", action="store_true",
                    help="grant allUsers objectViewer on the bucket (idempotent)")
    ap.add_argument("--keep", type=int, default=0,
                    help="after publishing, delete all but the newest N versions")
    ap.add_argument("--protect", type=int, action="append", default=[],
                    help="a version --keep must not delete (the one live now)")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        print("versions in bucket:", ", ".join(f"v{v}" for v in versions()) or "none")
        return 0
    if not a.version:
        ap.error("version required")
    url = publish(a.version)
    if a.public:
        _run(["gcloud", "storage", "buckets", "add-iam-policy-binding", BUCKET,
              "--member=allUsers", "--role=roles/storage.objectViewer", "-q"], capture_output=True)
        print("public read granted on", BUCKET)
    if a.keep:
        prune(a.keep, protect=set(a.protect) | {int(a.version)})
    print(f"served set at {url}")
    print(f'site-config.js:  dataBase: "{url}",  dataVersion: "{a.version}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
