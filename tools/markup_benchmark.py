"""How close the pipeline's own reading of a roof comes to the roof Josh drew.

    python tools/markup_benchmark.py run   [--jobs N] [--out DIR]   # build blind
    python tools/markup_benchmark.py score [--out DIR]              # compare

Josh hand-marked 144 roofs (data/roof_labels.json). Those roofs normally use
his drawing directly, so they never tested the pipeline. `run` builds each one
with SOLAR_HIDE_LABELS=1 -- as if nobody had drawn it -- through
tools/layout_ab.py, and `score` compares what came out with what he drew:

  face IoU      for each drawn face, the best overlap (intersection over union)
                with any built face; area-weighted over the roof
  line recall   share of his drawn ridge/hip/valley lines (edges two faces
                share) with a built line within LINE_TOL_M
  line precision  share of built lines with one of his within LINE_TOL_M
  line F        2PR/(P+R): one number for "the roof lines are right"
  obstruction precision  share of built obstruction area on or near one he drew
  obstruction recall     share of his drawn obstruction area the build marks too
                (both on roofs he marked complete)
  panels        blind count beside the count the drawn roof gets (live layout)

A change to the roof pipeline is judged by these numbers before renders and
before a full build (Josh, 24 Oct: "we need major improvements in accuracy").
Markup was traced on a photo that leans off the LiDAR by up to ~1.5 m, and the
built roof is drawn in whichever frame the build uses, so each roof's markup
is first slid (within SHIFT_MAX_M) to where its outline best overlaps the
built outline; the outline match itself is reported as outline IoU.
"""
import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LINE_TOL_M = 0.75
SHIFT_MAX_M = 2.0
SHIFT_STEP_M = 0.25
OUTLINE_BAND_M = 0.4
SEAM_GAP_M = 0.2
VOID = {"absent", "not_building", "unclear"}


def labelled():
    B = json.loads((ROOT / "data" / "roof_labels.json").read_text())["buildings"]
    out = defaultdict(list)
    for bid, v in B.items():
        if not v.get("faces") or (v.get("problem") in VOID) or not v.get("area"):
            continue
        out[v["area"]].append(int(bid))
    return out, B


def run(a):
    out_dir = Path(a.out); out_dir.mkdir(parents=True, exist_ok=True)
    regions, _ = labelled()
    env = dict(os.environ, SOLAR_HIDE_LABELS="1")
    for r, ids in sorted(regions.items()):
        sf = out_dir / f"{r}_ids.json"; sf.write_text(json.dumps(ids))
        cmd = [sys.executable, "tools/layout_ab.py", r, "--jobs", str(a.jobs),
               "--sample-file", str(sf), "--out", str(out_dir / f"{r}_blind.json")]
        p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
        tail = [l for l in p.stdout.splitlines() if "roofs in" in l]
        print(r, len(ids), tail[-1] if tail else p.stderr[-300:], flush=True)


def _lines(polys, outline=None):
    """The roof's lines: edges two faces share (ridges, hips, valleys, steps).
    Not the outline, and not the edge of a hole or of a gap between faces."""
    from shapely.ops import unary_union
    seams = []
    for i, p in enumerate(polys):
        for q in polys[i + 1:]:
            if p.distance(q) > SEAM_GAP_M:
                continue
            s = p.boundary.intersection(q.buffer(SEAM_GAP_M))
            if not s.is_empty and s.length > 0.3:
                seams.append(s)
    if not seams:
        return None
    u = unary_union(seams)
    if outline is not None:
        u = u.difference(outline.boundary.buffer(OUTLINE_BAND_M))
    return u if not u.is_empty and u.length > 0.3 else None


