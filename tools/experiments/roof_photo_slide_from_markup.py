"""Is the offset between the LiDAR roof and the markup's photo a lean:
the same metres-per-metre-of-height for neighbouring buildings?
Per marked pilot roof (not held back, <= 20 drawn faces): the slide that best
lays point-roof lines on the drawn lines, the roof's mean height, and the
score before and after."""
import json, sys
from pathlib import Path
sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
import numpy as np
from concurrent.futures import ProcessPoolExecutor
def one(bid):
    try:
        from shapely.geometry import Polygon, LineString
        from shapely.ops import unary_union
        from shapely.affinity import translate
        import markup_benchmark as M
        from src import point_roof as pr
        global _O, _B
        if '_O' not in globals():
            _O = pr.outlines('pilot'); _B = json.loads(Path('data/roof_labels.json').read_text())['buildings']
        v = _B[str(bid)]
        close = lambda ps: unary_union([q.buffer(0.15) for q in ps]).buffer(-0.15)
        drawn = [p for p in (Polygon(f['ring']).buffer(0) for f in v['faces']) if p.area > 0.5]
        o = close(drawn); dl = M._lines(drawn, o)
        st = [LineString([(p[0], p[1]) for p in l['points']]) for l in (v.get('lines') or []) if len(l.get('points') or []) >= 2]
        if st:
            u = unary_union(st).difference(o.boundary.buffer(M.OUTLINE_BAND_M))
            if dl is not None: u = u.difference(dl.buffer(0.6))
            if not u.is_empty: dl = u if dl is None else unary_union([dl, u])
        if dl is None or dl.length < 4: return None
        r = pr.reconstruct(bid, _O[bid])
        if not r['faces']: return None
        got = [f['poly'] for f in r['faces']]; gl = M._lines(got, close(got))
        if gl is None or gl.length < 4: return None
        A = sum(f['poly'].area for f in r['faces']); h = sum(f['poly'].area * (f['plane'][2]) for f in r['faces']) / A
        gb = gl.buffer(0.3); db = dl.buffer(0.3)
        def f1(dx, dy):
            t = translate(dl, -dx, -dy)      # built moved by (dx,dy) == drawn moved by the negative
            rr = t.intersection(gb).length / t.length; pp = gl.intersection(translate(db, -dx, -dy)).length / gl.length
            return 2 * rr * pp / max(rr + pp, 1e-9)
        best = (f1(0, 0), 0.0, 0.0); base = best[0]
        for dx in np.arange(-3, 3.01, 0.25):
            for dy in np.arange(-3, 3.01, 0.25):
                s = f1(dx, dy)
                if s > best[0]: best = (s, dx, dy)
        _, bx, by = best
        for dx in np.arange(bx - 0.1875, bx + 0.19, 0.0625):
            for dy in np.arange(by - 0.1875, by + 0.19, 0.0625):
                s = f1(dx, dy)
                if s > best[0]: best = (s, dx, dy)
        c = r['poly'].centroid
        return dict(id=bid, x=c.x, y=c.y, h=round(h, 2), base=round(base, 3), best=round(best[0], 3), dx=round(float(best[1]), 3), dy=round(float(best[2]), 3),
                    nd=len(drawn), ng=len(got), reg=r['shift'])
    except Exception as e:
        return dict(id=bid, err=repr(e)[:100])
if __name__ == '__main__':
    B = json.loads(Path('data/roof_labels.json').read_text())['buildings']
    hold = {int(h['id']) for h in json.load(open('data/roof_set.json'))['holdout']}
    from src import point_roof as pr
    O = pr.outlines('pilot')
    ids = [int(b) for b, v in B.items() if int(b) not in hold and int(b) in O and v.get('faces') and 2 <= len(v['faces']) <= 20 and not v.get('problem')]
    print(len(ids), 'roofs', flush=True)
    with ProcessPoolExecutor(6) as ex:
        rows = [r for r in ex.map(one, ids) if r]
    json.dump(rows, open(sys.argv[1], 'w'))
    ok = [r for r in rows if 'err' not in r]; print(len(ok), 'scored,', len(rows) - len(ok), 'errors')
    good = [r for r in ok if r['best'] >= 0.6]
    print('line F at 0.3 m, median: as built %.2f, after own slide %.2f; roofs with F>=0.7: %d -> %d of %d' % (
        np.median([r['base'] for r in ok]), np.median([r['best'] for r in ok]), sum(r['base'] >= .7 for r in ok), sum(r['best'] >= .7 for r in ok), len(ok)))
    d = np.array([[r['dx'], r['dy']] for r in good]); h = np.array([r['h'] for r in good]); xy = np.array([[r['x'], r['y']] for r in good])
    print(len(good), 'roofs with a confident slide (F>=0.6): slide length median %.2f m, p90 %.2f m' % (np.median(np.hypot(*d.T)), np.percentile(np.hypot(*d.T), 90)))
    k = d / h[:, None]
    # leave-one-out: predict each roof's slide from its 6 nearest confident neighbours
    from scipy.spatial import cKDTree
    t = cKDTree(xy); _, nn = t.query(xy, k=min(7, len(xy)))
    for name, pred in (('no slide', np.zeros_like(d)), ('neighbours\' median slide', np.array([np.median(d[n[1:]], axis=0) for n in nn])),
                       ('neighbours\' median lean x own height', np.array([np.median(k[n[1:]], axis=0) * h[i] for i, n in enumerate(nn)]))):
        e = np.hypot(*(d - pred).T); print(f'  {name:40s} residual median {np.median(e):.2f} m, p90 {np.percentile(e, 90):.2f} m, within 0.3 m {100 * np.mean(e <= .3):.0f}%')
