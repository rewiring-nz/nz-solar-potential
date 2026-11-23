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

The faces are src/line_graph.py's faces_from_regions, inside the roof edge
the build supplies. That edge is not moved by the slide.
"""
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODEL = os.environ.get("SOLAR_LINE_READER_MODEL", str(ROOT / "data/models/roof_lines.pt"))
SIZE, FILL_PX = 384, 304
_NET = []
SHARP = int(os.environ.get("SOLAR_LINES_SHARP", "0"))
SHARP_WINDOW_M, SHARP_STEP_M = 0.6, 2.0


def sharp_channels(z, base, mpp):
    """Two more LiDAR channels, unsmoothed: the height above the roof's low
    point, and the height step within SHARP_WINDOW_M (highest less lowest
    return), full scale at SHARP_STEP_M.

    The four smoothed channels blur the surface over 1.2 m or more, which
    keeps a ridge (a change of slope over metres) and loses a drop edge or a
    valley's foot. Read by a model that never saw them, 81% of drawn ridge
    pixels are seen (line map 0.5 or more) against 68% of valley and 59% of
    drop-edge pixels (168 drawn roofs, run I)."""
    from scipy.ndimage import maximum_filter, minimum_filter
    has = np.isfinite(z)
    zz = np.where(has, z - base, 0.0)
    k = max(3, int(round(SHARP_WINDOW_M / mpp)) | 1)
    hi = maximum_filter(np.where(has, zz, -1e6), k); lo = minimum_filter(np.where(has, zz, 1e6), k)
    step = np.where(has, np.clip(hi - lo, 0, None), 0.0)
    return [np.clip(zz / 10.0, 0, 1), np.clip(step / SHARP_STEP_M, 0, 1)]


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
                       (-dzx / mag + 1) / 2, (dzy / mag + 1) / 2] + (sharp_channels(z, base, mpp) if SHARP else []))
    return (chans * 255).astype(np.uint8), b, mpp


TTA = int(os.environ.get("SOLAR_LINE_READER_TTA", "8"))


def _turned(x, k):
    """The picture (H W 7, 0..1) flipped (k >= 4) and turned k % 4 quarter
    turns, the downhill channels turned with it, as in training."""
    lidar = bool(x[..., 3:].any())
    if k >= 4:
        x = x[:, ::-1].copy()
        if lidar:
            x[..., 5] = 1 - x[..., 5]
    for _ in range(k % 4):
        x = np.rot90(x).copy()
        if lidar:
            c5, c6 = x[..., 5].copy(), x[..., 6].copy()
            x[..., 5], x[..., 6] = 1 - c6, c5
    return x


def maps(img):
    """The reader's maps (C H W, 0..1) for a picture: the mean over the TTA
    views (the four quarter turns, each flipped and not), every view turned
    back before averaging. Read one way up, a line the reader half sees is
    kept or lost by chance; held-out drawn roofs score higher averaged."""
    import torch
    import torch.nn.functional as F
    from train_roof_lines import SIZE as RUN
    x0 = img.astype(np.float32) / 255
    acc = 0
    for k in range(8)[:max(1, TTA)] if TTA != 4 else range(4):
        x = torch.from_numpy(np.ascontiguousarray(_turned(x0, k))).permute(2, 0, 1)[None]
        with torch.no_grad():
            p = torch.sigmoid(net()(F.interpolate(x, size=(RUN, RUN), mode="bilinear", align_corners=False)))
            p = F.interpolate(p, size=x.shape[-2:], mode="bilinear", align_corners=False)[0].numpy()
        p = np.rot90(p, -(k % 4), axes=(1, 2))
        if p.shape[0] > 8 and k % 2:
            # the direction maps (train_roof_lines.NDIR) hold twice the line's
            # angle: a quarter turn back reverses both
            p = p.copy(); p[8:10] = 1 - p[8:10]
        if k >= 4:
            p = p[:, :, ::-1]
            if p.shape[0] > 8:                     # a mirror reverses the sine map
                p = p.copy(); p[9] = 1 - p[9]
        acc = acc + p
    return np.ascontiguousarray(acc / max(1, len(range(8)[:max(1, TTA)])))


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


EDGE = os.environ.get("SOLAR_LINE_READER_EDGE", "reader")
EDGE_MIN_IOU = 0.6
# Off: on 167 held-out drawn roofs read from the build's edge, reading again
# in a picture framed on the reader's own edge left face IoU at 0.662 and
# took the edge's overlap with the drawn one from 0.873 to 0.869.
REFRAME = int(os.environ.get("SOLAR_LINE_READER_REFRAME", "0"))
REFRAME_MIN = 0.05


def reader_edge(p, edge_px):
    """The roof's edge, in pixels: the reader's own where it has one.

    The build's edge comes from the surveyed outline and the LiDAR: it takes
    in balconies and decks, and it sits off the photo by the building's lean.
    The reader's roof map was taught on drawn roofs (the union of the drawn
    faces, which leaves balconies out) and is in the photo's frame. On the
    157 held-out drawn roofs it overlaps the drawn edge 0.86 on average
    (0.9 or better on 61). It is used where it agrees with the build's edge
    on what the building is (overlap EDGE_MIN_IOU or more); a reading that
    does not is not trusted over the survey."""
    from src.line_graph import roof_outline
    if EDGE != "reader":
        return edge_px
    try:
        own = roof_outline(p[7], p[5])
    except Exception:
        own = None
    if own is None or not own.is_valid or own.is_empty:
        return edge_px
    iou = own.intersection(edge_px).area / max(own.union(edge_px).area, 1e-9)
    return own if iou >= EDGE_MIN_IOU else edge_px


def roof_faces(geom, tiles, pts):
    """Faces of the roof whose edge `geom` and returns `pts` are in the photo
    frame as best known. Returns (faces, lines): polygons in metres in that
    frame and lines as (a, b, kind).

    The slide found by reading twice moves the LiDAR channels only, to help
    the reader. The build's edge is never moved by it: on the test street
    three ways of finding the slide disagreed by a metre on one roof
    (1 Sunrise Lane), and an edge moved by a wrong slide is a roof drawn off
    its building. The edge the faces fill is the reader's own (reader_edge)."""
    from shapely.affinity import affine_transform
    from src.line_graph import faces_from_regions
    from train_roof_lines import NAMES
    img, b, mpp = picture(geom, tiles, pts)
    p, _ = read_twice(img, mpp)
    edge_px = affine_transform(geom, [1 / mpp, 0, 0, -1 / mpp, -b[0] / mpp, b[3] / mpp])
    for _ in range(REFRAME):
        # The reader was taught on pictures centred on the true roof and
        # filled by it. A picture framed on the build's edge is off-centre
        # or the wrong size wherever that edge is wrong, which is where the
        # reading matters most. So the roof is read again in a picture
        # framed on the reader's own edge, when that edge is trusted
        # (reader_edge) and moves the frame by REFRAME_MIN or more.
        own = reader_edge(p, edge_px)
        if own is edge_px:
            break
        own_m = affine_transform(own, [mpp, 0, 0, -mpp, b[0], b[3]])
        ox0, oy0, ox1, oy1 = own_m.bounds; gx0, gy0, gx1, gy1 = geom.bounds
        span = max(gx1 - gx0, gy1 - gy0)
        moved = max(abs((ox0 + ox1) - (gx0 + gx1)) / 2, abs((oy0 + oy1) - (gy0 + gy1)) / 2, abs(max(ox1 - ox0, oy1 - oy0) - span))
        if moved < REFRAME_MIN * span:
            break
        geom = own_m
        img, b, mpp = picture(geom, tiles, pts)
        p, _ = read_twice(img, mpp)
        edge_px = affine_transform(geom, [1 / mpp, 0, 0, -1 / mpp, -b[0] / mpp, b[3] / mpp])
    # faces seeded from the face-interior map (faces_from_regions): on the 157
    # held-out drawn roofs, face IoU 0.678 and lines right on 99, against
    # 0.516 and 53 for the joins-and-lines drawing, which leaves lines that
    # close no face
    if os.environ.get("SOLAR_LINE_READER_DUMP"):
        # the picture, the maps and the edge of one reading, for looking at
        # which map carries a wrong line
        c = geom.centroid
        np.savez_compressed(os.path.join(os.environ["SOLAR_LINE_READER_DUMP"], f"{int(c.x)}_{int(c.y)}.npz"),
                            image=img, maps=(p * 255).astype(np.uint8), frame=np.array(list(b) + [mpp]),
                            edge=np.asarray(reader_edge(p, edge_px).exterior.coords))
    faces = faces_from_regions(p, NAMES, reader_edge(p, edge_px))
    to_m = [mpp, 0, 0, -mpp, b[0], b[3]]
    return [affine_transform(f, to_m) for f in faces], face_lines(faces, p, to_m)


