"""Can roof faces read from the PHOTO alone beat the hip guess where there is no LiDAR?

The no-LiDAR path (src/synthesize_dsm.py) models every house as a hip at one
pitch. This measures, on roofs where LiDAR does exist, three answers against
the LiDAR truth:

  hip      the current no-LiDAR guess: nearest-wall hip faces, 20 degrees
  photo    faces read from the photo alone -- the existing readers (SAM, the
           line network, the simple-form hypotheses) run with NO points and
           picked on photo evidence only -- each face then facing out over
           its eave, at 20 degrees; a roof the photo cannot read falls back
           to the hip
  layout   the TRUE faces with their true directions but the 20-degree
           pitch: the ceiling a perfect photo reader could reach with a
           guessed pitch

RESULT, 13 Oct 2026 (150 central-Queenstown houses, 60-400 m2, pitched):
sunshine per m2 of roof, median error -- hip 2.5%, photo gable/hip form
2.6%, photo faces (SAM) 3.9%, true layout at 20 degrees 2.0%. The form is
called right on 61 of 100 simple roofs (always-hip: 35) and holds at 0.3 m
(59), but whole-roof sunshine barely depends on gable vs hip at 20 degrees,
so the hip stays the default. LiDAR pitch of these houses: median 20.1
degrees (quartiles 14.0, 24.9) -- the 20-degree guess is right. Under 60 m2,
1 roof in 141 is a hip, which is why src/synthesize_dsm.py now gables them.

Truth is the LiDAR facets of a built layout (planes fitted to the point
cloud; drawn faces where the markup exists). The measure is each roof's
sun-weighted area -- sum over faces of sloped area x annual sunshine on that
slope and aspect (SolarModel.annual_poa_kwh_per_m2) -- which is what the
yield follows before panels are packed; plus the share of roof facing north.

    .venv-sam/bin/python tools/research/photo_only_roofs.py --layouts <file> --n 150
    ... --coarse 3    # the photo resampled to 3x coarser (0.3 m, as at Glenorchy)
"""

import argparse
import json
import math
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
PAD_M = 4.0
PITCH = 20.0


def eave_aspect(face, outline):
    """Compass aspect of a face that slopes down to its eave: the outward
    normal of its longest edge on the outline, else away from the middle."""
    from shapely.geometry import LineString
    rim = outline.exterior.buffer(0.6)
    best, L = None, 0.0
    cs = list(face.exterior.coords)
    for a, b in zip(cs, cs[1:]):
        seg = LineString([a, b])
        on = seg.intersection(rim).length
        if on > L and on > 0.5 * seg.length:
            best, L = (a, b), on
    if best is None:
        c, o = face.centroid, outline.centroid
        dx, dy = c.x - o.x, c.y - o.y
    else:
        (x0, y0), (x1, y1) = best
        nx, ny = (y1 - y0), -(x1 - x0)          # one normal
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        c = face.centroid
        if (mx - c.x) * nx + (my - c.y) * ny < 0:  # point it away from the face
            nx, ny = -nx, -ny
        dx, dy = nx, ny
    return (math.degrees(math.atan2(dx, dy)) + 360) % 360


class _PoaTable:
    """SolarModel.annual_poa_kwh_per_m2, precomputed on a grid."""
    def __init__(self, path):
        self.t = json.loads(Path(path).read_text())

    def annual_poa_kwh_per_m2(self, slope, aspect):
        s = min(60, max(0, int(round(slope))))
        a = int(round((aspect % 360) / 5.0)) * 5 % 360
        return self.t[f"{s},{a}"]


