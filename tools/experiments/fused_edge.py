"""The roof edge from three sources at once: the surveyed outline, the LiDAR
returns and the map's photo.

THE RULE UNDER TEST. Each source is trusted for what it measures well.
  - The surveyed outline says which building and which way its walls run.
  - The returns say where the roof stops along each wall, to about half a
    return spacing, in the LiDAR's frame (src/roof_edge.fit_edges), and how
    high the eave is there.
  - The photo says where that eave is drawn: each wall is put where the
    returns have it, moved by the building's lean times that wall's own
    eave height (src/photo_lean), and then slid along its normal, up to
    SNAP_MAX_M, onto the photo edge that runs along it, when one stands out
    (SNAP_Z) from the offsets round it. A wall with no clear photo edge
    stays where the returns and the lean put it.
The faces inside move by the lean at each corner's own height
(photo_lean.leaned), so a ridge moves further than an eave.

Drawn per building over the map's tiles: surveyed outline (yellow), the
build's roof edge today, moved as one piece by the lean at mean height
(cyan), and the fused edge with its leaned faces (red). Walls the photo
moved are listed with their slide.

  .venv/bin/python tools/experiments/fused_edge.py <out.jpg> <id> [<id> ...]
"""
import glob
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")
SNAP_MAX_M = 0.8
SNAP_STEP_M = 0.05
SNAP_Z = 3.0            # the photo edge's score, in robust standard deviations of the offsets tried
SNAP_COST = 1.5         # standard deviations per metre slid: the near edge wins a tie
WALL_MIN_M = 1.5
SLIDE_CHECK_M = 1.0     # a slide onto the returns this long is checked against the photo
LEAN_OUTLIER = 0.2      # m per m of height from the neighbours' median lean (p90 among sharp pilot roofs: 0.13)
VIEW_PAD_M = 6.0


def wall_height(faces, origin, a, b):
    """Median height above ground of the roof along the wall a-b."""
    from shapely.geometry import Point
    cx, cy = origin
    hs = []
    L = float(np.hypot(*(b - a)))
    for d in np.arange(0.2, max(L - 0.2, 0.21), 0.5):
        p = a + (b - a) * d / L
        q = Point(*p)
        h = [f["plane"][0] * (p[0] - cx) + f["plane"][1] * (p[1] - cy) + f["plane"][2]
             for f in faces if f["poly"].distance(q) < 0.4]
        if h:
            hs.append(max(h))
    return float(np.median(hs)) if hs else None


def corners(c, lines):
    """Polygon corners from each wall's moved line (point, direction)."""
    n = len(lines)
    out = []
    for i in range(n):
        (p0, t0), (p1, t1) = lines[i - 1], lines[i]
        cr = t0[0] * t1[1] - t0[1] * t1[0]
        if abs(cr) < 0.15:
            out.append((p0 + t0 * ((p1 - p0) @ t0) + p1) / 2)
        else:
            s = ((p1 - p0)[0] * t1[1] - (p1 - p0)[1] * t1[0]) / cr
            q = p0 + t0 * s
            out.append(q if np.hypot(*(q - c[i])) < 4.0 else p1)
    return out


def _edge_faces(pr, RE, PL, bid, outline, photo):
    from shapely.geometry import Polygon
    from shapely.geometry.polygon import orient
    rec = pr.reconstruct(bid, outline)
    if not rec["faces"]:
        return None
    edge, moves = RE.fit_edges(pr, rec["poly"])
    faces = RE.extend_to_edge(rec["faces"], edge) if moves is not None else rec["faces"]
    if moves is None:
        edge = rec["poly"]
    edge = orient(Polygon(edge.exterior), 1.0)
    origin = (rec["poly"].centroid.x, rec["poly"].centroid.y)
    return dict(edge=edge, faces=faces, origin=origin, slide=rec["shift"],
                k=PL.photo_lean(faces, origin, photo) or (0.0, 0.0, 0.0))


