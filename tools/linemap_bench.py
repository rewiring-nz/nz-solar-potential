"""Offline test of line extraction: a saved line-model map per roof ->
face_candidates.network_lines -> the line_facets arrangement (snap, seal,
node, polygonize) -> area-weighted best-match IoU against the drawn faces.

Maps come from tools/predict_faces.py with SOLAR_DUMP_LINEMAP=<dir> (an .npz
per roof: the model map, the crop bounds and the outline). MAP=oracle
rasterises the drawn lines onto the same crop instead, so extraction is
measured on perfect input. No LiDAR is read: faces are not plane-fitted.
Two minutes for 157 roofs on 8 cores.

Usage: MAP=real|oracle python tools/linemap_bench.py MAPDIR [OUT.json]"""
import json, sys, os, glob, statistics, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
import numpy as np
from shapely import wkt as W
from shapely.geometry import Polygon, LineString
from shapely.ops import unary_union, polygonize
from export_training_data import rasterise, LINE_WIDTH_PX
from src.label_geometry import snap_endpoints
from src import roof_partition as rp
from src.face_candidates import network_lines
from concurrent.futures import ProcessPoolExecutor
L = json.load(open('data/roof_labels.json'))['buildings']
MODE = os.environ.get('MAP', 'real')
def faces_from(segs, fp):
    segs = [((s[0], s[1]), (s[2], s[3])) for s in segs]
    segs = snap_endpoints(segs)
    segs = rp._seal_network(segs, fp.exterior)
    if not segs: return [fp]
    lines = [LineString(s) for s in segs] + [fp.exterior] + list(fp.interiors)
    cells = [p for p in polygonize(unary_union(lines)) if p.area >= rp.MIN_FACET_M2
             and p.representative_point().within(fp.buffer(0.01))]
    return cells or [fp]
def match(pred, ref):
    tot = sum(r.area for r in ref)
    return sum(r.area * max((r.intersection(p).area / r.union(p).area for p in pred), default=0) for r in ref) / tot
def one(f):
    i = os.path.basename(f)[:-4]
    z = np.load(f)
    b = tuple(z['b']); geom = W.loads(str(z['wkt'])); pr = z['pr'].astype(float)
    if geom.geom_type != 'Polygon': geom = max(geom.geoms, key=lambda g: g.area)
    h, w = pr.shape[1:]
    lab = L[i]
    ref = [p for p in (Polygon(x['ring']).buffer(0) for x in lab.get('faces') or []) if p.area > 1]
    if not ref: return None
    if MODE == 'oracle':
        lines = [l for l in lab.get('lines') or [] if l.get('a')]
        seg = lambda k: [(l['a'], l['b']) for l in lines if l.get('kind') == k]
        m = np.stack([rasterise((h, w), seg(k), b, LINE_WIDTH_PX) for k in ('ridge', 'valley', 'cliff')]) / 255.0
        pr = 1 / (1 + np.exp(-(m * 24 - 12)))
    try:
        segs = network_lines(pr, geom, b, w, h, None)
    except Exception as e:
        segs = []
    pred = faces_from(segs, geom) if segs else [geom]
    return (i, match(pred, ref), len(segs), len(pred), len(ref))
if __name__ == '__main__':
    files = sorted(glob.glob(sys.argv[1] + '/*.npz'))
    with ProcessPoolExecutor(8) as ex: res = [r for r in ex.map(one, files) if r]
    v = [r[1] for r in res]
    print(MODE, os.environ.get('SOLAR_NET_CLOSURE', '1'), 'roofs', len(res), 'face %.3f' % statistics.mean(v), '>=0.9', sum(x >= 0.9 for x in v))
    if len(sys.argv) > 2: json.dump(res, open(sys.argv[2], 'w'))
