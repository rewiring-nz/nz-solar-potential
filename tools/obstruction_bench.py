"""Obstruction detection scored against the hand-marked obstructions.

    python tools/obstruction_bench.py                 # every complete marked roof on this machine
    python tools/obstruction_bench.py --ids 5372604   # just these
    python tools/obstruction_bench.py --save base     # write data/obstruction_bench_base.json
    python tools/obstruction_bench.py --diff base     # compare against a saved run

WHY. src/validate_obstructions.py guards a handful of named over-carve and
under-detect cases; this measures the detector against all of the markup at
once, both directions, split by size -- because small objects (vents,
turbines, flues) are where it fails: on 25 Sep, of 365 marked obstructions
under 1 m2 it found 72 (20%), and of its 454 detections under 1.2 m2 only 67
(15%) touched a marked obstruction.

It runs the real layout stage (build_layout_geojson._build_one) and scores
the DETECTOR's output, which the build computes on every roof and then
replaces with the markup where there is one. Only roofs marked complete are
scored, so a detection with no mark near it is a false one, not an unmarked
object. Markup is generous -- one polygon often covers a cluster of vents --
so area recall understates; the per-object numbers are the ones to move.
Needs each region's point cloud and imagery.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SMALL_MARK_M2 = 1.0      # a marked obstruction this size or smaller is "small"
SMALL_DET_M2 = 1.2       # the colour-only floor; detections below it are "small"
MARK_TOL_M = 0.2         # a small mark counts as found if a detection is this close
DET_TOL_M = 0.3          # a detection counts as right if a mark is this close


def score_roof(bid):
    """Per-roof counts, or None when the roof could not be built."""
    from shapely.ops import unary_union
    import src.build_layout_geojson as blg
    from src.roof_line_source import drawn_obstruction_polys
    det = []
    real = blg.detect_obstructions_combined

    def spy(imagery_ds, pc_source, facet_geom, plane, **kw):
        out = real(imagery_ds, pc_source, facet_geom, plane, **kw)
        det.extend(out)
        return out
    blg.detect_obstructions_combined = spy
    try:
        blg._build_one(bid)
    except Exception:
        return None
    finally:
        blg.detect_obstructions_combined = real
    marked = [m for m in drawn_obstruction_polys(bid) if not m.is_empty]
    D = unary_union(det) if det else None
    M = unary_union(marked) if marked else None
    small = [m for m in marked if m.area <= SMALL_MARK_M2]
    sdet = [d for d in det if d.area <= SMALL_DET_M2]
    return {
        "bid": bid,
        "marked_m2": M.area if M is not None else 0.0,
        "det_m2": D.area if D is not None else 0.0,
        "hit_m2": D.intersection(M).area if (D is not None and M is not None) else 0.0,
        "det_near_m2": D.intersection(M.buffer(DET_TOL_M)).area if (D is not None and M is not None) else 0.0,
        "small": len(small),
        "small_found": sum(1 for m in small if D is not None and m.buffer(MARK_TOL_M).intersects(D)),
        "small_det": len(sdet),
        "small_det_right": sum(1 for d in sdet if M is not None and d.buffer(DET_TOL_M).intersects(M)),
    }


def summarise(rows):
    t = lambda k: sum(r[k] for r in rows)
    return {
        "roofs": len(rows),
        "area_recall": t("hit_m2") / t("marked_m2") if t("marked_m2") else 0.0,
        "area_precision": t("det_near_m2") / t("det_m2") if t("det_m2") else 0.0,
        "small_marks": t("small"), "small_found": t("small_found"),
        "small_dets": t("small_det"), "small_dets_right": t("small_det_right"),
    }


def show(s, label=""):
    print(f"{label}{s['roofs']} complete marked roofs")
    print(f"  by area:   recall {s['area_recall']:.3f}   precision {s['area_precision']:.3f}")
    sm = s["small_marks"] or 1
    sd = s["small_dets"] or 1
    print(f"  small marks (<= {SMALL_MARK_M2} m2): {s['small_found']}/{s['small_marks']} found ({s['small_found'] / sm:.1%})")
    print(f"  small detections (<= {SMALL_DET_M2} m2): {s['small_dets_right']}/{s['small_dets']} on a mark ({s['small_dets_right'] / sd:.1%})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="*", type=int)
    ap.add_argument("--save")
    ap.add_argument("--diff")
    a = ap.parse_args()
    import src.build_layout_geojson as blg
    from src.region_build import area_centroid_wgs84
    from src.solar_model import SolarModel
    from tools.cases import _find_region
    labs = json.loads((ROOT / "data" / "roof_labels.json").read_text())["buildings"]
    ids = a.ids or sorted(int(b) for b, v in labs.items() if v.get("obstructions") and v.get("complete"))
    by_region = {}
    for b in ids:
        r = _find_region(b)
        if r:
            by_region.setdefault(r, []).append(b)
    rows = []
    for region, bids in sorted(by_region.items()):
        c = area_centroid_wgs84(region)
        blg._init_worker(region, SolarModel(*c) if c else SolarModel())
        for b in bids:
            r = score_roof(b)
            if r:
                r["region"] = region
                rows.append(r)
    s = summarise(rows)
    show(s)
    if a.save:
        p = ROOT / "data" / f"obstruction_bench_{a.save}.json"
        p.write_text(json.dumps({"summary": s, "rows": rows}))
        print(f"saved {p.relative_to(ROOT)}")
    if a.diff:
        old = json.loads((ROOT / "data" / f"obstruction_bench_{a.diff}.json").read_text())["summary"]
        show(old, label=f"[{a.diff}] ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
