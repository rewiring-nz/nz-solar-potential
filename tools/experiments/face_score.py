"""Faces read from held-out maps, scored against the drawn FACES.

Line F says whether the lines are in the right place; it does not say
whether they enclose the right faces (a ridge that stops short scores well
and divides nothing). Per roof, each drawn face takes the read face that
overlaps it best: FACE IoU is the area-weighted mean of those overlaps, and
a roof's faces are right at 0.8 or more (tools/markup_benchmark.py's rule).

    python tools/experiments/face_score.py <heldout maps dir> <graph|regions|regions_own> [sheet.jpg]
"""
import sys, os, glob, json, numpy as np
sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
from PIL import Image, ImageDraw
from shapely.geometry import Polygon
from shapely.ops import unary_union
from scipy.ndimage import distance_transform_edt
from skimage.morphology import skeletonize
import src.line_graph as LG
NAMES = ["line", "core", "ridge", "valley", "cliff", "outline", "join", "roof"]
maps_dir, method = sys.argv[1], sys.argv[2]; sheet = sys.argv[3] if len(sys.argv) > 3 else None
labels = json.load(open('data/roof_labels.json'))['buildings']
# SOLAR_TRUTH_FACES: faces rebuilt from the drawn lines (tools/experiments/joined_faces.py) in place of the stored ones
if os.environ.get('SOLAR_TRUTH_FACES'):
    for _k, _rings in json.load(open(os.environ['SOLAR_TRUTH_FACES'])).items():
        if _k in labels and _rings:
            labels[_k] = dict(labels[_k], faces=[{'ring': r} for r in _rings])


def convert(p, outline):
    if method == 'graph':
        return LG.faces_from_maps(p, NAMES, outline)[0]
    if method == 'regions':
        return LG.faces_from_regions(p, NAMES, outline)
    if method == 'regions_own':                      # the reader's own roof edge, not the drawn one
        own = LG.roof_outline(p[7])
        return LG.faces_from_regions(p, NAMES, own if own is not None and own.is_valid and own.area > 50 else outline)
    raise SystemExit(method)


def raster(polys, size):
    lab = np.zeros((size, size), np.int32)
    for i, q in enumerate(polys, 1):
        im = Image.new('L', (size, size), 0); ImageDraw.Draw(im).polygon(list(q.exterior.coords), fill=1)
        lab[np.array(im) > 0] = i
    return lab


rows, tiles = [], []
ONLY = set(json.load(open(os.environ['SOLAR_LINES_ONLY']))) if os.environ.get('SOLAR_LINES_ONLY') else None
for f in sorted(glob.glob(f'{maps_dir}/*.npz')):
    bid = os.path.basename(f)[:-4]
    if ONLY is not None and bid not in ONLY:
        continue
    d = np.load(f"{os.environ.get('SOLAR_LINES_SET', 'data/line_set/typed_set2')}/hand/{bid}.npz"); p = np.load(f)['maps'].astype(np.float32) / 255
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
    try:
        read = [q for q in convert(p, outline) if q.is_valid and q.geom_type == 'Polygon']
    except Exception as e:
        print(bid, 'failed', type(e).__name__, e); read = [outline]
    D, R = raster(drawn, size), raster(read, size)
    tot, acc = 0.0, 0.0
    for i in range(1, len(drawn) + 1):
        m = D == i; a = m.sum()
        if not a:
            continue
        best = 0.0
        for j in np.unique(R[m]):
            if j:
                r = R == j; best = max(best, (m & r).sum() / (m | r).sum())
        tot += a; acc += a * best
    fiou = acc / max(tot, 1)
    # line F of the read faces' inner edges against the drawn ones
    edges = lambda L: ((L[:, :-1] != L[:, 1:]) & (L[:, :-1] > 0) & (L[:, 1:] > 0))
    def inner(L):
        e = np.zeros(L.shape, bool); e[:, :-1] |= edges(L); e[:-1, :] |= edges(L.T).T; return skeletonize(e)
    tb, pb = inner(D), inner(R); tol = 0.75 / mpp
    if tb.sum() < 10 and pb.sum() < 10: lf = 1.0
    elif tb.sum() < 10 or pb.sum() < 10: lf = 0.0
    else:
        rc = (distance_transform_edt(~pb)[tb] <= tol).mean(); pr = (distance_transform_edt(~tb)[pb] <= tol).mean(); lf = 2 * pr * rc / (pr + rc) if pr + rc else 0.0
    area = outline.area * mpp * mpp
    rows.append((fiou, lf, len(drawn), len(read), area, str(d['region']) == 'pilot'))
    if sheet:
        def view(polys, col):
            im = Image.fromarray(d['image'][..., :3].copy()); dr = ImageDraw.Draw(im)
            for q in polys: dr.line(list(q.exterior.coords), fill=col, width=2)
            return np.array(im)
        tiles.append((fiou, np.hstack([view(drawn, (255, 255, 0)), view(read, (255, 0, 0))])))
r = np.array(rows, float)
def line(name, m):
    if m.sum():
        print('%-34s %3d roofs: face IoU %.3f, faces right (>=0.8) %3d (%.0f%%), lines F %.3f, lines right (>=0.7) %3d, near perfect (>=0.9) %3d; faces drawn %d read %d'
              % (name, m.sum(), r[m, 0].mean(), (r[m, 0] >= .8).sum(), 100 * (r[m, 0] >= .8).mean(), r[m, 1].mean(), (r[m, 1] >= .7).sum(), (r[m, 1] >= .9).sum(), r[m, 2].sum(), r[m, 3].sum()))
house = r[:, 4] < 350; pilot = r[:, 5] > 0
print(method)
line('all', np.ones(len(r), bool)); line('house-sized, outside town centre', house & ~pilot); line('house-sized, town centre', house & pilot); line('larger', ~house)
if sheet:
    tiles.sort(key=lambda t: t[0]); pick = [tiles[int(i)][1] for i in np.linspace(0, len(tiles) - 1, 16)]
    Image.fromarray(np.vstack([np.hstack(pick[i:i + 4]) for i in range(0, 16, 4)])).resize((2304, 1152)).save(sheet, quality=85)
