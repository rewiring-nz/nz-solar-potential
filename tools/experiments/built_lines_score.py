"""Score a blind build's facets (layout_ab output) against the markup's lines,
each roof slid to its best match. usage: builtscore.py <layout.json> [<layout2.json> ...]"""
import json, sys
from pathlib import Path
sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
import numpy as np
from shapely.geometry import Polygon, LineString, shape
from shapely.ops import unary_union, transform as tf
from shapely.affinity import translate
from pyproj import Transformer
import markup_benchmark as M
T = Transformer.from_crs(4326, 2193, always_xy=True).transform
B = json.loads(Path('data/roof_labels.json').read_text())['buildings']
hold = {int(h['id']) for h in json.load(open('data/roof_set.json'))['holdout']}
close = lambda ps: unary_union([q.buffer(0.15) for q in ps]).buffer(-0.15)
for path in sys.argv[1:]:
    built = {}
    d = json.load(open(path))
    for f in d['features']:
        q = f['properties']
        if q.get('kind') == 'facet': built.setdefault(int(q['building_id']), []).append(tf(T, shape(f['geometry'])).buffer(0))
    a3, a75, n = [], [], 0
    for bid in d.get('ids', built):
        v = B.get(str(bid))
        if bid in hold or not v or not v.get('faces'): continue
        drawn = [p for p in (Polygon(fc['ring']).buffer(0) for fc in v['faces']) if p.area > 0.5]
        o = close(drawn); dl = M._lines(drawn, o)
        st = [LineString([(p[0], p[1]) for p in l['points']]) for l in (v.get('lines') or []) if len(l.get('points') or []) >= 2]
        if st:
            u = unary_union(st).difference(o.boundary.buffer(M.OUTLINE_BAND_M))
            if dl is not None: u = u.difference(dl.buffer(0.6))
            if not u.is_empty: dl = u if dl is None else unary_union([dl, u])
        if dl is None or dl.length < 4: continue
        got = [p for p in built.get(bid, []) if p.area > 0.5]
        gl = M._lines(got, close(got)) if got else None
        if gl is None: a3.append(0.0); a75.append(0.0); continue
        def f1(dd, tol):
            rr = dd.intersection(gl.buffer(tol)).length / dd.length; pp = gl.intersection(dd.buffer(tol)).length / gl.length
            return 2 * rr * pp / max(rr + pp, 1e-9)
        b = (f1(dl, .3), 0, 0)
        for dx in np.arange(-3, 3.01, 0.25):
            for dy in np.arange(-3, 3.01, 0.25):
                s = f1(translate(dl, dx, dy), .3)
                if s > b[0]: b = (s, dx, dy)
        a3.append(b[0]); a75.append(f1(translate(dl, b[1], b[2]), .75))
    a3, a75 = np.array(a3), np.array(a75)
    print(f"{Path(path).name}: {len(a3)} roofs, {d.get('panels')} panels | line F at 0.3 m median {np.median(a3):.2f}, roofs >= 0.7: {int((a3 >= .7).sum())} | at 0.75 m median {np.median(a75):.2f}, roofs >= 0.7: {int((a75 >= .7).sum())}")
