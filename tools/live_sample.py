#!/usr/bin/env python3
"""Draw a random sample of the live map's roofs for the owner to score.

The release's accuracy figure. Panel counts say nothing about whether a
layout is good (a roof can gain panels and get worse, and one that loses all
of them may now be right), so each release the owner scores a random sample
of the live map out of 10, one drawing each, with fault tags
(tools/review_page.py). The mean score, and how often each fault is ticked,
is the number to track release to release.

The sample is every built roof with roof faces, minus the 130 roofs of
data/roof_set.json (rules are fixed against those, so they would flatter
the score), drawn at random with a seed taken from the release, so the same
release always gives the same sample. Roofs with no panels are kept: "should
have panels" is a fault too.

    tools/live_sample.py --version 72 --n 40 --out data/live_review/v72

Writes <out>/sample.json ({version, seed, ids: [[region, id], ...]}) and
<out>/pairs.json for tools/review_page.py (images as <id>_now.jpg, from
tools/render_built.py --ids ... --tiles <out>/tiles).
"""
import argparse
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIN_FACE_M2 = 40.0   # smaller outlines are garden sheds and carports


def built_roofs(regions_dir):
    """[(region, building_id)] with at least MIN_FACE_M2 of roof faces."""
    out = []
    for f in sorted(Path(regions_dir).glob("*/solar_potential.geojson")):
        for ft in json.loads(f.read_text())["features"]:
            p = ft["properties"]
            if p.get("building_id") is not None and (p.get("facet_area_m2") or 0) >= MIN_FACE_M2:
                out.append((f.parent.name, int(p["building_id"])))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", type=int, required=True, help="the release being scored")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--regions", default=str(ROOT / "data" / "regions"))
    ap.add_argument("--served", help="file of served region names, one a line (default: every region)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    served = set(Path(a.served).read_text().split()) if a.served else None
    rs = json.loads((ROOT / "data" / "roof_set.json").read_text())
    skip = {int(r["id"]) for k in ("dev", "holdout") for r in rs[k]}
    pool = [(r, b) for r, b in built_roofs(a.regions)
            if b not in skip and (served is None or r in served)]
    seed = 7000 + a.version
    pick = sorted(random.Random(seed).sample(pool, min(a.n, len(pool))), key=lambda t: t[1])
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "sample.json").write_text(json.dumps(
        {"version": a.version, "seed": seed, "pool": len(pool), "ids": pick}, indent=1))
    (out / "pairs.json").write_text(json.dumps(
        [{"id": b, "a": f"{b}_now.jpg", "a_label": f"v{a.version}"} for _, b in pick], indent=1))
    print(f"{len(pick)} of {len(pool)} roofs (seed {seed}) -> {out}")
    print(" ".join(str(b) for _, b in pick))


if __name__ == "__main__":
    main()
