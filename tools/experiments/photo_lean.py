"""Where a LiDAR-built roof sits in a photo: its lean, read off the photo.

THE RULE. An orthophoto is rectified to the ground, so a point h metres up
is drawn k*h metres away from where the LiDAR has it, with one k = (east,
north) per building (metres of lean per metre of height). The roof's own
lines say what k is: the roof edge and the seams, each point moved by k
times its height, are laid over the photo for every k in +/-0.4 m/m, and the
k kept is the one where the photo's brightness changes ACROSS those lines.
Seams and roof edge vote as two standardised surfaces (a long roof edge
would otherwise outvote the ridges), less PRIOR per metre of slide at the
roof's mean height (a far-off gutter or shadow edge needs to beat the near
one clearly).

THE MEASUREMENT (16 Nov, 36 marked pilot roofs whose point-roof lines match
the markup at F >= 0.6 after their best slide; the markup is drawn on the
photo, so its slide is the truth): the lean lands within 0.3 m of the
markup's slide on 72%, within 0.5 m on 78%, over 1 m off on 6%. One flat
slide of the whole roof, same evidence: 56 / 69 / 14%. Seams and edge
pooled instead of standardised: 44 / 61 / 11%.
"""
import numpy as np
from scipy import ndimage as ndi
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

K_MAX = 0.4          # m of lean per m of height searched, each way
K_STEP = 0.0125
SIGMA_PX = 1.5       # photo smoothing before the gradient, 0.1 m pixels
PRIOR = 0.5          # standard deviations of the surface per metre of slide
PAD_M = 6.0


def _close(ps):
    return unary_union([q.buffer(0.15) for q in ps]).buffer(-0.15)


def _seams(polys, gap=0.3):
    out = []
    for i, p in enumerate(polys):
        for q in polys[i + 1:]:
            if p.distance(q) > gap:
                continue
            s = p.boundary.intersection(q.buffer(gap))
            if not s.is_empty and s.length > 0.3:
                out.append(s)
    return unary_union(out) if out else None


def line_samples(faces, origin, step=0.25):
    """(seams, edge): rows of x, y, across-line unit vector, height above
    ground. `faces` are point_roof faces ({'poly', 'plane'}), planes in the
    frame centred on `origin` with z above ground."""
    cx, cy = origin
    polys = [f["poly"] for f in faces]

    def hz(x, y):
        hs = [f["plane"][0] * (x - cx) + f["plane"][1] * (y - cy) + f["plane"][2]
              for f in faces if f["poly"].distance(Point(x, y)) < 0.3]
        return max(hs) if hs else 0.0

    def samples(geom):
        o = []
        if geom is None:
            return np.zeros((0, 5))
        for g in getattr(geom, "geoms", [geom]):
            if g.geom_type not in ("LineString", "LinearRing") or g.length < 1.0:
                continue
            for d in np.arange(0.1, g.length, step):
                a, b = g.interpolate(max(d - 0.1, 0)), g.interpolate(min(d + 0.1, g.length))
                p = g.interpolate(d)
                t = np.array([b.x - a.x, b.y - a.y]); n = np.hypot(*t)
                if n > 1e-6:
                    o.append((p.x, p.y, -t[1] / n, t[0] / n, hz(p.x, p.y)))
        return np.array(o).reshape(-1, 5)

    out = _close(polys)
    edge = unary_union([g.exterior for g in (out.geoms if hasattr(out, "geoms") else [out])])
    seams = _seams(polys)
    if seams is not None:
        seams = seams.difference(edge.buffer(0.4))
    return samples(seams), samples(edge), out


def photo_lean(faces, origin, img):
    """(kx, ky, sharpness) for one roof, or None when there is too little to
    go on. `img` is an open rasterio dataset in the faces' CRS."""
    from rasterio.windows import from_bounds
    seams, edge, out = line_samples(faces, origin)
    if len(edge) < 15:
        return None
    x0, y0, x1, y1 = out.buffer(PAD_M).bounds
    px = img.read([1, 2, 3], window=from_bounds(x0, y0, x1, y1, transform=img.transform),
                  boundless=True).astype(np.float32).mean(0)
    H, W = px.shape
    sx, sy = (x1 - x0) / W, (y1 - y0) / H
    g = ndi.gaussian_filter(px, SIGMA_PX)
    gx, gy = ndi.sobel(g, axis=1), -ndi.sobel(g, axis=0)
    ks = np.arange(-K_MAX, K_MAX + 1e-9, K_STEP)

    def surface(S):
        if len(S) < 15:
            return None
        s = np.zeros((len(ks), len(ks)))
        for i, kx in enumerate(ks):
            for j, ky in enumerate(ks):
                c = np.clip(((S[:, 0] + kx * S[:, 4] - x0) / sx).astype(int), 0, W - 1)
                r = np.clip(((y1 - S[:, 1] - ky * S[:, 4]) / sy).astype(int), 0, H - 1)
                s[i, j] = np.abs(gx[r, c] * S[:, 2] + gy[r, c] * S[:, 3]).mean()
        return (s - s.mean()) / max(s.std(), 1e-9)

    ss, se = surface(seams), surface(edge)
    s = se if ss is None else ss + se
    s = (s - s.mean()) / max(s.std(), 1e-9)
    hm = float(np.mean(np.r_[seams[:, 4], edge[:, 4]]))
    s = s - PRIOR * np.hypot(*np.meshgrid(ks, ks, indexing="ij")) * hm
    i, j = np.unravel_index(np.argmax(s), s.shape)
    return float(ks[i]), float(ks[j]), float(s[i, j])


def leaned(faces, origin, kx, ky):
    """The faces as the photo shows them: every vertex moved by k times its
    own height on its face's plane."""
    cx, cy = origin
    out = []
    for f in faces:
        a, b, c = f["plane"]
        mv = lambda ring: [(x + kx * (a * (x - cx) + b * (y - cy) + c),
                            y + ky * (a * (x - cx) + b * (y - cy) + c)) for x, y in ring.coords]
        p = Polygon(mv(f["poly"].exterior), [mv(i) for i in f["poly"].interiors]).buffer(0)
        if not p.is_empty and p.area > 0.3:
            out.append(p)
    return out
