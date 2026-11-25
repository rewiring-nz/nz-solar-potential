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
    if not len(p):                                    # a roof with no usable face has no outer edge
        return []
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


def closed_lines(d, rings):
    """HxW bool: the edges between the faces of the completed drawing.

    The drawn lines alone stop where the marker's hand stopped: a ridge 0.5 m
    short of the eave, a valley that ends beside the line it meets. The faces
    rebuilt from them (tools/experiments/joined_faces.py) carry every such
    end on to what it meets, and a face edge exists along the carried part
    too. A target drawn from the raw lines teaches the reader to stop short
    there and gives the converter a gap to merge two faces through: of the
    100 reference roofs, the raw drawn lines fed to the converter as maps
    lost a drawn face on 27. `rings`: the rebuilt faces in NZTM."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    size = d["weight"].shape[0]
    mpp, b0, b1, b2, b3, sx, sy = [float(v) for v in d["meta"]]
    dx, dy = (sx, sy) if str(d["region"]) == "pilot" else (0.0, 0.0)
    px = lambda x, y: ((x + dx - b0) / (b2 - b0) * size, (1 - (y + dy - b1) / (b3 - b1)) * size)
    faces = [Polygon(r).buffer(0) for r in rings if len(r) >= 3]
    faces = [f for f in faces if not f.is_empty and f.geom_type == "Polygon"]
    if len(faces) < 2:
        return np.zeros((size, size), bool)
    roof = unary_union([f.buffer(0.02) for f in faces]).buffer(-0.02)
    im = Image.new("L", (size, size), 0); dr = ImageDraw.Draw(im)
    for f in faces:
        dr.line([px(*c) for c in f.exterior.coords], fill=255, width=LINE_PX)
    inner = Image.new("L", (size, size), 0)
    for g in getattr(roof, "geoms", [roof]):
        ImageDraw.Draw(inner).polygon([px(*c) for c in g.exterior.coords], fill=255)
    return (np.array(im) > 0) & binary_erosion(np.array(inner) > 0, iterations=4)


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
        # 10 of 157 drawn roofs have face rings that miss most of their drawn
        # lines (faces drawn apart, so their edges read as outer edge): the
        # drawn lines inside the roof are lines between faces too
        drawn = (t[..., 2] | t[..., 3] | t[..., 4]) & binary_erosion(t[..., 7], iterations=4)
        t[..., 0] |= drawn
        t[..., 1] &= ~binary_dilation(drawn, iterations=4)
        # a face marked as never able to take a panel (a wall inside the
        # outline, a canopy, a balcony: 67 faces on 37 drawn roofs) is not
        # roof: it leaves the roof map, and its edge is outer edge
        notroof = Image.new("L", (size, size), 0); dn = ImageDraw.Draw(notroof)
        for f in label["faces"]:
            if f.get("ring") and len(f["ring"]) >= 3 and not f.get("usable", True):
                dn.polygon([px(*p) for p in f["ring"]], fill=255)
        notroof = np.array(notroof) > 0
        if notroof.any():
            roof = roof & ~notroof
            t[..., 7] = roof
            t[..., 5] = binary_dilation(roof) & ~binary_erosion(roof)
            t[..., 1] &= binary_erosion(roof, iterations=2)
            t[..., 0] &= binary_erosion(roof, iterations=3)
            for ch in KINDS.values():
                t[..., ch] &= binary_dilation(roof, iterations=2)
        rings = [Polygon(f["ring"]).buffer(0) for f in label["faces"]
                 if f.get("ring") and len(f["ring"]) >= 3 and f.get("usable", True)]
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
        # The roof's outer edge is only as good as the outline under the
        # drawing. On most roofs drawn before 22 Nov the surveyed outline was
        # left as it was (balconies and decks included), so those roofs teach
        # lines and joins but not the edge or the roof map; a roof whose
        # outline was drawn or corrected (`outline` in its label) teaches both.
        if not label.get("outline"):
            known[5] = known[7] = False
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
