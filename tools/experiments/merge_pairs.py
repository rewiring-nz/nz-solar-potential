"""Pairs of neighbouring regions of the reader's maps, with what the drawing says of each: one face or two.

The converter cuts the roof at every crest of the line maps and then joins
regions back by fixed thresholds on one or two measures (line strength,
height off the other's plane). This writes, for every drawn roof read by a
model that never saw it, every pair of neighbouring regions before any
joining, the measures of the boundary between them, and the answer from the
drawing (both regions lie in the same drawn face, or in different ones), so
that the joining rule can be fitted to the drawing and tested on roofs kept
out of the fit.

    python tools/experiments/merge_pairs.py <heldout maps dir> <out.npz>
"""
import sys, os, glob, json, numpy as np
sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
FEATURES = ["line_mean", "line_med", "line_p25", "line_min", "line_max", "gap_share", "len_m", "len_rel", "core_mean",
            "ridge", "valley", "cliff", "has_planes", "off_max", "off_min", "own_res", "normal_deg", "step_m", "area_min_m2", "area_max_m2", "slope_dif"]


def pair_features(lab, barrier, ch, height, mpp):
    """{(i, j): feature vector} for every pair of neighbouring regions of `lab`."""
    from scipy.ndimage import binary_erosion
    import src.line_graph as LG
    has = height > 0
    acc = {}
    for a, b, sl in ((lab[:, :-1], lab[:, 1:], (slice(None), slice(None, -1), slice(None), slice(1, None))),
                     (lab[:-1, :], lab[1:, :], (slice(None, -1), slice(None), slice(1, None), slice(None)))):
        m = (a != b) & (a > 0) & (b > 0)
        A = lambda x: np.maximum(x[sl[0], sl[1]], x[sl[2], sl[3]])[m]
        lo, hi = np.minimum(a[m], b[m]), np.maximum(a[m], b[m])
        cols = np.c_[A(barrier), A(ch["core"]), A(ch["ridge"]), A(ch["valley"]), A(ch["cliff"])]
        h0, h1 = height[sl[0], sl[1]][m], height[sl[2], sl[3]][m]
        for l, h, row, z0, z1 in zip(lo.tolist(), hi.tolist(), cols, h0, h1):
            acc.setdefault((l, h), []).append((*row, z0, z1))
    size = np.bincount(lab.ravel())
    planes = {}

    def plane(i):
        if i not in planes:
            m = binary_erosion(lab == i, iterations=LG.PL_ERODE_PX) & has
            ys, xs = np.nonzero(m)
            if len(ys) < 60:
                planes[i] = None
            else:
                k = LG._plane(ys, xs, height[ys, xs])
                planes[i] = (ys, xs, height[ys, xs], k, float(np.median(np.abs(xs * k[0] + ys * k[1] + k[2] - height[ys, xs]))))
        return planes[i]
    out = {}
    for (i, j), rows in acc.items():
        r = np.asarray(rows, np.float32); line = r[:, 0]
        pi, pj = plane(i), plane(j)
        if pi is not None and pj is not None:
            off = lambda p, q: float(np.median(np.abs(p[1] * q[3][0] + p[0] * q[3][1] + q[3][2] - p[2])))
            o1, o2 = off(pi, pj), off(pj, pi)
            n1 = np.array([-pi[3][0] / mpp, -pi[3][1] / mpp, 1.0]); n2 = np.array([-pj[3][0] / mpp, -pj[3][1] / mpp, 1.0])
            ang = float(np.degrees(np.arccos(np.clip(n1 @ n2 / np.linalg.norm(n1) / np.linalg.norm(n2), -1, 1))))
            sd = abs(float(np.degrees(np.arctan(np.hypot(*n1[:2]))) - np.degrees(np.arctan(np.hypot(*n2[:2])))))
            pl = [1.0, min(max(o1, o2), 3.0), min(min(o1, o2), 3.0), min(max(pi[4], pj[4]), 1.0), ang, sd]
        else:
            pl = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        both = (r[:, 5] > 0) & (r[:, 6] > 0)
        step = float(np.median(np.abs(r[both, 5] - r[both, 6]))) if both.sum() >= 3 else 0.0
        amin, amax = sorted((size[i] * mpp * mpp, size[j] * mpp * mpp))
        out[(i, j)] = [float(line.mean()), float(np.median(line)), float(np.percentile(line, 25)), float(line.min()), float(line.max()), float((line < 0.2).mean()),
                       len(line) * mpp, len(line) / max(np.sqrt(min(size[i], size[j])), 1.0), float(r[:, 1].mean()),
                       float(r[:, 2].mean()), float(r[:, 3].mean()), float(r[:, 4].mean()), pl[0], pl[1], pl[2], pl[3], pl[4], min(step, 3.0), min(amin, 500.0), min(amax, 2000.0), pl[5]]
    return out


