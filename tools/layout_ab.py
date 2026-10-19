"""
Judge a layout change on a few hundred real roofs in minutes, not a rebuild.

Runs the build's own per-building function (build_layout_geojson._build_one:
segmentation, every geometry stage, obstructions, panels, yield) on a sample
of one region's roofs, then scores the result with tools/layout_health.py.
Run it twice with a change switched off and on (each change has an
environment switch) and compare. The quick preview tool (preview_sample.py)
skips the geometry stages, so it cannot judge a change made in them.

    python tools/layout_ab.py arrowtown_millbrook --n 200
    SOLAR_STRAIGHTEN_FACES=0 python tools/layout_ab.py pilot --n 200 --out off.json
    python tools/layout_ab.py pilot --ids 4725488 4725584 --out on.json

The sample: --ids, or --n roofs drawn half from the region's worst roofs on
the health check of its shipped layout and half at random (seeded, so two
runs see the same roofs).
"""

import argparse
import json
import multiprocessing as mp
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


def _sample(region, n, seed=7):
    import layout_health as LH
    from src.region_build import area_paths
    shipped = LH.check_region(region)
    worst = [r["id"] for r in shipped["worst"]]
    lay = json.loads(Path(area_paths(region)["panel_layouts"]).read_text())["features"]
    every = sorted({int(f["properties"]["building_id"]) for f in lay
                    if f["properties"].get("kind") == "facet"})
    rng = random.Random(seed)
    pick = worst[: n // 2]
    rest = [b for b in every if b not in set(pick)]
    pick += rng.sample(rest, min(n - len(pick), len(rest)))
    return sorted(pick), shipped


def _one(bid):
    import src.build_layout_geojson as B
    t = time.time()
    try:
        return bid, B._build_one(bid), time.time() - t
    except Exception as exc:
        return bid, [], f"{type(exc).__name__}: {exc}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("region")
    ap.add_argument("--ids", nargs="*", type=int)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--out")
    a = ap.parse_args()
    import layout_health as LH
    import src.build_layout_geojson as B
    from src.region_build import area_centroid_wgs84
    from src.solar_model import SolarModel
    ids, shipped = (a.ids, None) if a.ids else _sample(a.region, a.n)
    c = area_centroid_wgs84(a.region)
    model = SolarModel() if c is None else SolarModel(*c)
    feats, errs, t0 = [], [], time.time()
    # spawn, as the build does: forking after GDAL has opened the rasters
    # left every worker idle on a lock.
    with ProcessPoolExecutor(a.jobs, mp_context=mp.get_context("spawn"),
                             initializer=B._init_worker, initargs=(a.region, model)) as ex:
        for fut in as_completed([ex.submit(_one, b) for b in ids]):
            bid, fs, dt = fut.result()
            if isinstance(dt, str):
                errs.append((bid, dt))
            feats.extend(fs)
    res = LH.check_region(a.region, features=feats)
    res["ids"] = ids
    res["errors"] = errs
    res["features"] = feats if a.out else None
    print(f"{a.region}: {len(ids)} roofs in {time.time() - t0:.0f}s, {res['panels']} panels, "
          f"{100 * res['clean_share']:.1f}% clean, {len(errs)} errors")
    for k, v in res["rates"].items():
        print(f"  {k:22s} {v}")
    if a.out:
        Path(a.out).write_text(json.dumps(res))


if __name__ == "__main__":
    main()
