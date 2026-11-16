"""Line by line: which drawn lines the built roof has, which it lacks, and
which built lines are not in the markup. Everything in the photo's frame.
usage: diff.py <out.jpg> <id>"""
import json, sys
from pathlib import Path
sys.path.insert(0, '.'); sys.path.insert(0, 'tools'); sys.path.insert(0, 'tools/experiments')
import numpy as np, matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt, rasterio
from rasterio.windows import from_bounds
from shapely.geometry import Polygon, LineString
from shapely.ops import unary_union, linemerge
import markup_benchmark as M, photo_lean as PL
from src import point_roof as pr
from src.region_build import area_paths
out, bid = sys.argv[1], int(sys.argv[2]); TOL = 0.3
hold = {int(h['id']) for h in json.load(open('data/roof_set.json'))['holdout']}; assert bid not in hold
v = json.loads(Path('data/roof_labels.json').read_text())['buildings'][str(bid)]
img = rasterio.open(area_paths('pilot')['imagery']); O = pr.outlines('pilot')
r = pr.reconstruct(bid, O[bid])
import os
if os.environ.get('EDGE') == '1':
    import roof_edge as RE
    from shapely.affinity import translate as _tr
    _p = O[bid] if O[bid].geom_type == 'Polygon' else max(O[bid].geoms, key=lambda g: g.area)
    _e = RE.roof_edge(pr, _tr(_p, *r['shift'])); pr.REGISTER = False; r = pr.reconstruct(bid, _e)
org = (r['poly'].centroid.x, r['poly'].centroid.y)
k = PL.photo_lean(r['faces'], org, img); got = PL.leaned(r['faces'], org, k[0], k[1])
close = lambda ps: unary_union([q.buffer(0.15) for q in ps]).buffer(-0.15)
drawn = [p for p in (Polygon(f['ring']).buffer(0) for f in v['faces']) if p.area > 0.5]
o = close(drawn); dl = M._lines(drawn, o)
st = [LineString([(p[0], p[1]) for p in l['points']]) for l in (v.get('lines') or []) if len(l.get('points') or []) >= 2]
if st:
    u = unary_union(st).difference(o.boundary.buffer(M.OUTLINE_BAND_M))
    if dl is not None: u = u.difference(dl.buffer(0.6))
    if not u.is_empty: dl = u if dl is None else unary_union([dl, u])
gout = close(got); gl = M._lines(got, gout)
def pieces(l, step=0.5):
    o_ = []
    for g in getattr(l, 'geoms', [l]):
        if g.geom_type != 'LineString': continue
        n = max(1, int(round(g.length / step)))
        for i in range(n):
            a, b = g.interpolate(i / n, normalized=True), g.interpolate((i + 1) / n, normalized=True)
            o_.append(LineString([a, b]))
    return o_
dp, gp = pieces(dl), pieces(gl)
dist_d = np.array([p.interpolate(0.5, normalized=True).distance(gl) for p in dp]); dist_g = np.array([p.interpolate(0.5, normalized=True).distance(dl) for p in gp])
L = lambda ps: sum(p.length for p in ps)
print(f"{bid} {v.get('address')} lean k={k[0]:.3f},{k[1]:.3f}")
print(f"drawn lines {L(dp):.0f} m: built line within 0.3 m {100*L([p for p,d in zip(dp,dist_d) if d<=.3])/L(dp):.0f}%, 0.3-0.75 m {100*L([p for p,d in zip(dp,dist_d) if .3<d<=.75])/L(dp):.0f}%, none within 0.75 m {100*L([p for p,d in zip(dp,dist_d) if d>.75])/L(dp):.0f}%")
print(f"built lines {L(gp):.0f} m: drawn line within 0.3 m {100*L([p for p,d in zip(gp,dist_g) if d<=.3])/L(gp):.0f}%, 0.3-0.75 m {100*L([p for p,d in zip(gp,dist_g) if .3<d<=.75])/L(gp):.0f}%, none within 0.75 m {100*L([p for p,d in zip(gp,dist_g) if d>.75])/L(gp):.0f}%")
# outline: drawn roof edge against built roof edge
de, ge = o.boundary, gout.boundary
print(f"roof edge: drawn {de.length:.0f} m, built edge within 0.3 m of {100*de.intersection(ge.buffer(.3)).length/de.length:.0f}%, within 0.75 m {100*de.intersection(ge.buffer(.75)).length/de.length:.0f}%; area drawn {o.area:.0f} m2 built {gout.area:.0f} m2")
x0, y0, x1, y1 = o.union(gout).buffer(2).bounds; s = max(x1 - x0, y1 - y0); cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
b = (cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2)
px = np.moveaxis(img.read([1, 2, 3], window=from_bounds(*b, transform=img.transform), boundless=True), 0, -1)
fig, axs = plt.subplots(1, 3, figsize=(27, 9), dpi=80)
col = lambda d: '#00e060' if d <= .3 else ('#ffd000' if d <= .75 else '#ff2020')
ax = axs[0]; ax.imshow(px, extent=(b[0], b[2], b[1], b[3])); [ax.plot(*g_.exterior.xy, color='white', lw=1) for g_ in getattr(o, 'geoms', [o])]
for p, d in zip(dp, dist_d): ax.plot(*p.xy, color=col(d), lw=3)
ax.set_title('YOUR lines: green = built within 0.3 m, yellow = 0.3-0.75 m, red = not built')
ax = axs[1]; ax.imshow(px, extent=(b[0], b[2], b[1], b[3]))
for g in (gout.geoms if hasattr(gout, 'geoms') else [gout]): ax.plot(*g.exterior.xy, color='white', lw=1)
for p, d in zip(gp, dist_g): ax.plot(*p.xy, color=col(d), lw=3)
ax.set_title('BUILT lines: green = on a drawn line, yellow = near, red = not in your markup')
ax = axs[2]; ax.set_facecolor('#222')
pts = r['pts']; lab = r['lab']; hh = pts[:, 2] - (pts[:, 2].min() - min(f['plane'][2] for f in r['faces']) * 0)   # absolute z
zg = pts[:, 2]; ground = zg.min() - 0
# lean each return by k * height above ground (ground = reconstruct's median ground: plane c is relative to it)
f0 = r['faces'][0]; a_, b_, c_ = f0['plane']; rp = f0['poly'].representative_point()
gz = None
import numpy as _n
# recover ground z: nearest return to a face point has z = ground + plane height
d2 = (pts[:, 0] - rp.x) ** 2 + (pts[:, 1] - rp.y) ** 2; i0 = int(_n.argmin(d2))
gz = pts[i0, 2] - (a_ * (pts[i0, 0] - org[0]) + b_ * (pts[i0, 1] - org[1]) + c_)
h = pts[:, 2] - gz
ax.scatter(pts[:, 0] + k[0] * h, pts[:, 1] + k[1] * h, c=np.where(lab < 0, 19, lab % 19), cmap='tab20', s=14, lw=0)
for g in getattr(dl, 'geoms', [dl]):
    if g.geom_type == 'LineString': ax.plot(*g.xy, color='white', lw=1.5)
[ax.plot(*g_.exterior.xy, color='white', lw=1.5) for g_ in getattr(o, 'geoms', [o])]
ax.set_title('LiDAR returns coloured by the plane they were grown into (leaned to the photo); white = your lines')
for a in axs: a.set_xlim(b[0], b[2]); a.set_ylim(b[1], b[3]); a.set_xticks([]); a.set_yticks([]); a.set_aspect('equal')
fig.tight_layout(); fig.savefig(out, pil_kwargs={'quality': 88})
