"""Roof faces, roof edge and obstructions read from the photo by a vision model.

THE PROBLEM. Pinned notes on 24 houses built from the LiDAR returns
(data/blind_check/owner_notes_13nov_point_roof_houses.json) found 2 right.
The faults were judgements the photo settles and nine returns per square
metre do not: a balcony taken for roof, a dormer cut into odd faces, a
skylight not seen, an outline that is not the roof edge.

THE RULE. A vision model is shown a gridded crop of the photo and traces
what a person would: each roof face, the obstructions on it and what is not
roof (PROMPT). Its reading is in photo pixels. Here it becomes the building's
reading: the faces are snapped together, the union of the faces is the roof
edge (it replaces the surveyed outline, as a hand-drawn outline does), and
the whole reading is slid onto the roof the LiDAR sees (point_roof.register)
so each face takes its pitch and heights from the returns under it. The
slide is stored; the drawing is moved back by it to sit on the photo where
it was traced. A reading the model itself rates under MIN_CONFIDENCE is not
used (dark, blurred, shadowed or overexposed photos).

MEASURED (14 Nov, 24 hand-drawn pilot dev roofs, the reader never shown the
markup, benchmark arithmetic): line F 0.664, lines right 15, against faces
from the returns 0.621 / 11 and production 0.575 / 11. Readings rated 0.5 or
over (15 roofs) scored 0.81; under 0.5 (9 roofs) 0.41.

On by default; SOLAR_VISION_ROOF=0 turns it off. Hand-drawn markup still wins.

Usage:
  python src/vision_roof.py prep <region> --ids ... --out <dir>
      writes <dir>/img/<key>_a.jpg (grid), <key>_b.jpg (grid + surveyed
      outline) and <dir>/key.json; a reader writes <dir>/out/<key>.json
  python src/vision_roof.py read <dir> [--model m] [--max-usd 5]
      the reader as a paid API call per crop; prints what it spent
  python src/vision_roof.py batch-submit <dir> --max-usd N   (half price, slower)
  python src/vision_roof.py batch-collect <dir>
  python src/vision_roof.py ingest <dir> [--faces-dir data/vision_faces]
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATA = Path(__file__).resolve().parent.parent / "data"
# SOLAR_VISION_FACES_DIR reads another set of readings (a trial of a
# different reader) without touching the stored ones
VISION_FACES_DIR = Path(os.environ.get("SOLAR_VISION_FACES_DIR") or DATA / "vision_faces")
# the face-region model's readings, same format, for roofs with no vision
# reading (tools/student_faces.py)
STUDENT_FACES_DIR = Path(os.environ.get("SOLAR_STUDENT_FACES_DIR") or DATA / "student_faces")
USE_VISION_ROOF = os.environ.get("SOLAR_VISION_ROOF", "1") == "1"  # on; =0 turns it off


def reading_path(building_id):
    """The file a building's photo reading is in: the vision model's, else
    the face-region model's; None when it has neither or readings are off."""
    if not USE_VISION_ROOF or building_id is None:
        return None
    for d in (VISION_FACES_DIR, STUDENT_FACES_DIR):
        try:
            fp = d / f"{int(building_id)}.json"
        except (TypeError, ValueError):
            return None
        if fp.exists():
            return fp
    return None
MIN_CONFIDENCE = 0.5   # 24 drawn roofs: line F 0.81 at or over, 0.41 under
CROP_PX = 1260
GRID_PX = 90
PAD_M = 5.0            # the surveyed outline sits up to ~2 m off the roof edge
SNAP_M = 0.3           # a traced corner is good to ~5 px (0.1-0.5 m)
MIN_FACE_M2 = 1.0
BRIDGE_M = (0.5, 1.0, 1.5)  # half-widths tried in turn; joins gaps up to 3 m
MIN_OBST_M2 = 0.05

