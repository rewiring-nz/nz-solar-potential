"""Is the offset between built lines (LiDAR frame) and drawn lines (photo
frame) a lean: a displacement proportional to height above ground?
Fit one (kx, ky) metres-per-metre-of-height per house and re-score."""
import json, sys
from pathlib import Path
sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
import numpy as np
from shapely.geometry import Polygon, LineString
from shapely.ops import unary_union
from shapely.affinity import translate
import markup_benchmark as M
from src import point_roof as pr
B = json.loads(Path('data/roof_labels.json').read_text())['buildings']
hold = {int(h['id']) for h in json.load(open('data/roof_set.json'))['holdout']}
OUT = pr.outlines('pilot'); lean = json.load(open('data/regions/pilot/image_shift.json'))
close = lambda ps: unary_union([q.buffer(0.15) for q in ps]).buffer(-0.15)
def lr(a, b, tol):
    if a is None: return 1.0
    if b is None: return 0.0
    return a.intersection(b.buffer(tol)).length / max(a.length, 1e-9)
def drawn_lines(v):
    drawn = [p for p in (Polygon(f['ring']).buffer(0) for f in v['faces']) if p.area > 0.5]
    o = close(drawn); dl = M._lines(drawn, o)
    st = [LineString([(p[0], p[1]) for p in l['points']]) for l in (v.get('lines') or []) if len(l.get('points') or []) >= 2]
    if st:
        u = unary_union(st).difference(o.boundary.buffer(M.OUTLINE_BAND_M))
        if dl is not None: u = u.difference(dl.buffer(0.6))
        if not u.is_empty: dl = u if dl is None else unary_union([dl, u])
    return dl, o
def leaned(r, kx, ky, bx=0, by=0):
    ox, oy = r['poly'].centroid.x, r['poly'].centroid.y; out = []
    for f in r['faces']:
        a, b, c = f['plane']
        mv = lambda ring: [(x + kx * (a * (x - ox) + b * (y - oy) + c) + bx, y + ky * (a * (x - ox) + b * (y - oy) + c) + by) for x, y in ring.coords]
        p = Polygon(mv(f['poly'].exterior), [mv(i) for i in f['poly'].interiors]).buffer(0)
        if p.area > 0.3: out.append(p)
    return out
def score(got, dl):
    gl = M._lines(got, close(got))
    return {t: (lr(dl, gl, t), lr(gl, dl, t)) for t in (0.3, 0.75)}
f1 = lambda rp: 2 * rp[0] * rp[1] / max(rp[0] + rp[1], 1e-9)
for bid in map(int, sys.argv[1:]):
    assert bid not in hold
    r = pr.reconstruct(bid, OUT[bid]); dl, o = drawn_lines(B[str(bid)])
    hs = [f['plane'][2] for f in r['faces']]
    base = score(leaned(r, 0, 0), dl)
    best = (-1, 0, 0)
    for kx in np.arange(-0.4, 0.41, 0.02):
        for ky in np.arange(-0.4, 0.41, 0.02):
            s = f1(score(leaned(r, kx, ky), dl)[0.3])
            if s > best[0]: best = (s, kx, ky)
    _, kx, ky = best; after = score(leaned(r, kx, ky), dl)
    # the alternative: one flat shift for the whole roof, no height term
    bf = (-1, 0, 0)
    for bx in np.arange(-2.5, 2.51, 0.125):
        for by in np.arange(-2.5, 2.51, 0.125):
            s = f1(score(leaned(r, 0, 0, bx, by), dl)[0.3])
            if s > bf[0]: bf = (s, bx, by)
    flat = score(leaned(r, 0, 0, bf[1], bf[2]), dl)
    pct = lambda s: f"found {100*s[0.3][0]:.0f}/{100*s[0.75][0]:.0f} real {100*s[0.3][1]:.0f}/{100*s[0.75][1]:.0f}"
    print(f"{bid} heights {min(hs):.1f}-{max(hs):.1f} m | as built: {pct(base)} | best flat shift ({bf[1]:.2f},{bf[2]:.2f}): {pct(flat)} | best lean k=({kx:.2f},{ky:.2f}) m/m: {pct(after)} | register shift {r['shift']} | image_shift {lean.get(str(bid))}", flush=True)