def main(maps_dir, out):
    from PIL import Image, ImageDraw
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    from rasterio import features
    import src.line_graph as LG
    NAMES = ["line", "core", "ridge", "valley", "cliff", "outline", "join", "roof"]
    J = json.load(open('data/line_set/faces_joined_side.json'))
    X, Y, R = [], [], []
    for f in sorted(glob.glob(f'{maps_dir}/*.npz')):
        bid = os.path.basename(f)[:-4]
        if bid not in J:
            continue
        d = np.load(f"data/line_set/typed_sharp/hand/{bid}.npz"); p = np.load(f)['maps'].astype(np.float32) / 255
        mpp, b0, b1, b2, b3, sx, sy = [float(v) for v in d['meta']]; size = d['weight'].shape[0]
        dx, dy = (sx, sy) if str(d['region']) == 'pilot' else (0.0, 0.0)
        px = lambda x, y: ((x + dx - b0) / (b2 - b0) * size, (1 - (y + dy - b1) / (b3 - b1)) * size)
        drawn = sorted([Polygon([px(*c) for c in r]).buffer(0) for r in J[bid] if len(r) >= 3], key=lambda q: -q.area)
        drawn = [max(q.geoms, key=lambda g: g.area) if q.geom_type == 'MultiPolygon' else q for q in drawn if not q.is_empty]
        if len(drawn) < 1:
            continue
        D = np.zeros((size, size), np.int32)
        for i, q in enumerate(drawn, 1):
            im = Image.new('L', (size, size), 0); ImageDraw.Draw(im).polygon(list(q.exterior.coords), fill=1); D[np.array(im) > 0] = i
        outline = unary_union([q.buffer(0.3) for q in drawn]).buffer(-0.3)
        outline = max(outline.geoms, key=lambda g: g.area) if outline.geom_type == 'MultiPolygon' else outline
        inside = features.rasterize([outline], out_shape=(size, size)).astype(bool)
        ch = {n: p[i] for i, n in enumerate(NAMES)}
        barrier = np.maximum.reduce([ch[k] for k in ("line",) + LG.KINDS])
        height = d['image'][..., 7].astype(np.float32) / 255 * 10.0
        lab = LG._basins(barrier, inside, merge_at=0.0)
        if lab is None:
            continue
        own, pure = {}, {}
        for i in np.unique(lab[lab > 0]):
            c = np.bincount(D[lab == i]); c[0] = 0
            if c.sum():
                own[i] = int(c.argmax()); pure[i] = c.max() / (lab == i).sum()
        for (i, j), v in pair_features(lab, barrier, ch, height, mpp).items():
            if pure.get(i, 0) < 0.7 or pure.get(j, 0) < 0.7:
                continue
            X.append(v); Y.append(int(own[i] == own[j])); R.append(bid)
    np.savez_compressed(out, X=np.asarray(X, np.float32), y=np.asarray(Y, np.int8), roof=np.asarray(R), names=np.asarray(FEATURES))
    print(len(Y), 'pairs on', len(set(R)), 'roofs; one face:', int(np.sum(Y)), 'two faces:', int(len(Y) - np.sum(Y)))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
