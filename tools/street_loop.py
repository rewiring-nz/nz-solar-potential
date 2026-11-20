#!/usr/bin/env python3
"""Re-lay a handful of roofs with the working code and snapshot the result.

The loop for refining panel placement on a small street: the build's own
per-building layout (build_layout_geojson._build_one) and its panel gate
(gate_panels._gate_feature, rerack) run on the listed roofs only, in a
minute or two, and the result is written as a tools/cases_sheet.py snapshot
so cases_sheet.py sheet draws it and mark_faults.py score checks it against
the owner's marks. Panels the gate drops are kept in the snapshot's
"dropped" list with the reason, to show where a ragged array comes from.

    python tools/street_loop.py <label> <cases.json> [--jobs 8]

cases.json: {"cases": [{"id": ..., "region": ...}, ...]} (roof_cases.json form).
Writes data/case_snapshots/<label>.json. Runs where the region's LiDAR and
rasters are (the build VM).
"""
import argparse
import json
import multiprocessing as mp
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


def _one(bid):
    import src.build_layout_geojson as B
    try:
        return bid, B._build_one(bid), None
    except Exception as exc:
        return bid, [], f"{type(exc).__name__}: {exc}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("label")
    ap.add_argument("cases")
    ap.add_argument("--jobs", type=int, default=8)
    a = ap.parse_args()
    import src.build_layout_geojson as B
    import src.gate_panels as G
    from src.pointcloud_source import PointCloudSource
    from src.region_build import area_centroid_wgs84
    from src.register_imagery import drawing_shifts
    from src.solar_model import SolarModel
    hold = {int(h["id"]) for h in json.loads((ROOT / "data/roof_set.json").read_text())["holdout"]}
    by_region = defaultdict(list)
    for c in json.loads(Path(a.cases).expanduser().read_text())["cases"]:
        if int(c["id"]) not in hold:
            by_region[c["region"]].append(int(c["id"]))
    found, why_all = {}, Counter()
    pc = PointCloudSource(max_cached_tiles=G.GATE_CACHED_TILES)
    for region, ids in sorted(by_region.items()):
        c = area_centroid_wgs84(region)
        model = SolarModel() if c is None else SolarModel(*c)
        per = defaultdict(list)
        # spawn, as the build does: forked workers inherit GDAL state and hang
        with ProcessPoolExecutor(min(a.jobs, len(ids)), mp_context=mp.get_context("spawn"),
                                 initializer=B._init_worker, initargs=(region, model)) as ex:
            for fut in as_completed([ex.submit(_one, b) for b in ids]):
                bid, fs, err = fut.result()
                if err:
                    print(f"  {region} #{bid}: {err}")
                per[bid] = fs
        photo_read = G._photo_read(region)
        sh = drawing_shifts(ROOT / "data" / "regions" / region)
        for bid in ids:
            kept, dropped = [], []
            skip = G._has_usable_markup(bid) or bid in photo_read
            for f in per[bid]:
                if f["properties"].get("kind") != "panel" or skip:
                    kept.append(f)
                    continue
                ok, why = G._gate_feature(f, pc)
                if ok:
                    kept.append(f)
                else:
                    dropped.append(dict(why=why, geometry=f["geometry"]))
                    why_all[why] += 1
            n0 = len(kept)
            kept, n_rerack = G.rerack(kept)
            why_all["rerack"] += n_rerack
            found[str(bid)] = dict(region=region, features=kept, dropped=dropped, gate_skipped=bool(skip),
                                   shift=[float(v) for v in (sh.get(str(bid)) or sh.get(bid) or [0, 0, 0])[:2]])
            print(f"  {region} #{bid}: {sum(f['properties'].get('kind') == 'panel' for f in kept)} panels, "
                  f"gate dropped {len(dropped)}, rerack {n_rerack}" + (" (gate skipped)" if skip else ""))
    import cases_sheet as CS
    CS.SNAPS.mkdir(parents=True, exist_ok=True)
    (CS.SNAPS / f"{a.label}.json").write_text(json.dumps(
        dict(label=a.label, shift_source="drawing_shifts", buildings=found)))
    print(a.label, ":", len(found), "roofs; gate drops", dict(why_all))


if __name__ == "__main__":
    main()
