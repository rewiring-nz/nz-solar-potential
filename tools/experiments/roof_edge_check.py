"""Point roof on the surveyed outline against point roof on the roof edge
from the returns; both leaned by the photo; scored against the markup."""
import json, sys
from pathlib import Path
sys.path.insert(0, '.'); sys.path.insert(0, 'tools'); sys.path.insert(0, 'tools/experiments')
import numpy as np
from concurrent.futures import ProcessPoolExecutor
def one(row):
    import rasterio
    from shapely.geometry import Polygon, LineString
    from shapely.ops import unary_union
    from shapely.affinity import translate
    import markup_benchmark as M, photo_lean as PL, roof_edge as RE
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
    def f1(a, b, tol=0.3):
        if a is None or b is None: return 0.0
        rr = a.intersection(b.buffer(tol)).length / a.length; pp = b.intersection(a.buffer(tol)).length / b.length
        return 2 * rr * pp / max(rr + pp, 1e-9)
    res = dict(id=bid, addr=v.get('address'))
    poly = _O[bid]
    if poly.geom_type == 'MultiPolygon': poly = max(poly.geoms, key=lambda g: g.area)
    pr.REGISTER = True; r0 = pr.reconstruct(bid, poly)
    for name in ('outline', 'edge'):
        try:
            if name == 'outline': r = r0
            else:
                e = RE.roof_edge(pr, translate(poly, *r0['shift']))
                pr.REGISTER = False; r = pr.reconstruct(bid, e); pr.REGISTER = True
            org = (r['poly'].centroid.x, r['poly'].centroid.y)
            k = PL.photo_lean(r['faces'], org, _I); ln = PL.leaned(r['faces'], org, k[0], k[1]) if k else [f['poly'] for f in r['faces']]
            go = close(ln); gl = M._lines(ln, go)
            res[name] = dict(lines=f1(dl, gl), lines75=f1(dl, gl, .75), edge=f1(o.boundary, go.boundary), edge75=f1(o.boundary, go.boundary, .75), area=go.area / o.area, n=len(ln),
                             iou=go.intersection(o).area / go.union(o).area)
        except Exception as ex:
            res[name] = dict(err=repr(ex)[:120])
    return res
if __name__ == '__main__':
    rows = [r for r in json.load(open(sys.argv[1])) if 'err' not in r]
    if len(sys.argv) > 2: rows = [r for r in rows if r['id'] in {int(x) for x in sys.argv[2:]}]
    with ProcessPoolExecutor(6) as ex: out = list(ex.map(one, rows))
    json.dump(out, open(Path(sys.argv[1]).with_name('e2e2.json'), 'w'))
    for name in ('outline', 'edge'):
        ok = [r[name] for r in out if 'err' not in r[name]]; print(name, len(ok), 'roofs,', len(out) - len(ok), 'errors', [r[name]['err'] for r in out if 'err' in r[name]][:2])
        for key in ('lines', 'lines75', 'edge', 'edge75', 'iou'):
            v = np.array([r[key] for r in ok]); print(f"   {key:8s} median {np.median(v):.2f}  roofs >= 0.7: {int((v >= .7).sum())}")
    for r in out:
        if r['id'] in (4734696, 4725546, 4735292): print(r['id'], r['addr'], {n: {k: round(x, 2) for k, x in r[n].items() if k != 'err'} for n in ('outline', 'edge')})
