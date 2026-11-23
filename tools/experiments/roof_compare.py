"""Roof-by-roof comparison of two ways of reading the unseen drawn roofs.

A count of "roofs with faces right" over a few dozen roofs moves by chance
(three roofs of 35 is inside the fold-to-fold spread) and hides a change that
helps most roofs a little. This scores every roof on continuous measures and
compares two runs roof by roof:

    roof_compare.py score <heldout maps dir> <regions|regions_own> <out.json>
        per roof: face IoU, line F, drawn faces merged into a neighbour,
        split in two or more, shifted (best read face under 0.8), roof edge IoU
    roof_compare.py compare <a.json> <b.json>
        per class of roof: mean paired difference b - a with a bootstrap 95%
        interval, roofs better and worse by more than 0.02, the sign test

Switches (SOLAR_LINE_*) are read from the environment at `score`, and
SOLAR_MAPS_ALSO=<dir>[,<dir>] averages the maps with those of other runs
that also never saw the roof.
"""
import sys, os, glob, json, numpy as np
sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
NAMES = ["line", "core", "ridge", "valley", "cliff", "outline", "join", "roof"]


def score(maps_dir, method, out):
    from PIL import Image, ImageDraw
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    from scipy.ndimage import distance_transform_edt
    from skimage.morphology import skeletonize
    import src.line_graph as LG
    labels = json.load(open('data/roof_labels.json'))['buildings']
    for k, rings in json.load(open('data/line_set/faces_joined_1.0.json')).items():
        if k in labels and rings:
            labels[k] = dict(labels[k], faces=[{'ring': r} for r in rings])
    also = [d for d in os.environ.get('SOLAR_MAPS_ALSO', '').split(',') if d]

    def raster(polys, size):
        lab = np.zeros((size, size), np.int32)
        for i, q in enumerate(polys, 1):
            im = Image.new('L', (size, size), 0); ImageDraw.Draw(im).polygon(list(q.exterior.coords), fill=1); lab[np.array(im) > 0] = i
        return lab
    rows = {}
    for f in sorted(glob.glob(f'{maps_dir}/*.npz')):
        bid = os.path.basename(f)[:-4]
        if any(not os.path.exists(f'{d}/{bid}.npz') for d in also):
            continue
        d = np.load(f"data/line_set/typed_sharp/hand/{bid}.npz")
        p = np.mean([np.load(g)['maps'].astype(np.float32) / 255 for g in [f] + [f'{d_}/{bid}.npz' for d_ in also]], 0)
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
        edge = outline
        if method == 'regions_own':
            own = LG.roof_outline(p[7])
            edge = own if own is not None and own.is_valid and own.area > 50 else outline
        height = d['image'][..., 7].astype(np.float32) / 255 * 10.0
        try:
            read = [q for q in LG.faces_from_regions(p, NAMES, edge, height=height) if q.is_valid and q.geom_type == 'Polygon']
        except Exception as e:
            print(bid, 'failed', type(e).__name__, e); read = [edge]
        D, R = raster(drawn, size), raster(read, size)
        tot = acc = 0.0; merged = split = shifted = 0
        for i in range(1, len(drawn) + 1):
            m = D == i; a = m.sum()
            if not a:
                continue
            best, bj = 0.0, 0
            for j in np.unique(R[m]):
                if j:
                    r = R == j; v = (m & r).sum() / (m | r).sum()
                    if v > best: best, bj = v, j
            tot += a; acc += a * best
            if best < 0.8:
                r = R == bj; in_r = (m & r).sum() / a; r_in = (m & r).sum() / max(r.sum(), 1)
                if in_r >= 0.85 and r_in < 0.8: merged += 1
                elif r_in >= 0.85 and in_r < 0.8: split += 1
                else: shifted += 1
        edges = lambda L: ((L[:, :-1] != L[:, 1:]) & (L[:, :-1] > 0) & (L[:, 1:] > 0))
        def inner(L):
            e = np.zeros(L.shape, bool); e[:, :-1] |= edges(L); e[:-1, :] |= edges(L.T).T; return skeletonize(e)
        tb, pb = inner(D), inner(R); tol = 0.75 / mpp
        if tb.sum() < 10 and pb.sum() < 10: lf = 1.0
        elif tb.sum() < 10 or pb.sum() < 10: lf = 0.0
        else:
            rc = (distance_transform_edt(~pb)[tb] <= tol).mean(); pr = (distance_transform_edt(~tb)[pb] <= tol).mean(); lf = 2 * pr * rc / (pr + rc) if pr + rc else 0.0
        area = outline.area * mpp * mpp
        ru = unary_union(read)
        rows[bid] = dict(fiou=acc / tot, lf=lf, drawn=len(drawn), read=len(read), merged=merged, split=split, shifted=shifted,
                         edge=outline.intersection(ru).area / max(outline.union(ru).area, 1e-9),
                         cls='larger' if area >= 350 else ('centre home' if str(d['region']) == 'pilot' else 'home'))
    json.dump(rows, open(out, 'w'))
    summary(rows)


def summary(rows):
    for c in ('all', 'home', 'centre home', 'larger'):
        r = [v for v in rows.values() if c == 'all' or v['cls'] == c]
        if r:
            print('%-12s %3d roofs: face IoU %.3f  right %3d  line F %.3f  edge %.3f  merged %3d split %3d shifted %3d  faces drawn %d read %d'
                  % (c, len(r), np.mean([v['fiou'] for v in r]), sum(v['fiou'] >= .8 for v in r), np.mean([v['lf'] for v in r]), np.mean([v['edge'] for v in r]),
                     sum(v['merged'] for v in r), sum(v['split'] for v in r), sum(v['shifted'] for v in r), sum(v['drawn'] for v in r), sum(v['read'] for v in r)))


def compare(fa, fb):
    from math import comb
    A, B = json.load(open(fa)), json.load(open(fb)); ids = sorted(set(A) & set(B)); rng = np.random.default_rng(0)
    for c in ('all', 'home', 'centre home', 'larger'):
        k = [i for i in ids if c == 'all' or A[i]['cls'] == c]
        if not k:
            continue
        out = '%-12s %3d roofs:' % (c, len(k))
        for key in ('fiou', 'lf'):
            d = np.array([B[i][key] - A[i][key] for i in k])
            bs = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(2000)]
            up, dn = int((d > 0.02).sum()), int((d < -0.02).sum()); n = up + dn
            pv = min(1.0, 2 * sum(comb(n, x) for x in range(0, min(up, dn) + 1)) / 2 ** n) if n else 1.0
            out += '  %s %+.3f [%+.3f, %+.3f] better %d worse %d (sign p %.2f)' % ({'fiou': 'face IoU', 'lf': 'line F'}[key], d.mean(), np.percentile(bs, 2.5), np.percentile(bs, 97.5), up, dn, pv)
        out += '  merged %+d split %+d shifted %+d right %+d' % (tuple(sum(B[i][q] for i in k) - sum(A[i][q] for i in k) for q in ('merged', 'split', 'shifted')) + (sum(B[i]['fiou'] >= .8 for i in k) - sum(A[i]['fiou'] >= .8 for i in k),))
        print(out)


if __name__ == '__main__':
    if sys.argv[1] == 'score': score(*sys.argv[2:5])
    else: compare(*sys.argv[2:4])
