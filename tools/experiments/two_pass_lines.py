"""Held-out drawn roofs, read as the build would: no label to align the LiDAR.

Pass 1 reads the photo with the LiDAR as exported. The LiDAR channels are
then slid onto pass 1's own lines and outline (tools/align_lidar_channels.py
slide_for), and pass 2 reads again. Scores the joins-and-lines drawing
(src/line_graph.py) against the markup at 0.75 m, for: LiDAR as exported,
two-pass, and aligned by the label (the ceiling).

    python tools/experiments/two_pass_lines.py <model.pt> <fold> [sheet.jpg]
"""
import sys, os, glob, random, numpy as np, torch, torch.nn.functional as F
sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
import train_roof_lines as T, align_lidar_channels as AL, src.line_graph as LG
from scipy.ndimage import distance_transform_edt, binary_erosion, binary_dilation
from skimage.morphology import skeletonize
from PIL import Image, ImageDraw
model, fold = sys.argv[1], int(sys.argv[2]); sheet = sys.argv[3] if len(sys.argv) > 3 else None
SRC, ALIGNED = 'data/line_set/typed_set2/hand', 'data/line_set/typed_set3/hand'
net = T.load_net(model)
fs = sorted(glob.glob('data/line_set/typed_set/hand/*.npz')); random.Random(11).shuffle(fs); fs = [os.path.basename(f) for f in fs[fold::5]]


def read(img):
    x = torch.from_numpy(img.astype(np.float32) / 255).permute(2, 0, 1)[None]
    with torch.no_grad():
        p = torch.sigmoid(net(F.interpolate(x, size=(T.SIZE, T.SIZE), mode='bilinear', align_corners=False)))
        return F.interpolate(p, size=x.shape[-2:], mode='bilinear', align_corners=False)[0].numpy()


def score(pb, tb, tol):
    if tb.sum() < 10 and pb.sum() < 10: return 1.0
    if tb.sum() < 10 or pb.sum() < 10: return 0.0
    rc = (distance_transform_edt(~pb)[tb] <= tol).mean(); pr = (distance_transform_edt(~tb)[pb] <= tol).mean()
    return 2 * pr * rc / (pr + rc) if pr + rc else 0.0


def drawing_score(p, d):
    w = d['weight']; tb = skeletonize(d['target'][..., 0] & w); mpp = float(d['meta'][0])
    faces, lines, nodes, outline = LG.faces_from_maps(p, T.NAMES)
    im = Image.new('L', w.shape, 0); dr = ImageDraw.Draw(im)
    for a, b, _ in lines: dr.line([a, b], fill=255, width=1)
    raw = score(skeletonize((p[0] > 0.5) & w), tb, 0.75 / mpp)
    return score((np.array(im) > 0) & w, tb, 0.75 / mpp), raw, (lines, nodes, outline)


COL = {'ridge': (255, 0, 0), 'valley': (0, 120, 255), 'cliff': (255, 160, 0)}
rows, tiles, slides = [], [], []
for f in fs:
    d = np.load(f'{SRC}/{f}'); da = np.load(f'{ALIGNED}/{f}'); mpp = float(d['meta'][0])
    p1 = read(d['image'])
    if os.environ.get('PHOTO_FIRST', '1') == '1':
        # pass 1 for the slide reads the photo alone: with the LiDAR in, its
        # lines sit where the LiDAR is and the slide found is nothing
        img0 = d['image'].copy(); img0[..., 3:] = 0; p0 = read(img0)
    else:
        p0 = p1
    roof = p0[7] > 0.5
    dx, dy, gain = AL.slide_for(d['image'].astype(np.float32) / 255, (p0[0] * binary_erosion(roof, iterations=5)).astype(np.float32),
                                p0[5].astype(np.float32), roof, mpp)
    p2 = read(AL.moved(d['image'], dx, dy)) if (dx or dy) else p1
    pa = read(da['image'])
    g1, r1, _ = drawing_score(p1, d); g2, r2, geo = drawing_score(p2, d); ga, ra, _ = drawing_score(pa, d)
    rows.append((g1, g2, ga, r1, r2, ra)); t = da['lidar_slide_px']
    slides.append(np.hypot(dx - t[0], dy - t[1]) * mpp)
    if sheet:
        tt = d['target']; a = d['image'][..., :3].copy()
        for ch, c in ((2, COL['ridge']), (3, COL['valley']), (4, COL['cliff']), (5, (255, 255, 255)), (6, (0, 255, 0))): a[tt[..., ch]] = c
        im = Image.fromarray(d['image'][..., :3].copy()); dr = ImageDraw.Draw(im); lines, nodes, outline = geo
        if outline is not None: dr.line(list(outline.exterior.coords), fill=(255, 255, 255), width=2)
        for u, v, k in lines: dr.line([u, v], fill=COL[k], width=2)
        for u, v in nodes: dr.ellipse([u - 2, v - 2, u + 2, v + 2], fill=(0, 255, 0))
        tiles.append((g2, np.hstack([a, np.array(im)])))
r = np.array(rows); sl = np.array(slides)
for name, c in (('LiDAR as exported', 0), ('two-pass (no label)', 1), ('aligned by the label', 2)):
    print('%-22s drawing F %.3f (>=0.7: %d, >=0.9: %d of %d)   raw line map F %.3f' % (name, r[:, c].mean(), (r[:, c] >= .7).sum(), (r[:, c] >= .9).sum(), len(r), r[:, c + 3].mean()))
print('two-pass slide within 0.3 m of the label\'s slide: %d of %d; median difference %.2f m' % ((sl <= 0.3).sum(), len(sl), np.median(sl)))
if sheet:
    tiles.sort(key=lambda t: t[0]); pick = [tiles[int(i)][1] for i in np.linspace(0, len(tiles) - 1, 12)]
    Image.fromarray(np.vstack([np.hstack(pick[i:i + 3]) for i in range(0, 12, 3)])).save(sheet, quality=88)
