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
the roof edge the build supplies. That edge is not moved by the slide.
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


def read_twice(img, mpp):
    """(maps, (dx, dy) px the LiDAR channels were moved by)."""
    from scipy.ndimage import binary_erosion
    net()
    from align_lidar_channels import slide_for, moved
    blank = img.copy(); blank[..., 3:] = 0
    p0 = maps(blank)
    roof = p0[7] > 0.5
    if roof.sum() < 400:
        return maps(img), (0, 0)
    dx, dy, _ = slide_for(img.astype(np.float32) / 255, (p0[0] * binary_erosion(roof, iterations=5)).astype(np.float32),
                          p0[5].astype(np.float32), roof, mpp)
    return maps(moved(img, dx, dy) if (dx or dy) else img), (dx, dy)


def roof_faces(geom, tiles, pts):
    """Faces of the roof whose edge `geom` and returns `pts` are in the photo
    frame as best known. Returns (faces, lines): polygons in metres in that
    frame and lines as (a, b, kind).

    The slide found by reading twice moves the LiDAR channels only, to help
    the reader. The roof edge stays where the build put it: on the test
    street three ways of finding the slide disagreed by a metre on one roof
    (1 Sunrise Lane) whose edge already sat on the photo, and an edge moved
    by a wrong slide is a roof drawn off its building."""
    from shapely.affinity import affine_transform
    from src.line_graph import faces_from_maps
    from train_roof_lines import NAMES
    img, b, mpp = picture(geom, tiles, pts)
    p, _ = read_twice(img, mpp)
    edge_px = affine_transform(geom, [1 / mpp, 0, 0, -1 / mpp, -b[0] / mpp, b[3] / mpp])
    faces, lines, _, _ = faces_from_maps(p, NAMES, outline=edge_px)
    to_m = [mpp, 0, 0, -mpp, b[0], b[3]]
    pt = lambda q: (b[0] + q[0] * mpp, b[3] - q[1] * mpp)
    return [affine_transform(f, to_m) for f in faces], [(pt(a), pt(c), k) for a, c, k in lines]
