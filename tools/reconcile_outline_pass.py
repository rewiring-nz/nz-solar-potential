"""Lines and faces made to agree with an outline corrected after the lines
were drawn.

The outline pass (tools/build_label_bundle.py --outline-pass) reopens roofs
drawn on the surveyed outline so the outline can be corrected: a balcony or
deck taken out, a shifted edge moved onto the roof. The lines were drawn
against the old outline, so against the new one:

  - a line that now runs along the outline is the outline, not a line between
    two faces (a drop edge drawn in front of a balcony): dropped;
  - a line is cut to the part inside the new outline;
  - a line end that sat on the old outline, or now stops within SNAP_M of the
    new one, is carried along the line's own direction onto the new outline,
    if that takes EXTEND_MAX_M or less; a longer reach is reported, not made.

Faces are then the areas the outline and the lines enclose. Writes a label
file tools/ingest_labels.py takes, and prints every roof where a line could
not be reconciled or the face count differs from the marking page's own.

    python tools/reconcile_outline_pass.py <raw pass json> <out labels json>
"""
import json, sys
import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

ON_M, SNAP_M, EXTEND_MAX_M, MIN_LINE_M, MIN_FACE_M2 = 0.3, 0.4, 1.5, 0.3, 0.5


def reconcile(rec):
    new = Polygon(rec["outline"]).buffer(0)
    if new.geom_type == "MultiPolygon":
        new = max(new.geoms, key=lambda g: g.area)
    old = Polygon(rec["surveyed"]).buffer(0)
    edge, old_edge = new.exterior, old.boundary
    lines, notes = [], []
    for ln in rec["lines"]:
        a, b = np.array(ln["a"], float), np.array(ln["b"], float)
        g = LineString([a, b])
        if g.length < MIN_LINE_M:
            continue
        if all(edge.distance(Point(*p)) <= ON_M for p in (a, b, (a + b) / 2)):
            notes.append("a line along the new outline dropped"); continue
        was_on = [old_edge.distance(Point(*p)) <= ON_M for p in (a, b)]
        inside = g.intersection(new.buffer(0.02))
        parts = [p for p in getattr(inside, "geoms", [inside]) if p.geom_type == "LineString" and p.length >= MIN_LINE_M]
        if not parts:
            notes.append("a line outside the new outline dropped"); continue
        p = max(parts, key=lambda q: q.length)
        ends = [np.array(p.coords[0]), np.array(p.coords[-1])]
        # keep the ends in the line's own order
        if np.hypot(*(ends[0] - a)) > np.hypot(*(ends[1] - a)):
            ends.reverse()
        u = (b - a) / np.hypot(*(b - a))
        for i, sign in ((0, -1.0), (1, 1.0)):
            e = ends[i]; d = edge.distance(Point(*e))
            if d <= 0.03:
                continue
            if not (was_on[i] or d <= SNAP_M):
                continue                              # an end inside the roof stays where it is
            ray = LineString([e, e + sign * u * (EXTEND_MAX_M + 5.0)])
            hit = ray.intersection(edge)
            pts = [q for q in getattr(hit, "geoms", [hit]) if q.geom_type == "Point"]
            if not pts:
                notes.append("a line end does not reach the new outline"); continue
            q = min(pts, key=lambda t: t.distance(Point(*e)))
            reach = q.distance(Point(*e))
            if reach > EXTEND_MAX_M:
                notes.append(f"a line end is {reach:.1f} m short of the new outline: left"); continue
            ends[i] = np.array([q.x, q.y])
        out = dict(ln); out["a"] = [round(float(v), 3) for v in ends[0]]; out["b"] = [round(float(v), 3) for v in ends[1]]
        out["points"] = [out["a"], out["b"]]
        lines.append(out)
    net = [LineString(list(edge.coords))]
    for ln in lines:
        a, b = np.array(ln["a"]), np.array(ln["b"]); u = (b - a) / max(np.hypot(*(b - a)), 1e-9)
        net.append(LineString([a - u * 0.03, b + u * 0.03]))     # a hair long, so ends cross what they touch
    faces = [f for f in polygonize(unary_union(net)) if f.area >= MIN_FACE_M2 and new.buffer(0.05).contains(f.representative_point())]
    nop = [Point(*p) for p in rec.get("nopanel") or []]
    fdoc = [{"ring": [[round(x, 3), round(y, 3)] for x, y in f.exterior.coords], "m2": round(f.area, 1),
             "usable": not any(f.contains(p) for p in nop)} for f in faces]
    return lines, fdoc, notes


def main():
    raw = json.load(open(sys.argv[1]))
    out = {"tool": "label_template", "saved_utc": raw["saved_utc"], "crs": "EPSG:2193", "buildings": {}}
    for bid, rec in raw["buildings"].items():
        lines, faces, notes = reconcile(rec)
        page = len(rec.get("faces") or [])
        if len(faces) != page:
            notes.append(f"faces {len(faces)} here, {page} on the page")
        out["buildings"][bid] = {"area": rec["area"], "address": rec.get("address", ""), "outline": rec["outline"],
                                 **({"olines": rec["olines"]} if rec.get("olines") else {}),
                                 "lines": lines, "obstructions": rec.get("obstructions") or [], "nopanel": rec.get("nopanel") or [],
                                 "complete": bool(rec.get("complete")), "problem": rec.get("problem"), "faces": faces}
        print(bid, rec["area"], "changed" if rec.get("changed") else "confirmed", f"lines {len(rec['lines'])} -> {len(lines)}",
              f"faces {len(faces)}", "; ".join(sorted(set(notes))))
    json.dump(out, open(sys.argv[2], "w"), indent=1)


if __name__ == "__main__":
    main()