def score(a):
    from shapely.geometry import Polygon, shape
    from shapely.ops import transform as tf, unary_union
    from pyproj import Transformer
    T = Transformer.from_crs(4326, 2193, always_xy=True).transform
    out_dir = Path(a.out)
    regions, B = labelled()
    from shapely.affinity import translate
    live = {}
    rows = []
    for r, ids in sorted(regions.items()):
        bf = out_dir / f"{r}_blind.json"
        if not bf.exists():
            continue
        built = defaultdict(lambda: {"facet": [], "obstruction": [], "panel": 0})
        for f in json.loads(bf.read_text()).get("features") or []:
            q = f["properties"]; k = q.get("kind")
            if k in ("facet", "obstruction"):
                built[q["building_id"]][k].append(tf(T, shape(f["geometry"])))
            elif k == "panel":
                built[q["building_id"]]["panel"] += 1
        lf = (Path(a.live) / f"{r}.geojson") if a.live else (ROOT / "data" / "regions" / r / "panel_layouts.geojson")
        if lf and lf.exists():
            for f in json.loads(lf.read_text())["features"]:
                q = f["properties"]
                if q.get("kind") == "panel" and q.get("building_id") in ids:
                    live[q["building_id"]] = live.get(q["building_id"], 0) + 1
        for bid in ids:
            v = B[str(bid)]
            drawn = []
            for fc in v["faces"]:
                try:
                    p = Polygon([(x, y) for x, y in fc["ring"]]).buffer(0)
                except Exception:
                    continue
                if p.area > 0.5:
                    drawn.append(p)
            got = [p.buffer(0) for p in built[bid]["facet"] if p.area > 0.5]
            if not drawn:
                continue
            # closed over hairline gaps between faces, or a seam with a gap
            # along it reads as outline
            close = lambda ps: unary_union([q.buffer(0.15) for q in ps]).buffer(-0.15)
            outline = close(drawn)
            gout = close(got) if got else None
            dx = dy = 0.0
            oiou = 0.0
            if gout is not None:
                k = int(SHIFT_MAX_M / SHIFT_STEP_M)
                for i in range(-k, k + 1):
                    for j in range(-k, k + 1):
                        t = translate(outline, i * SHIFT_STEP_M, j * SHIFT_STEP_M)
                        u = t.intersection(gout).area / max(t.union(gout).area, 1e-9)
                        if u > oiou:
                            oiou, dx, dy = u, i * SHIFT_STEP_M, j * SHIFT_STEP_M
                drawn = [translate(p, dx, dy) for p in drawn]
                outline = translate(outline, dx, dy)
            area = sum(p.area for p in drawn)
            iou = 0.0
            for d in drawn:
                best = max((d.intersection(g).area / max(d.union(g).area, 1e-9) for g in got), default=0.0)
                iou += best * d.area
            iou /= area
            dl, gl = _lines(drawn, outline), _lines(got, gout if gout is not None else outline)
            if dl is None and gl is None:
                rec = prec = 1.0
            elif dl is None:
                rec, prec = 1.0, 0.0
            elif gl is None:
                rec, prec = 0.0, 1.0
            else:
                rec = dl.intersection(gl.buffer(LINE_TOL_M)).length / max(dl.length, 1e-9)
                prec = gl.intersection(dl.buffer(LINE_TOL_M)).length / max(gl.length, 1e-9)
            lf1 = 2 * rec * prec / max(rec + prec, 1e-9)
            dobs = []
            for o in v.get("obstructions") or []:
                try:
                    dobs.append(translate(Polygon([(x, y) for x, y in o["ring"]]).buffer(0), dx, dy))
                except Exception:
                    pass
            gobs = unary_union(built[bid]["obstruction"]) if built[bid]["obstruction"] else None
            # recall: how much of what he drew the build also marks (complete roofs)
            dsum = unary_union(dobs) if dobs else None
            orec_hit = orec_all = 0.0
            if v.get("complete") and dsum is not None and dsum.area > 0.1:
                orec_all = dsum.area
                orec_hit = dsum.intersection(gobs.buffer(0.5)).area if gobs is not None else 0.0
            if gobs is None or gobs.area < 0.1 or not v.get("complete"):
                oprec = None   # only roofs he marked complete have every obstruction drawn
            else:
                near = unary_union(dobs).buffer(0.5) if dobs else None
                oprec = (gobs.intersection(near).area / gobs.area) if near is not None else 0.0
            rows.append({"id": bid, "region": r, "address": v.get("address"), "faces_drawn": len(drawn),
                         "faces_built": len(got), "outline_iou": round(oiou, 3), "shift": [dx, dy], "face_iou": round(iou, 3), "line_recall": round(rec, 3),
                         "line_precision": round(prec, 3), "line_f": round(lf1, 3),
                         "obstruction_precision": None if oprec is None else round(oprec, 3),
                         "obstruction_m2_built": round(gobs.area, 1) if gobs is not None else 0.0,
                         "obstruction_m2_drawn": round(orec_all, 1), "obstruction_m2_found": round(orec_hit, 1),
                         "panels_blind": built[bid]["panel"], "panels_drawn": live.get(bid)})
    if not rows:
        print("nothing to score -- run first"); return
    n = len(rows)
    mean = lambda k: sum(r[k] for r in rows if r[k] is not None) / max(1, sum(1 for r in rows if r[k] is not None))
    obs_area = sum(r["obstruction_m2_built"] for r in rows if r["obstruction_precision"] is not None)
    obs_good = sum(r["obstruction_m2_built"] * r["obstruction_precision"] for r in rows if r["obstruction_precision"] is not None)
    pb = sum(r["panels_blind"] for r in rows); pd = sum(r["panels_drawn"] or 0 for r in rows if r["panels_drawn"] is not None)
    pb_matched = sum(r["panels_blind"] for r in rows if r["panels_drawn"] is not None)
    summary = {"roofs": n, "outline_iou": round(mean("outline_iou"), 3), "face_iou": round(mean("face_iou"), 3), "line_recall": round(mean("line_recall"), 3),
               "line_precision": round(mean("line_precision"), 3), "line_f": round(mean("line_f"), 3),
               "obstruction_precision_by_area": round(obs_good / obs_area, 3) if obs_area else None,
               "obstruction_recall_by_area": round(sum(r["obstruction_m2_found"] for r in rows)
                                                   / max(sum(r["obstruction_m2_drawn"] for r in rows), 1e-9), 3),
               "panels_blind_vs_drawn": f"{pb_matched} vs {pd}" if pd else None,
               "roofs_lines_right(F>=0.7)": sum(1 for r in rows if r["line_f"] >= 0.7),
               "roofs_faces_right(IoU>=0.8)": sum(1 for r in rows if r["face_iou"] >= 0.8)}
    (out_dir / "score.json").write_text(json.dumps({"summary": summary, "roofs": rows}, indent=1))
    print(json.dumps(summary, indent=1))
    print("worst 15 by line F:")
    for r in sorted(rows, key=lambda r: r["line_f"])[:15]:
        print(f"  {r['id']} {r['region']:20s} {str(r['address'])[:24]:24s} faces {r['faces_drawn']}->{r['faces_built']}  "
              f"IoU {r['face_iou']:.2f}  lines F {r['line_f']:.2f} (R {r['line_recall']:.2f} P {r['line_precision']:.2f})  "
              f"obstr prec {r['obstruction_precision']}  panels {r['panels_blind']} vs {r['panels_drawn']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["run", "score"])
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--out", default=str(Path.home() / "ab" / "markup_bench"))
    ap.add_argument("--live", default=None, help="dir of <region>.geojson layouts for the drawn roofs' panel counts (default: the built regions)")
    a = ap.parse_args()
    (run if a.what == "run" else score)(a)


if __name__ == "__main__":
    main()
