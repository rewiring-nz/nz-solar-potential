"""Read one roof with the roof-line reader, as the build does: no label.

The reader (tools/train_roof_lines.py) was trained on pictures made by
tools/export_roof_lines.py `sample`: the map's display photo, 384 px with the
roof filling 304, and four LiDAR channels. `picture` makes the same picture
for any roof edge.

The LiDAR sits some tenths of a metre off the photo (median 0.7 m on the 873
training roofs, and not predictable from the neighbours: their median lean
was within 0.3 m of a roof's own on 12%). So the roof is read twice. Pass 1
reads the photo alone, LiDAR channels blank; the LiDAR's own edges are slid
onto pass 1's lines and outline (tools/align_lidar_channels.py slide_for);
pass 2 reads with the LiDAR moved. On 32 drawn roofs the model never saw
(fold 0) the drawing scored F 0.722 with the LiDAR as it came, 0.757 read
twice, 0.758 with the LiDAR aligned by the markup itself.

The faces are src/line_graph.py's: joins connected by straight lines inside
the roof edge the build supplies, moved with the LiDAR onto the photo.
"""
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODEL = os.environ.get("SOLAR_LINE_READER_MODEL", str(ROOT / "data/models/roof_lines.pt"))
SIZE, FILL_PX = 384, 304
_NET = []


def net():
    if not _NET:
        import torch
        sys.path.insert(0, str(ROOT / "tools"))
        from train_roof_lines import load_net
        torch.set_num_threads(1)
        _NET.append(load_net(MODEL))
    return _NET[0]


def picture(geom, tiles, pts):
    """(H W 7 uint8, bounds, m per px) for a roof edge `geom` in the photo
    frame; `pts` are the building's LiDAR returns, already in that frame."""
    from PIL import Image
    from scipy.ndimage import gaussian_filter
    from scipy.spatial import cKDTree
    minx, miny, maxx, maxy = geom.bounds
    mpp = max(maxx - minx, maxy - miny) / FILL_PX
    half = SIZE / 2 * mpp
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    b = (cx - half, cy - half, cx + half, cy + half)
    n = max(int(round((b[2] - b[0]) / 0.1)), 8)        # the tiles come on a 0.1 m grid
    arr = np.moveaxis(np.asarray(tiles.window(b, n, n)), 0, -1)
    rgb = np.asarray(Image.fromarray(arr).resize((SIZE, SIZE), Image.BILINEAR)).astype(np.float32) / 255.0
    z = np.full((SIZE, SIZE), np.nan, np.float32)
    if pts is not None and len(pts) >= 50:
        gx, gy = np.meshgrid(np.linspace(b[0] + mpp / 2, b[2] - mpp / 2, SIZE),
                             np.linspace(b[3] - mpp / 2, b[1] + mpp / 2, SIZE))
        dist, idx = cKDTree(pts[:, :2]).query(np.c_[gx.ravel(), gy.ravel()], k=1)
        z = pts[idx, 2].reshape(SIZE, SIZE).astype(np.float32)
        z[dist.reshape(SIZE, SIZE) > 2.5] = np.nan
    base = np.nanpercentile(z, 5) if np.isfinite(z).any() else 0.0
    zf = gaussian_filter(np.nan_to_num(z - base, nan=0.0), max(2.0, 1.2 / mpp))
    dzy, dzx = np.gradient(zf, mpp)
    slope = np.degrees(np.arctan(np.hypot(dzx, dzy)))
    mag = np.hypot(dzx, dzy) + 1e-9
    chans = np.dstack([rgb, np.clip(zf / 10.0, 0, 1), np.clip(slope / 45.0, 0, 1),
                       (-dzx / mag + 1) / 2, (dzy / mag + 1) / 2])
    return (chans * 255).astype(np.uint8), b, mpp


def maps(img):
    """The reader's maps (C H W, 0..1) for a picture."""
    import torch
    import torch.nn.functional as F
    from train_roof_lines import SIZE as RUN
    x = torch.from_numpy(img.astype(np.float32) / 255).permute(2, 0, 1)[None]
    with torch.no_grad():
        p = torch.sigmoid(net()(F.interpolate(x, size=(RUN, RUN), mode="bilinear", align_corners=False)))
        return F.interpolate(p, size=x.shape[-2:], mode="bilinear", align_corners=False)[0].numpy()


SEARCH_M, PRIOR_PER_M, LINE_SHARE, AREA_KEEP = 1.6, 0.05, 0.5, 0.9