def truth_form(faces):
    """The LiDAR roof's form, coarsely: gable when two big opposed faces hold
    nearly all of it, hip when two opposed pairs each hold a real share."""
    tot = sum(a for a, s, _ in faces if s >= 5) or 1.0
    bins = [0.0] * 4                    # aspect quadrants centred N, E, S, W
    for a, s, asp in faces:
        if s >= 5:
            bins[int(((asp + 45) % 360) // 90)] += a / tot
    pair_ns, pair_ew = bins[0] + bins[2], bins[1] + bins[3]
    if max(pair_ns, pair_ew) >= 0.85:
        big = (bins[0], bins[2]) if pair_ns >= pair_ew else (bins[1], bins[3])
        if min(big) >= 0.25:
            return "gable"
    if min(pair_ns, pair_ew) >= 0.2 and min(bins) >= 0.05:
        return "hip"
    return "other"


FORM_MARGIN = 0.06   # activation a form must lead by to overrule the hip default


def photo_form_faces(geom, P, to_px):
    """Gable or hip, decided per rectangular part from the PHOTO: a gable's
    ridge runs to both ends, a hip's four diagonals run to the corners. Each
    is scored by the line network's activation along exactly those lines;
    a clear winner sets the form, anything else stays a hip.
    Returns (faces, forms)."""
    import numpy as np
    from shapely.geometry import Polygon, LineString
    from shapely.ops import split
    from src.face_candidates import rect_parts
    from src.line_extract import _line_mean
    from src.synthesize_dsm import hip_faces, roof_outline

    def sup(a, b):
        return _line_mean(P, np.array(to_px(*a)), np.array(to_px(*b)))

    faces, forms = [], []
    for part in rect_parts(geom):
        part = roof_outline(part)
        cs = np.array(list(part.minimum_rotated_rectangle.exterior.coords)[:4])
        e0, e1 = cs[1] - cs[0], cs[2] - cs[1]
        (u, L), (v, W) = sorted(((e0 / np.linalg.norm(e0), np.linalg.norm(e0)),
                                 (e1 / np.linalg.norm(e1), np.linalg.norm(e1))),
                                key=lambda t: -t[1])
        c = cs.mean(axis=0)
        hip = [q for q, _ in hip_faces(part)] or [part]
        if W < 3.0 or part.area < 20:
            faces += hip
            forms.append("hip-small")
            continue
        half = max((L - W) / 2, 0.0)
        r0, r1 = c - u * half, c + u * half
        end0, end1 = c - u * L / 2, c + u * L / 2
        s_ends = (sup(r1, end1) + sup(r0, end0)) / 2
        s_hips = (sup(r1, end1 + v * W / 2) + sup(r1, end1 - v * W / 2)
                  + sup(r0, end0 + v * W / 2) + sup(r0, end0 - v * W / 2)) / 4
        if s_ends > s_hips + FORM_MARGIN:
            ridge = LineString([tuple(c - u * L), tuple(c + u * L)])
            try:
                halves = [g for g in split(part, ridge).geoms if g.geom_type == "Polygon" and g.area > 1]
            except Exception:
                halves = []
            if len(halves) >= 2:
                faces += halves
                forms.append("gable")
                continue
        faces += hip
        forms.append("hip" if s_hips >= s_ends + FORM_MARGIN else "hip-default")
    return faces, forms


def sun_area(model, faces):
    """[(plan_area, slope, aspect)] -> sum of sloped area x annual POA."""
    return sum(a / math.cos(math.radians(s)) * model.annual_poa_kwh_per_m2(s, asp)
               for a, s, asp in faces)


def mean_sun(model, faces):
    """Area-weighted sunshine per m2 of roof: orientation and pitch only,
    free of how much of the footprint each answer covers."""
    A = sum(a / math.cos(math.radians(s)) for a, s, _ in faces) or 1.0
    return sun_area(model, faces) / A


def north_share(faces):
    tot = sum(a for a, _, _ in faces) or 1.0
    return sum(a for a, s, asp in faces
               if s >= 5 and min(abs(asp), 360 - abs(asp)) <= 45) / tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layouts", required=True, help="a built panel_layouts.geojson with LiDAR facets")
    ap.add_argument("--region", default="pilot")
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--coarse", type=int, default=1)
    ap.add_argument("--out", default=None)
    ap.add_argument("--poa-table", required=True,
                    help="JSON of SolarModel.annual_poa_kwh_per_m2 on a slope x 5-degree aspect grid "
                         "(the SAM environment has no pvlib)")
    a = ap.parse_args()

    import numpy as np
    import pyproj
    import torch
    import geopandas as gpd
    import rasterio
    import rasterio.windows
    from shapely.geometry import shape
    from shapely.ops import transform as st
    from segment_anything import sam_model_registry, SamPredictor
    from segment_anything.utils.transforms import ResizeLongestSide as _RLS
    from src.region_build import area_paths
    from src.synthesize_dsm import hip_faces, roof_outline, wall_plane
    from src.roof_partition import _slope_aspect
    from src.face_candidates import (sam_faces, line_faces, score_candidate,
                                     evidence_map, hypothesis_faces)
    import train_line_model as T

    _oac = _RLS.apply_coords
    _RLS.apply_coords = lambda self, c, sz: _oac(self, c, sz).astype(np.float32)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    sam = sam_model_registry["vit_b"](checkpoint=str(ROOT / "data/sam_vit_b.pth")).to(device)
    predictor = SamPredictor(sam)

    def load(name, ch):
        ck = torch.load(ROOT / f"data/models/{name}", map_location="cpu", weights_only=False)
        m = T.build_unet(ck.get("pretrained", False), out_channels=ch)
        m.load_state_dict(ck["state_dict"])
        return m.to(device).eval()
    lm, lm6 = load("roof_lines_v5.pt", 3), load("roof_lines_v6.pt", 4)

    model = _PoaTable(a.poa_table)
    to2193 = pyproj.Transformer.from_crs(4326, 2193, always_xy=True).transform
    truth = {}
    for f in json.load(open(a.layouts))["features"]:
        p = f["properties"]
        if p.get("kind") != "facet":
            continue
        g = st(to2193, shape(f["geometry"]))
        truth.setdefault(p["building_id"], []).append((g.area, p["slope_deg"], p["aspect_deg"], g))

    paths = area_paths(a.region)
    gdf = gpd.read_file(paths["dir"] / "building_outlines_dedup.geojson").set_index("building_id", drop=False)
    img = rasterio.open(paths["imagery"])
    # houses: residential footprints, pitched in the LiDAR, faces covering the roof
    cand = []
    for bid, fs in truth.items():
        if bid not in gdf.index:
            continue
        geom = gdf.loc[bid].geometry
        if geom.geom_type != "Polygon" or not (60 <= geom.area <= 400):
            continue
        cover = sum(x[0] for x in fs) / geom.area
        wslope = sum(x[0] * x[1] for x in fs) / max(sum(x[0] for x in fs), 1e-9)
        if cover >= 0.7 and wslope >= 10:
            cand.append(bid)
    cand = sorted(cand)
    rng = np.random.default_rng(1)
    ids = sorted(rng.choice(cand, size=min(a.n, len(cand)), replace=False).tolist())
    print(f"{len(cand)} pitched residential roofs with LiDAR truth; testing {len(ids)}"
          f"{' at 1/' + str(a.coarse) + ' resolution' if a.coarse > 1 else ''}", flush=True)

    rows = []
    empty = np.empty((0, 3))
    for k, bid in enumerate(ids):
        geom = gdf.loc[bid].geometry
        minx, miny, maxx, maxy = geom.bounds
        b = (minx - PAD_M, miny - PAD_M, maxx + PAD_M, maxy + PAD_M)
        win = rasterio.windows.from_bounds(*b, img.transform)
        rgb = np.moveaxis(img.read([1, 2, 3], window=win, boundless=True, fill_value=0), 0, -1).astype("uint8")
        if a.coarse > 1:      # the same scene seen 3x coarser, back on the same grid
            from PIL import Image
            hh, ww = rgb.shape[:2]
            small = Image.fromarray(rgb).resize((max(ww // a.coarse, 1), max(hh // a.coarse, 1)), Image.BILINEAR)
            rgb = np.asarray(small.resize((ww, hh), Image.BILINEAR))
        h, w = rgb.shape[:2]
        if h < 32 or w < 32:
            continue

        def to_px(x, y):
            return ((x - b[0]) / (b[2] - b[0]) * w, (1 - (y - b[1]) / (b[3] - b[1])) * h)

        def inv_px(px2, py2):
            return (b[0] + px2 / w * (b[2] - b[0]), b[1] + (1 - py2 / h) * (b[3] - b[1]))

        def run(m):
            ph, pw = (-h) % 16, (-w) % 16
            x = torch.from_numpy(np.pad(rgb, ((0, ph), (0, pw), (0, 0)))).float().permute(2, 0, 1)[None] / 255.0
            with torch.no_grad():
                return torch.sigmoid(m(x.to(device)))[0].cpu().numpy()[:, :h, :w]
        try:
            f_sam, _ = sam_faces(predictor, rgb, geom, b, empty)
        except Exception:
            f_sam = []
        try:
            f_line, pr = line_faces(lm, device, rgb, geom, b, empty, bid)
        except Exception:
            f_line, pr = [], None
        pr = pr if pr is not None else run(lm)
        ev = pr.max(axis=0)
        p6 = run(lm6)
        if p6.shape[0] >= 4:
            ev = np.maximum(ev, p6[3])
        P = evidence_map(ev, rgb)
        try:
            f_hyp = hypothesis_faces(empty, geom, P, to_px)
            conf = getattr(hypothesis_faces, "last_confidence", 0.0)
        except Exception:
            f_hyp, conf = [], 0.0
        sc = {name: (score_candidate(f, geom, P, to_px, empty, inv_px) if f else 0.0)
              for name, f in (("sam", f_sam), ("line", f_line), ("hypothesis", f_hyp))}
        reading = {"sam": f_sam, "line": f_line, "hypothesis": f_hyp}
        multi = {n: f for n, f in reading.items() if len(f) >= 2}
        pick = max(multi, key=lambda n: sc[n]) if multi else None

        out = roof_outline(geom)
        hip = [(q.area, PITCH, _slope_aspect(wall_plane(wl, 0, 0, PITCH))[1]) for q, wl in hip_faces(out)]
        if pick:
            photo = [(q.area, PITCH, eave_aspect(q, geom)) for q in reading[pick]]
        else:
            photo = hip
        f_form, forms = photo_form_faces(geom, P, to_px)
        form = [(q.area, PITCH, eave_aspect(q, geom)) for q in f_form]
        tr = [(x[0], x[1], x[2]) for x in truth[bid]]
        lay = [(x[0], PITCH, x[2]) for x in truth[bid]]
        T_ = sun_area(model, tr)
        row = {"bid": bid, "area": round(geom.area, 1), "pick": pick or "hip-fallback",
               "score": round(sc.get(pick, 0.0), 3) if pick else 0.0,
               "n_truth": len(tr), "n_photo": len(photo), "n_hip": len(hip),
               "truth_slope": round(sum(x[0] * x[1] for x in tr) / sum(x[0] for x in tr), 1),
               "err_hip": sun_area(model, hip) / T_ - 1, "err_photo": sun_area(model, photo) / T_ - 1,
               "err_layout": sun_area(model, lay) / T_ - 1,
               "err_form": sun_area(model, form) / T_ - 1, "forms": forms,
               "per_m2_hip": mean_sun(model, hip) / mean_sun(model, tr) - 1,
               "per_m2_photo": mean_sun(model, photo) / mean_sun(model, tr) - 1,
               "per_m2_form": mean_sun(model, form) / mean_sun(model, tr) - 1,
               "per_m2_layout": mean_sun(model, lay) / mean_sun(model, tr) - 1,
               "truth_form": truth_form(tr),
               "north_form": north_share(form),
               "north_truth": north_share(tr), "north_hip": north_share(hip), "north_photo": north_share(photo)}
        rows.append(row)
        if (k + 1) % 25 == 0:
            print(f"  {k + 1}/{len(ids)}", flush=True)

    import statistics as S
    def summ(key):
        v = [r[key] for r in rows]
        return (f"median {100 * S.median(v):+.1f}%  median |err| {100 * S.median([abs(x) for x in v]):.1f}%  "
                f"within 10%: {100 * sum(abs(x) <= 0.10 for x in v) / len(v):.0f}%")
    picks = ", ".join("%s %d" % (n, sum(r["pick"] == n for r in rows))
                      for n in ("hypothesis", "line", "sam", "hip-fallback"))
    used = sum(r["pick"] != "hip-fallback" for r in rows)
    print(f"\n{len(rows)} roofs; photo reading used on {used} ({picks})")
    import collections
    fc = collections.Counter(f for r in rows for f in r["forms"])
    print("  photo forms per part: " + ", ".join(f"{k} {v}" for k, v in fc.most_common()))
    for key, label in (("err_hip", "hip guess"), ("err_photo", "photo faces"),
                       ("err_form", "photo gable/hip form"), ("err_layout", "true layout at 20 deg")):
        print(f"  sun-weighted area, {label:22s} {summ(key)}")
    for key, label in (("per_m2_hip", "hip guess"), ("per_m2_photo", "photo faces"),
                       ("per_m2_form", "photo gable/hip form"), ("per_m2_layout", "true layout at 20 deg")):
        print(f"  sunshine per m2,   {label:22s} {summ(key)}")
    gt = collections.Counter(r["truth_form"] for r in rows)
    print("  LiDAR roof forms: " + ", ".join(f"{k} {v}" for k, v in gt.most_common()))
    agree = [r for r in rows if r["truth_form"] in ("gable", "hip")]
    if agree:
        said = lambda r: "gable" if "gable" in r["forms"] else "hip"
        print(f"  form called right on {sum(said(r) == r['truth_form'] for r in agree)}/{len(agree)} "
              f"simple roofs (gable {sum(said(r) == 'gable' == r['truth_form'] for r in agree)}/"
              f"{sum(r['truth_form'] == 'gable' for r in agree)}, hip "
              f"{sum(said(r) == 'hip' == r['truth_form'] for r in agree)}/{sum(r['truth_form'] == 'hip' for r in agree)})")
    for key, label in (("north_hip", "hip guess"), ("north_photo", "photo faces"), ("north_form", "photo form")):
        d = [abs(r[key] - r["north_truth"]) for r in rows]
        print(f"  north-facing share error, {label:12s} median {100 * S.median(d):.0f} points")
    ts = [r["truth_slope"] for r in rows]
    print(f"  LiDAR pitch of these roofs: median {S.median(ts):.1f} deg, quartiles {[round(q, 1) for q in S.quantiles(ts, n=4)]}")
    if a.out:
        Path(a.out).write_text(json.dumps(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