def fuse(pr, RE, PL, bid, outline, photo, neighbours_k=None):
    """dict(edge_lidar, faces, k, fused, moved, notes) or None.

    TWO CHECKS AGAINST THE PHOTO AND THE NEIGHBOURS (18 Nov).
    (1) A slide onto the returns of SLIDE_CHECK_M or more is kept only when
    the roof it gives matches the photo's edges at least as sharply as the
    roof built without it (3 Kerry Drive #4735154: slid (-1.5, 2.5) m at
    sharpness 1.1, unslid 1.4; 7 Leeds Lane #4750959: slid 5.0, unslid 4.4).
    (2) A lean is one camera's view of a neighbourhood. One at the limit of
    the search, or over LEAN_OUTLIER from the median lean of the sharp roofs
    round it, is a shadow or gutter edge that won, and the neighbours'
    median is used (Frankton #4721959: (0.4, -0.4) m per m, the corner of
    the search, 2.6 m off the roof along its own shadow). `neighbours_k`:
    () -> (kx, ky) or None."""
    from scipy import ndimage as ndi
    from shapely.geometry import Polygon
    notes = []
    r0 = _edge_faces(pr, RE, PL, bid, outline, photo)
    if r0 is None:
        return None
    if float(np.hypot(*r0["slide"])) >= SLIDE_CHECK_M:
        keep = pr.REGISTER
        try:
            pr.REGISTER = False
            r1 = _edge_faces(pr, RE, PL, bid, outline, photo)
        finally:
            pr.REGISTER = keep
        if r1 is not None and r1["k"][2] > r0["k"][2]:
            notes.append("slide of %.1f m dropped" % float(np.hypot(*r0["slide"])))
            r0 = r1
    edge, faces, origin, k = r0["edge"], r0["faces"], r0["origin"], r0["k"]
    if neighbours_k is not None:
        at_limit = max(abs(k[0]), abs(k[1])) >= PL.K_MAX - 1e-6
        nk = neighbours_k()
        if nk is not None and (at_limit or float(np.hypot(k[0] - nk[0], k[1] - nk[1])) > LEAN_OUTLIER):
            notes.append("lean from neighbours")
            k = (float(nk[0]), float(nk[1]), 0.0)
        elif at_limit:
            notes.append("lean at the search limit, no neighbours: not leaned")
            k = (0.0, 0.0, 0.0)
    kv = np.array(k[:2])
    x0, y0, x1, y1 = edge.buffer(VIEW_PAD_M).bounds
    img = np.asarray(photo(x0, y0, x1, y1), dtype=np.float32).mean(0)
    H, W = img.shape
    sx, sy = (x1 - x0) / W, (y1 - y0) / H
    g = ndi.gaussian_filter(img, PL.SIGMA_PX)
    gx, gy = ndi.sobel(g, axis=1), -ndi.sobel(g, axis=0)
    c = np.array(edge.exterior.coords)[:-1]
    n = len(c)
    offs = np.arange(-SNAP_MAX_M, SNAP_MAX_M + 1e-9, SNAP_STEP_M)
    hs = [wall_height(faces, origin, c[i], c[(i + 1) % n]) for i in range(n)]
    hm = float(np.median([h for h in hs if h is not None])) if any(h is not None for h in hs) else 0.0
    lines, moved = [], []
    for i in range(n):
        a, b = c[i], c[(i + 1) % n]
        t = b - a
        L = float(np.hypot(*t))
        t = t / max(L, 1e-9)
        nrm = np.array([t[1], -t[0]])
        h = hs[i] if hs[i] is not None else hm
        base = a + kv * h
        d_best = 0.0
        if L >= WALL_MIN_M:
            al = np.arange(0.3, L - 0.3 + 1e-9, 0.2)
            P = base[None, :] + al[:, None] * t[None, :]
            sc = []
            for d in offs:
                Q = P + nrm * d
                col = np.clip(((Q[:, 0] - x0) / sx).astype(int), 0, W - 1)
                row = np.clip(((y1 - Q[:, 1]) / sy).astype(int), 0, H - 1)
                sc.append(np.abs(gx[row, col] * nrm[0] + gy[row, col] * nrm[1]).mean())
            sc = np.array(sc)
            mad = np.median(np.abs(sc - np.median(sc))) * 1.4826 + 1e-9
            z = (sc - np.median(sc)) / mad
            j = int(np.argmax(z - SNAP_COST * np.abs(offs)))
            if z[j] >= SNAP_Z:
                d_best = float(offs[j])
                if abs(d_best) >= 0.1:
                    moved.append((i, round(L, 1), round(d_best, 2), round(float(z[j]), 1)))
        lines.append((base + nrm * d_best, t))
    fused = Polygon(corners(c, lines)).buffer(0)
    if fused.is_empty or fused.geom_type != "Polygon" or not (0.7 * edge.area < fused.area < 1.4 * edge.area):
        fused = None
    return dict(edge_lidar=edge, faces=faces, origin=origin, k=k, hm=hm, fused=fused, moved=moved, n_walls=n, notes=notes)


