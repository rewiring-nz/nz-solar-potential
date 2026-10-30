// 3D BUILDINGS: every building in view as real geometry -- vertical walls,
// planar roof faces, and the panels lying flat on those faces.
//
// WHY NOT THE TERRAIN. The old 3D view draped the map over the LiDAR surface
// model (DSM) as MapLibre terrain. A height grid cannot have a vertical wall:
// at 1 m per pixel every roof edge became a smeared slope, and the 2D panel
// and roof layers draped over it hung down those slopes. This replaces it
// with flat ground and a mesh per building, built in the browser from data
// the map already has:
//
//   footprint     the "buildings" vector tiles (buildings-fill)
//   roof faces    the "layout" tiles, kind = "facet", each carrying the
//                 slope_deg / aspect_deg the solar model used for that face
//   panels        the "layout" tiles, kind = "panel" (and "obstruction")
//   heights       the DSM terrain-RGB tiles the old 3D view used
//                 (data/terrain/{z}/{x}/{y}.png, decoded as sun_shadows.js does)
//
// HOW A ROOF FACE GETS ITS HEIGHT. The face's tilt and direction come from
// the tiles (slope_deg, aspect_deg), so the drawn face is the face the solar
// figures were computed for. Only its height is missing, and that is ONE
// number: the DSM is sampled on a 0.6 m grid inside the face, 0.45 m in
// from its edges (a roof edge in a 1 m DSM is a cliff, and a sample on the
// cliff belongs to the ground), and the plane's offset is the median of
// (DSM - tilt), with outliers beyond 2.5 MADs rejected. A face whose tile
// carries no tilt gets a least-squares plane with the same rejection. Faces
// that meet within 0.6 m are then nudged together, so a ridge closes rather
// than showing a sliver of step.
//
// GROUND. Each building stands on the 25th percentile of the DSM sampled 2
// to 5 m outside its footprint -- low enough to skip parked cars, hedges
// and neighbouring roofs (see GROUND_Q for why not lower).
//
// THE LAND AND THE TREES (Josh, 27 Oct: "You have removed trees from the 3D
// view ... we need the other 3D terrain back"). Under the solids the map is
// draped over data/terrain_ground: the same LiDAR surface with every
// building pressed flat to that same ground beside it
// (tools/build_terrain_tiles.py --ground), so hills, trees and hedges stand
// as surveyed and each solid sits on a level pad at its own ground height.
// Heights are built relative to the building's ground and lifted by it when
// drawn (gOf). Where a data version has no ground tiles the map stays flat
// and every building stands at zero, as before.
//
// THE SOLID. A building is the union of: its footprint extruded up to the
// roof (the footprint is filled on a grid whose heights follow the LOWER
// ENVELOPE of the nearby face planes -- the classic roof-from-planes rule,
// so ridges stay sharp and nothing pokes up through a face), each face on
// its own plane, and a wall under every face edge away from the footprint
// edge (a step between two roof levels). Tile-clipped pieces are drawn as
// pieces: the clip-line walls sit inside the solid and cannot be seen.
//
// RENDERING. three.js as a MapLibre custom layer, rendered into a 4x MSAA
// target (the map's own canvas is not antialiased) and composited over the
// map. Lighting is baked into vertex colours, so it cannot be upset by the
// mirrored axis of web mercator. three.js loads on the first switch to 3D,
// so the 2D map's download is unchanged.
//
// Which panels show is decided by preview.html's own panelVisible() -- the
// density slider, the open building's system size, the economics bands, and
// the panel editor's live set -- so the 3D panels are the 2D panels.
//
// Wiring in preview.html: this script tag, Buildings3D.init(map, ...) in
// map.on("load"), setTerrain3D asking Buildings3D.setActive first (the DSM
// terrain stays as the fallback if three.js cannot run), and a call from
// applyToggles so the hidden 2D panel layers stay hidden in 3D.
(function () {
  "use strict";

  const THREE_URL = "https://cdnjs.cloudflare.com/ajax/libs/three.js/0.160.0/three.min.js";
  const DSM_Z = 17, T = 256;                // DSM tiles: z17 is ~0.85 m/px here
  const BUILD_MIN_ZOOM = 16;                // tiles are full detail from z16
  // Which ring sample is "the ground". Fernhill's steep sections put 4-6 m
  // between the 15th and 50th percentile around one house (4746694:
  // 386.4 vs 392.5 m), and on flat ground the 15th made every hillside
  // house read a storey taller than it looks. The 25th still skips the
  // hedges, cars and neighbouring roofs a ring catches.
  const GROUND_Q = 0.25;
  // OBSTRUCTION BOX TOP FROM THE SURVEY (switch: ?SOLAR_OBST_TOP_DSM=1, or
  // SITE.obstTopFromDsm). By default a box's flat top is the HIGHEST corner of
  // the roof plane under it plus the obstruction's height above that plane, so
  // a tilted plane lifts the whole top by the plane's fall across the box.
  // #5372566 (19 Camp St): its plant deck carries 4.4 deg where the LiDAR is
  // about flat; the plane falls 1.48 m under the 140 m2 box, so the top stood
  // 1.2 m above the deck's surveyed height, with walls to 2.3 m beside a real
  // step of about 0.5 m. With the switch on, the top is the 85th percentile of
  // the DSM inside the obstruction (what the survey measured there), but never
  // below 0.15 m over the roof plane's highest corner. Replayed over the 6,254
  // boxes in pilot, frankton_flats and arrowtown_millbrook (live v66), the top's
  // distance from the DSM falls from an area-weighted 0.80 m to 0.49 m, and
  // boxes more than 0.5 m off from 32.6% to 16.5%. Taking the DSM alone
  // reaches 0.23 m, but buries 16% of boxes in the plane of a tilted face.
  const OBST_TOP_DSM = /[?&]SOLAR_OBST_TOP_DSM=1\b/.test(location.search)
    || !!(window.SITE && window.SITE.obstTopFromDsm);
  const EARTH = 40075016.686;
  const LAYER_ID = "buildings-3d";
  // 2D layers the meshes replace: drawn on the flat ground under a 3D roof
  // they would only show at the edges, a step out of place.
  const HIDE_2D = ["facets-outline", "obstructions-fill", "obstructions-outline",
    "panels-fill", "panels-outline", "live-facets-outline", "live-obstructions-fill",
    "live-panels-fill", "live-panels-outline"];

  // Light from the north-west and above (the sun is in the north here), so
  // a north face reads bright, a south face darker, and walls by direction.
  const LIGHT = (() => { const v = [-0.42, 0.62, 0.66]; const n = Math.hypot(...v); return v.map(x => x / n); })();
  const C = {
    roof: [0.93, 0.92, 0.90],
    wall: [0.74, 0.745, 0.76],
    // the 2D map's obstruction red (#ff3b30), a touch deeper so the lit top
    // and shaded sides still read as a box: grey boxes read as rocks
    obst: [0.86, 0.22, 0.18],
    roofLine: [0.22, 0.24, 0.27],
    wallLine: [0.40, 0.42, 0.45],
    panel: hex("#0d2c5e"),
    panelLine: hex("#8fb4e3"),
    select: hex("#ffd166"),
    selectLine: hex("#f5a300"),
  };
  function hex(h) { const v = parseInt(h.slice(1), 16); return [(v >> 16 & 255) / 255, (v >> 8 & 255) / 255, (v & 255) / 255]; }
  const RA = 0.5, RD = 0.55, WA = 0.52, WD = 0.5;   // ambient / directional, roofs and walls
  const shade = (nx, ny, nz, amb, dif) => amb + dif * Math.max(0, nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2]);
  // the heat map's facet ramp (preview.html sunCol): south/low -> north/high
  const RAMP = [[49, 18, 59], [140, 41, 129], [222, 73, 104], [254, 159, 109], [252, 253, 191]];
  function heatCol(v) {
    const t = Math.max(0, Math.min(1, ((v || 0) - 600) / 700));
    const i = Math.min(RAMP.length - 2, Math.floor(t * (RAMP.length - 1)));
    const u = t * (RAMP.length - 1) - i, a = RAMP[i], b = RAMP[i + 1];
    return [0, 1, 2].map(k => (a[k] + u * (b[k] - a[k])) / 255);
  }

  const S = {
    map: null, base: "", ver: "",
    on: false,          // 3D buildings wanted
    k: 0,               // height scale, 0..1 (the rise when switching)
    O: null,            // scene origin { mx, my, s }
    cache: new Map(),   // building_id -> built record
    feat: new Map(),    // building_id -> gathered tile features
    inRange: [],        // building ids drawn now
    queue: [], queued: new Set(), waiting: new Map(), fresh: [], drawn: new Set(), heat: undefined,
    sig: null, selSig: "", modeSig: "",
    layerAdded: false, ticket: 0,
    abs: false,         // buildings at their real ground height, on the terrain
    gt: null,           // the ground terrain's availability (a promise)
    stats: { built: 0, failed: 0, buildMs: 0, mergeMs: 0, tris: 0, panels: 0 },
  };
  let THREE = null, threeLoading = null;
  let R = null;         // three.js renderer objects

  // ---------------------------------------------------------------- coords
  const mercX = lng => (lng + 180) / 360;
  const mercY = lat => (180 - (180 / Math.PI) * Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360))) / 360;
  function setOrigin(lng, lat) {
    S.O = { mx: mercX(lng), my: mercY(lat), s: 1 / (EARTH * Math.cos(lat * Math.PI / 180)), lng, lat };
  }
  // local metres: X east, Y north, relative to the origin
  const toXY = (lng, lat) => [(mercX(lng) - S.O.mx) / S.O.s, (S.O.my - mercY(lat)) / S.O.s];
  const fromXY = (X, Y) => [S.O.mx + X * S.O.s, S.O.my - Y * S.O.s];

  // ---------------------------------------------------------------- DSM
  const dsm = new Map(), DSM_MAX = 220;
  const NPX = (1 << DSM_Z) * T;
  function dsmTile(x, y) {
    const k = x + "/" + y;
    let e = dsm.get(k);
    if (e) { dsm.delete(k); dsm.set(k, e); return e; }
    e = { h: null, done: false, p: null };
    e.p = fetch(`${S.base}data/terrain/${DSM_Z}/${x}/${y}.png?v=${S.ver}`)
      .then(r => (r.ok ? r.blob() : null))
      .then(b => b && createImageBitmap(b, { colorSpaceConversion: "none", premultiplyAlpha: "none" }))
      .then(bmp => {
        if (!bmp) return null;
        const c = typeof OffscreenCanvas !== "undefined" ? new OffscreenCanvas(T, T)
          : Object.assign(document.createElement("canvas"), { width: T, height: T });
        const ctx = c.getContext("2d", { willReadFrequently: true });
        ctx.drawImage(bmp, 0, 0);
        if (bmp.close) bmp.close();
        const d = ctx.getImageData(0, 0, T, T).data, h = new Float32Array(T * T);
        for (let i = 0, j = 0; i < h.length; i++, j += 4) h[i] = -10000 + (d[j] * 65536 + d[j + 1] * 256 + d[j + 2]) * 0.1;
        return h;
      })
      .catch(() => null)
      .then(h => { e.h = h; e.done = true; return h; });
    dsm.set(k, e);
    while (dsm.size > DSM_MAX) dsm.delete(dsm.keys().next().value);
    return e;
  }
  // HEIGHTS AT THE LIDAR POSITION. Every building's geometry arrives moved
  // onto the photo by its measured lean (img_dx / img_dy on the buildings
  // tiles, src/emit_region.py), but the DSM is the survey, unmoved. Sampled at
  // the moved faces, a face reads the roof up to a metre and a half beside it:
  // over the live Queenstown regions 37% of multi-face roofs had faces that
  // step apart in 3D where the LiDAR shows one roof, against 20% with the lean
  // taken off (tools/layout_health.py seam_step; leaning 0.75 m or more, 19.8%
  // of shared edges against 6.7%). The solid is still drawn where the map draws
  // the building; only the height lookup takes the lean off. Switch:
  // ?SOLAR_LEAN_HEIGHTS=0 turns it off, SITE.leanHeights=false likewise.
  const LEAN_HEIGHTS = !/[?&]SOLAR_LEAN_HEIGHTS=0\b/.test(location.search)
    && !(window.SITE && window.SITE.leanHeights === false);
  let HLEAN = [0, 0];
  // the DSM tiles under a box in local metres; null when all are in hand
  function dsmPending(x0, y0, x1, y1) {
    const a = fromXY(x0, y1), b = fromXY(x1, y0);
    const tx0 = Math.floor(a[0] * (1 << DSM_Z)), ty0 = Math.floor(a[1] * (1 << DSM_Z));
    const tx1 = Math.floor(b[0] * (1 << DSM_Z)), ty1 = Math.floor(b[1] * (1 << DSM_Z));
    const ps = [];
    for (let x = tx0; x <= tx1; x++) for (let y = ty0; y <= ty1; y++) {
      const e = dsmTile(x, y);
      if (!e.done) ps.push(e.p);
    }
    return ps.length ? Promise.all(ps) : null;
  }
  // nearest DSM pixel (the tiles are nearest-resampled at this zoom, so a
  // roof edge is a step; interpolating would invent a ramp)
  function H(X, Y) {
    X -= HLEAN[0]; Y -= HLEAN[1];
    const px = Math.floor((S.O.mx + X * S.O.s) * NPX), py = Math.floor((S.O.my - Y * S.O.s) * NPX);
    const e = dsm.get((px >> 8) + "/" + (py >> 8));
    if (!e || !e.h) return NaN;
    const v = e.h[(py & 255) * T + (px & 255)];
    return v > -500 ? v : NaN;
  }

  // ---------------------------------------------------------------- geometry helpers
  function inRings(rings, x, y) {
    let inside = false;
    for (const r of rings) {
      for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
        const xi = r[i][0], yi = r[i][1], xj = r[j][0], yj = r[j][1];
        if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
      }
    }
    return inside;
  }
  function segDist2(px, py, ax, ay, bx, by) {
    const dx = bx - ax, dy = by - ay, l = dx * dx + dy * dy;
    let t = l > 0 ? ((px - ax) * dx + (py - ay) * dy) / l : 0;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    const qx = ax + t * dx - px, qy = ay + t * dy - py;
    return qx * qx + qy * qy;
  }
  function edgeDist(rings, x, y) {
    let d = Infinity;
    for (const r of rings) for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
      const v = segDist2(x, y, r[j][0], r[j][1], r[i][0], r[i][1]);
      if (v < d) d = v;
    }
    return Math.sqrt(d);
  }
  function ringArea(r) {
    let a = 0;
    for (let i = 0, j = r.length - 1; i < r.length; j = i++) a += (r[j][0] - r[i][0]) * (r[j][1] + r[i][1]);
    return a / 2;
  }
  function bboxOf(rings, b) {
    b = b || [Infinity, Infinity, -Infinity, -Infinity];
    for (const r of rings) for (const p of r) {
      if (p[0] < b[0]) b[0] = p[0]; if (p[1] < b[1]) b[1] = p[1];
      if (p[0] > b[2]) b[2] = p[0]; if (p[1] > b[3]) b[3] = p[1];
    }
    return b;
  }
  function triangulate(rings) {
    const V = THREE.Vector2;
    const contour = rings[0].map(p => new V(p[0], p[1]));
    const holes = rings.slice(1).filter(r => r.length >= 3).map(r => r.map(p => new V(p[0], p[1])));
    let faces;
    try { faces = THREE.ShapeUtils.triangulateShape(contour, holes); } catch (e) { return { pts: [], faces: [] }; }
    const pts = contour.concat(...holes).map(v => [v.x, v.y]);
    return { pts, faces };
  }
  function percentile(a, q) {
    if (!a.length) return NaN;
    const s = Float64Array.from(a).sort();
    return s[Math.min(s.length - 1, Math.max(0, Math.round(q * (s.length - 1))))];
  }
  const median = a => percentile(a, 0.5);
  // Sutherland-Hodgman: a ring clipped to an axis-aligned box
  function clipRing(ring, x0, y0, x1, y1) {
    let out = ring;
    const planes = [[0, x0, 1], [0, x1, -1], [1, y0, 1], [1, y1, -1]];
    for (const [ax, v, sgn] of planes) {
      const inp = out; out = [];
      if (!inp.length) break;
      for (let i = 0; i < inp.length; i++) {
        const P = inp[i], Q = inp[(i + 1) % inp.length];
        const pin = (P[ax] - v) * sgn >= 0, qin = (Q[ax] - v) * sgn >= 0;
        if (pin) out.push(P);
        if (pin !== qin) {
          const t = (v - P[ax]) / (Q[ax] - P[ax]);
          out.push(ax === 0 ? [v, P[1] + t * (Q[1] - P[1])] : [P[0] + t * (Q[0] - P[0]), v]);
        }
      }
    }
    return out;
  }

  // ---------------------------------------------------------------- tile features
  function ringsOf(geom) {
    const toLocal = r => {
      const out = r.map(c => toXY(c[0], c[1]));
      const a = out[0], b = out[out.length - 1];
      if (out.length > 1 && Math.abs(a[0] - b[0]) < 1e-6 && Math.abs(a[1] - b[1]) < 1e-6) out.pop();
      return out;
    };
    if (geom.type === "Polygon") return [geom.coordinates.map(toLocal).filter(r => r.length >= 3)];
    if (geom.type === "MultiPolygon") return geom.coordinates.map(pg => pg.map(toLocal).filter(r => r.length >= 3));
    return [];
  }
  const facetKey = q => [q.aspect_deg, q.slope_deg, q.poa_kwh_m2_yr, q.panel_count, q.sb, q.ab].join("|");

  const firstCoord = g => (g.type === "Polygon" ? g.coordinates[0][0] : g.type === "MultiPolygon" ? g.coordinates[0][0][0] : null);
  // Footprints in the loaded tiles, by building, unconverted (cheap: this
  // runs on every settle of the map). Tiles clip features at their edges and
  // a feature near one arrives once per tile; identical pieces are dropped
  // (as preview.html's liveFeatures does), the rest are kept together, and
  // the set of pieces is the signature that says a building needs a rebuild.
  function gatherOutlines() {
    const out = new Map(), seen = new Set();
    let bl = [];
    try { bl = S.map.querySourceFeatures("buildings", { sourceLayer: "buildings" }); } catch (e) { /* not loaded */ }
    for (const f of bl) {
      const g = f.geometry, bid = f.properties && f.properties.building_id;
      if (!g || bid == null) continue;
      const c0 = firstCoord(g);
      if (!c0) continue;
      const key = c0[0].toFixed(7) + "," + c0[1].toFixed(7);
      if (seen.has(bid + ":" + key)) continue;
      seen.add(bid + ":" + key);
      let o = out.get(bid);
      if (!o) out.set(bid, o = { geoms: [], osig: "", c0,
        lean: [+f.properties.img_dx || 0, +f.properties.img_dy || 0] });
      o.geoms.push(g);
      o.osig += key + ";";
    }
    return out;
  }
  // Footprint, faces, obstructions and panels for these buildings only, in
  // local metres. The layout query is filtered to them (a match is a hash
  // lookup), so a pan that brings in ten new roofs converts ten roofs, not
  // the 80,000 features of a town centre (measured: 120 ms unfiltered at z16).
  function gatherFull(outl, bids) {
    const feat = new Map();
    for (const bid of bids) {
      const o = outl.get(bid);
      const F = { outl: [], facets: new Map(), obst: [], panels: [], osig: o.osig, lean: o.lean };
      for (const g of o.geoms) for (const rings of ringsOf(g)) if (rings.length) F.outl.push(rings);
      feat.set(bid, F);
    }
    if (!bids.length) return feat;
    let lay = [];
    try {
      lay = S.map.querySourceFeatures("layout", { sourceLayer: "layout",
        filter: ["match", ["get", "building_id"], bids, true, false] });
    } catch (e) { /* not loaded */ }
    const seen = new Set();
    for (const f of lay) {
      const g = f.geometry, q = f.properties;
      if (!g || !q || !feat.has(q.building_id)) continue;
      const c0 = firstCoord(g);
      if (!c0) continue;
      // A piece repeated by two tiles is identical; two faces can still START
      // at the same vertex. #5372566's 328 m2 east face and its NE face both
      // begin at the ridge top, and a key of kind + first vertex dropped the
      // east face, so the 3D view filled it from its neighbours' planes. The
      // face's own properties and the vertex count tell them apart.
      const nv = g.type === "Polygon" ? g.coordinates[0].length : g.type === "MultiPolygon" ? g.coordinates[0][0].length : 0;
      const key = q.building_id + q.kind + (q.kind === "panel" ? q.fill_order : "")
        + (q.kind === "facet" ? "|" + facetKey(q) : "") + ":" + c0[0].toFixed(7) + "," + c0[1].toFixed(7) + ":" + nv;
      if (seen.has(key)) continue;
      seen.add(key);
      const F = feat.get(q.building_id);
      const polys = ringsOf(g).filter(r => r.length);
      if (q.kind === "facet") {
        const k = facetKey(q);
        let fc = F.facets.get(k);
        if (!fc) F.facets.set(k, fc = { q, polys: [] });
        fc.polys.push(...polys);
      } else if (q.kind === "obstruction") {
        for (const rings of polys) F.obst.push({ q, rings });
      } else if (q.kind === "panel") {
        for (const rings of polys) F.panels.push({ q, rings });
      }
    }
    return feat;
  }
  function featFor(bid) {
    const o = gatherOutlines();
    return o.has(bid) ? gatherFull(o, [bid]).get(bid) : null;
  }

  // ---------------------------------------------------------------- one building
  function fitPlane(pts, q) {
    // pts: [[x, y, z]...]. Tilt from the tile when it has one, else least squares.
    let a = NaN, b = NaN;
    if (q && isFinite(q.slope_deg) && isFinite(q.aspect_deg)) {
      const t = Math.tan(q.slope_deg * Math.PI / 180), A = q.aspect_deg * Math.PI / 180;
      // aspect is the compass direction the face looks (downhill)
      a = -t * Math.sin(A); b = -t * Math.cos(A);
    }
    let use = pts;
    if (!isFinite(a)) {
      // least squares with two rounds of rejection
      for (let round = 0; round < 3; round++) {
        if (use.length < 3) break;
        let sx = 0, sy = 0, sz = 0;
        for (const p of use) { sx += p[0]; sy += p[1]; sz += p[2]; }
        const n = use.length, mx = sx / n, my = sy / n, mz = sz / n;
        let xx = 0, xy = 0, yy = 0, xz = 0, yz = 0;
        for (const p of use) {
          const dx = p[0] - mx, dy = p[1] - my, dz = p[2] - mz;
          xx += dx * dx; xy += dx * dy; yy += dy * dy; xz += dx * dz; yz += dy * dz;
        }
        const det = xx * yy - xy * xy;
        if (Math.abs(det) < 1e-9) { a = 0; b = 0; } else { a = (xz * yy - yz * xy) / det; b = (yz * xx - xz * xy) / det; }
        const res = use.map(p => p[2] - (a * p[0] + b * p[1]));
        const med = median(res), mad = median(res.map(r => Math.abs(r - med))) * 1.4826;
        const lim = Math.max(0.25, 2.5 * mad);
        const next = use.filter((p, i) => Math.abs(res[i] - med) <= lim);
        if (next.length === use.length) break;
        use = next;
      }
      if (!isFinite(a)) { a = 0; b = 0; }
    }
    // the offset: median of the residuals, outliers rejected
    let res = pts.map(p => p[2] - (a * p[0] + b * p[1]));
    let c = median(res);
    for (let round = 0; round < 3; round++) {
      const mad = median(res.map(r => Math.abs(r - c))) * 1.4826;
      const lim = Math.max(0.25, 2.5 * mad);
      const kept = res.filter(r => Math.abs(r - c) <= lim);
      if (!kept.length || kept.length === res.length) break;
      res = kept; c = median(res);
    }
    return { a, b, c, n: pts.length };
  }

  // DSM samples inside a face: a grid `step` apart, `inset` in from the edges,
  // plus the vertices pulled inward toward the centroid
  function faceSamples(polys, step, inset) {
    const out = [];
    for (const rings of polys) {
      const bb = bboxOf(rings);
      for (let y = Math.ceil(bb[1] / step) * step; y <= bb[3]; y += step) {
        for (let x = Math.ceil(bb[0] / step) * step; x <= bb[2]; x += step) {
          if (!inRings(rings, x, y) || edgeDist(rings, x, y) < inset) continue;
          const z = H(x, y);
          if (isFinite(z)) out.push([x, y, z]);
        }
      }
      const r = rings[0];
      let cx = 0, cy = 0;
      for (const p of r) { cx += p[0]; cy += p[1]; }
      cx /= r.length; cy /= r.length;
      for (const p of r) {
        const dx = cx - p[0], dy = cy - p[1], d = Math.hypot(dx, dy);
        if (d < inset * 2) continue;
        const x = p[0] + dx / d * inset * 1.4, y = p[1] + dy / d * inset * 1.4;
        if (!inRings(rings, x, y)) continue;
        const z = H(x, y);
        if (isFinite(z)) out.push([x, y, z]);
      }
    }
    return out;
  }

  // DSM heights in a ring 2-5 m outside a footprint (both sides of every
  // edge are tried, so the ring is right whichever way the edge winds)
  function ringSamples(outl, insideAny) {
    const gs = [];
    for (const rings of outl) for (const r of rings) for (let i = 0; i < r.length; i++) {
      const p = r[i], q = r[(i + 1) % r.length];
      const dx = q[0] - p[0], dy = q[1] - p[1], len = Math.hypot(dx, dy);
      if (len < 0.2) continue;
      const nx = dy / len, ny = -dx / len;
      for (let t = 0.5; t < len; t += 1) {
        const ex = p[0] + dx * t / len, ey = p[1] + dy * t / len;
        for (const off of [2, 3.5, 5]) {
          for (const sg of [1, -1]) {
            const x = ex + nx * off * sg, y = ey + ny * off * sg;
            if (insideAny(x, y)) continue;
            const z = H(x, y);
            if (isFinite(z)) gs.push(z);
          }
        }
      }
    }
    return gs;
  }

  function buildRec(bid, F) {
    HLEAN = LEAN_HEIGHTS && F.lean ? F.lean : [0, 0];
    try { return buildRecAt(bid, F); } finally { HLEAN = [0, 0]; }
  }
  function buildRecAt(bid, F) {
    const t0 = performance.now();
    const outl = F.outl;
    const allOut = outl.flat();
    const bb = bboxOf(allOut);
    F.facets.forEach(fc => fc.polys.forEach(rings => bboxOf(rings, bb)));
    const insideAny = (x, y) => outl.some(rings => inRings(rings, x, y));

    // ground: a low percentile of the DSM 2-5 m outside the footprint
    const gs = ringSamples(outl, insideAny);
    const ground = percentile(gs, GROUND_Q);
    if (!isFinite(ground)) return null;

    // every DSM sample inside the footprint: the height ceiling, and the flat
    // roof of a building with no modelled faces
    const inside = [];
    {
      const area = outl.reduce((s, rings) => s + Math.abs(ringArea(rings[0])), 0);
      const step = Math.max(0.8, Math.sqrt(area / 400));
      for (const rings of outl) {
        const b = bboxOf(rings);
        for (let y = b[1] + step / 2; y <= b[3]; y += step) for (let x = b[0] + step / 2; x <= b[2]; x += step) {
          if (!inRings(rings, x, y) || edgeDist(rings, x, y) < 0.5) continue;
          const z = H(x, y);
          if (isFinite(z)) inside.push(z);
        }
      }
    }
    const top = inside.length ? percentile(inside, 0.98) : ground + 6;
    const lo = ground + 0.5, hi = Math.max(top + 1, ground + 2);

    // roof faces: tilt from the tiles, height from the DSM
    const faces = [];
    F.facets.forEach(fc => {
      let pts = faceSamples(fc.polys, 0.6, 0.45);
      if (pts.length < 4) pts = pts.concat(faceSamples(fc.polys, 0.35, 0.15));
      if (pts.length < 2) return;
      const pl = fitPlane(pts, fc.q);
      const bbf = [Infinity, Infinity, -Infinity, -Infinity];
      fc.polys.forEach(rings => bboxOf(rings, bbf));
      faces.push({ q: fc.q, polys: fc.polys, a: pl.a, b: pl.b, c: pl.c, c0: pl.c, n: pl.n, bb: bbf });
    });
    // close ridges: faces whose edges meet within 0.6 m are nudged together
    if (faces.length > 1) {
      const cons = faces.map(() => []);
      for (let i = 0; i < faces.length; i++) for (let j = 0; j < faces.length; j++) {
        if (i === j) continue;
        const A = faces[i], B = faces[j];
        if (A.bb[0] > B.bb[2] + 0.4 || B.bb[0] > A.bb[2] + 0.4 || A.bb[1] > B.bb[3] + 0.4 || B.bb[1] > A.bb[3] + 0.4) continue;
        for (const rings of A.polys) for (const p of rings[0]) {
          if (!B.polys.some(rb => edgeDist(rb, p[0], p[1]) < 0.35)) continue;
          const d = (A.a * p[0] + A.b * p[1] + A.c) - (B.a * p[0] + B.b * p[1] + B.c);
          if (Math.abs(d) < 0.6) cons[i].push([j, (A.a - B.a) * p[0] + (A.b - B.b) * p[1]]);
        }
      }
      for (let it = 0; it < 25; it++) {
        for (let i = 0; i < faces.length; i++) {
          if (!cons[i].length) continue;
          const w0 = 2;
          let s = w0 * faces[i].c0;
          for (const [j, da] of cons[i]) s += faces[j].c - da;
          faces[i].c = s / (w0 + cons[i].length);
        }
      }
    }
    const planeZ = (f, x, y) => Math.min(hi, Math.max(lo, f.a * x + f.b * y + f.c));
    const flatTop = inside.length ? percentile(inside, 0.6) : ground + 3;
    // the roof surface for the footprint fill: the lower envelope of the
    // face planes within reach, else the nearest face, else flat
    const area = outl.reduce((s, rings) => s + Math.abs(ringArea(rings[0])), 0);
    const g = Math.min(2, Math.max(0.75, Math.sqrt(area) / 12));
    const reach = 1.2 * g + 0.3;
    function roofZ(x, y) {
      if (!faces.length) return Math.min(hi, Math.max(lo, flatTop));
      let best = Infinity, near = null, nd = Infinity;
      for (const f of faces) {
        const bd = Math.hypot(Math.max(f.bb[0] - x, 0, x - f.bb[2]), Math.max(f.bb[1] - y, 0, y - f.bb[3]));
        if (bd > reach && bd > nd) continue;
        let d = Infinity;
        for (const rings of f.polys) {
          if (inRings(rings, x, y)) { d = 0; break; }
          d = Math.min(d, edgeDist(rings, x, y));
        }
        if (d <= reach) best = Math.min(best, planeZ(f, x, y));
        if (d < nd) { nd = d; near = f; }
      }
      if (best === Infinity) {
        if (!near) for (const f of faces) {
          let d = Infinity;
          for (const rings of f.polys) d = Math.min(d, edgeDist(rings, x, y));
          if (d < nd) { nd = d; near = f; }
        }
        best = planeZ(near, x, y);
      }
      return best;
    }
    const FILL_DROP = 0.04;   // the fill sits just under the faces drawn on it
    const zMemo = new Map();
    const fillZ = (x, y) => {
      const k = x.toFixed(4) + "," + y.toFixed(4);
      let z = zMemo.get(k);
      if (z === undefined) { z = roofZ(x, y) - FILL_DROP - ground; zMemo.set(k, z); }
      return z;
    };

    // ---- output buffers
    const tp = [], tcG = [], tcH = [], lp = [], lc = [];
    const tri = (A, B, Cc, col, colH) => {
      tp.push(A[0], A[1], A[2], B[0], B[1], B[2], Cc[0], Cc[1], Cc[2]);
      for (let i = 0; i < 3; i++) { tcG.push(col[0], col[1], col[2]); tcH.push((colH || col)[0], (colH || col)[1], (colH || col)[2]); }
    };
    const seg = (A, B, col) => { lp.push(A[0], A[1], A[2], B[0], B[1], B[2]); lc.push(col[0], col[1], col[2], col[0], col[1], col[2]); };
    const lit = (col, f) => [col[0] * f, col[1] * f, col[2] * f];
    const triLit = (A, B, Cc, base, amb, dif, colHbase) => {
      let nx = (B[1] - A[1]) * (Cc[2] - A[2]) - (B[2] - A[2]) * (Cc[1] - A[1]);
      let ny = (B[2] - A[2]) * (Cc[0] - A[0]) - (B[0] - A[0]) * (Cc[2] - A[2]);
      let nz = (B[0] - A[0]) * (Cc[1] - A[1]) - (B[1] - A[1]) * (Cc[0] - A[0]);
      const l = Math.hypot(nx, ny, nz) || 1;
      if (nz < 0) { nx = -nx; ny = -ny; nz = -nz; }
      const f = shade(nx / l, ny / l, nz / l, amb, dif);
      tri(A, B, Cc, lit(base, f), colHbase ? lit(colHbase, f) : null);
    };

    // ---- footprint fill on a grid, clipped to the footprint
    const gx = v => Math.floor(v / g);
    for (const rings of outl) {
      const b = bboxOf(rings);
      const i0 = gx(b[0]), i1 = gx(b[2]), j0 = gx(b[1]), j1 = gx(b[3]);
      const W = i1 - i0 + 1, Hh = j1 - j0 + 1;
      const touched = new Uint8Array(W * Hh);
      for (const r of rings) for (let i = 0; i < r.length; i++) {
        const p = r[i], q = r[(i + 1) % r.length];
        const ci0 = gx(Math.min(p[0], q[0])), ci1 = gx(Math.max(p[0], q[0]));
        const cj0 = gx(Math.min(p[1], q[1])), cj1 = gx(Math.max(p[1], q[1]));
        // walk the edge in small steps to mark the cells it crosses
        const len = Math.hypot(q[0] - p[0], q[1] - p[1]), n = Math.max(1, Math.ceil(len / (g * 0.25)));
        if ((ci1 - ci0 + 1) * (cj1 - cj0 + 1) <= 4) {
          for (let ci = ci0; ci <= ci1; ci++) for (let cj = cj0; cj <= cj1; cj++) touched[(cj - j0) * W + (ci - i0)] = 1;
        } else {
          for (let s = 0; s <= n; s++) {
            const x = p[0] + (q[0] - p[0]) * s / n, y = p[1] + (q[1] - p[1]) * s / n;
            const ci = gx(x), cj = gx(y);
            for (let di = -1; di <= 1; di++) for (let dj = -1; dj <= 1; dj++) {
              const ii = ci + di - i0, jj = cj + dj - j0;
              if (ii >= 0 && ii < W && jj >= 0 && jj < Hh) touched[jj * W + ii] = 1;
            }
          }
        }
      }
      for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) {
        const x0 = i * g, y0 = j * g, x1 = x0 + g, y1 = y0 + g;
        let polys;
        if (!touched[(j - j0) * W + (i - i0)]) {
          if (!inRings(rings, x0 + g / 2, y0 + g / 2)) continue;
          polys = [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]];
          const P = polys[0].map(p => [p[0], p[1], fillZ(p[0], p[1])]);
          triLit(P[0], P[1], P[2], C.roof, RA, RD);
          triLit(P[0], P[2], P[3], C.roof, RA, RD);
          continue;
        }
        const outer = clipRing(rings[0], x0, y0, x1, y1);
        if (outer.length < 3 || Math.abs(ringArea(outer)) < 1e-4) continue;
        const holes = rings.slice(1).map(h => clipRing(h, x0, y0, x1, y1)).filter(h => h.length >= 3 && Math.abs(ringArea(h)) > 1e-4);
        const tr = triangulate([outer, ...holes]);
        const P = tr.pts.map(p => [p[0], p[1], fillZ(p[0], p[1])]);
        for (const f of tr.faces) triLit(P[f[0]], P[f[1]], P[f[2]], C.roof, RA, RD);
      }
    }

    // ---- footprint walls, split where the fill grid crosses them so the
    // wall top meets the fill exactly
    const topPos = [];   // the eave line, for the selection outline
    for (const rings of outl) for (const r of rings) {
      for (let i = 0; i < r.length; i++) {
        const p = r[i], q = r[(i + 1) % r.length];
        const dx = q[0] - p[0], dy = q[1] - p[1], len = Math.hypot(dx, dy);
        if (len < 1e-3) continue;
        let nx = dy / len, ny = -dx / len;
        const mx = (p[0] + q[0]) / 2, my = (p[1] + q[1]) / 2;
        if (inRings(rings, mx + nx * 0.05, my + ny * 0.05)) { nx = -nx; ny = -ny; }
        const ts = [0, 1];
        if (Math.abs(dx) > 1e-9) for (let k = Math.ceil(Math.min(p[0], q[0]) / g); k * g < Math.max(p[0], q[0]); k++) ts.push((k * g - p[0]) / dx);
        if (Math.abs(dy) > 1e-9) for (let k = Math.ceil(Math.min(p[1], q[1]) / g); k * g < Math.max(p[1], q[1]); k++) ts.push((k * g - p[1]) / dy);
        ts.sort((u, v) => u - v);
        const pts = ts.filter((t, k) => t >= 0 && t <= 1 && (k === 0 || t - ts[k - 1] > 1e-6))
          .map(t => { const x = p[0] + dx * t, y = p[1] + dy * t; return [x, y, Math.max(0.3, fillZ(x, y))]; });
        const col = lit(C.wall, shade(nx, ny, 0, WA, WD));
        for (let k = 0; k + 1 < pts.length; k++) {
          const A = pts[k], B = pts[k + 1];
          tri([A[0], A[1], 0], [B[0], B[1], 0], [B[0], B[1], B[2]], col);
          tri([A[0], A[1], 0], [B[0], B[1], B[2]], [A[0], A[1], A[2]], col);
          seg([A[0], A[1], A[2] + 0.02], [B[0], B[1], B[2] + 0.02], C.wallLine);
          topPos.push(A[0], A[1], A[2] + 0.05, B[0], B[1], B[2] + 0.05);
        }
        // a vertical line at each real corner
        const o = r[(i - 1 + r.length) % r.length];
        const ux = p[0] - o[0], uy = p[1] - o[1], ul = Math.hypot(ux, uy);
        if (ul > 1e-3 && Math.abs((ux * dx + uy * dy) / (ul * len)) < 0.92) {
          seg([p[0], p[1], 0], [p[0], p[1], Math.max(0.3, fillZ(p[0], p[1]))], C.wallLine);
        }
      }
    }

    // ---- roof faces on their planes, and a wall under every face edge that
    // is not on the footprint edge (a step between levels)
    for (const f of faces) {
      const nl = Math.hypot(f.a, f.b, 1);
      const fsh = shade(-f.a / nl, -f.b / nl, 1 / nl, RA, RD);
      const colG = lit(C.roof, fsh), colH = lit(heatCol(f.q.poa_kwh_m2_yr), Math.min(1, fsh + 0.12));
      const z = (x, y) => planeZ(f, x, y) - ground;
      for (const rings of f.polys) {
        const tr = triangulate(rings);
        const P = tr.pts.map(p => [p[0], p[1], z(p[0], p[1])]);
        for (const t of tr.faces) tri(P[t[0]], P[t[1]], P[t[2]], colG, colH);
        for (const r of rings) for (let i = 0; i < r.length; i++) {
          const p = r[i], q = r[(i + 1) % r.length];
          const A = [p[0], p[1], z(p[0], p[1])], B = [q[0], q[1], z(q[0], q[1])];
          seg([A[0], A[1], A[2] + 0.03], [B[0], B[1], B[2] + 0.03], C.roofLine);
          const mx = (p[0] + q[0]) / 2, my = (p[1] + q[1]) / 2;
          if (outl.some(o => edgeDist(o, mx, my) < 0.35)) continue;
          const dx = q[0] - p[0], dy = q[1] - p[1], len = Math.hypot(dx, dy) || 1;
          const wc = lit(C.wall, shade(dy / len, -dx / len, 0, WA, WD));
          tri([p[0], p[1], 0], [q[0], q[1], 0], B, wc);
          tri([p[0], p[1], 0], B, A, wc);
        }
      }
    }

    // the face a point sits on (for panels and obstructions)
    function faceAt(x, y) {
      let near = null, nd = Infinity;
      for (const f of faces) for (const rings of f.polys) {
        if (inRings(rings, x, y)) return f;
        const d = edgeDist(rings, x, y);
        if (d < nd) { nd = d; near = f; }
      }
      return near;
    }

    // ---- obstructions: a box from the roof up to the DSM's height there
    const boxes = [];
    for (const o of F.obst) {
      const r = o.rings[0];
      let cx = 0, cy = 0;
      for (const p of r) { cx += p[0]; cy += p[1]; }
      cx /= r.length; cy /= r.length;
      const f = faceAt(cx, cy);
      const roofAt = (x, y) => (f ? planeZ(f, x, y) : roofZ(x, y));
      const hs = [], zs = [];
      const ob = bboxOf(o.rings);
      for (let y = ob[1] + 0.25; y <= ob[3]; y += 0.5) for (let x = ob[0] + 0.25; x <= ob[2]; x += 0.5) {
        if (!inRings(o.rings, x, y)) continue;
        const zz = H(x, y);
        if (isFinite(zz)) { hs.push(zz - roofAt(x, y)); zs.push(zz); }
      }
      const up = hs.length ? percentile(hs, 0.85) : 0;
      if (!(up > 0.15)) continue;
      const hgt = Math.min(1.5, up);
      let baseMax = -Infinity, baseMin = Infinity;
      for (const p of r) {
        const zr = roofAt(p[0], p[1]);
        baseMax = Math.max(baseMax, zr); baseMin = Math.min(baseMin, zr);
      }
      // the surveyed top, clamped between 0.15 m over the highest roof
      // corner (lower, and the face's plane would bury part of the box: 16%
      // of boxes in pilot, frankton_flats and arrowtown_millbrook) and the
      // default rule's top
      const zt = (OBST_TOP_DSM
        ? Math.min(baseMax + hgt, Math.max(baseMax + 0.15, percentile(zs, 0.85)))
        : baseMax + hgt) - ground;
      boxes.push({ area: Math.abs(ringArea(r)), top: zt + ground, old: baseMax + hgt,
        dsm: percentile(zs, 0.85), fall: baseMax - baseMin });
      const tr = triangulate(o.rings);
      const P = tr.pts.map(p => [p[0], p[1], zt]);
      const tc = lit(C.obst, shade(0, 0, 1, 0.6, 0.42));
      for (const t of tr.faces) tri(P[t[0]], P[t[1]], P[t[2]], tc);
      for (let i = 0; i < r.length; i++) {
        const p = r[i], q = r[(i + 1) % r.length];
        const zb = Math.min(roofAt(p[0], p[1]), roofAt(q[0], q[1])) - ground - 0.1;
        const dx = q[0] - p[0], dy = q[1] - p[1], len = Math.hypot(dx, dy) || 1;
        let nx = dy / len, ny = -dx / len;
        if (inRings(o.rings, (p[0] + q[0]) / 2 + nx * 0.03, (p[1] + q[1]) / 2 + ny * 0.03)) { nx = -nx; ny = -ny; }
        const wc = lit(C.obst, shade(nx, ny, 0, 0.5, 0.5));
        tri([p[0], p[1], zb], [q[0], q[1], zb], [q[0], q[1], zt], wc);
        tri([p[0], p[1], zb], [q[0], q[1], zt], [p[0], p[1], zt], wc);
        seg([p[0], p[1], zt + 0.01], [q[0], q[1], zt + 0.01], C.roofLine);
      }
    }

    // ---- panels, lifted onto their face (kept per panel; which ones show is
    // decided at draw time by panelVisible)
    const PANEL_UP = 0.06;
    function liftPanel(q, rings) {
      const r = rings[0];
      let cx = 0, cy = 0;
      for (const p of r) { cx += p[0]; cy += p[1]; }
      cx /= r.length; cy /= r.length;
      const f = faceAt(cx, cy);
      const z = f ? (x, y) => planeZ(f, x, y) - ground + PANEL_UP : (x, y) => fillZ(x, y) + FILL_DROP + PANEL_UP;
      const tr = triangulate(rings);
      const P = tr.pts.map(p => [p[0], p[1], z(p[0], p[1])]);
      const pos = [];
      for (const t of tr.faces) for (const k of t) pos.push(P[k][0], P[k][1], P[k][2]);
      const edge = [];
      for (let i = 0; i < r.length; i++) {
        const p = r[i], qq = r[(i + 1) % r.length];
        edge.push(p[0], p[1], z(p[0], p[1]) + 0.012, qq[0], qq[1], z(qq[0], qq[1]) + 0.012);
      }
      let sh = 0.62 + 0.42 * LIGHT[2];
      if (f) { const nl = Math.hypot(f.a, f.b, 1); sh = shade(-f.a / nl, -f.b / nl, 1 / nl, 0.62, 0.5); }
      return { q, pos, edge, sh };
    }
    const panels = F.panels.map(p => liftPanel(p.q, p.rings));

    let zmax = 0;
    for (let i = 2; i < tp.length; i += 3) if (tp[i] > zmax) zmax = tp[i];
    const dt = performance.now() - t0;
    S.stats.buildMs += dt;
    if (dt > (S.stats.maxMs || 0)) { S.stats.maxMs = dt; S.stats.maxBid = bid; }
    return {
      bid, osig: F.osig, outl, bb, zmax, ground, boxes,
      tp: new Float32Array(tp), tcG: new Float32Array(tcG), tcH: new Float32Array(tcH),
      lp: new Float32Array(lp), lc: new Float32Array(lc), top: new Float32Array(topPos),
      panels, liftPanel,
      faces: faces.map(f => ({ slope: f.q.slope_deg, aspect: f.q.aspect_deg, a: f.a, b: f.b, c: f.c, c0: f.c0, n: f.n, rel: f.a * ((f.bb[0] + f.bb[2]) / 2) + f.b * ((f.bb[1] + f.bb[3]) / 2) + f.c - ground })),
    };
  }

  // ---------------------------------------------------------------- scheduling
  let refreshTimer = null;
  function scheduleRefresh(ms) {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(refresh, ms == null ? 120 : ms);
  }
  function viewRadius() {
    const m = S.map, c = m.getCanvas();
    const mpp = 156543.03392 * Math.cos(m.getCenter().lat * Math.PI / 180) / Math.pow(2, m.getZoom());
    const w = c.clientWidth, h = c.clientHeight;
    return 0.5 * Math.hypot(w, h) * mpp * (1 + 2 * Math.sin(m.getPitch() * Math.PI / 180));
  }
  function refresh() {
    if (!S.on || !S.map) return;
    const m = S.map, z = m.getZoom();
    updateHint();
    const ctr = m.getCenter();
    if (!S.O) setOrigin(ctr.lng, ctr.lat);
    else {
      const [cx, cy] = toXY(ctr.lng, ctr.lat);
      if (Math.hypot(cx, cy) > 8000) {        // keep float precision: re-centre the scene
        setOrigin(ctr.lng, ctr.lat);
        S.cache.clear(); S.feat.clear(); S.queue = []; S.queued.clear(); S.waiting.clear(); S.fresh = [];
        if (R) dropBatches();
      }
    }
    const [cx, cy] = toXY(ctr.lng, ctr.lat);
    const rad = viewRadius();
    if (z >= BUILD_MIN_ZOOM) {
      const outl = gatherOutlines();
      const todo = [];
      outl.forEach((o, bid) => {
        const [x, y] = toXY(o.c0[0], o.c0[1]);
        const d = Math.hypot(x - cx, y - cy);
        if (d > rad) return;
        const rec = S.cache.get(bid);
        if (rec && rec.osig === o.osig) return;
        todo.push([d, bid]);
      });
      // only once both sources have their tiles: a roof built before its
      // layout tile arrived would be cached without its faces
      let ready = false;
      try { ready = m.isSourceLoaded("layout") && m.isSourceLoaded("buildings"); } catch (e) { /* not added yet */ }
      if (todo.length && ready) {
        todo.sort((a, b) => a[0] - b[0]);
        const bids = todo.map(t => t[1]);
        const fresh = bids.filter(b => { const F = S.feat.get(b); return !F || F.osig !== outl.get(b).osig; });
        gatherFull(outl, fresh).forEach((F, bid) => S.feat.set(bid, F));
        // nearest first, every time the view settles
        S.queue = bids.filter(b => !S.waiting.has(b));
        S.queued = new Set(S.queue);
        for (const k of S.feat.keys()) if (!S.queued.has(k) && !S.waiting.has(k)) S.feat.delete(k);
      }
    }
    // what is drawn: built buildings near the view
    // zoomed well out, a scatter of roofs built earlier would only look
    // patchy: draw none, and the caption says to zoom in
    // (the flat 2D panel layers come back meanwhile: on flat ground they
    // lie true)
    const meshes = z >= BUILD_MIN_ZOOM - 1;
    if (meshes !== S.meshes) { S.meshes = meshes; hide2D(meshes); }
    const keep = [];
    if (meshes) S.cache.forEach((rec, bid) => {
      const d = Math.hypot((rec.bb[0] + rec.bb[2]) / 2 - cx, (rec.bb[1] + rec.bb[3]) / 2 - cy);
      if (d <= rad * 1.15) keep.push(bid);
      else if (d > rad * 4 && S.cache.size > 2500) S.cache.delete(bid);
    });
    S.inRange = keep;
    sync();
    updateHint();
    pump();
  }
  let pumping = false;
  function pump() {
    if (pumping) return;
    pumping = true;
    requestAnimationFrame(work);
  }
  let lastMerge = 0;
  function work() {
    pumping = false;
    if (!S.on) return;
    const t0 = performance.now();
    const moving = S.map.isMoving();
    const budget = moving ? 4 : 10;
    let built = 0;
    while (S.queue.length && performance.now() - t0 < budget) {
      const bid = S.queue.shift();
      S.queued.delete(bid);
      const F = S.feat.get(bid);
      if (!F || !F.outl.length) continue;
      const bb = bboxOf(F.outl.flat());
      const pend = dsmPending(bb[0] - 6, bb[1] - 6, bb[2] + 6, bb[3] + 6);
      if (pend) {
        S.waiting.set(bid, true);
        pend.then(() => {
          S.waiting.delete(bid);
          if (!S.queued.has(bid)) { S.queue.unshift(bid); S.queued.add(bid); }
          pump();
        });
        continue;
      }
      let rec = null;
      try { rec = buildRec(bid, F); } catch (e) { console.warn("3D building " + bid + " failed", e); }
      S.feat.delete(bid);
      if (rec) { S.cache.set(bid, rec); S.inRange.push(bid); S.fresh.push(rec); built++; S.stats.built++; } else S.stats.failed++;
    }
    const now = performance.now();
    if (S.dirty) { merge(); lastMerge = now; S.fresh = []; }
    else if (S.fresh.length && (!S.queue.length || now - lastMerge > (moving ? 500 : 200))) {
      addRecs(S.fresh); S.fresh = []; lastMerge = performance.now();
      if (S.selSig) mergeSel();
    }
    if (S.queue.length || S.fresh.length) pump();
  }

  // ---------------------------------------------------------------- what preview.html decides
  // Bare names resolve to preview.html's top-level bindings at call time.
  function panelShown(q) {
    try { return panelVisible(q); } catch (e) { return !(q.fill_rank != null && q.fill_rank > 80); }
  }
  function panelColour(q) {
    try {
      if (panelEcon && (panelEconGlobal || q.building_id === panelEcon.buildingId)) {
        const key = panelBandOf(q);
        const b = PANEL_ECON_BANDS.find(x => x.key === key);
        if (b) return hex(b.colour);
      }
    } catch (e) { /* economics not ready */ }
    return C.panel;
  }
  function viewState() {
    const v = {};
    try { v.overlay = overlayOn; v.mode = mode; } catch (e) { v.overlay = true; v.mode = "layout"; }
    return v;
  }
  function panelSig() {
    const s = [];
    try {
      s.push(overlayOn, mode, panelDensityPct, bldgSys, bldgSys && bldgSys.k, panelEcon,
        panelEcon && panelEcon.breakEvenKwh, panelEconGlobal, panelEconMerit, panelEconMatch,
        layoutExcludedBuildingId, window.__liveLayoutData);
      PANEL_ECON_KEYS.forEach(k => s.push(panelEconShow[k]));
    } catch (e) { /* page not ready */ }
    return s;
  }
  function selection() {
    try { return selectedIds && selectedIds.length ? selectedIds.slice() : (selectedBuildingId !== -1 ? [selectedBuildingId] : []); }
    catch (e) { return []; }
  }
  function sameSig(a, b) {
    if (!a || !b || a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
  }
  function watch() {
    if (!S.on) return;
    const ps = panelSig(), sel = selection().join(","), v = viewState();
    const heat = v.overlay && v.mode === "heatmap";
    if (heat !== S.heat) { S.sig = ps; S.selSig = sel; merge(); return; }
    if (!sameSig(ps, S.sig)) { S.sig = ps; mergePanels(); }
    if (sel !== S.selSig) { S.selSig = sel; mergeSel(); }
  }

  // ---------------------------------------------------------------- merge into three.js
  // Three separate uploads, so the frequent changes stay cheap: dragging the
  // density slider re-uploads only the panels, opening a building only its
  // own tinted copy; the town's walls and roofs go up again only when the
  // set of buildings (or the heat-map colouring) changes.
  // BATCHES. Buildings are drawn in batches of up to ~60k triangles, filled
  // in the order they are built. A newly built roof re-uploads only the open
  // batch (a few MB) instead of the whole town (measured 30 MB and a 50-70
  // ms stall per merge at z16 over the CBD). The batches are rebuilt from
  // scratch only when the colouring changes (heat map) or when roofs now
  // far out of view make up a third of what is drawn.
  const BATCH_TRIS = 60000;
  function drawnRecs() {
    const recs = [], seen = new Set();
    for (const bid of S.inRange) {
      if (seen.has(bid)) continue;
      seen.add(bid);
      const r = S.cache.get(bid);
      if (r) recs.push(r);
    }
    return recs;
  }
  function newBatch() {
    const mk = (T, m) => { const o = new T(new THREE.BufferGeometry(), m); o.frustumCulled = false; o.matrixAutoUpdate = false; o.visible = false; R.scene.add(o); return o; };
    const b = { recs: [], tris: 0, dirty: true,
      mesh: mk(THREE.Mesh, R.mat.mesh), lines: mk(THREE.LineSegments, R.mat.lines),
      panels: mk(THREE.Mesh, R.mat.panels), panelLines: mk(THREE.LineSegments, R.mat.panelLines) };
    R.batches.push(b);
    return b;
  }
  function dropBatches() {
    for (const b of R.batches) for (const o of [b.mesh, b.lines, b.panels, b.panelLines]) { o.geometry.dispose(); R.scene.remove(o); }
    R.batches = [];
    S.drawn = new Set();
  }
  // the height a record is lifted by: its ground, where the land is drawn
  const gOf = r => (S.abs ? r.ground : 0);
  function liftInto(dst, off, src, g) {
    dst.set(src, off);
    if (g) for (let i = off + 2; i < off + src.length; i += 3) dst[i] += g;
  }
  function uploadBatch(b) {
    const heat = S.heat;
    let nt = 0, nl = 0;
    for (const r of b.recs) { nt += r.tp.length; nl += r.lp.length; }
    const tp = new Float32Array(nt), tc = new Float32Array(nt);
    const lp = new Float32Array(nl), lc = new Float32Array(nl);
    let ot = 0, ol = 0;
    for (const r of b.recs) {
      liftInto(tp, ot, r.tp, gOf(r)); tc.set(heat ? r.tcH : r.tcG, ot); ot += r.tp.length;
      liftInto(lp, ol, r.lp, gOf(r)); lc.set(r.lc, ol); ol += r.lp.length;
    }
    setGeom(b.mesh, tp, tc);
    setGeom(b.lines, lp, lc);
    uploadPanels(b);
    b.dirty = false;
  }
  function addRecs(recs) {
    if (!R) return;
    let b = R.batches[R.batches.length - 1];
    for (const r of recs) {
      if (S.drawn.has(r.bid)) continue;
      if (!b || b.tris > BATCH_TRIS) b = newBatch();
      b.recs.push(r); b.tris += r.tp.length / 9; b.dirty = true;
      S.drawn.add(r.bid);
    }
    for (const x of R.batches) if (x.dirty) uploadBatch(x);
    stat();
    S.map.triggerRepaint();
  }
  function stat() {
    let t = 0, n = 0, np = 0;
    for (const b of R.batches) { t += b.tris; n += b.recs.length; np += b.np || 0; }
    S.stats.tris = Math.round(t); S.stats.buildings = n; S.stats.batches = R.batches.length; S.stats.panels = np;
  }
  // everything again: new colouring, or too much drawn out of view
  function merge() {
    S.dirty = false;
    if (!R) return;
    const t0 = performance.now();
    const v = viewState();
    S.heat = v.overlay && v.mode === "heatmap";
    dropBatches();
    addRecs(drawnRecs());
    mergeSel();
    S.stats.mergeMs = performance.now() - t0;
  }
  // bring the drawn set up to date with S.inRange: add what is missing,
  // rebuild when the out-of-view share has grown
  function sync() {
    if (!R) return;
    const want = drawnRecs();
    const wantSet = new Set(want.map(r => r.bid));
    let stale = 0;
    S.drawn.forEach(b => { if (!wantSet.has(b)) stale++; });
    if (stale > 200 && stale > S.drawn.size / 3) { merge(); return; }
    const add = want.filter(r => !S.drawn.has(r.bid));
    if (add.length) addRecs(add);
  }
  function panelList(r) {
    let liveBid, live;
    try { liveBid = layoutExcludedBuildingId; live = window.__liveLayoutData; } catch (e) { /* none */ }
    if (liveBid === r.bid && live && live.features) {
      // the panel editor's own set replaces this building's tile panels
      return live.features.filter(f => f.properties && f.properties.kind === "panel" &&
        f.properties.building_id === r.bid && f.geometry && f.geometry.type === "Polygon")
        .map(f => r.liftPanel(f.properties, ringsOf(f.geometry)[0]));
    }
    return r.panels.filter(p => panelShown(p.q));
  }
  function uploadPanels(b) {
    const v = viewState();
    const lists = [];
    let nf = 0, ne = 0, np = 0;
    if (v.overlay && v.mode === "layout") {
      for (const r of b.recs) for (const p of panelList(r)) { lists.push([p, gOf(r)]); nf += p.pos.length; ne += p.edge.length; np++; }
    }
    const pp = new Float32Array(nf), pc = new Float32Array(nf), pl = new Float32Array(ne);
    let of = 0, oe = 0;
    for (const [p, lift] of lists) {
      const col = panelColour(p.q), r = col[0] * p.sh, g = col[1] * p.sh, bl = col[2] * p.sh;
      liftInto(pp, of, p.pos, lift);
      for (let i = 0; i < p.pos.length; i += 3) { pc[of + i] = r; pc[of + i + 1] = g; pc[of + i + 2] = bl; }
      of += p.pos.length;
      liftInto(pl, oe, p.edge, lift); oe += p.edge.length;
    }
    setGeom(b.panels, pp, pc);
    setGeom(b.panelLines, pl, null);
    b.np = np;
  }
  function mergePanels() {
    if (!R) return;
    let np = 0;
    for (const b of R.batches) { uploadPanels(b); np += b.np; }
    S.stats.panels = np;
    S.map.triggerRepaint();
  }
  // the open building, drawn again over itself tinted, plus its eave line
  function mergeSel() {
    if (!R) return;
    const recs = drawnRecs();
    const sel = new Set(selection());
    const tp = [], tc = [], sp = [];
    for (const r of recs) {
      if (!sel.has(r.bid)) continue;
      const g = gOf(r);
      for (let i = 0; i < r.top.length; i++) sp.push(r.top[i] + (i % 3 === 2 ? g : 0));
      if (S.heat) continue;
      const src = r.tcG;
      for (let i = 0; i < r.tp.length; i++) tp.push(r.tp[i] + (i % 3 === 2 ? g : 0));
      for (let i = 0; i < src.length; i += 3) {
        tc.push(src[i] * 0.72 + C.select[0] * 0.28, src[i + 1] * 0.72 + C.select[1] * 0.28, src[i + 2] * 0.72 + C.select[2] * 0.28);
      }
    }
    setGeom(R.selMesh, new Float32Array(tp), new Float32Array(tc));
    setGeom(R.sel, new Float32Array(sp), null);
    S.map.triggerRepaint();
  }
  function setGeom(obj, pos, col) {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    if (col) g.setAttribute("color", new THREE.BufferAttribute(col, 3));
    obj.geometry.dispose();
    obj.geometry = g;
    obj.visible = pos.length > 0;
  }

  // ---------------------------------------------------------------- the custom layer
  function loadThree() {
    if (THREE) return Promise.resolve(THREE);
    if (threeLoading) return threeLoading;
    threeLoading = new Promise((res, rej) => {
      if (window.THREE) { THREE = window.THREE; return res(THREE); }
      const s = document.createElement("script");
      s.src = THREE_URL;
      s.crossOrigin = "anonymous";
      s.onload = () => { THREE = window.THREE; THREE ? res(THREE) : rej(new Error("three.js did not load")); };
      s.onerror = () => { threeLoading = null; rej(new Error("three.js did not load")); };
      document.head.appendChild(s);
    });
    return threeLoading;
  }
  function makeRenderer(gl) {
    THREE.ColorManagement.enabled = false;
    const renderer = new THREE.WebGLRenderer({ canvas: S.map.getCanvas(), context: gl, antialias: false });
    renderer.autoClear = false;
    renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
    const scene = new THREE.Scene();
    const camera = new THREE.Camera();
    const mat = {
      mesh: new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide,
        polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }),
      lines: new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.75 }),
      panels: new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide }),
      panelLines: new THREE.LineBasicMaterial({ color: new THREE.Color(...C.panelLine), transparent: true, opacity: 0.8 }),
    };
    // same depths as the main mesh (same polygon offset), drawn after it with
    // the default less-or-equal test, so it paints over its own building only
    const selMesh = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial({ vertexColors: true,
      side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }));
    selMesh.renderOrder = 1;
    const sel = new THREE.LineSegments(new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({ color: new THREE.Color(...C.selectLine), depthTest: false, transparent: true }));
    [selMesh, sel].forEach(o => { o.frustumCulled = false; o.visible = false; o.matrixAutoUpdate = false; scene.add(o); });
    sel.renderOrder = 10;
    // MSAA target, composited over the map with premultiplied alpha
    const rt = new THREE.WebGLRenderTarget(1, 1, { samples: 4, depthBuffer: true });
    const quadMat = new THREE.ShaderMaterial({
      uniforms: { t: { value: rt.texture } },
      vertexShader: "varying vec2 v; void main() { v = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }",
      fragmentShader: "uniform sampler2D t; varying vec2 v; void main() { gl_FragColor = texture2D(t, v); }",
      transparent: true, depthTest: false, depthWrite: false,
      blending: THREE.CustomBlending, blendSrc: THREE.OneFactor, blendDst: THREE.OneMinusSrcAlphaFactor,
      blendSrcAlpha: THREE.OneFactor, blendDstAlpha: THREE.OneMinusSrcAlphaFactor,
    });
    const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), quadMat);
    quad.frustumCulled = false;
    const quadScene = new THREE.Scene();
    quadScene.add(quad);
    return { renderer, scene, camera, mat, batches: [], selMesh, sel, rt, quadScene, mvp: null };
  }
  const layer = {
    id: LAYER_ID, type: "custom", renderingMode: "3d",
    onAdd(map, gl) {
      if (!R) R = makeRenderer(gl);
      S.dirty = true;
      merge();
    },
    render(gl, args) {
      if (!R || !S.O || S.k <= 0.001) return;
      const arr = (args && args.defaultProjectionData && args.defaultProjectionData.mainMatrix) ||
        (args && args.modelViewProjectionMatrix) || args;
      const m = new THREE.Matrix4().fromArray(Array.from(arr));
      const l = new THREE.Matrix4().makeTranslation(S.O.mx, S.O.my, 0)
        .scale(new THREE.Vector3(S.O.s, -S.O.s, S.O.s * S.k));
      R.camera.projectionMatrix = m.multiply(l);
      R.mvp = R.camera.projectionMatrix.clone();
      const w = gl.drawingBufferWidth, h = gl.drawingBufferHeight;
      const fb = gl.getParameter(gl.FRAMEBUFFER_BINDING);
      const rn = R.renderer;
      rn.resetState();
      if (fb === null) {
        if (R.rt.width !== w || R.rt.height !== h) R.rt.setSize(w, h);
        rn.setRenderTarget(R.rt);
        rn.setClearColor(0x000000, 0);
        rn.clear(true, true, false);
        rn.render(R.scene, R.camera);
        rn.setRenderTarget(null);
        rn.setViewport(0, 0, w, h);
        rn.render(R.quadScene, R.camera);
      } else {
        // something else owns the framebuffer: draw straight into it
        rn.setViewport(0, 0, w, h);
        rn.clearDepth();
        rn.render(R.scene, R.camera);
      }
      rn.resetState();
    },
    onRemove() { /* the renderer is kept for the next switch to 3D */ },
  };

  // ---------------------------------------------------------------- picking
  // A click on a roof selects THAT building: MapLibre alone would pick the
  // footprint on the flat ground behind it.
  function pick(px, py) {
    if (!R || !R.mvp || !S.O) return null;
    const c = S.map.getCanvas();
    const nx = px / c.clientWidth * 2 - 1, ny = 1 - py / c.clientHeight * 2;
    const inv = R.mvp.clone().invert();
    const a = new THREE.Vector4(nx, ny, -1, 1).applyMatrix4(inv), b = new THREE.Vector4(nx, ny, 1, 1).applyMatrix4(inv);
    const oW = [a.x / a.w, a.y / a.w, a.z / a.w], e = [b.x / b.w, b.y / b.w, b.z / b.w];
    const d = [e[0] - oW[0], e[1] - oW[1], e[2] - oW[2]];
    let best = Infinity, hit = null;
    const seen = new Set();
    for (const bid of S.inRange) {
      if (seen.has(bid)) continue;
      seen.add(bid);
      const r = S.cache.get(bid);
      if (!r) continue;
      const o = [oW[0], oW[1], oW[2] - gOf(r)];
      // slab test against the building's box
      let t0 = 0, t1 = best;
      const lo = [r.bb[0], r.bb[1], 0], hi = [r.bb[2], r.bb[3], r.zmax + 0.1];
      let miss = false;
      for (let k = 0; k < 3; k++) {
        if (Math.abs(d[k]) < 1e-12) { if (o[k] < lo[k] || o[k] > hi[k]) { miss = true; break; } continue; }
        let u = (lo[k] - o[k]) / d[k], v = (hi[k] - o[k]) / d[k];
        if (u > v) [u, v] = [v, u];
        t0 = Math.max(t0, u); t1 = Math.min(t1, v);
        if (t0 > t1) { miss = true; break; }
      }
      if (miss) continue;
      const p = r.tp;
      for (let i = 0; i < p.length; i += 9) {
        const e1x = p[i + 3] - p[i], e1y = p[i + 4] - p[i + 1], e1z = p[i + 5] - p[i + 2];
        const e2x = p[i + 6] - p[i], e2y = p[i + 7] - p[i + 1], e2z = p[i + 8] - p[i + 2];
        const hx = d[1] * e2z - d[2] * e2y, hy = d[2] * e2x - d[0] * e2z, hz = d[0] * e2y - d[1] * e2x;
        const det = e1x * hx + e1y * hy + e1z * hz;
        if (Math.abs(det) < 1e-12) continue;
        const f = 1 / det, sx = o[0] - p[i], sy = o[1] - p[i + 1], sz = o[2] - p[i + 2];
        const u = f * (sx * hx + sy * hy + sz * hz);
        if (u < 0 || u > 1) continue;
        const qx = sy * e1z - sz * e1y, qy = sz * e1x - sx * e1z, qz = sx * e1y - sy * e1x;
        const v = f * (d[0] * qx + d[1] * qy + d[2] * qz);
        if (v < 0 || u + v > 1) continue;
        const t = f * (e2x * qx + e2y * qy + e2z * qz);
        if (t > 1e-6 && t < best) { best = t; hit = { bid, x: o[0] + d[0] * t, y: o[1] + d[1] * t }; }
      }
    }
    return hit;
  }
  function onClickCapture(ev) {
    // only clicks on the map itself: the zoom, 3D and other controls sit
    // inside the same container
    if (!S.on || S.k < 0.5 || !R || ev.target !== S.map.getCanvas()) return;
    const m = S.map, c = m.getCanvas(), rect = c.getBoundingClientRect();
    const px = ev.clientX - rect.left, py = ev.clientY - rect.top;
    const hit = pick(px, py);
    if (!hit) return;
    // a point on the ground inside that footprint, on screen, where the
    // map's own hit test finds the same building
    const rec = S.cache.get(hit.bid);
    const cands = [[hit.x, hit.y]];
    if (rec) for (const rings of rec.outl) {
      const r = rings[0];
      let cx = 0, cy = 0;
      for (const p of r) { cx += p[0]; cy += p[1]; }
      cands.push([cx / r.length, cy / r.length]);
      for (const p of r) cands.push([p[0] + (cx / r.length - p[0]) * 0.2, p[1] + (cy / r.length - p[1]) * 0.2]);
    }
    for (const [x, y] of cands) {
      const [mx, my] = fromXY(x, y);
      const ll = new maplibregl.MercatorCoordinate(mx, my, 0).toLngLat();
      const pt = m.project(ll);
      if (pt.x < 1 || pt.y < 1 || pt.x > c.clientWidth - 1 || pt.y > c.clientHeight - 1) continue;
      let ok = false;
      try { ok = m.queryRenderedFeatures([pt.x, pt.y], { layers: ["buildings-fill"] }).some(f => f.properties.building_id === hit.bid); } catch (e) { /* no layer */ }
      if (!ok) continue;
      ev.stopPropagation();
      ev.preventDefault();
      const fake = {
        type: "click", clientX: rect.left + pt.x, clientY: rect.top + pt.y, target: c, button: 0,
        shiftKey: ev.shiftKey, metaKey: ev.metaKey, ctrlKey: ev.ctrlKey, altKey: ev.altKey,
        preventDefault() {}, stopPropagation() {},
      };
      try { m.fire(new maplibregl.MapMouseEvent("click", m, fake)); }
      catch (e) { m.fire({ type: "click", point: pt, lngLat: ll, originalEvent: fake, target: m, preventDefault() {} }); }
      return;
    }
  }

  // ---------------------------------------------------------------- switching
  function hide2D(hide) {
    const m = S.map;
    for (const id of HIDE_2D) {
      if (!m.getLayer(id)) continue;
      if (hide) m.setLayoutProperty(id, "visibility", "none");
    }
    if (!hide) {
      // hand visibility back to the page's own rules
      try { applyToggles(); } catch (e) { HIDE_2D.forEach(id => m.getLayer(id) && m.setLayoutProperty(id, "visibility", "visible")); }
    }
  }
  const HINT_ON = "3D: each roof face as measured, standing on the ground beside the building. Land and trees from the LiDAR survey.";
  const HINT_FLAT = "3D: each roof face as measured from LiDAR, standing on the ground beside the building. Ground and trees shown flat.";
  const HINT_ZOOM = "3D: zoom in to see the buildings.";
  function updateHint() {
    const d = document.getElementById("map-3d-hint");
    if (!d || !S.on) return;
    const t = S.map.getZoom() < BUILD_MIN_ZOOM && !S.inRange.length ? HINT_ZOOM : S.abs ? HINT_ON : HINT_FLAT;
    if (d.textContent !== t) d.textContent = t;
  }
  const ease = t => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  const GT = "ground-terrain";
  // The ground tiles for this data version: added as a terrain source once,
  // false where the version has none (the map then stays flat).
  function groundTerrain() {
    if (S.gt) return S.gt;
    const m = S.map;
    S.gt = fetch(`${S.base}data/terrain_ground/meta.json?v=${S.ver}`).then(r => (r.ok ? r.json() : null)).then(meta => {
      if (!meta) return false;
      if (!m.getSource(GT)) m.addSource(GT, {
        type: "raster-dem", tiles: [`${S.base}data/terrain_ground/{z}/{x}/{y}.png?v=${S.ver}`],
        tileSize: meta.tileSize || 256, encoding: meta.encoding || "mapbox",
        minzoom: meta.minzoom != null ? meta.minzoom : 8, maxzoom: meta.maxzoom != null ? meta.maxzoom : 17 });
      return true;
    }).catch(() => false);
    return S.gt;
  }
  // The land rises with the buildings: its height scale is k, so a building
  // lifted by its ground meets the ground drawn under it at every frame.
  function setLand(k) {
    const m = S.map;
    if (!S.abs) return;
    const e = Math.max(k, 0.0001);
    if (m.terrain && typeof m.terrain.exaggeration === "number" && m.getTerrain() && m.getTerrain().source === GT) {
      m.terrain.exaggeration = e; m.triggerRepaint();
    } else m.setTerrain({ source: GT, exaggeration: e });
  }
  function animate(kTo, pitchTo, ms, ticket) {
    const m = S.map, kFrom = S.k, pFrom = m.getPitch(), z0 = m.getZoom();
    let elapsed = 0, last = null;
    return new Promise(res => {
      const step = now => {
        if (ticket !== S.ticket) return res(false);
        elapsed += last === null ? 0 : Math.min(now - last, 34);
        last = now;
        const u = Math.min(1, elapsed / ms), e = ease(u);
        S.k = kFrom + (kTo - kFrom) * e;
        setLand(S.k);
        // the zoom held every frame: the land rising under the camera
        // would otherwise pull it in (preview.html, "ONE MOVE, NOT FIVE")
        if (pitchTo != null) m.jumpTo(S.abs ? { pitch: pFrom + (pitchTo - pFrom) * e, zoom: z0 } : { pitch: pFrom + (pitchTo - pFrom) * e });
        else if (S.abs) m.jumpTo({ zoom: z0 });
        else m.triggerRepaint();
        if (u < 1) requestAnimationFrame(step); else res(true);
      };
      requestAnimationFrame(step);
    });
  }
  let watchTimer = null;
  // Returns false when 3D buildings cannot run (the caller falls back to the
  // DSM terrain), true otherwise.
  async function setActive(on, opts) {
    const m = S.map;
    if (!m) return false;
    const ticket = ++S.ticket;
    opts = opts || {};
    if (on) {
      try {
        await loadThree();
        // pressed before the map finished loading: wait for it, then add
        for (let tries = 0; !m.getLayer(LAYER_ID); tries++) {
          try { m.addLayer(layer); } catch (e) {
            if (tries > 20 || !/not done loading/i.test(String(e && e.message))) throw e;
            await new Promise(r => { m.once("idle", r); setTimeout(r, 1500); });
            if (ticket !== S.ticket) return true;
          }
        }
      } catch (e) {
        console.warn("3D buildings unavailable; using the terrain view", e);
        return false;
      }
      if (ticket !== S.ticket) return true;
      S.on = true;
      // the fallback's draped DSM off; the ground under the solids on
      const tr = m.getTerrain && m.getTerrain();
      if (tr && tr.source !== GT) m.setTerrain(null);
      const abs = await groundTerrain();
      if (ticket !== S.ticket) return true;
      if (abs !== S.abs) { S.abs = abs; S.cache.clear(); S.feat.clear(); if (R) dropBatches(); }
      setLand(S.k);
      S.meshes = m.getZoom() >= BUILD_MIN_ZOOM - 1;
      if (S.meshes) hide2D(true);
      S.sig = panelSig(); S.selSig = selection().join(",");
      refresh();
      clearInterval(watchTimer);
      watchTimer = setInterval(watch, 250);
      updateHint();
      const target = opts.pitch != null ? opts.pitch : Math.min(m.getMaxPitch(), 55);
      await animate(1, Math.max(m.getPitch(), target), 1200, ticket);
      return true;
    }
    S.on = false;
    clearInterval(watchTimer);
    const done = await animate(0, opts.keepPitch ? null : 0, 650, ticket);
    if (done && ticket === S.ticket) {
      if (m.getLayer(LAYER_ID)) m.removeLayer(LAYER_ID);
      const tr = m.getTerrain && m.getTerrain();
      if (tr && tr.source === GT) { const z = m.getZoom(); m.setTerrain(null); m.jumpTo({ zoom: z }); }
      S.meshes = false;
      hide2D(false);
    }
    return true;
  }

  function init(map, o) {
    S.map = map;
    S.base = (o && o.dataBase) || "";
    S.ver = (o && o.dataVersion) || "";
    map.on("moveend", () => { if (S.on) scheduleRefresh(150); });
    map.on("idle", () => { if (S.on) scheduleRefresh(60); });
    map.on("sourcedata", e => {
      if (S.on && e.isSourceLoaded && (e.sourceId === "layout" || e.sourceId === "buildings")) scheduleRefresh(250);
    });
    map.getContainer().addEventListener("click", onClickCapture, true);
  }

  window.Buildings3D = {
    init, setActive,
    get active() { return S.on; },
    // preview.html's applyToggles calls this: the 2D layers it just showed
    // stay hidden while the meshes replace them
    onToggles() { if (S.on) { if (S.meshes) hide2D(true); watch(); } },
    _S: S, _pick: pick, _refresh: refresh, get _R() { return R; },
    // tile tilt vs a free least-squares fit of the DSM, per face: the check
    // that the aspect convention and the heights agree with the surface
    checkFaces(bid) {
      const F = featFor(bid);
      if (!F) return null;
      const out = [];
      HLEAN = LEAN_HEIGHTS && F.lean ? F.lean : [0, 0];
      F.facets.forEach(fc => {
        const pts = faceSamples(fc.polys, 0.6, 0.45);
        if (pts.length < 6) return;
        const t = fitPlane(pts, fc.q), l = fitPlane(pts, null);
        const rms = p => Math.sqrt(median(pts.map(q => (q[2] - (p.a * q[0] + p.b * q[1] + p.c)) ** 2)));
        const sl = p => Math.atan(Math.hypot(p.a, p.b)) * 180 / Math.PI;
        const as = p => ((Math.atan2(-p.a, -p.b) * 180 / Math.PI) + 360) % 360;
        out.push({ n: pts.length, tile: [fc.q.slope_deg, fc.q.aspect_deg], lsq: [+sl(l).toFixed(1), +as(l).toFixed(0)],
          medAbsTile: +rms(t).toFixed(2), medAbsLsq: +rms(l).toFixed(2) });
      });
      HLEAN = [0, 0];
      return out;
    },
    groundStats(bid) {
      const F = featFor(bid);
      if (!F) return null;
      const gs = ringSamples(F.outl, (x, y) => F.outl.some(r => inRings(r, x, y)));
      return [0.05, 0.15, 0.3, 0.5, 0.8].map(q => +percentile(gs, q).toFixed(1));
    },
    debug(bid) { const r = S.cache.get(bid); return r && { ground: r.ground, zmax: r.zmax, boxes: r.boxes, faces: r.faces, tris: r.tp.length / 9, panels: r.panels.length }; },
  };
})();
