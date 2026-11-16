"""Can the slide between the LiDAR roof and the photo be read off the photo
itself, with no markup? Slide the built roof's lines (seams and roof edge)
over the photo and keep the place where the photo's edges run along them.
Checked against the slide the markup gives (leanfield.json)."""
import json, sys
from pathlib import Path
sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
import numpy as np, rasterio
from rasterio.windows import from_bounds
from scipy import ndimage as ndi
from shapely.ops import unary_union
import markup_benchmark as M
from src import point_roof as pr
from src.region_build import area_paths
rows = [r for r in json.load(open(sys.argv[1])) if 'err' not in r and r['best'] >= 0.6]
img = rasterio.open(area_paths('pilot')['imagery']); O = pr.outlines('pilot')
close = lambda ps: unary_union([q.buffer(0.15) for q in ps]).buffer(-0.15)
R = 0.1; SEARCH = 3.0
def samples(geom, step=0.2):
    out = []
    for g in getattr(geom, 'geoms', [geom]):
        if g.geom_type != 'LineString' or g.length < 1.0: continue
        for d in np.arange(0.1, g.length, step):
            a, b = g.interpolate(max(d - 0.1, 0)), g.interpolate(min(d + 0.1, g.length)); p = g.interpolate(d)
            t = np.array([b.x - a.x, b.y - a.y]); n = np.hypot(*t)
            if n > 1e-6: out.append((p.x, p.y, -t[1] / n, t[0] / n))
    return np.array(out)
res = []
for r in rows:
    rc = pr.reconstruct(r['id'], O[r['id']]); got = [f['poly'] for f in rc['faces']]
    gout = close(got); seams = M._lines(got, gout)
    parts = {'seams': samples(seams) if seams is not None else np.zeros((0, 4)), 'edge': samples(gout.boundary)}
    x0, y0, x1, y1 = gout.buffer(SEARCH + 2).bounds
    px = img.read([1, 2, 3], window=from_bounds(x0, y0, x1, y1, transform=img.transform), boundless=True).astype(np.float32).mean(0)
    H, W = px.shape; sx = (x1 - x0) / W; sy = (y1 - y0) / H
    g = ndi.gaussian_filter(px, 1.5); gx = ndi.sobel(g, axis=1); gy = -ndi.sobel(g, axis=0)   # east, north
    offs = np.arange(-SEARCH, SEARCH + 1e-6, R)
    def surface(S):
        out = np.zeros((len(offs), len(offs)))
        for i, dx in enumerate(offs):
            for j, dy in enumerate(offs):
                c = ((S[:, 0] + dx - x0) / sx).astype(int); rr = ((y1 - S[:, 1] - dy) / sy).astype(int)
                ok = (c >= 0) & (c < W) & (rr >= 0) & (rr < H)
                # photo gradient ACROSS the line: an edge running along it
                out[i, j] = np.mean(np.abs(gx[rr[ok], c[ok]] * S[ok, 2] + gy[rr[ok], c[ok]] * S[ok, 3])) if ok.sum() > 10 else 0
        return out
    est = {}
    for name in ('seams', 'edge', 'both'):
        S = np.vstack([parts['seams'], parts['edge']]) if name == 'both' else parts[name]
        if len(S) < 15: continue
        s = surface(S); i, j = np.unravel_index(np.argmax(s), s.shape)
        est[name] = (offs[i], offs[j], float(s.max() / max(np.median(s), 1e-9)))
    res.append(dict(id=r['id'], truth=(r['dx'], r['dy']), est=est, h=r['h']))
    print(r['id'], 'markup slide', (r['dx'], r['dy']), {k: (round(v[0], 2), round(v[1], 2), round(v[2], 1)) for k, v in est.items()}, flush=True)
json.dump(res, open(Path(sys.argv[1]).with_name('photoslide.json'), 'w'))
for name in ('seams', 'edge', 'both'):
    e = np.array([np.hypot(x['est'][name][0] - x['truth'][0], x['est'][name][1] - x['truth'][1]) for x in res if name in x['est']])
    print(f"{name:6s} n={len(e)} error against the markup slide: median {np.median(e):.2f} m, within 0.3 m {100 * np.mean(e <= .3):.0f}%, within 0.5 m {100 * np.mean(e <= .5):.0f}%")