def face_lines(faces, p, to_m):
    """The lines between faces as (a, b, kind) in metres: each straight run
    two faces share, typed ridge, valley or cliff by whichever of those maps
    is strongest along it. A draft for the markup page to open with."""
    from scipy.ndimage import map_coordinates
    kinds = ("ridge", "valley", "cliff")
    out = []
    for i in range(len(faces)):
        for j in range(i + 1, len(faces)):
            s = faces[i].boundary.intersection(faces[j].boundary)
            for g in getattr(s, "geoms", [s]):
                if g.geom_type != "LineString" or g.length < 2.0:
                    continue
                c = np.asarray(g.simplify(0.5).coords)
                for a, q in zip(c[:-1], c[1:]):
                    n = max(int(np.hypot(*(q - a))), 2)
                    xs, ys = np.linspace(a[0], q[0], n), np.linspace(a[1], q[1], n)
                    v = [float(map_coordinates(p[k], [ys, xs], order=1, mode="nearest").mean()) for k in (2, 3, 4)]
                    pa = (to_m[0] * a[0] + to_m[4], to_m[3] * a[1] + to_m[5]); pb = (to_m[0] * q[0] + to_m[4], to_m[3] * q[1] + to_m[5])
                    out.append((pa, pb, kinds[int(np.argmax(v))]))
    return out