PROMPT = """You are tracing roof shapes from aerial photos, as a careful human draughtsperson would. Read only the two images named; do not open anything else.

1. <key>_b.jpg: the target building is the one under the cyan outline (a rough surveyed footprint; it may be up to ~2 m off or the wrong shape; trust the photo for the real roof edge).
2. <key>_a.jpg: the same photo without the outline. Both are 1260x1260 px with a yellow grid every 90 px, labelled with pixel coordinates (x right, y down). North is up.
3. Work out the roof: every separate roof face (a planar piece of roof: each side of a ridge, each hip, each flat deck, lean-tos, dormers large enough to hold a solar panel, i.e. over ~2 m across). Faces meet at ridges, hips, valleys and height steps; trace those lines where the photo shows them (tone change between sunlit and shaded slopes, ridge capping, shadows at steps). Adjacent faces share their common edge exactly (same vertex coordinates). Together the faces cover the whole roof of the target building out to the real eaves, and nothing else: leave out balconies, terraces, decks, pergolas, ground, neighbouring buildings.
4. List obstructions on the roof: chimneys, skylights, vents, small dormers, plant, existing solar panels or water heaters, as small polygons.
5. Write out/<key>.json (pixel coordinates, integers, rings not closed):
{"faces":[{"ring":[[x,y],...],"kind":"pitched"|"flat","downslope":"N|NE|E|SE|S|SW|W|NW|none"}], "obstructions":[[[x,y],...]], "not_roof":[{"ring":[[x,y],...],"what":"balcony"}], "confidence":0-1, "notes":"one sentence on anything doubtful"}

Locate each corner to within ~5 px from the grid labels and check each ring against the image before writing. Straight edges of a building are usually parallel or perpendicular to each other: keep them so."""


def vision_doc(building_id):
    """The building's vision reading, or None (off, absent or unreadable)."""
    fp = reading_path(building_id)
    if fp is None:
        return None
    try:
        return json.loads(fp.read_text())
    except (OSError, ValueError, TypeError):
        return None


def _polys(rings, min_area):
    from shapely.geometry import Polygon
    out = []
    for r in rings or []:
        try:
            p = Polygon(r).buffer(0)
        except Exception:
            continue
        for g in (p.geoms if hasattr(p, "geoms") else [p]):
            if g.area >= min_area:
                out.append(g)
    return out


def vision_outline(building_id):
    """The roof edge the reader traced, in the LiDAR frame, or None."""
    d = vision_doc(building_id)
    if not d or not d.get("outline"):
        return None
    p = _polys([d["outline"]], 1.0)
    return max(p, key=lambda g: g.area) if p else None


def vision_obstructions(building_id):
    """The obstructions the reader listed, in the LiDAR frame."""
    d = vision_doc(building_id)
    return _polys(d.get("obstructions"), MIN_OBST_M2) if d else []


def vision_shift(building_id):
    """(dx, dy) the reading was slid by, photo -> LiDAR; the drawing moves
    back by its negative. None when there is no reading."""
    d = vision_doc(building_id)
    if not d:
        return None
    s = d.get("shift") or [0.0, 0.0]
    return float(s[0]), float(s[1])


def write_crops(region, ids, out):
    import numpy as np
    import rasterio
    from PIL import Image, ImageDraw
    from rasterio.windows import from_bounds
    from src.region_build import area_paths
    from src import point_roof as pr
    out = Path(out)
    (out / "img").mkdir(parents=True, exist_ok=True)
    (out / "out").mkdir(exist_ok=True)
    O = pr.outlines(region)
    img = rasterio.open(area_paths(region)["imagery"])
    key = []

    def grid(im):
        d = ImageDraw.Draw(im)
        for i in range(0, CROP_PX, GRID_PX):
            d.line([(i, 0), (i, CROP_PX)], fill=(255, 255, 0), width=1)
            d.line([(0, i), (CROP_PX, i)], fill=(255, 255, 0), width=1)
            d.text((i + 2, 2), str(i), fill=(255, 255, 0))
            d.text((2, i + 2), str(i), fill=(255, 255, 0))
        return im

    for n, bid in enumerate(ids, 1):
        g = O.get(int(bid))
        if g is None:
            continue
        x0, y0, x1, y1 = g.bounds
        s = max(x1 - x0, y1 - y0) + 2 * PAD_M
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        b = (cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2)
        px = np.moveaxis(img.read([1, 2, 3], window=from_bounds(*b, transform=img.transform), boundless=True), 0, -1)
        base = Image.fromarray(px).resize((CROP_PX, CROP_PX), Image.LANCZOS)
        k = f"v{n:03d}"
        grid(base.copy()).save(out / "img" / f"{k}_a.jpg", quality=88)
        t = base.copy()
        d = ImageDraw.Draw(t)
        for p in (g.geoms if hasattr(g, "geoms") else [g]):
            d.line([((x - b[0]) / s * CROP_PX, (b[3] - y) / s * CROP_PX) for x, y in p.exterior.coords],
                   fill=(0, 255, 255), width=2)
        grid(t).save(out / "img" / f"{k}_b.jpg", quality=88)
        key.append({"key": k, "id": int(bid), "bounds": b, "m_per_px": s / CROP_PX})
    (out / "key.json").write_text(json.dumps(key, indent=1))
    print(f"{len(key)} crops -> {out}")


