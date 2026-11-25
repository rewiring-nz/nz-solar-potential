import json, sys
from pathlib import Path
sys.path.insert(0, '.'); sys.path.insert(0, 'tools'); sys.path.insert(0, 'tools/experiments')
import numpy as np, matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt, rasterio
from rasterio.windows import from_bounds
from shapely.geometry import Polygon, LineString
import photo_lean as PL, roof_edge as RE
from shapely.affinity import translate
from src import point_roof as pr
from src.region_build import area_paths
out = sys.argv[1]; ids = [int(x) for x in sys.argv[2:]]
B = json.loads(Path('data/roof_labels.json').read_text())['buildings']; E = {r['id']: r for r in json.load(open(Path(__file__).with_name('e2e.json')))}
img = rasterio.open(area_paths('pilot')['imagery']); O = pr.outlines('pilot')
fig, axs = plt.subplots(len(ids), 4, figsize=(20, 5 * len(ids)), dpi=90)
for row, bid in enumerate(ids):
    v = B[str(bid)]; r = pr.reconstruct(bid, O[bid]); org = (r['poly'].centroid.x, r['poly'].centroid.y)
    k = PL.photo_lean(r['faces'], org, img)
    drawn = [Polygon(f['ring']).buffer(0) for f in v['faces']]
    st = [LineString([(p[0], p[1]) for p in l['points']]) for l in (v.get('lines') or []) if len(l.get('points') or []) >= 2]
    x0, y0, x1, y1 = r['poly'].buffer(3).bounds; s = max(x1 - x0, y1 - y0); cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    b = (cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2)
    px = np.moveaxis(img.read([1, 2, 3], window=from_bounds(*b, transform=img.transform), boundless=True), 0, -1)
    _p = O[bid] if O[bid].geom_type == 'Polygon' else max(O[bid].geoms, key=lambda g: g.area)
    e, _ = RE.fit_edges(pr, translate(_p, *r['shift'])); r2 = dict(r, faces=RE.extend_to_edge(r['faces'], e))
    org2 = (r2['poly'].centroid.x, r2['poly'].centroid.y); k2 = PL.photo_lean(r2['faces'], org2, img)
    sets = [('Your markup', drawn, '#00e070', st), ('LiDAR roof as placed now', [f['poly'] for f in r['faces']], '#ff2040', []),
            ('Leaned to the photo, surveyed outline', PL.leaned(PL.absorb_slivers(r['faces']), org, k[0], k[1]), '#ff2040', []),
            ('Leaned to the photo, roof edge from LiDAR', PL.leaned(PL.absorb_slivers(r2['faces']), org2, k2[0], k2[1]), '#ff2040', [])]
    for ax, (title, ps, col, strokes) in zip(axs[row], sets):
        ax.imshow(px, extent=(b[0], b[2], b[1], b[3]))
        for p in ps:
            for g in (p.geoms if hasattr(p, 'geoms') else [p]):
                if g.geom_type == 'Polygon': ax.plot(*g.exterior.xy, color=col, lw=2)
        for l in strokes: ax.plot(*l.xy, color='#ffe000', lw=2)
        ax.set_title((v.get('address') or str(bid)) + ': ' + title if ax is axs[row][0] else title, fontsize=11); ax.set_xticks([]); ax.set_yticks([])
        ax.set_xlim(b[0], b[2]); ax.set_ylim(b[1], b[3])
fig.tight_layout(); fig.savefig(out, pil_kwargs={'quality': 88})
