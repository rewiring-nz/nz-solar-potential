"""
Did any building lose roof-face area between two builds?

The v51 deploy gate showed panel drops, but the worst bug that build had --
a face split at a narrow neck and the smaller piece dropped, 230 m2 of
12/16 Kent Street's roof gone -- is clearest as LOST FACE AREA: the
segmentation reads the same roof, so a building's total face area should
barely move between builds whatever the layout code does. This compares a
saved copy of each region's panel_layouts.geojson with the current one.

    # before a build: keep the layouts you are comparing against
    mkdir -p ~/prev_layouts && for r in REGIONS; do \
        cp -n data/regions/$r/panel_layouts.geojson ~/prev_layouts/$r.geojson; done
    # after it:
    python tools/face_area_check.py --before ~/prev_layouts pilot frankton_flats ...

Prints, per region, the buildings whose face area fell by more than
--drop (default 8%), largest first. Exit status 1 if any did.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def face_area(path):
    from shapely.geometry import shape
    from shapely.ops import transform
    from pyproj import Transformer
    to_m = Transformer.from_crs(4326, 2193, always_xy=True).transform
    out = defaultdict(float)
    for f in json.loads(Path(path).read_text())["features"]:
        q = f["properties"]
        if q.get("kind") == "facet" and q.get("building_id") is not None:
            out[int(q["building_id"])] += transform(to_m, shape(f["geometry"])).area
    return out


def main():
    from src.region_build import area_paths
    ap = argparse.ArgumentParser()
    ap.add_argument("regions", nargs="+")
    ap.add_argument("--before", required=True, help="folder of <region>.geojson saved before the build")
    ap.add_argument("--drop", type=float, default=0.08)
    ap.add_argument("--min-m2", type=float, default=20.0)
    a = ap.parse_args()
    total = 0
    for r in a.regions:
        prev = Path(a.before) / f"{r}.geojson"
        if not prev.exists():
            print(f"{r:22s} no saved layout in {a.before}")
            continue
        b, n = face_area(prev), face_area(area_paths(r)["panel_layouts"])
        lost = sorted(((b[k] - n.get(k, 0.0)), k) for k in b
                      if b[k] > a.min_m2 and n.get(k, 0.0) < (1 - a.drop) * b[k])
        total += len(lost)
        worst = [(k, round(x)) for x, k in sorted(lost, reverse=True)[:5]]
        print(f"{r:22s} {len(lost):3d} of {len(b)} lost >{100 * a.drop:.0f}% face area; m2 lost: {worst}")
    print(f"total {total}")
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