def to_doc(reading, bounds, m_per_px, register=True):
    """A reader's pixel reading as a faces document in the LiDAR frame, or
    None when it holds no usable face."""
    from shapely import snap
    from shapely.affinity import translate
    from shapely.ops import unary_union
    w = lambda ring: [(bounds[0] + x * m_per_px, bounds[3] - y * m_per_px) for x, y in ring]
    not_roof = _polys([w(n["ring"]) for n in reading.get("not_roof") or [] if len(n.get("ring") or []) >= 3], 0.2)
    void = unary_union(not_roof) if not_roof else None
    faces, done = [], None
    for f in reading.get("faces") or []:
        if len(f.get("ring") or []) < 3:
            continue
        for p in _polys([w(f["ring"])], MIN_FACE_M2):
            # corners traced a few pixels apart are the same corner; a face
            # never overlaps one already placed, nor anything named not-roof
            if done is not None:
                p = snap(p, done, SNAP_M).buffer(0).difference(done)
            if void is not None:
                p = p.difference(void)
            for g in (p.geoms if hasattr(p, "geoms") else [p]):
                if g.geom_type == "Polygon" and g.area >= MIN_FACE_M2:
                    faces.append(g)
                    done = g if done is None else unary_union([done, g])
    if not faces:
        return None
    # one roof edge round every face: parts of a roof the reader traced a
    # gap apart (a lower roof past a gutter or a shadow) are bridged by the
    # smallest closing that joins them; a part further off than BRIDGE_M is
    # another building and is dropped
    for close in (SNAP_M / 2,) + BRIDGE_M:
        edge = unary_union([g.buffer(close) for g in faces]).buffer(-close)
        if not hasattr(edge, "geoms"):
            break
    edge = max(edge.geoms, key=lambda g: g.area) if hasattr(edge, "geoms") else edge
    faces = [g for g in faces if g.representative_point().within(edge.buffer(0.05))]
    obst = [o for o in _polys([w(r) for r in reading.get("obstructions") or [] if len(r) >= 3], MIN_OBST_M2)
            if o.intersects(edge)]
    dx = dy = 0.0
    if register:
        from src import point_roof as pr
        dx, dy = pr.register(edge)
    ring = lambda g: [[round(x + dx, 2), round(y + dy, 2)] for x, y in g.exterior.coords]
    return {"source": "vision", "score": 1.0, "confidence": reading.get("confidence"),
            "shift": [dx, dy], "outline": ring(edge), "faces": [ring(g) for g in faces],
            "obstructions": [ring(o) for o in obst]}


def ingest(work, faces_dir=VISION_FACES_DIR, min_confidence=MIN_CONFIDENCE):
    work, faces_dir = Path(work), Path(faces_dir)
    faces_dir.mkdir(parents=True, exist_ok=True)
    used = unsure = missing = 0
    for k in json.loads((work / "key.json").read_text()):
        f = work / "out" / f"{k['key']}.json"
        dst = faces_dir / f"{k['id']}.json"
        try:
            reading = json.loads(f.read_text())
        except (OSError, ValueError):
            missing += 1
            continue
        if float(reading.get("confidence") or 0) < min_confidence:
            unsure += 1
            dst.unlink(missing_ok=True)
            continue
        doc = to_doc(reading, k["bounds"], k["m_per_px"])
        if doc is None:
            missing += 1
            continue
        dst.write_text(json.dumps(doc))
        used += 1
        print(f"  {k['key']} {k['id']}: {len(doc['faces'])} faces, {len(doc['obstructions'])} obstructions, "
              f"confidence {reading.get('confidence')}, slide {doc['shift']}")
    print(f"{used} readings written to {faces_dir}; {unsure} under confidence {min_confidence}; {missing} missing or empty")


