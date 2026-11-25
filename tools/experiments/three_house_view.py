"""Three houses, looked at closely: markup, LiDAR, point-roof result, 3D.
usage: view.py <out dir> <id...>"""
import json, sys, math
from pathlib import Path
sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
import numpy as np, matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt, rasterio
from rasterio.windows import from_bounds
from shapely.geometry import Polygon, LineString, MultiPolygon
from shapely.ops import unary_union
from shapely.affinity import translate
import markup_benchmark as M
from src import point_roof as pr
from src.region_build import area_paths
out = Path(sys.argv[1]); ids = [int(x) for x in sys.argv[2:]]
B = json.loads(Path('data/roof_labels.json').read_text())['buildings']
hold = {int(h['id']) for h in json.load(open('data/roof_set.json'))['holdout']}
img = rasterio.open(area_paths('pilot')['imagery']); OUT = pr.outlines('pilot')
close = lambda ps: unary_union([q.buffer(0.15) for q in ps]).buffer(-0.15)
def lr(a, b, tol):
    if a is None: return 1.0
    if b is None: return 0.0
    return a.intersection(b.buffer(tol)).length / max(a.length, 1e-9)
def run(bid):
    assert bid not in hold
    v = B[str(bid)]
    drawn = [p for p in (Polygon(f['ring']).buffer(0) for f in v['faces']) if p.area > 0.5]
    strokes = [LineString([(p[0], p[1]) for p in l['points']]) for l in (v.get('lines') or []) if len(l.get('points') or []) >= 2]
    r = pr.reconstruct(bid, OUT[bid]); dx, dy = r['shift']
    got = [f['poly'] for f in r['faces']]            # LiDAR frame
    doutl = close(drawn); gout = close(got) if got else None
    # markup (photo frame) onto the LiDAR frame: best outline overlap
    best = (0, 0, 0)
    if gout is not None:
        for i in range(-10, 11):
            for j in range(-10, 11):
                t = translate(doutl, i * .25, j * .25); u = t.intersection(gout).area / max(t.union(gout).area, 1e-9)
                if u > best[0]: best = (u, i * .25, j * .25)
    sx, sy = best[1], best[2]
    dl = M._lines(drawn, doutl)
    if strokes:
        u = unary_union(strokes).difference(doutl.boundary.buffer(M.OUTLINE_BAND_M))
        if dl is not None: u = u.difference(dl.buffer(0.6))
        if not u.is_empty: dl = u if dl is None else unary_union([dl, u])
    dlL = translate(dl, sx, sy) if dl is not None else None
    gl = M._lines(got, gout) if got else None
    sc = {t: (lr(dlL, gl, t), lr(gl, dlL, t)) for t in (0.3, 0.75, 1.5)}
    pts = r['pts']; lab = r.get('lab')
    x0, y0, x1, y1 = r['poly'].buffer(3).bounds; s = max(x1 - x0, y1 - y0); cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    b = (cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2)
    fig = plt.figure(figsize=(20, 10), dpi=80)
    def photo(ax, ox, oy):   # photo drawn in the LiDAR frame by moving the window
        bb = (b[0] - ox, b[1] - oy, b[2] - ox, b[3] - oy)
        px = np.moveaxis(img.read([1, 2, 3], window=from_bounds(*bb, transform=img.transform), boundless=True), 0, -1)
        ax.imshow(px, extent=(b[0], b[2], b[1], b[3]))
    def polys(ax, ps, col, lw=2):
        for p in ps:
            for g in (p.geoms if hasattr(p, 'geoms') else [p]):
                ax.plot(*g.exterior.xy, color=col, lw=lw)
    def lines(ax, l, col, lw=2, ls='-'):
        if l is None: return
        for g in getattr(l, 'geoms', [l]):
            if g.geom_type == 'LineString': ax.plot(*g.xy, color=col, lw=lw, ls=ls)
    ax = fig.add_subplot(2, 4, 1); photo(ax, sx, sy); polys(ax, [translate(p, sx, sy) for p in drawn], '#00e070'); lines(ax, unary_union([translate(q, sx, sy) for q in strokes]) if strokes else None, '#ffe000', 2)
    ax.set_title(f'{bid} markup: {len(drawn)} faces (green), {len(strokes)} strokes (yellow)')
    ax = fig.add_subplot(2, 4, 2); photo(ax, sx, sy); polys(ax, got, '#ff2040'); ax.set_title(f'point roof: {len(got)} faces')
    ax = fig.add_subplot(2, 4, 3)
    nrm = pr.normals(pts - pts.mean(0)); nrm = nrm[0] if isinstance(nrm, tuple) else nrm
    nz = np.abs(nrm[:, 2]); asp = (np.degrees(np.arctan2(nrm[:, 0] * np.sign(nrm[:, 2]), nrm[:, 1] * np.sign(nrm[:, 2]))) % 360) / 360
    slope = np.degrees(np.arccos(np.clip(nz, 0, 1)))
    import matplotlib.colors as mc
    col = mc.hsv_to_rgb(np.c_[asp, np.clip(slope / 25, 0, 1), np.full(len(asp), 0.95)])
    ax.scatter(pts[:, 0], pts[:, 1], c=col, s=5, lw=0); lines(ax, dlL, 'k', 1.5); ax.set_facecolor('#888')
    ax.set_title('LiDAR returns: colour = downhill direction, grey = flat; black = drawn lines')
    ax = fig.add_subplot(2, 4, 4)
    if lab is not None:
        ax.scatter(pts[:, 0], pts[:, 1], c=np.where(lab < 0, -1, lab % 20), cmap='tab20', s=5, lw=0)
    lines(ax, gl, 'r', 2); lines(ax, dlL, 'k', 1.5, '--'); ax.set_title('grown planes; red = built lines, black dashed = drawn')
    for a in fig.axes: a.set_xlim(b[0], b[2]); a.set_ylim(b[1], b[3]); a.set_aspect('equal'); a.set_xticks([]); a.set_yticks([])
    z = pts[:, 2] - pts[:, 2].min()
    for k, (el, az) in enumerate(((35, -60), (35, 30), (35, 120), (35, 210))):
        ax = fig.add_subplot(2, 4, 5 + k, projection='3d')
        ax.scatter(pts[:, 0] - cx, pts[:, 1] - cy, z, c=col, s=4, lw=0, depthshade=False)
        for f in r['faces']:
            a_, b_, c_ = f['plane']; xs, ys = f['poly'].exterior.xy
            ox_, oy_ = r['poly'].centroid.x, r['poly'].centroid.y
            zz = [a_ * (x - ox_) + b_ * (y - oy_) + c_ - (pts[:, 2].min() - 0) + 0 for x, y in zip(xs, ys)]
            ax.plot(np.array(xs) - cx, np.array(ys) - cy, zz, color='r', lw=1.2)
        ax.view_init(el, az); ax.set_box_aspect((1, 1, max(z.max(), 1) / s * 1.0)); ax.set_axis_off()
        ax.set_xlim(-s / 2, s / 2); ax.set_ylim(-s / 2, s / 2); ax.set_title(f'3D from azimuth {az}')
    fig.tight_layout(); fig.savefig(out / f'{bid}.jpg', pil_kwargs={'quality': 85}); plt.close(fig)
    print(bid, v.get('address'), 'faces drawn/built', len(drawn), len(got), 'shift', (sx, sy),
          '| drawn lines found 0.3/0.75/1.5: ' + '/'.join(f'{100 * sc[t][0]:.0f}' for t in sc),
          '| built lines real: ' + '/'.join(f'{100 * sc[t][1]:.0f}' for t in sc), flush=True)
for bid in ids: run(bid)