if __name__ == "__main__":
    import geopandas as gpd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from shapely.affinity import translate
    from src import photo_lean as PL, point_roof as pr, roof_edge as RE
    from src.register_imagery import RES_M, _Tiles

    out, ids = sys.argv[1], [int(a) for a in sys.argv[2:]]
    where = {}
    for p in sorted(glob.glob("data/regions/*/building_outlines.geojson")):
        g = gpd.read_file(p)
        for b in ids:
            if b not in where and (g.building_id == b).any():
                where[b] = (Path(p).parent, g[g.building_id == b].geometry.iloc[0])
    cols = 3
    rows = (len(ids) + cols - 1) // cols
    fig, ax = plt.subplots(rows, cols, figsize=(8.5 * cols, 8.5 * rows), squeeze=False)
    for a in ax.ravel():
        a.axis("off")
    for num, b in enumerate(ids):
        a = ax.ravel()[num]
        if b not in where:
            a.set_title(f"{b}: no local data"); continue
        d, geom = where[b]
        tiles = _Tiles(d / "display_tiles")

        def photo(x0, y0, x1, y1):
            return tiles.window((x0, y0, x1, y1), int(round((y1 - y0) / RES_M)), int(round((x1 - x0) / RES_M)))
        try:
            r = fuse(pr, RE, PL, b, geom, photo)
        except Exception as exc:
            a.set_title(f"{b}: failed {exc!r}"[:90]); print(b, "failed", repr(exc), flush=True); continue
        if r is None:
            a.set_title(f"{b}: no faces from the returns"); print(b, "no faces", flush=True); continue
        x0, y0, x1, y1 = geom.buffer(VIEW_PAD_M + 2).bounds
        a.imshow(np.moveaxis(np.asarray(photo(x0, y0, x1, y1)), 0, -1), extent=(x0, x1, y0, y1))
        for p in getattr(geom, "geoms", [geom]):
            a.plot(*p.exterior.xy, color="yellow", lw=1.2)
        flat = translate(r["edge_lidar"], r["k"][0] * r["hm"], r["k"][1] * r["hm"])
        a.plot(*flat.exterior.xy, color="cyan", lw=1.4)
        for p in PL.leaned(r["faces"], r["origin"], r["k"][0], r["k"][1]):
            a.plot(*p.exterior.xy, color="red", lw=0.9)
        if r["fused"] is not None:
            a.plot(*r["fused"].exterior.xy, color="red", lw=2.4)
        a.set_xlim(x0, x1); a.set_ylim(y0, y1)
        a.set_title(f"{b} {d.name}: lean {r['k'][0]:+.2f},{r['k'][1]:+.2f} m/m (sharp {r['k'][2]:.1f}), "
                    f"{len(r['moved'])} of {r['n_walls']} walls moved by the photo", fontsize=10)
        print(b, d.name, "lean", [round(v, 2) for v in r["k"]], "walls", r["n_walls"], "moved", r["moved"],
              "fused" if r["fused"] is not None else "NO FUSED POLYGON", flush=True)
    plt.tight_layout(); plt.savefig(out, dpi=60)
