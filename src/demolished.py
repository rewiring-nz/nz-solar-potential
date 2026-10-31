"""Buildings demolished or replaced since the survey: found in the photos, withheld.

WHAT GOES WRONG. The LiDAR and the LINZ outlines are 2021; the map's photo
is 2026. An outline whose building has since been knocked down still gets a
roof read from the 2021 returns, panels and a full estimate -- 18 Beetham St
(5373533) shows 26 panels on what is now a road. LINZ still lists such
outlines as current, so the outlines cannot say it; only the photos can.

WHAT SEPARATES "GONE" FROM "RE-ROOFED". register_imagery already flags a roof
whose photo changed (brightness and edge structure inside the outline), and
on its own that flag is mostly re-roofs, new colours and shade. A standing
building, re-roofed or not, still has walls where the outline is: the photo
has a strong edge along the outline and a smooth surface inside it. A
cleared site has neither -- ground, road, gravel or garden runs across the
line. Measured on 240 roofs drawn at random from the district's
changed-photo candidates (gradient correlation under 0.3; 5,701 of 22,385
buildings) and labelled by eye on the two photos: 10 gone, 6 rebuilt on a
different footprint, 6 hidden by shade or trees, 218 standing. In the
current photo, the gone ones had an outline-edge strength (median over the
outline of the strongest across-line gradient within 0.5 m, over the
window's median gradient) of at most 2.55, where 90% of standing roofs had
2.9 or more; edge on at most 44% of the outline, against 49% or more for
90% of standing roofs; and an inside texture of 0.27 or more. A road over
the site defeats the edge test -- 18 Beetham St's lane markings run along
its outline -- but the brightness change catches it.

TWO STAGES. The screen below (SCREEN_*) is built for recall: on the 15 gone
and 10 replaced buildings known (the sample's plus those found by hand) it
keeps all 15 gone and 6 replaced, and 890 of 22,385 buildings district-wide.
Each screened roof goes to a vision referee looking at both photos, and is
withheld only when it says "gone" or "replaced" with confidence
REFEREE_MIN_CONFIDENCE or more. On 63 labelled roofs the referee withheld
none of 30 standing and none of 9 hidden by shade, and 12 of 15 gone and 5
of 9 replaced; a standing roof withheld is worse than a phantom left up.
Answers are cached per building and per input (outline and lean), so a
building is asked once (about $0.004 each); a build without the key, or past
REFEREE_MAX_USD, withholds only what is already cached and says so.

Hand-drawn markup wins: a roof drawn in data/roof_labels.json was traced on
the current photo and is never withheld. Withheld buildings stay on the map
with the reason "demolished" and the card says why.

Writes data/regions/<r>/demolished.json: {building_id: {"key"}} for every
outline screened out, so a rebuild does not screen it again, and
{building_id: {"key", "screen": {...}, "verdict": {...}|None}} for the rest.
and rewrites panel_layouts.geojson in place: every feature of a withheld
building is replaced by one no_estimate feature.

Switches: SOLAR_DEMOLISHED=0 turns the stage off (nothing withheld). The
paid referee asks only when SOLAR_DEMOLISHED_REFEREE=1; unset or 0 screens and
applies cached verdicts but asks nothing. Build entry points set it from a
yes/no question that defaults to no (src/build_plan.py confirm_paid).

Usage: python src/demolished.py <region>
"""

import base64
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.region_build import area_paths, write_json_atomic

REASON = "demolished"
PAD_M = 4.0                # window around the outline the edge scale is taken over
EDGE_STEP_M = 0.2          # outline sampled this often
EDGE_REACH_M = 0.5         # an edge counts within this of the line, either side
MIN_AREA_M2 = 20.0
# the screen (see the header for the 240-roof measurement)
SCREEN_STRUCT = 0.15       # gradient correlation inside the outline, survey photo vs map photo
SCREEN_EDGE_FRAC = 0.7     # share of the outline with an edge over 3x the window's median gradient
SCREEN_TEXTURE = 0.5       # median gradient inside the outline / the window's median gradient
SCREEN_LUM = 60.0          # ...or a change in mean brightness inside the outline (0-255)
# the referee
REFEREE_MODEL = "claude-sonnet-5-5"
REFEREE_PRICE = (3.0, 15.0)        # USD per million tokens in / out, for the budget stop
REFEREE_MAX_USD = float(os.environ.get("SOLAR_DEMOLISHED_MAX_USD", "5"))
REFEREE_MIN_CONFIDENCE = 0.7
WITHHOLD = {"gone", "replaced"}
TILE_PX = 384
CONTEXT_M = 8.0