def footprint(img):
    """Where the building's LiDAR returns are: the part of the picture with
    height, the piece under the picture's centre."""
    from scipy.ndimage import gaussian_filter, label
    has = gaussian_filter(img[..., 3].astype(np.float32) / 255, 1.0) > 0.02
    lab, n = label(has)
    if not n:
        return has
    c = lab[lab.shape[0] // 2 - 20:lab.shape[0] // 2 + 20, lab.shape[1] // 2 - 20:lab.shape[1] // 2 + 20]
    ids = np.bincount(c.ravel(), minlength=n + 1)[1:]
    return lab == (1 + int(np.argmax(ids))) if ids.max() else has


def slide(img, p0, mpp, foot=None):
    """(dx, dy) px to move the LiDAR channels (and the roof edge with them)
    onto the photo, from pass 1's reading of the photo alone.

    Two kinds of evidence, each scaled to its own best: the AREA of the
    building (`foot`: the build's roof edge as a mask, else where the LiDAR
    has returns) over pass 1's roof map, and the LiDAR's creases and steps
    over pass 1's lines. The area decides first: sliding the LiDAR's edges
    over the lines alone moved 1 Sunrise Lane 1.2 m off a roof it already
    sat on (street L3). PRIOR_PER_M per metre is taken off, so a move has to
    earn its distance."""
    from scipy.ndimage import binary_erosion, gaussian_filter
    from align_lidar_channels import lidar_edges
    roof = p0[7] > 0.5
    if roof.sum() < 400:
        return 0, 0
    foot = footprint(img) if foot is None else foot
    F, iF = np.fft.rfft2, np.fft.irfft2
    corr = lambda a, b: iF(np.conj(F(a.astype(np.float32))) * F(b.astype(np.float32)), s=a.shape)
    # overlap less what falls off the roof: a footprint larger than the roof
    # map (eaves, a deck) must not slide to hide its surplus
    area = corr(foot, p0[7]) - 0.5 * corr(foot, 1.0 - p0[7])
    crease, step, _ = lidar_edges(img.astype(np.float32) / 255, roof)
    lines = gaussian_filter(p0[0] * binary_erosion(roof, iterations=5), 1.0)
    line = corr(crease + 0.5 * step, lines)
    r = int(round(SEARCH_M / mpp))
    idx = np.arange(-r, r + 1)
    win = lambda m: m[np.ix_(idx % m.shape[0], idx % m.shape[1])]
    A, L = win(area), win(line)
    # each surface from 0 (its worst slide) to 1 (its best). The lines can
    # match a parallel line a metre off as well as their own, so they only
    # choose among slides where the area is within AREA_KEEP of its best.
    unit = lambda m: (m - m.min()) / max(float(m.max() - m.min()), 1e-6)
    A = unit(A)
    L = unit(L) if lines.sum() > 40 else np.zeros_like(A)
    dist = np.hypot(*np.meshgrid(idx, idx)) * mpp
    surf = A + LINE_SHARE * L - PRIOR_PER_M * dist
    surf[A < AREA_KEEP] = -1e9
    iy, ix = np.unravel_index(int(np.argmax(surf)), surf.shape)
    return int(idx[ix]), int(idx[iy])


def read_twice(img, mpp, foot=None):
    """(maps, (dx, dy) px the LiDAR channels were moved by)."""
    net()
    from align_lidar_channels import moved
    blank = img.copy(); blank[..., 3:] = 0
    p0 = maps(blank)
    dx, dy = slide(img, p0, mpp, foot)
    return maps(moved(img, dx, dy) if (dx or dy) else img), (dx, dy)


def roof_faces(geom, tiles, pts):
    """Faces of the roof whose edge `geom` and returns `pts` are in the photo
    frame as best known. Returns (faces, lines, slide): polygons in metres in
    the frame of the PHOTO, lines as (a, b, kind), and the (east, north)
    metres the LiDAR (and the edge with it) was moved to get there."""
    from shapely.affinity import affine_transform
    from src.line_graph import faces_from_maps
    from train_roof_lines import NAMES
    from PIL import Image, ImageDraw
    img, b, mpp = picture(geom, tiles, pts)
    fm = Image.new("L", (SIZE, SIZE), 0)
    for g in getattr(geom, "geoms", [geom]):
        ImageDraw.Draw(fm).polygon([((x - b[0]) / mpp, (b[3] - y) / mpp) for x, y in g.exterior.coords], fill=1)
    p, (dx, dy) = read_twice(img, mpp, np.array(fm) > 0)
    east, north = dx * mpp, -dy * mpp
    # metres -> pixels of the picture, the edge moved with the LiDAR
    to_px = [1 / mpp, 0, 0, -1 / mpp, -(b[0] - east) / mpp, (b[3] - north) / mpp]
    edge_px = affine_transform(geom, to_px)
    faces, lines, _, _ = faces_from_maps(p, NAMES, outline=edge_px)
    to_m = [mpp, 0, 0, -mpp, b[0], b[3]]
    pt = lambda q: (b[0] + q[0] * mpp, b[3] - q[1] * mpp)
    return ([affine_transform(f, to_m) for f in faces], [(pt(a), pt(c), k) for a, c, k in lines], (east, north))
