"""Roof edge alone on the 100 reference roofs: which edge rule puts the
roof's outline on the drawn one (the ledger's outline test: overlap 0.90 or
more and 95% of each edge within 0.5 m of the other).

    edge_probe.py <maps dir> [out.json]

Rules scored per roof: the reader's own edge, the surveyed outline slid
onto the reader's edge (line_graph.slide_outline), the build's choice
between them (line_graph.best_edge), and each of those with every side
moved on its own onto the edge map (line_graph.fit_sides).
"""
import sys, os, glob, json
import numpy as np
sys.path.insert(0, '.')
from PIL import Image, ImageDraw
from shapely.geometry import Polygon, shape
from shapely.ops import unary_union
from shapely.affinity import affine_transform
from scipy.ndimage import distance_transform_edt
import src.line_graph as LG

maps_dir = sys.argv[1]
ids = {r['id'] for r in json.load(open('data/roof100.json'))['roofs']}
labels = json.load(open('data/roof_labels.json'))['buildings']
for k, rings in json.load(open('data/line_set/faces_joined_side.json')).items():
    if k in labels and rings:
        labels[k] = dict(labels[k], faces=[{'ring': r} for r in rings])
surv = {}
for g in glob.glob('data/regions/*/building_outlines.geojson'):
    for ft in json.load(open(g))['features']:
        k = str(ft['properties'].get('building_id') or ft['properties'].get('id') or ft.get('id'))
        if k in ids:
            surv.setdefault(k, shape(ft['geometry']))


def rim(g, size):
    im = Image.new('L', (size, size), 0); dr = ImageDraw.Draw(im)
    for q in getattr(g, 'geoms', [g]):
        dr.line(list(q.exterior.coords), fill=1, width=1)
    return np.array(im) > 0


def judge(outline, e, size, mpp):
    if e is None or e.is_empty:
        return dict(ok=False, iou=0, found=0, real=0)
    t5 = 0.5 / mpp
    near = lambda a, b: float((distance_transform_edt(~b)[a] <= t5).mean()) if a.sum() else 1.0
    ob, rb = rim(outline, size), rim(e, size)
    iou = outline.intersection(e).area / max(outline.union(e).area, 1e-9)
    f, r = near(ob, rb), near(rb, ob)
    return dict(ok=bool(iou >= 0.9 and f >= 0.95 and r >= 0.95), iou=iou, found=f, real=r)


rows = {}
for f in sorted(glob.glob(f'{maps_dir}/*.npz')):
    bid = os.path.basename(f)[:-4]
    if bid not in ids:
        continue
    d = np.load(f"data/line_set/typed_sharp/hand/{bid}.npz")
    p = np.load(f)['maps'].astype(np.float32) / 255
    mpp, b0, b1, b2, b3, sx, sy = [float(v) for v in d['meta']]; size = d['weight'].shape[0]
    dx, dy = (sx, sy) if str(d['region']) == 'pilot' else (0.0, 0.0)
    px = lambda x, y: ((x + dx - b0) / (b2 - b0) * size, (1 - (y + dy - b1) / (b3 - b1)) * size)
    drawn = [Polygon([px(*c) for c in fc['ring']]).buffer(0) for fc in labels[bid]['faces'] if fc.get('ring') and len(fc['ring']) >= 3]
    drawn = [max(q.geoms, key=lambda g: g.area) if q.geom_type == 'MultiPolygon' else q for q in drawn if not q.is_empty]
    drawn = [q for q in drawn if q.area * mpp * mpp >= 1.0]
    if not drawn:
        continue
    outline = unary_union([q.buffer(0.3) for q in drawn]).buffer(-0.3)
    outline = max(outline.geoms, key=lambda g: g.area) if outline.geom_type == 'MultiPolygon' else outline
    own = LG.roof_outline(p[7], p[5])
    own = own if own is not None and own.is_valid and own.area > 50 else None
    fit = choice = None
    o = surv.get(bid)
    if o is not None:
        o = affine_transform(o, [size / (b2 - b0), 0, 0, -size / (b3 - b1), -b0 / (b2 - b0) * size, size + b1 / (b3 - b1) * size]).buffer(0)
        o = max(o.geoms, key=lambda g: g.area) if o.geom_type == 'MultiPolygon' else o
        fit, _ = LG.slide_outline(o, p[5], p[7], 2.5 / mpp, 0.25 / mpp)
        choice = LG.best_edge(o, own, p[5], p[7], 1.0 / mpp)
    choice = choice or own
    cand = dict(own=own, fit=fit, choice=choice)
    if hasattr(LG, 'fit_sides'):
        for k in ('fit', 'choice'):
            cand[k + '+sides'] = LG.fit_sides(cand[k], p[5], 1.0 / mpp) if cand[k] is not None else None
    rows[bid] = {k: judge(outline, v, size, mpp) for k, v in cand.items()}

keys = list(next(iter(rows.values())))
print(len(rows), 'roofs; edge right of', len(rows))
for k in keys:
    print('  %-14s %3d   mean IoU %.3f  found %.3f  real %.3f' % (k, sum(r[k]['ok'] for r in rows.values()),
          np.mean([r[k]['iou'] for r in rows.values()]), np.mean([r[k]['found'] for r in rows.values()]), np.mean([r[k]['real'] for r in rows.values()])))
if len(sys.argv) > 2:
    json.dump(rows, open(sys.argv[2], 'w'))