def _grad(rgb):
    from scipy import ndimage
    lum = ndimage.gaussian_filter(rgb.astype(float).mean(axis=0), 1.0)
    return ndimage.sobel(lum, 1), -ndimage.sobel(lum, 0)      # east, north


def edge_support(gx, gy, ring, origin, res, scale):
    """(strength, fraction) of the outline `ring` carrying an across-line edge.
    strength: median over points every EDGE_STEP_M of the strongest
    |gradient . normal| within EDGE_REACH_M of the line, over `scale`;
    fraction: share of those points over 3 x scale."""
    L = ring.length
    n = max(int(L / EDGE_STEP_M), 20)
    d = np.arange(n) * L / n
    pts = np.array([ring.interpolate(t).coords[0] for t in d])
    fwd = np.array([ring.interpolate(min(t + 0.1, L)).coords[0] for t in d])
    bwd = np.array([ring.interpolate(max(t - 0.1, 0)).coords[0] for t in d])
    t = fwd - bwd
    tl = np.hypot(t[:, 0], t[:, 1])
    ok = tl > 1e-6
    if not ok.any():
        return None
    pts, t, tl = pts[ok], t[ok], tl[ok]
    nx, ny = t[:, 1] / tl, -t[:, 0] / tl
    H, W = gx.shape
    best = np.zeros(len(pts))
    for o in np.arange(-EDGE_REACH_M, EDGE_REACH_M + 1e-9, 0.1):
        x, y = pts[:, 0] + nx * o, pts[:, 1] + ny * o
        c = ((x - origin[0]) / res).astype(int)
        r = ((origin[1] - y) / res).astype(int)
        inside = (r >= 0) & (r < H) & (c >= 0) & (c < W)
        v = np.zeros(len(pts))
        v[inside] = np.abs(gx[r[inside], c[inside]] * nx[inside] + gy[r[inside], c[inside]] * ny[inside])
        best = np.maximum(best, v)
    return float(np.median(best) / (scale + 1e-9)), float(np.mean(best > 3 * scale))


def evidence(geom, ref_ds, tiles, shift):
    """What the screen reads, for one outline (EPSG:2193), or None."""
    from rasterio.features import geometry_mask
    from rasterio.transform import from_origin
    from rasterio.windows import from_bounds
    from src.register_imagery import RES_M
    if geom.geom_type == "MultiPolygon":
        geom = max(geom.geoms, key=lambda g: g.area)
    b = geom.buffer(PAD_M).bounds
    h, w = int((b[3] - b[1]) / RES_M), int((b[2] - b[0]) / RES_M)
    A = ref_ds.read([1, 2, 3], window=from_bounds(*b, ref_ds.transform), out_shape=(3, h, w))
    B = tiles.window((b[0] + shift[0], b[1] + shift[1], b[2] + shift[0], b[3] + shift[1]), h, w)
    inner = geom.buffer(-0.8)
    m = geometry_mask([inner if not inner.is_empty else geom], out_shape=(h, w),
                      transform=from_origin(b[0], b[3], RES_M, RES_M), invert=True)
    if m.sum() < 100:
        return None
    out = {}
    mags = {}
    for tag, X in (("ref", A), ("cur", B)):
        gx, gy = _grad(X)
        g = np.hypot(gx, gy)
        scale = float(np.median(g))
        es = edge_support(gx, gy, geom.exterior, (b[0], b[3]), RES_M, scale)
        if es is None:
            return None
        out[f"edge_{tag}"], out[f"edgefrac_{tag}"] = round(es[0], 3), round(es[1], 3)
        out[f"tex_{tag}"] = round(float(np.median(g[m]) / (scale + 1e-9)), 3)
        out[f"lum_{tag}"] = round(float(X.astype(float).mean(axis=0)[m].mean()), 1)
        mags[tag] = g[m]
    ga, gb = mags["ref"], mags["cur"]
    out["struct"] = round(float(np.corrcoef(ga, gb)[0, 1]), 3) \
        if ga.std() > 1e-6 and gb.std() > 1e-6 else 0.0
    return out


def screened(ev):
    """True when the inside of the outline no longer looks like the survey's
    and either the map's photo shows no wall edge along much of the outline
    over an uneven surface, or the brightness changed outright."""
    if not ev or ev["struct"] >= SCREEN_STRUCT:
        return False
    return bool((ev["edgefrac_cur"] < SCREEN_EDGE_FRAC and ev["tex_cur"] > SCREEN_TEXTURE)
                or abs(ev["lum_ref"] - ev["lum_cur"]) > SCREEN_LUM)


def input_key(geom, shift):
    """What a building's answer depends on besides the photos themselves."""
    s = f"{geom.wkt}|{shift[0]:.2f},{shift[1]:.2f}"
    return hashlib.md5(s.encode()).hexdigest()[:12]


