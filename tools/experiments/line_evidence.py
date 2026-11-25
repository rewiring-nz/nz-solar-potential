"""Does the LiDAR show a fold or a step where a roof line is?
For sample points along drawn lines and along built lines, fit a plane to the
LiDAR returns on each side and measure the fold angle and the height step."""
import json, sys, collections
from pathlib import Path
sys.path.insert(0, 'tools'); sys.path.insert(0, '.')
import numpy as np
import markup_benchmark as M
from shapely.geometry import Polygon, shape
from shapely.ops import transform as tf, unary_union
from shapely.affinity import translate
from pyproj import Transformer
from scipy.spatial import cKDTree
from src.pointcloud_source import PointCloudSource
T = Transformer.from_crs(4326, 2193, always_xy=True).transform
B = json.loads(Path('data/roof_labels.json').read_text())['buildings']
hold = {int(h['id']) for h in json.load(open('data/roof_set.json'))['holdout']}
src = Path(sys.argv[1]); R = float(sys.argv[2]) if len(sys.argv) > 2 else 1.5
built = collections.defaultdict(list)
for bf in src.glob('*_blind.json'):
    for f in json.loads(bf.read_text()).get('features') or []:
        q = f['properties']
        if q.get('kind') == 'facet': built[q['building_id']].append(tf(T, shape(f['geometry'])).buffer(0))
cen = {r['id']: r for r in json.load(open(src / 'census_lidar.json'))}
close = lambda ps: unary_union([q.buffer(0.15) for q in ps]).buffer(-0.15)
pc = PointCloudSource(max_cached_tiles=4)
def plane(p):
    if len(p) < 8: return None
    A = np.c_[p[:, :2], np.ones(len(p))]
    for _ in range(3):
        c, *_ = np.linalg.lstsq(A, p[:, 2], rcond=None)
        r = np.abs(A @ c - p[:, 2]); k = r <= max(0.08, np.percentile(r, 70))
        if k.sum() < 6: break
        A, p = A[k], p[k]
    return c
def samples(lines, step=0.5):
    out = []
    for g in getattr(lines, 'geoms', [lines]):
        if g.geom_type != 'LineString' or g.length < 0.8: continue
        for d in np.arange(0.4, g.length - 0.3, step):
            a, b = g.interpolate(d - 0.2), g.interpolate(d + 0.2); p = g.interpolate(d)
            t = np.array([b.x - a.x, b.y - a.y]); n = np.linalg.norm(t)
            if n < 1e-6: continue
            t /= n; out.append((p.x, p.y, -t[1], t[0]))
    return out
def evid(s, pts, tree):
    x, y, nx, ny = s
    idx = tree.query_ball_point([x, y], R)
    if len(idx) < 16: return None
    q = pts[idx]; d = (q[:, 0] - x) * nx + (q[:, 1] - y) * ny
    a, b = plane(q[d > 0.3]), plane(q[d < -0.3])
    if a is None or b is None: return None
    na, nb = np.array([-a[0], -a[1], 1]), np.array([-b[0], -b[1], 1])
    ang = np.degrees(np.arccos(np.clip(na @ nb / np.linalg.norm(na) / np.linalg.norm(nb), -1, 1)))
    step = abs((a[0] * x + a[1] * y + a[2]) - (b[0] * x + b[1] * y + b[2]))
    return ang, step
res = collections.defaultdict(list)
for bid, got in built.items():
    v = B.get(str(bid)); c = cen.get(bid)
    if bid in hold or not v or not v.get('faces') or not got or not c: continue
    drawn = [p for p in (Polygon(fc['ring']).buffer(0) for fc in v['faces']) if p.area > 0.5]
    outline = close(drawn); gout = close(got); best = (0, 0, 0)
    for i in range(-8, 9):
        for j in range(-8, 9):
            t = translate(outline, i * .25, j * .25); u = t.intersection(gout).area / max(t.union(gout).area, 1e-9)
            if u > best[0]: best = (u, i * .25, j * .25)
    drawn = [translate(p, best[1], best[2]) for p in drawn]; outline = translate(outline, best[1], best[2])
    dl, gl = M._lines(drawn, outline), M._lines(got, gout)
    x0, y0, x1, y1 = gout.union(outline).bounds
    pts = pc.points_in_bbox(x0 - 3, y0 - 3, x1 + 3, y1 + 3, building_only=True)
    if pts is None or len(pts) < 50: continue
    tree = cKDTree(pts[:, :2])
    if dl is not None:
        for s in samples(dl):
            e = evid(s, pts, tree); res['drawn'].append((bid, e))
    if gl is not None:
        from shapely.geometry import Point
        for s in samples(gl):
            e = evid(s, pts, tree); far = dl is None or Point(s[0], s[1]).distance(dl) > 1.5
            res['built_invented' if far else 'built_real'].append((bid, e))
json.dump({k: [(b, None if e is None else [float(e[0]), float(e[1])]) for b, e in v] for k, v in res.items()}, open(src / 'evidence.json', 'w'))
for k, v in res.items():
    m = [e for _, e in v if e is not None]
    print(f"{k:15s} samples {len(v):5d} measurable {len(m):5d}", end=' ')
    for A_, S_ in ((5, .2), (8, .3), (12, .4)):
        print(f"| fold>{A_} or step>{S_}: {100 * sum(e[0] > A_ or e[1] > S_ for e in m) / max(len(m), 1):.0f}%", end=' ')
    print()