# USD per million tokens (input, output), Anthropic price list of 3 Nov 2026.
# Only for the spend this tool reports; the bill is the authority.
PRICES = {"claude-fable-5-2": (8.0, 40.0), "claude-opus-5-5": (4.0, 20.0)}
READER_MODEL = "claude-fable-5-2"   # the model the measurements above were made with

API_PROMPT = PROMPT.replace(
    "Read only the two images named; do not open anything else.", "Two images follow."
).replace("1. <key>_b.jpg:", "1. The first image:").replace("2. <key>_a.jpg:", "2. The second image:").replace(
    "5. Write out/<key>.json (pixel coordinates, integers, rings not closed):",
    "5. Reply with one JSON object and nothing else (pixel coordinates, integers, rings not closed):")


def read_one(client, work, key, model, effort):
    """One reader call for one crop: (reading dict or None, usd, stop_reason)."""
    import base64
    content = []
    for tag in ("b", "a"):
        data = base64.standard_b64encode((work / "img" / f"{key}_{tag}.jpg").read_bytes()).decode()
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
    content.append({"type": "text", "text": API_PROMPT})
    # no fallback model: a reading must come from the model it is scored as;
    # a refusal is recorded and the roof falls back to the other readings
    with client.messages.stream(model=model, max_tokens=32000, output_config={"effort": effort},
                                messages=[{"role": "user", "content": content}]) as stream:
        r = stream.get_final_message()
    pin, pout = PRICES.get(model, (0.0, 0.0))
    usd = (r.usage.input_tokens * pin + r.usage.output_tokens * pout) / 1e6
    text = "".join(b.text for b in r.content if b.type == "text")
    try:
        reading = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except ValueError:
        reading = None
    return reading, usd, r.stop_reason, (r.usage.input_tokens, r.usage.output_tokens)


def read_all(work, model=READER_MODEL, out_dir="out", effort="high", jobs=4, max_usd=5.0):
    """Read every crop in `work` that has no reading yet. Stops starting new
    calls once the spend passes `max_usd`."""
    import anthropic
    from concurrent.futures import ThreadPoolExecutor
    from src import project_secrets
    work = Path(work)
    (work / out_dir).mkdir(exist_ok=True)
    client = anthropic.Anthropic(api_key=project_secrets.anthropic_api_key())
    todo = [k["key"] for k in json.loads((work / "key.json").read_text())
            if not (work / out_dir / f"{k['key']}.json").exists()]
    spent = [0.0]
    tok = [0, 0]

    def one(key):
        if spent[0] >= max_usd:
            return key, "skipped: spend cap"
        try:
            reading, usd, stop, (ti, to) = read_one(client, work, key, model, effort)
        except anthropic.APIError as e:
            return key, f"error: {type(e).__name__}"
        spent[0] += usd
        tok[0] += ti
        tok[1] += to
        if reading is None:
            return key, f"no JSON ({stop})"
        (work / out_dir / f"{key}.json").write_text(json.dumps(reading))
        return key, f"{len(reading.get('faces') or [])} faces, confidence {reading.get('confidence')}, ${usd:.3f}"

    with ThreadPoolExecutor(jobs) as ex:
        for key, msg in ex.map(one, todo):
            print(f"  {key}: {msg}", flush=True)
    n = max(len(todo), 1)
    print(f"{model}: {len(todo)} crops, ${spent[0]:.2f} (${spent[0] / n:.3f} each; "
          f"{tok[0] // n} tokens in, {tok[1] // n} out per crop)")


BATCH_USD_PER_ROOF = 0.17   # measured 14 Nov: 4.7k tokens in, 7.4k out, fable-5-2, effort high, at half price


