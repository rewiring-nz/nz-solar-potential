"""The lean read off the MAP's photo (LINZ Basemaps tiles), per marked roof,
against the best estimate of the truth there: the markup's slide on the
survey-year photo plus the measured survey-photo -> map-photo shift
(image_shift.json). Also what the map applies today (image_shift alone)."""
import json, sys
from pathlib import Path
sys.path.insert(0, '.'); sys.path.insert(0, 'tools'); sys.path.insert(0, 'tools/experiments')
import numpy as np
from scipy import ndimage as ndi
import photo_lean as PL
from src import point_roof as pr
from src import register_imagery as RI
from src.region_build import area_paths

rows = [r for r in json.load(open(sys.argv[1])) if 'err' not in r and r['best'] >= 0.6]
paths = area_paths('pilot'); O = pr.outlines('pilot')
tiles = RI._Tiles(paths['dir'] / 'display_tiles')
shift = json.load(open(paths['dir'] / 'image_shift.json'))


class Arr:
    """rasterio-like reader over the map tiles, for photo_lean."""
    transform = None

    def read(self, bands, window=None, boundless=True):
        x0, y0, x1, y1 = window
        w, h = int(round((x1 - x0) / RI.RES_M)), int(round((y1 - y0) / RI.RES_M))
        return tiles.window((x0, y0, x1, y1), h, w)


import rasterio.windows as RW
_fb = RW.from_bounds
PL_from_bounds = lambda x0, y0, x1, y1, transform=None: (x0, y0, x1, y1)
import rasterio.windows
rasterio.windows.from_bounds = PL_from_bounds     # photo_lean imports it at call time
res = []
for r in rows:
    bid = r['id']
    rc = pr.reconstruct(bid, O[bid]); org = (rc['poly'].centroid.x, rc['poly'].centroid.y)
    try:
        k = PL.photo_lean(rc['faces'], org, Arr())
    except Exception as e:
        print(bid, 'failed', repr(e)[:120]); continue
    if k is None: continue
    seams, edge, _ = PL.line_samples(rc['faces'], org)
    hm = float(np.mean(np.r_[seams[:, 4], edge[:, 4]]))
    est = np.array([k[0], k[1]]) * hm
    s = shift.get(str(bid)) or [0.0, 0.0, 0.0]
    truth = np.array([r['dx'], r['dy']]) + np.array(s[:2])
    res.append(dict(id=bid, est=est.tolist(), truth=truth.tolist(), now=list(s[:2]), q=s[2] if len(s) > 2 else 0,
                    e_new=float(np.hypot(*(est - truth))), e_now=float(np.hypot(*(np.array(s[:2]) - truth)))))
    print(bid, 'lean on map photo', np.round(est, 2), 'expected', np.round(truth, 2), 'map applies now', s[:2], flush=True)
json.dump(res, open(Path(sys.argv[1]).with_name('maplean.json'), 'w'))
for name in ('e_now', 'e_new'):
    e = np.array([x[name] for x in res])
    print(f"{name}: n={len(e)} median {np.median(e):.2f} m, within 0.3 m {100*np.mean(e<=.3):.0f}%, within 0.5 m {100*np.mean(e<=.5):.0f}%, over 1 m {100*np.mean(e>1):.0f}%")
