"""Typed targets for the roof-line reader, from everything the markup records.

The markup (data/roof_labels.json) gives each line a kind (ridge, valley or
cliff: a drop edge), the roof's outer edge (the union of its faces) and the
points where lines end and meet. The first training set kept one fact only:
"a line between two faces is here", with the outer edge erased. This writes
eight target maps per roof, on the grid of the sample already exported:

    0 line     any line between two faces (as before)
    1 core     the inside of each face (as before)
    2 ridge    3 valley    4 cliff      the drawn lines by kind
    5 outline  the roof's outer edge
    6 join     where drawn lines end or meet, and the outline's corners
    7 roof     inside the roof

`known` says which maps a sample can teach: a hand-drawn roof all eight, a
paid reading 0, 1, 5 and 7 (it has no line kinds, and its corners are not
trusted). `near` is the roof grown by NEAR_PX, the area the line maps are
scored on; the outline and roof maps are scored on the whole picture.

    python tools/line_targets.py data/line_set/lines_set data/line_set/typed_set
"""
import json, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_dilation, binary_erosion

ROOT = Path(__file__).resolve().parents[1]
CHANNELS = ["line", "core", "ridge", "valley", "cliff", "outline", "join", "roof"]
KINDS = {"ridge": 2, "valley": 3, "cliff": 4}
LINE_PX, JOIN_PX, NEAR_PX = 3, 3, 8
JOIN_MERGE_M, CORNER_DEG, CORNER_MIN_M = 0.3, 25.0, 0.6


def corners(ring):
    """The vertices of a ring where it turns by CORNER_DEG or more."""
    p = np.asarray(ring, float)
    if len(p) > 1 and np.allclose(p[0], p[-1]):
        p = p[:-1]
    keep = [p[0]]
    for q in p[1:]:                                   # drop vertices closer than CORNER_MIN_M
        if np.hypot(*(q - keep[-1])) >= CORNER_MIN_M:
            keep.append(q)
    p = np.array(keep); out = []
    for i in range(len(p)):
        a, b = p[i] - p[i - 1], p[(i + 1) % len(p)] - p[i]
        na, nb = np.hypot(*a), np.hypot(*b)
        if na < 1e-6 or nb < 1e-6:
            continue
        turn = np.degrees(np.arccos(np.clip(np.dot(a, b) / (na * nb), -1, 1)))
        if turn >= CORNER_DEG:
            out.append(p[i])
    return out


def typed(d, label):
    """(target HxWx8 bool, known 8 bool, near HxW bool) for one exported sample."""
    old, roof = d["target"].astype(bool), d["weight"].astype(bool)
    size = roof.shape[0]
    mpp, b0, b1, b2, b3, sx, sy = [float(v) for v in d["meta"]]
    t = np.zeros((size, size, 8), bool)
    t[..., 0], t[..., 1], t[..., 7] = old[..., 0], old[..., 1], roof
    t[..., 5] = binary_dilation(roof) & ~binary_erosion(roof)
    known = np.array([1, 1, 0, 0, 0, 1, 0, 1], bool)
    if label is not None:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
        # pilot drawings are in the LiDAR frame and move with the drawing shift
        dx, dy = (sx, sy) if str(d["region"]) == "pilot" else (0.0, 0.0)

        def px(x, y):
            return ((x + dx - b0) / (b2 - b0) * size, (1 - (y + dy - b1) / (b3 - b1)) * size)
        ends = []
        for kind, ch in KINDS.items():
            im = Image.new("L", (size, size), 0); dr = ImageDraw.Draw(im)
            for ln in label.get("lines") or []:
                if ln.get("kind") != kind:
                    continue
                pts = ln.get("points") or [ln["a"], ln["b"]]
                dr.line([px(*p) for p in pts], fill=255, width=LINE_PX)
                ends += [tuple(pts[0]), tuple(pts[-1])]
            t[..., ch] = np.array(im) > 0
        rings = [Polygon(f["ring"]).buffer(0) for f in label["faces"] if f.get("ring") and len(f["ring"]) >= 3]
        outer = unary_union([r.buffer(0.02) for r in rings]).buffer(-0.02)
        for g in getattr(outer, "geoms", [outer]):
            ends += [tuple(c) for c in corners(g.exterior.coords)]
        joins = []
        for e in ends:                                # one join per meeting point
            if all(np.hypot(e[0] - j[0], e[1] - j[1]) > JOIN_MERGE_M for j in joins):
                joins.append(e)
        im = Image.new("L", (size, size), 0); dr = ImageDraw.Draw(im)
        for j in joins:
            x, y = px(*j)
            dr.ellipse([x - JOIN_PX, y - JOIN_PX, x + JOIN_PX, y + JOIN_PX], fill=255)
        t[..., 6] = np.array(im) > 0
        known[:] = True
    near = binary_dilation(roof, iterations=NEAR_PX)
    return t, known, near


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    labels = json.loads((ROOT / "data/roof_labels.json").read_text())["buildings"]
    for kind in ("hand", "vision"):
        (out / kind).mkdir(parents=True, exist_ok=True)
        n = 0
        for f in sorted((src / kind).glob("*.npz")):
            d = np.load(f)
            t, known, near = typed(d, labels.get(f.stem) if kind == "hand" else None)
            np.savez_compressed(out / kind / f.name, image=d["image"], target=t, known=known, near=near,
                                weight=d["weight"], meta=d["meta"], region=d["region"])
            n += 1
        print(kind, n, flush=True)


if __name__ == "__main__":
    main()