def batch_submit(work, model=READER_MODEL, effort="high", max_usd=0.0):
    """Send every unread crop in `work` as one Message Batch (half price,
    results within a day). Refuses when the estimate passes `max_usd`."""
    import base64
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    from src import project_secrets
    work = Path(work)
    (work / "out").mkdir(exist_ok=True)
    todo = [k["key"] for k in json.loads((work / "key.json").read_text())
            if not (work / "out" / f"{k['key']}.json").exists()]
    est = len(todo) * BATCH_USD_PER_ROOF
    print(f"{len(todo)} crops, estimate ${est:.0f} at ${BATCH_USD_PER_ROOF} a roof (an estimate, not a cap)")
    if est > max_usd:
        raise SystemExit(f"estimate ${est:.0f} is over --max-usd {max_usd:.0f}; nothing sent")
    reqs = []
    for key in todo:
        content = []
        for tag in ("b", "a"):
            data = base64.standard_b64encode((work / "img" / f"{key}_{tag}.jpg").read_bytes()).decode()
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
        content.append({"type": "text", "text": API_PROMPT})
        reqs.append(Request(custom_id=key, params=MessageCreateParamsNonStreaming(
            model=model, max_tokens=32000, output_config={"effort": effort},
            messages=[{"role": "user", "content": content}])))
    client = anthropic.Anthropic(api_key=project_secrets.anthropic_api_key())
    ids = []
    # a batch holds at most 256 MB; two ~250 kB images a roof -> 300 roofs a batch
    for i in range(0, len(reqs), 300):
        b = client.messages.batches.create(requests=reqs[i:i + 300])
        ids.append(b.id)
        print(f"  batch {b.id}: {len(reqs[i:i + 300])} requests, {b.processing_status}")
    (work / "batch.json").write_text(json.dumps({"model": model, "ids": ids}))


def batch_collect(work):
    """Write the readings of the finished batches of `work`; say what is
    still running and what was spent."""
    import anthropic
    from src import project_secrets
    work = Path(work)
    meta = json.loads((work / "batch.json").read_text())
    client = anthropic.Anthropic(api_key=project_secrets.anthropic_api_key())
    pin, pout = PRICES.get(meta["model"], (0.0, 0.0))
    usd, done, bad, running = 0.0, 0, 0, 0
    for bid in meta["ids"]:
        b = client.messages.batches.retrieve(bid)
        if b.processing_status != "ended":
            running += b.request_counts.processing
            continue
        for r in client.messages.batches.results(bid):
            if r.result.type != "succeeded":
                bad += 1
                continue
            m = r.result.message
            usd += (m.usage.input_tokens * pin + m.usage.output_tokens * pout) / 2e6
            text = "".join(c.text for c in m.content if c.type == "text")
            try:
                reading = json.loads(text[text.index("{"):text.rindex("}") + 1])
            except ValueError:
                bad += 1
                continue
            (work / "out" / f"{r.custom_id}.json").write_text(json.dumps(reading))
            done += 1
    print(f"{done} readings written, {bad} failed, {running} still running; ${usd:.2f} spent on the finished ones")


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prep")
    p.add_argument("region")
    p.add_argument("--ids", type=int, nargs="+", required=True)
    p.add_argument("--out", required=True)
    i = sub.add_parser("ingest")
    i.add_argument("work")
    i.add_argument("--faces-dir", default=str(VISION_FACES_DIR))
    i.add_argument("--min-confidence", type=float, default=MIN_CONFIDENCE)
    r = sub.add_parser("read")
    r.add_argument("work")
    r.add_argument("--model", default=READER_MODEL)
    r.add_argument("--out-dir", default="out")
    r.add_argument("--effort", default="high")
    r.add_argument("--jobs", type=int, default=4)
    r.add_argument("--max-usd", type=float, default=5.0)
    bs = sub.add_parser("batch-submit")
    bs.add_argument("work")
    bs.add_argument("--model", default=READER_MODEL)
    bs.add_argument("--max-usd", type=float, required=True)
    bc = sub.add_parser("batch-collect")
    bc.add_argument("work")
    a = ap.parse_args()
    if a.cmd == "batch-submit":
        return batch_submit(a.work, a.model, max_usd=a.max_usd)
    if a.cmd == "batch-collect":
        return batch_collect(a.work)
    if a.cmd == "read":
        return read_all(a.work, a.model, a.out_dir, a.effort, a.jobs, a.max_usd)
    if a.cmd == "prep":
        write_crops(a.region, a.ids, a.out)
    else:
        ingest(a.work, a.faces_dir, a.min_confidence)


if __name__ == "__main__":
    main()
