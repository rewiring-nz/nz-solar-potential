"""Rebuild a published version's search index (data/addresses.json) from the
LINZ NZ Addresses layer, without re-running the build.

A build indexes every address point (src/add_addresses.py writes
address_points.json, emit_region turns it into addresses.json). Versions built
before that hold one address per building, so an address whose point sits in
no outline, or shares a building with a lower number, cannot be searched for
(v85: 10, 16 and 20 Elspeth Street, Cromwell). This writes the full index for
such a version:

- every LINZ address point within KEEP_M of a building the version indexes
  (the built area; points further out are in places the map has no roofs);
- labelled with LINZ's full address, ", Suburb, Town" kept;
- pointing at a building only where the version already titles a building
  with the same address within MATCH_M of the point, else 0 -- the page
  then opens the outline under the point, if there is one.

Usage:
  python tools/refresh_address_index.py --version 85 --out /tmp/addresses.json
  python tools/refresh_address_index.py --from data/addresses.json --out new.json

It only writes a local file; publishing it is a separate, deliberate step.
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import requests
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.add_addresses import NZ_ADDRESSES_LAYER  # noqa: E402

TILES = "https://storage.googleapis.com/solar-potential-map-tiles"
KEEP_M = 150.0      # a point this far from every indexed building is off the built area
MATCH_M = 60.0      # a title this close with the same label is the same address
CELL_DEG = 0.02     # WFS request cell; split when a cell returns MAX_FEATURES
MAX_FEATURES = 10000


def _wfs(bbox, key):
    params = {"service": "WFS", "version": "1.0.0", "request": "GetFeature",
              "typeName": f"data.linz.govt.nz:layer-{NZ_ADDRESSES_LAYER}",
              "outputFormat": "json", "maxFeatures": MAX_FEATURES,
              "bbox": ",".join(f"{v:.6f}" for v in bbox) + ",EPSG:4326"}
    r = requests.get(f"https://data.linz.govt.nz/services;key={key}/wfs", params=params, timeout=120)
    r.raise_for_status()
    return r.json()["features"]


def fetch_points(cells, key):
    """{address_id: (label, lon, lat)} over the given cells, split until no
    request is truncated at MAX_FEATURES."""
    out = {}
    todo = list(cells)
    while todo:
        b = todo.pop()
        feats = _wfs(b, key)
        if len(feats) >= MAX_FEATURES:
            mx, my = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            todo += [[b[0], b[1], mx, my], [mx, b[1], b[2], my],
                     [b[0], my, mx, b[3]], [mx, my, b[2], b[3]]]
            continue
        for f in feats:
            g, p = f.get("geometry"), f["properties"]
            label = p.get("full_address") or p.get("full_address_ascii")
            if not g or g.get("type") != "Point" or not label:
                continue
            aid = p.get("address_id") or (label, tuple(g["coordinates"][:2]))
            out[aid] = (label, g["coordinates"][0], g["coordinates"][1])
    return out


def build(old_rows, points):
    lat0 = math.radians(np.mean([r[2] for r in old_rows]))
    scale = np.array([math.cos(lat0) * 111320.0, 111132.0])
    old_xy = np.array([[r[1], r[2]] for r in old_rows]) * scale
    tree = cKDTree(old_xy)
    rows = []
    for label, lon, lat in points.values():
        xy = np.array([lon, lat]) * scale
        d, _ = tree.query(xy)
        if d > KEEP_M:
            continue
        short = label.split(",")[0].strip()
        bid = 0
        for i in tree.query_ball_point(xy, MATCH_M):
            if old_rows[i][0] == short or old_rows[i][0] == label:
                bid = old_rows[i][3]
                break
        rows.append([label, round(lon, 6), round(lat, 6), bid])
    rows.sort()
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--version", help="published data version, e.g. 85")
    src.add_argument("--from", dest="from_file", help="a local addresses.json")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.version:
        r = requests.get(f"{TILES}/v{a.version}/data/addresses.json", timeout=120)
        r.raise_for_status()
        old = r.json()
    else:
        old = json.loads(Path(a.from_file).read_text())
    from src.project_secrets import linz_api_key
    key = linz_api_key()
    if not key:
        raise SystemExit("LINZ_API_KEY not set and not readable from Secret Manager")
    cells = sorted({(math.floor(r[1] / CELL_DEG), math.floor(r[2] / CELL_DEG)) for r in old})
    bboxes = [[i * CELL_DEG, j * CELL_DEG, (i + 1) * CELL_DEG, (j + 1) * CELL_DEG] for i, j in cells]
    print(f"{len(old)} indexed buildings; fetching LINZ addresses over {len(bboxes)} cells")
    points = fetch_points(bboxes, key)
    rows = build(old, points)
    n0 = sum(1 for r in rows if not r[3])
    old_labels = {r[0] for r in old}
    new = sum(1 for r in rows if r[0].split(",")[0].strip() not in old_labels)
    print(f"{len(rows)} addresses ({n0} on no indexed building; {new} not searchable before)")
    Path(a.out).write_text(json.dumps(rows, separators=(",", ":")))
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
