"""End to end, no markup used to place the roof: point roof -> lean read off
the photo -> leaned faces, scored against the markup's lines at 0.3 m."""
import json, sys
from pathlib import Path
sys.path.insert(0, '.'); sys.path.insert(0, 'tools'); sys.path.insert(0, 'tools/experiments')
import numpy as np
from concurrent.futures import ProcessPoolExecutor
def one(row):
    import rasterio
    from shapely.geometry import Polygon, LineString
    from shapely.ops import unary_union
    import markup_benchmark as M, photo_lean as PL
    from src import point_roof as pr
    from src.region_build import area_paths
    global _O, _B, _I
    if '_O' not in globals():
        _O = pr.outlines('pilot'); _B = json.loads(Path('data/roof_labels.json').read_text())['buildings']; _I = rasterio.open(area_paths('pilot')['imagery'])
    bid = row['id']; v = _B[str(bid)]
    close = lambda ps: unary_union([q.buffer(0.15) for q in ps]).buffer(-0.15)
    drawn = [p for p in (Polygon(f['ring']).buffer(0) for f in v['faces']) if p.area > 0.5]
    o = close(drawn); dl = M._lines(drawn, o)
    st = [LineString([(p[0], p[1]) for p in l['points']]) for l in (v.get('lines') or []) if len(l.get('points') or []) >= 2]
    if st:
        u = unary_union(st).difference(o.boundary.buffer(M.OUTLINE_BAND_M))
        if dl is not None: u = u.difference(dl.buffer(0.6))
        if not u.is_empty: dl = u if dl is None else unary_union([dl, u])
    r = pr.reconstruct(bid, _O[bid]); org = (r['poly'].centroid.x, r['poly'].centroid.y)
    k = PL.photo_lean(r['faces'], org, _I)
    def f1(polys, tol=0.3):
        gl = M._lines(polys, close(polys))
        if gl is None: return 0.0
        rr = dl.intersection(gl.buffer(tol)).length / dl.length; pp = gl.intersection(dl.buffer(tol)).length / gl.length
        return 2 * rr * pp / max(rr + pp, 1e-9)
    asb = [f['poly'] for f in r['faces']]
    ln = PL.leaned(r['faces'], org, k[0], k[1]) if k else asb
    return dict(id=bid, k=k, base=f1(asb), lean=f1(ln), lean75=f1(ln, 0.75), base75=f1(asb, 0.75), oracle=row['best'], h=row['h'], addr=v.get('address'))
if __name__ == '__main__':
    rows = [r for r in json.load(open(sys.argv[1])) if 'err' not in r]
    with ProcessPoolExecutor(6) as ex: out = list(ex.map(one, rows))
    json.dump(out, open(Path(sys.argv[1]).with_name('e2e.json'), 'w'))
    for name in ('base', 'lean', 'oracle'):
        v = np.array([r[name] for r in out]); print(f"{name:7s} line F at 0.3 m: median {np.median(v):.2f}, roofs >= 0.7: {int((v >= .7).sum())} of {len(v)}, >= 0.5: {int((v >= .5).sum())}")
    for name in ('base75', 'lean75'):
        v = np.array([r[name] for r in out]); print(f"{name:7s} line F at 0.75 m: median {np.median(v):.2f}, roofs >= 0.7: {int((v >= .7).sum())} of {len(v)}")
    for b in (4734696, 4725546, 4735292):
        for r in out:
            if r['id'] == b: print(b, r['addr'], 'as built %.2f, leaned by photo %.2f, best slide by markup %.2f, k=%s' % (r['base'], r['lean'], r['oracle'], r['k']))