def render_pair(geom, ref_ds, tiles, shift):
    """The survey photo and the map's photo of the outline's surroundings,
    each TILE_PX square with the outline in red."""
    from PIL import Image, ImageDraw
    from rasterio.windows import from_bounds
    from src.register_imagery import RES_M
    if geom.geom_type == "MultiPolygon":
        geom = max(geom.geoms, key=lambda g: g.area)
    c = geom.centroid
    gb = geom.bounds
    half = max(gb[2] - gb[0], gb[3] - gb[1]) / 2 + CONTEXT_M
    b = (c.x - half, c.y - half, c.x + half, c.y + half)
    n = int(2 * half / RES_M)
    A = ref_ds.read([1, 2, 3], window=from_bounds(*b, ref_ds.transform), out_shape=(3, n, n))
    B = tiles.window((b[0] + shift[0], b[1] + shift[1], b[2] + shift[0], b[3] + shift[1]), n, n)
    ims = []
    for X in (A, B):
        im = Image.fromarray(np.ascontiguousarray(X.transpose(1, 2, 0)))
        ImageDraw.Draw(im).line([((x - b[0]) / RES_M, (b[3] - y) / RES_M) for x, y in geom.exterior.coords],
                                fill=(255, 40, 40), width=max(2, n // 180))
        ims.append(im.resize((TILE_PX, TILE_PX)))
    return ims


PROMPT = """Two aerial photos of the same place, north up, with the same red outline drawn on both.
Photo 1 was taken in 2021, when a building's outline was traced: the red line is that building.
Photo 2 was taken in 2026.

Is the 2021 building still there in photo 2? Answer with one word:
- "standing": the same building is still there. A new roof colour or material, solar panels, an extension, or the outline sitting a little off the roof all count as standing.
- "replaced": the old building is gone and a different building stands there whose roof does NOT follow the red outline (a different shape, size or position).
- "gone": no building inside the red outline: ground, grass, garden, trees on open ground, gravel, paving, road, car park, or a construction site / foundations.
- "unclear": shade, trees or blur hide what is inside the outline, so you cannot tell.

Shade is common in photo 2: a dark outline is "unclear", not "gone", unless you can see ground inside it.
Think briefly (two or three sentences), then end with one line of JSON:
{"answer": "<standing|replaced|gone|unclear>", "confidence": <0 to 1>, "why": "<one short sentence>"}"""


def _b64(im):
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def ask(client, ims, model=REFEREE_MODEL):
    """The referee's verdict on one pair of photos: (dict, usd)."""
    content = [{"type": "text", "text": PROMPT}]
    for k, im in enumerate(ims, 1):
        content.append({"type": "text", "text": f"Photo {k}:"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                    "data": _b64(im)}})
    r = client.messages.create(model=model, max_tokens=400, messages=[{"role": "user", "content": content}])
    text = r.content[0].text.strip()
    try:
        ans = json.loads(text[text.rindex("{"):text.rindex("}") + 1])
    except Exception:
        ans = {"answer": None, "raw": text[-300:]}
    ans["model"] = model
    usd = r.usage.input_tokens / 1e6 * REFEREE_PRICE[0] + r.usage.output_tokens / 1e6 * REFEREE_PRICE[1]
    return ans, usd


def withheld_by(verdict):
    """True when a referee verdict withholds the building."""
    return bool(verdict and verdict.get("answer") in WITHHOLD
                and float(verdict.get("confidence") or 0) >= REFEREE_MIN_CONFIDENCE)


def drawn_ids():
    """Roofs with drawn markup: traced on the current photo, so standing."""
    f = Path(__file__).resolve().parents[1] / "data" / "roof_labels.json"
    try:
        B = json.loads(f.read_text())["buildings"]
    except Exception:
        return set()
    return {int(b) for b, v in B.items()
            if v.get("problem") != "absent" and (v.get("faces") or v.get("lines"))}


def withhold(features, ids, outlines_wgs, text):
    """The layout features with every feature of a building in `ids` replaced
    by one no_estimate feature carrying its outline (WGS84 GeoJSON geometry)."""
    out = [f for f in features if int(f["properties"].get("building_id", -1)) not in ids]
    for bid in sorted(ids):
        g = outlines_wgs.get(bid)
        if g is None:
            continue
        out.append({"type": "Feature", "geometry": g,
                    "properties": {"kind": "no_estimate", "building_id": int(bid),
                                   "no_estimate_reason": REASON, "no_estimate_text": text}})
    return out


def detect(region, paths):
    """{building_id: record} for the region, reusing cached answers."""
    import geopandas as gpd
    import rasterio
    from src.register_imagery import _Tiles, drawing_shifts
    from src.surveys import survey_for
    from src.region_build import area_bbox_wgs84
    out_path = paths["dir"] / "demolished.json"
    try:
        cache = json.loads(out_path.read_text())
    except (OSError, ValueError):
        cache = {}
    try:
        sv = survey_for(area_bbox_wgs84(region), region)
    except Exception:
        sv = {}
    ref_layer = sv.get("reference_imagery_layer")
    if not ref_layer or ref_layer == sv.get("imagery_layer"):
        print(f"[{region}] the survey photo is the map's photo -- nothing to compare")
        return {}
    ref_path = paths["reference_imagery"]
    if not ref_path.exists() or not (paths["dir"] / "display_tiles").exists():
        print(f"[{region}] no survey photo or map tiles -- nothing to compare")
        return {}
    ref_ds = rasterio.open(ref_path)
    tiles = _Tiles(paths["dir"] / "display_tiles")
    shifts = drawing_shifts(paths["dir"])
    gdf = gpd.read_file(paths["outlines"]).to_crs("EPSG:2193")
    drawn = drawn_ids()
    referee = os.environ.get("SOLAR_DEMOLISHED_REFEREE", "0") == "1"
    client, spent, asked, skipped, n_screened = None, 0.0, 0, 0, 0
    recs = {}
    t0 = time.time()
    for row in gdf.itertuples():
        bid, g = int(row.building_id), row.geometry
        if g is None or g.is_empty or g.area < MIN_AREA_M2 or bid >= 900_000_000 or bid in drawn:
            continue
        s = shifts.get(str(bid)) or [0.0, 0.0, 0.0]
        key = input_key(g, s)
        old = cache.get(str(bid))
        if old and old.get("key") == key:
            rec = old
        else:
            try:
                ev = evidence(g, ref_ds, tiles, s)
            except Exception:
                ev = None
            rec = {"key": key, "screen": ev, "verdict": None} if screened(ev) else {"key": key}
        recs[str(bid)] = rec
        if "screen" not in rec:
            continue
        n_screened += 1
        if rec.get("verdict") is None and referee:
            if spent >= REFEREE_MAX_USD:
                skipped += 1
            else:
                try:
                    if client is None:
                        import anthropic
                        from src import project_secrets
                        k = project_secrets.anthropic_api_key()
                        if not k:
                            raise RuntimeError("no ANTHROPIC_API_KEY")
                        client = anthropic.Anthropic(api_key=k)
                    v, usd = ask(client, render_pair(g, ref_ds, tiles, s))
                    rec["verdict"], spent, asked = v, spent + usd, asked + 1
                except Exception as exc:
                    print(f"[{region}] referee unavailable ({exc!r}) -- unanswered roofs stay on the map")
                    referee = False
    write_json_atomic(out_path, recs)
    unanswered = sum(1 for r in recs.values() if "screen" in r and r.get("verdict") is None)
    print(f"[{region}] {n_screened} screened, {asked} asked (${spent:.2f}), {unanswered} unanswered"
          + (f", {skipped} left unasked past ${REFEREE_MAX_USD:.0f}" if skipped else "")
          + f" in {time.time() - t0:.0f}s", flush=True)
    return recs


def main():
    region = sys.argv[1]
    from src.preflight import preflight
    preflight("demolished", region)
    paths = area_paths(region)
    lay_path = paths["panel_layouts"]
    if os.environ.get("SOLAR_DEMOLISHED", "1") == "0" or not lay_path.exists():
        return 0
    # A failure here only means nothing is withheld; it must never stop a build.
    try:
        recs = detect(region, paths)
    except Exception as exc:
        print(f"[{region}] WARNING: demolished-building check failed ({exc!r}) -- nothing withheld")
        return 0
    ids = {int(b) for b, r in recs.items() if withheld_by(r.get("verdict"))}
    d = json.loads(lay_path.read_text())
    import geopandas as gpd
    from src.derive_solar_potential import _REASON_TEXT
    gdf = gpd.read_file(paths["outlines"]).to_crs("EPSG:4326")
    wgs = {int(r.building_id): r.geometry.__geo_interface__ for r in gdf.itertuples() if int(r.building_id) in ids}
    before = sum(1 for f in d["features"] if f["properties"].get("kind") == "panel"
                 and int(f["properties"].get("building_id", -1)) in ids)
    d["features"] = withhold(d["features"], ids, wgs, _REASON_TEXT[REASON])
    write_json_atomic(lay_path, d)
    print(f"[{region}] {len(ids)} buildings withheld as demolished or replaced "
          f"({before} panels removed)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
