/* Hand-editing a roof's panel layout (Advanced > Edit panels).
 *
 * Turn the mode on, click a roof, then:
 *   - click empty roof to add a panel there (a preview shows where it lands);
 *   - click a panel to select it; shift-click or shift-drag a box for several;
 *   - drag a panel to move it (the whole selection moves with it);
 *   - Delete / Backspace or the bin removes the selection; the + bubbles grow
 *     a row from one selected panel; Cmd/Ctrl-Z undoes.
 *
 * WHY THIS SITS ON THE LIVE-LAYOUT OVERLAY rather than a new rendering path:
 * the panels on screen come from vector tiles, which cannot be edited in place.
 * The map already knows how to hide one building's tile panels
 * (setLayoutExclusion) and draw a replacement set from GeoJSON (the live-layout
 * source). Editing copies the panels the map is showing for the building out of
 * the tiles, hides the originals, and from then on owns them. The card's
 * figures follow (liveFeatures / generationForInner in rooftop-solar.html).
 *
 * GEOMETRY. Panels are quadrilaterals in lng/lat at whatever angle the roof
 * runs. Everything works in a local metric frame centred on the building; flat
 * earth is more than good enough over 50 m.
 *
 * A PANEL ADDED OR MOVED TAKES THE ROOF UNDER IT. Each panel carries the
 * sunlight inputs generation.js prices it with: the tilt/direction bin (sb, ab)
 * and its shading factor (sh). An added or moved panel takes sb/ab from the
 * roof face under its centre (the facet features in the same tiles), and sh
 * from the build's own panels nearest it on that face (inverse-distance mean of
 * those within NEAR_SHADE_M), else the face's average shading. A panel off
 * every face keeps what it had; a brand-new one there is not allowed.
 *
 * NEW PANELS LAND ON THE ROOF'S GRID. On a face that already has panels the new
 * one copies the nearest panel's shape and angle and snaps to whole steps of the
 * face's measured pitch. The step is measured from neighbouring pairs, not from
 * the drawn size: tiles carry panels shrunk by 7 cm (shrink_panels_for_tiles),
 * so stepping by the drawn edge would creep. On an empty face the panel is laid
 * portrait, its long side down the slope, with its plan length shortened by the
 * cosine of the face's tilt (what a sloped panel covers seen from above).
 *
 * Edits are LOCAL to the browser tab, remembered per roof until the page
 * reloads. "Send correction" posts the edited layout through the issue form.
 */
(function () {
  "use strict";

  const SHRINK_M = 0.07;        // shrink_panels_for_tiles, for the fallback pitch
  const OVERLAP_TOL_M = 0.05;   // panels may touch, not overlap by more than this
  const EDGE_TOL_M = 0.15;      // a corner may hang this far past the face edge
  const NEAR_SHADE_M = 4;       // build panels used for an added panel's shading
  const PANEL_W_M = 1.134, PANEL_H_M = 1.961;   // config.PANEL_WIDTH_M / HEIGHT_M
  const EDIT_ZOOM = 20.5;       // zoom the editor brings a roof to when it is under 20
  const MAX_UNDO = 100;
  const NUDGE_M = 1.0;          // a new panel near an edge may shift this far to fit

  const S = {
    modeOn: false,            // Advanced > Edit panels is switched on
    active: false,            // a roof is open in the editor
    buildingId: null,
    panels: [],               // [{ring:[[x,y]x4] local metres, sun:{sb,ab,sh}|null}]
    faces: [],                // [{key, rings:[[[x,y]...]], props}]
    tileShade: [],            // the build's own panels: [{c, key, sh}]
    selected: new Set(),
    origin: null,             // [lng,lat] of the local frame
    mPerDegLng: 1, mPerDegLat: 111320,
    dragging: null,
    box: null,
    ghost: null,              // ring where a click would add a panel, or null
    hover: -1,
    undo: [],
    originalCount: 0,
    saved: new Map(),         // buildingId -> {origin, panels, originalCount, undo}
    sent: false,
  };

  // ---------- local metric frame ----------
  function setOrigin(lng, lat) {
    S.origin = [lng, lat];
    S.mPerDegLat = 111320;
    S.mPerDegLng = 111320 * Math.cos(lat * Math.PI / 180);
  }
  const toLocal = (lng, lat) => [(lng - S.origin[0]) * S.mPerDegLng,
                                 (lat - S.origin[1]) * S.mPerDegLat];
  const toLngLat = (x, y) => [S.origin[0] + x / S.mPerDegLng,
                              S.origin[1] + y / S.mPerDegLat];

  const centroid = ring => {
    let x = 0, y = 0;
    for (const p of ring) { x += p[0]; y += p[1]; }
    return [x / ring.length, y / ring.length];
  };
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1]];
  const add = (a, b) => [a[0] + b[0], a[1] + b[1]];
  const scale = (a, k) => [a[0] * k, a[1] * k];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1];
  const len = a => Math.hypot(a[0], a[1]);
  const unit = a => scale(a, 1 / (len(a) || 1));
  const translate = (ring, d) => ring.map(p => add(p, d));
  const copyPanels = ps => ps.map(p => ({ ring: p.ring.map(q => [...q]),
                                          sun: p.sun ? Object.assign({}, p.sun) : null }));

  function ringArea(ring) {
    let a = 0;
    for (let i = 0; i < ring.length; i++) {
      const p = ring[i], q = ring[(i + 1) % ring.length];
      a += p[0] * q[1] - q[0] * p[1];
    }
    return Math.abs(a) / 2;
  }

  function inRing(pt, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i], [xj, yj] = ring[j];
      if ((yi > pt[1]) !== (yj > pt[1]) &&
          pt[0] < (xj - xi) * (pt[1] - yi) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  /* A panel's two edge directions and lengths, from its own corners. */
  function axes(ring) {
    const e1 = sub(ring[1], ring[0]), e2 = sub(ring[3] || ring[2], ring[0]);
    return { u: e1, v: e2, lu: len(e1), lv: len(e2) };
  }

  /* Do two convex quads overlap by more than OVERLAP_TOL_M? Separating axes. */
  function overlaps(a, b) {
    for (const poly of [a, b]) {
      for (let i = 0; i < poly.length; i++) {
        const e = sub(poly[(i + 1) % poly.length], poly[i]);
        const n = unit([-e[1], e[0]]);
        let a0 = Infinity, a1 = -Infinity, b0 = Infinity, b1 = -Infinity;
        for (const p of a) { const t = dot(p, n); a0 = Math.min(a0, t); a1 = Math.max(a1, t); }
        for (const p of b) { const t = dot(p, n); b0 = Math.min(b0, t); b1 = Math.max(b1, t); }
        if (Math.min(a1, b1) - Math.max(a0, b0) <= OVERLAP_TOL_M) return false;
      }
    }
    return true;
  }

  function occupied(ring, ignore) {
    return S.panels.some((p, i) => !(ignore && ignore.has(i)) && overlaps(p.ring, ring));
  }

  // ---------- roof faces ----------
  function faceAt(pt) {
    for (const f of S.faces) if (f.rings.some(r => inRing(pt, r))) return f;
    return null;
  }

  /* On the roof: centre on a face, and every corner (pulled EDGE_TOL_M in
   * toward the centre) on one of this building's faces. */
  function onRoof(ring) {
    const c = centroid(ring);
    if (!faceAt(c)) return false;
    return ring.every(p => {
      const d = sub(c, p), l = len(d);
      const q = l > EDGE_TOL_M ? add(p, scale(d, EDGE_TOL_M / l)) : c;
      return !!faceAt(q);
    });
  }

  /* The sunlight inputs of the roof under a panel (see the header). */
  function sunFor(ring) {
    const c = centroid(ring);
    const f = faceAt(c);
    if (!f || f.props.sb == null) return null;
    let w = 0, t = 0;
    for (const q of S.tileShade) {
      if (q.key !== f.key) continue;
      const d = len(sub(q.c, c));
      if (d > NEAR_SHADE_M) continue;
      const k = 1 / Math.max(d, 0.5);
      w += k; t += k * q.sh;
    }
    const sh = w ? t / w : (f.props.sh != null ? f.props.sh : 1);
    return { sb: f.props.sb, ab: f.props.ab, sh: Math.round(sh * 10000) / 10000 };
  }

  /* Centre-to-centre spacing used on this roof face, along the given panel's
   * own axes, measured from the closest neighbouring pairs on the same face. */
  function measurePitch(ring) {
    const a = axes(ring), un = unit(a.u), vn = unit(a.v);
    const f = faceAt(centroid(ring));
    const cs = S.panels.filter(p => {
      const b = axes(p.ring);
      return Math.abs(dot(unit(b.u), un)) > 0.98 || Math.abs(dot(unit(b.u), vn)) > 0.98;
    }).map(p => centroid(p.ring)).filter(c => !f || faceAt(c) === f);
    let bu = Infinity, bv = Infinity;
    for (let i = 0; i < cs.length; i++) {
      for (let j = i + 1; j < cs.length; j++) {
        const d = sub(cs[j], cs[i]);
        const du = Math.abs(dot(d, un)), dv = Math.abs(dot(d, vn));
        if (dv < 0.25 && du > 0.2 && du < bu) bu = du;
        if (du < 0.25 && dv > 0.2 && dv < bv) bv = dv;
      }
    }
    return { u: isFinite(bu) && bu < a.lu * 1.6 ? bu : a.lu + SHRINK_M,
             v: isFinite(bv) && bv < a.lv * 1.6 ? bv : a.lv + SHRINK_M };
  }

  function stepFor(ring, dir) {
    const a = axes(ring), p = measurePitch(ring);
    const un = unit(a.u), vn = unit(a.v);
    return [scale(un, p.u), scale(un, -p.u), scale(vn, p.v), scale(vn, -p.v)][dir];
  }

  /* Where a panel added at pt would go (see the header), or null. */
  function candidateAt(pt) {
    const f = faceAt(pt);
    if (!f) return null;
    let best = null, bd = Infinity;
    for (const p of S.panels) {
      const c = centroid(p.ring);
      if (faceAt(c) !== f) continue;
      const d = len(sub(c, pt));
      if (d < bd) { bd = d; best = p; }
    }
    const fits = r => faceAt(centroid(r)) === f && onRoof(r) && !occupied(r);
    if (best) {
      // the grid cell under the pointer, else the nearest free one beside it
      const c0 = centroid(best.ring), a = axes(best.ring), pitch = measurePitch(best.ring);
      const un = unit(a.u), vn = unit(a.v), d = sub(pt, c0);
      const k0 = dot(d, un) / pitch.u, m0 = dot(d, vn) / pitch.v;
      const cells = [];
      for (let k = Math.round(k0) - 1; k <= Math.round(k0) + 1; k++) {
        for (let m = Math.round(m0) - 1; m <= Math.round(m0) + 1; m++) {
          cells.push({ k, m, dist: Math.hypot((k - k0) * pitch.u, (m - m0) * pitch.v) });
        }
      }
      cells.sort((x, y) => x.dist - y.dist);
      for (const { k, m, dist } of cells) {
        if (dist > Math.max(pitch.u, pitch.v)) break;
        const r = translate(best.ring, add(scale(un, k * pitch.u), scale(vn, m * pitch.v)));
        if (fits(r)) return r;
      }
      return null;
    }
    let ring;
    {
      const slope = f.props.slope_deg || 0, asp = (f.props.aspect_deg || 0) * Math.PI / 180;
      let down, across;
      if (slope < 3) {
        // flat: square to the face's longest edge
        const r = f.rings[0];
        let e = [1, 0], el = 0;
        for (let i = 0; i < r.length; i++) {
          const d = sub(r[(i + 1) % r.length], r[i]);
          if (len(d) > el) { el = len(d); e = d; }
        }
        across = unit(e); down = [-across[1], across[0]];
      } else {
        down = [Math.sin(asp), Math.cos(asp)];       // aspect: compass, clockwise from north
        across = [down[1], -down[0]];
      }
      const hl = (PANEL_H_M * Math.cos(slope * Math.PI / 180) - SHRINK_M) / 2;
      const hw = (PANEL_W_M - SHRINK_M) / 2;
      ring = [[-hw, -hl], [hw, -hl], [hw, hl], [-hw, hl]].map(([x, y]) =>
        add(pt, add(scale(across, x), scale(down, y))));
    }
    // Centred on the pointer if it fits; near an edge, nudged up to
    // NUDGE_M inward to the closest place it does.
    if (fits(ring)) return ring;
    for (let r = 0.1; r <= NUDGE_M + 1e-9; r += 0.1) {
      for (let i = 0; i < 16; i++) {
        const th = i * Math.PI / 8;
        const moved = translate(ring, [r * Math.cos(th), r * Math.sin(th)]);
        if (fits(moved)) return moved;
      }
    }
    return null;
  }

  // ---------- reading the building out of the tiles ----------
  function outerRings(geom) {
    if (!geom) return [];
    if (geom.type === "Polygon") return [geom.coordinates[0]];
    if (geom.type === "MultiPolygon") return geom.coordinates.map(p => p[0]);
    return [];
  }

  function readTiles(buildingId) {
    let feats = [];
    try {
      feats = map.querySourceFeatures("layout", {
        sourceLayer: "layout",
        filter: ["==", ["get", "building_id"], buildingId],
      });
    } catch (e) { feats = []; }
    const faces = new Map(), panels = new Map();
    for (const f of feats) {
      const q = f.properties || {};
      const rings = outerRings(f.geometry).filter(r => r && r.length >= 4);
      if (!rings.length) continue;
      if (q.kind === "facet") {
        const key = [q.sb, q.ab, q.slope_deg, q.aspect_deg, q.poa_kwh_m2_yr].join("_");
        if (!faces.has(key)) faces.set(key, { key, props: q, rings: [] });
        faces.get(key).rings.push(...rings);
      } else if (q.kind === "panel") {
        // A panel comes back once per tile it touches, sometimes clipped:
        // keep the largest piece of each.
        const ring = rings[0];
        const k = q.fill_order != null ? "o" + q.fill_order : ring[0].join(",");
        const area = ringArea(ring);
        if (!panels.has(k) || panels.get(k).area < area) panels.set(k, { ring, props: q, area });
      }
    }
    return { faces: [...faces.values()], panels: [...panels.values()] };
  }

  // ---------- rendering ----------
  function featureCollection() {
    return {
      type: "FeatureCollection",
      features: S.panels.map((p, i) => ({
        type: "Feature", id: i,
        properties: Object.assign({ kind: "panel", building_id: S.buildingId,
                      _edit_index: i, _selected: S.selected.has(i) ? 1 : 0 }, p.sun || {}),
        geometry: { type: "Polygon",
                    coordinates: [p.ring.map(([x, y]) => toLngLat(x, y))
                                        .concat([toLngLat(p.ring[0][0], p.ring[0][1])])] },
      })),
    };
  }

  function render() {
    const src = map.getSource("live-layout");
    if (src) src.setData(featureCollection());
    setHover(-1);
    renderHandles();
    updateToolbar();
    // the card's figures are recomputed a frame after setData; show them here
    // the frame after that
    requestAnimationFrame(() => requestAnimationFrame(updateToolbar));
  }

  function setHover(i) {
    if (i === S.hover) return;
    if (S.hover >= 0) try { map.setFeatureState({ source: "live-layout", id: S.hover }, { hover: false }); } catch (e) {}
    S.hover = i;
    if (i >= 0) try { map.setFeatureState({ source: "live-layout", id: i }, { hover: true }); } catch (e) {}
  }

  function renderGhost(ring) {
    S.ghost = ring;
    const src = map.getSource("pe-ghost");
    if (!src) return;
    src.setData({ type: "FeatureCollection", features: ring ? [{
      type: "Feature", properties: {},
      geometry: { type: "Polygon", coordinates: [ring.map(([x, y]) => toLngLat(x, y))
                                                 .concat([toLngLat(ring[0][0], ring[0][1])])] },
    }] : [] });
  }

  // ---------- DOM handles (bin + four plus bubbles) ----------
  let handleLayer = null;
  function ensureHandleLayer() {
    if (handleLayer) return handleLayer;
    handleLayer = document.createElement("div");
    handleLayer.id = "panel-edit-handles";
    handleLayer.style.cssText = "position:absolute; inset:0; pointer-events:none; z-index:6;";
    map.getCanvasContainer().parentElement.appendChild(handleLayer);
    return handleLayer;
  }

  function mkHandle(cls, label, title, onClick) {
    const b = document.createElement("button");
    b.className = "pe-handle " + cls;
    b.innerHTML = label;
    b.title = title;
    b.setAttribute("aria-label", title);
    b.style.pointerEvents = "auto";
    b.addEventListener("mousedown", ev => ev.stopPropagation());
    b.addEventListener("click", ev => { ev.stopPropagation(); onClick(); });
    b.addEventListener("dblclick", ev => ev.stopPropagation());
    return b;
  }

  function renderHandles() {
    const layer = ensureHandleLayer();
    layer.innerHTML = "";
    if (!S.active || S.selected.size === 0 || S.dragging) return;

    // The bin sits above the selection and acts on all of it.
    const sel = [...S.selected];
        // top-right of the selection, clear of the + bubbles and of the roof
    // the next click is likely to land on
    const scr = sel.flatMap(i => S.panels[i].ring.map(p => map.project(toLngLat(p[0], p[1]))));
    const right = Math.max(...scr.map(q => q.x)), top = Math.min(...scr.map(q => q.y));
    const bin = mkHandle("pe-bin", "&#128465;",
      sel.length > 1 ? `Remove ${sel.length} panels` : "Remove this panel", deleteSelected);
    bin.style.left = (right + 10) + "px";
    bin.style.top = (top - 34) + "px";
    layer.appendChild(bin);

    // The plus bubbles grow from one panel.
    if (sel.length !== 1) return;
    const idx = sel[0], ring = S.panels[idx].ring, c = centroid(ring);
    for (let dir = 0; dir < 4; dir++) {
      const d = stepFor(ring, dir);
      const cand = translate(ring, d);
      if (occupied(cand) || !onRoof(cand)) continue;
      const at = add(c, scale(d, 0.72));
      const pt = map.project(toLngLat(at[0], at[1]));
      const plus = mkHandle("pe-plus", "+", "Add a panel here", () => addAdjacent(idx, dir));
      plus.style.left = (pt.x - 11) + "px";
      plus.style.top = (pt.y - 11) + "px";
      layer.appendChild(plus);
    }
  }

  // ---------- operations ----------
  function pushUndo() {
    S.undo.push({ panels: copyPanels(S.panels), selected: [...S.selected] });
    if (S.undo.length > MAX_UNDO) S.undo.shift();
    S.sent = false;
  }
  function undo() {
    const u = S.undo.pop();
    if (!u) return;
    S.panels = u.panels;
    S.selected = new Set(u.selected.filter(i => i < S.panels.length));
    render();
  }

  /* A panel placed by clicking the roof is not selected, so the next click
   * places the next one; one grown with a + bubble is, so the row keeps
   * growing from it. */
  function addPanel(ring, select) {
    pushUndo();
    S.panels.push({ ring, sun: sunFor(ring) });
    S.selected = select ? new Set([S.panels.length - 1]) : new Set();
    render();
  }

  function addAdjacent(idx, dir) {
    const ring = S.panels[idx].ring;
    const cand = translate(ring, stepFor(ring, dir));
    if (occupied(cand) || !onRoof(cand)) return;
    addPanel(cand, true);
  }

  function deleteSelected() {
    if (!S.selected.size) return;
    pushUndo();
    S.panels = S.panels.filter((_, i) => !S.selected.has(i));
    S.selected.clear();
    render();
  }

  function panelAt(point) {
    const ll = map.unproject(point);
    const p = toLocal(ll.lng, ll.lat);
    for (let i = S.panels.length - 1; i >= 0; i--) if (inRing(p, S.panels[i].ring)) return i;
    return -1;
  }

  // ---------- session ----------
  function canEdit() {
    return typeof map !== "undefined" && map.getSource && map.getSource("live-layout");
  }

  function save() {
    if (!S.active || S.buildingId == null) return;
    S.saved.set(S.buildingId, { origin: S.origin, panels: copyPanels(S.panels),
                                originalCount: S.originalCount, undo: S.undo });
  }

  function enter(buildingId, opts) {
    opts = opts || {};
    if (!canEdit()) return;
    if (S.active && S.buildingId !== buildingId) save();
    const t = readTiles(buildingId);
    // the roof's tiles may still be loading (a shared link, a fresh switch to
    // Panel Layout): try again once the map has settled
    if (!t.faces.length && !t.panels.length && !opts.retried && !map.loaded()) {
      map.once("idle", () => enter(buildingId, Object.assign({}, opts, { retried: true })));
      return;
    }
    const savedEdit = opts.fresh ? null : S.saved.get(buildingId);
    if (opts.fresh) S.saved.delete(buildingId);
    const seed = savedEdit ? savedEdit.origin
               : t.faces.length ? t.faces[0].rings[0][0]
               : t.panels.length ? t.panels[0].ring[0]
               : map.getCenter().toArray();
    setOrigin(seed[0], seed[1]);
    S.buildingId = buildingId;
    S.faces = t.faces.map(f => ({ key: f.key, props: f.props,
      rings: f.rings.map(r => r.slice(0, -1).map(([lng, lat]) => toLocal(lng, lat))) }));
    S.tileShade = t.panels.filter(p => p.props.sh != null).map(p => {
      const c = centroid(p.ring.slice(0, -1).map(([lng, lat]) => toLocal(lng, lat)));
      const f = faceAt(c);
      return { c, key: f ? f.key : null, sh: p.props.sh };
    });
    if (savedEdit) {
      S.panels = copyPanels(savedEdit.panels);
      S.originalCount = savedEdit.originalCount;
      S.undo = savedEdit.undo || [];
    } else {
      // Start from exactly what the map is showing for this roof (density,
      // system size and payback bands), not every panel the build placed.
      const shown = t.panels.filter(p => typeof panelVisible !== "function" || panelVisible(p.props));
      S.panels = shown.map(p => ({
        ring: p.ring.slice(0, -1).map(([lng, lat]) => toLocal(lng, lat)),
        sun: p.props.sb != null ? { sb: p.props.sb, ab: p.props.ab, sh: p.props.sh } : null,
      }));
      S.originalCount = S.panels.length;
      S.undo = [];
    }
    S.selected = new Set();
    S.active = true;
    S.sent = false;
    if (typeof setLayoutExclusion === "function") setLayoutExclusion(buildingId);
    showToolbar();
    render();
    if (!opts.noZoom) frame();
  }

  /* Bring the roof close enough to place panels by hand. */
  function frame() {
    const pts = S.faces.flatMap(f => f.rings.flat()).concat(S.panels.flatMap(p => p.ring));
    if (!pts.length || map.getZoom() >= 20) return;
    const c = centroid(pts);
    const side = document.getElementById("side-panel");
    const right = side && side.classList.contains("visible") ? side.getBoundingClientRect().width : 0;
    map.easeTo({ center: toLngLat(c[0], c[1]), zoom: EDIT_ZOOM, duration: 600,
                 padding: { left: 0, right, top: 0, bottom: 0 } });
  }

  const hasEdits = rec => !!(rec && rec.undo && rec.undo.length);

  /* An edited roof keeps its edited layout whenever it is open, editor on or
   * off, so the card's figures stay those of the edited panels. Reset in the
   * editor gives it back the map's layout. */
  function showEdited(buildingId) {
    const rec = S.saved.get(buildingId);
    if (!hasEdits(rec) || !canEdit()) return false;
    setOrigin(rec.origin[0], rec.origin[1]);
    S.buildingId = buildingId;
    S.panels = copyPanels(rec.panels);
    S.selected = new Set();
    if (typeof setLayoutExclusion === "function") setLayoutExclusion(buildingId);
    map.getSource("live-layout").setData(featureCollection());
    return true;
  }

  /* The roof stops being edited (the card closed, or the mode went off): keep
   * the edits, and stop taking clicks. */
  function leaveRoof() {
    if (!S.active) return;
    save();
    const bid = S.buildingId;
    S.active = false;
    S.selected.clear();
    S.faces = [];
    S.dragging = null;
    S.box = null;
    renderGhost(null);
    if (handleLayer) handleLayer.innerHTML = "";
    map.getCanvas().style.cursor = "";
    const open = currentSelection();
    if (!(open.length === 1 && open[0] === bid && showEdited(bid))) {
      S.panels = [];
      if (typeof setLayoutExclusion === "function") setLayoutExclusion(undefined);
      const src = map.getSource("live-layout");
      if (src) src.setData({ type: "FeatureCollection", features: [] });
    }
    updateToolbar();
  }

  function setModeOn(on) {
    if (on === S.modeOn) return;
    S.modeOn = on;
    const btn = document.getElementById("toggle-edit-panels");
    if (btn) { btn.classList.toggle("active", on); btn.setAttribute("aria-pressed", on ? "true" : "false"); }
    if (!on) { leaveRoof(); hideToolbar(); return; }
    showToolbar();
    updateToolbar();
  }

  /* Open a roof in the editor once its panel tiles are on the map. Panels only
   * exist as tiles in Panel Layout, so switch to it first. */
  function start(buildingId) {
    setModeOn(true);
    if (buildingId == null) return;
    const go = () => enter(buildingId);
    if (typeof setMode === "function" && typeof mode !== "undefined" && mode !== "layout") {
      setMode("layout");
      map.once("idle", go);
    } else go();
  }

  function currentSelection() {
    try { return (typeof selectedIds !== "undefined" && selectedIds) ? selectedIds.slice() : []; }
    catch (e) { return []; }
  }

  function toggleMode() {
    if (S.modeOn) { setModeOn(false); return; }
    const sel = currentSelection();
    start(sel.length === 1 ? sel[0] : null);
  }

  /* Called by the page when the selection changes (openSelection) or clears. */
  function onSelection(ids) {
    if (!S.modeOn) {
      if (ids && ids.length === 1 && hasEdits(S.saved.get(ids[0]))) setTimeout(() => showEdited(ids[0]), 0);
      return;
    }
    if (!ids || ids.length !== 1) { leaveRoof(); return; }
    if (S.active && ids[0] === S.buildingId) return;
    // selectBuilding has just cleared the live-layout overlay; take it back
    // after the page has finished opening the roof
    if (S.active) { save(); S.active = false; }
    setTimeout(() => start(ids[0]), 0);
  }
  function onCleared() {
    if (!S.modeOn) return;
    // clearSelection has already emptied the overlay; just keep the edits
    if (S.active) { save(); S.active = false; S.selected.clear(); S.panels = []; S.faces = [];
                    renderGhost(null); if (handleLayer) handleLayer.innerHTML = ""; }
    updateToolbar();
  }

  // ---------- toolbar ----------
  let bar = null;
  function showToolbar() {
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "panel-edit-bar";
      bar.setAttribute("role", "region");
      bar.setAttribute("aria-label", "Panel editor");
      document.body.appendChild(bar);
    }
    bar.style.display = "";
    updateToolbar();
  }
  function hideToolbar() { if (bar) bar.style.display = "none"; }

  function figures() {
    const G = typeof window.__generation === "function" ? window.__generation() : null;
    if (!G || !G.ids || G.ids.length !== 1 || G.ids[0] !== S.buildingId || G.source !== "panels") return "";
    const kw = G.kwp && G.kwp.value, kwh = G.gen && G.gen.value;
    if (kw == null || kwh == null) return "";
    return ` &middot; ${kw.toFixed(1)} kW &middot; ${kwh >= 10000 ? (kwh / 1000).toFixed(1) + " MWh" : Math.round(kwh).toLocaleString() + " kWh"} a year`;
  }

  function updateToolbar() {
    if (!bar || !S.modeOn) return;
    if (!S.active) {
      bar.innerHTML = `
        <div class="pe-title">Edit panels</div>
        <div class="pe-hint">Click a roof to edit its panels.</div>
        <div class="pe-actions"><button id="pe-done">Done</button></div>`;
      bar.querySelector("#pe-done").onclick = () => setModeOn(false);
      return;
    }
    const n = S.panels.length, delta = n - (S.originalCount || 0);
    const p = (typeof selectedBuildingProps !== "undefined" && selectedBuildingProps
               && selectedBuildingProps.building_id === S.buildingId) ? selectedBuildingProps : null;
    const name = p && p.address ? p.address : `building #${S.buildingId}`;
    const nsel = S.selected.size;
    bar.innerHTML = `
      <div class="pe-title">Editing panels &middot; ${name}</div>
      <div class="pe-count"><b>${n}</b> panel${n === 1 ? "" : "s"}${delta
        ? ` <span class="pe-delta">${delta > 0 ? "+" : "&minus;"}${Math.abs(delta)}</span>` : ""}${figures()}
        ${S.sent ? '<span class="pe-sent">&#10003; sent</span>' : ""}</div>
      <div class="pe-hint">${nsel
        ? `${nsel} selected. Drag to move, <b>Delete</b> or the bin to remove${nsel === 1 ? ", + to add next to it" : ""}.`
        : "Click empty roof to add a panel. Click a panel to select it; shift-click or shift-drag for several."}</div>
      <div class="pe-actions">
        <button id="pe-undo" ${S.undo.length ? "" : "disabled"} title="Undo (Ctrl/Cmd-Z)">Undo</button>
        <button id="pe-reset" ${delta || S.undo.length ? "" : "disabled"} title="Back to the map's layout for this roof">Reset</button>
        <button id="pe-send" ${S.undo.length && !S.sent ? "" : "disabled"} title="Send this layout to the map's makers">Send correction</button>
        <button id="pe-done" class="primary">Done</button>
      </div>`;
    bar.querySelector("#pe-undo").onclick = undo;
    bar.querySelector("#pe-reset").onclick = () => enter(S.buildingId, { fresh: true, noZoom: true });
    bar.querySelector("#pe-done").onclick = () => setModeOn(false);
    bar.querySelector("#pe-send").onclick = sendCorrection;
  }

  /* Corrections go through the same form as issue reports and flags, as counts
   * plus panel centres: enough to rebuild the intended layout and find the
   * building without a geometry dump in a form field. */
  function sendCorrection() {
    const note = document.getElementById("bug-f-note");
    const ctx = document.getElementById("bug-f-context");
    const form = document.getElementById("bug-form");
    if (!note || !ctx || !form) return;
    const cs = S.panels.map(p => {
      const c = toLngLat(...centroid(p.ring));
      return c[0].toFixed(6) + " " + c[1].toFixed(6);
    });
    note.value = `PANEL CORRECTION building ${S.buildingId}: `
      + `${S.originalCount} -> ${S.panels.length} panels`;
    ctx.value = `EDITED LAYOUT building ${S.buildingId} | `
      + (window.__bugCtx ? window.__bugCtx() : "")
      + " | centres: " + cs.join("; ");
    form.submit();
    S.sent = true;
    updateToolbar();
  }

  // ---------- input ----------
  function canvasPoint(e) {
    const rect = map.getCanvas().getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  function wire() {
    if (!map.getSource("pe-ghost")) {
      map.addSource("pe-ghost", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "pe-ghost-fill", type: "fill", source: "pe-ghost",
                     paint: { "fill-color": "#25c25f", "fill-opacity": 0.35 } });
      map.addLayer({ id: "pe-ghost-line", type: "line", source: "pe-ghost",
                     paint: { "line-color": "#7ee2a8", "line-width": 1.5, "line-dasharray": [2, 1.5] } });
    }

    map.on("click", ev => {
      if (!S.modeOn || !S.active) return;
      const oe = ev.originalEvent || {};
      const multi = oe.shiftKey || oe.metaKey || oe.ctrlKey;
      const idx = panelAt(ev.point);
      if (idx >= 0) {
        if (multi) { if (S.selected.has(idx)) S.selected.delete(idx); else S.selected.add(idx); }
        else S.selected = new Set([idx]);
        render();
        return;
      }
      if (!multi && S.ghost) {
        const ring = S.ghost;
        renderGhost(null);
        addPanel(ring);
        return;
      }
      if (S.selected.size) { S.selected.clear(); render(); }
    });

    const cvs = map.getCanvas();
    cvs.addEventListener("mousedown", e => {
      if (!S.modeOn || !S.active || e.button !== 0) return;
      const pt = canvasPoint(e);
      const idx = panelAt(pt);
      if (idx >= 0 && !(e.shiftKey && !S.selected.has(idx))) {
        // press on a panel and drag: move it (and the rest of the selection)
        if (!S.selected.has(idx)) { S.selected = new Set([idx]); render(); }
        const ll = map.unproject(pt);
        S.dragging = { from: toLocal(ll.lng, ll.lat), moved: false,
                       start: [...S.selected].map(i => S.panels[i].ring.map(p => [...p])) };
        map.dragPan.disable();
        renderGhost(null);
        e.preventDefault();
      } else if (idx < 0 && e.shiftKey) {
        // shift-drag on empty roof: a box that selects every panel it covers
        const el = document.createElement("div");
        el.id = "pe-box";
        map.getCanvasContainer().parentElement.appendChild(el);
        S.box = { start: pt, el };
        map.dragPan.disable();
        renderGhost(null);
        e.preventDefault();
      }
    }, true);

    window.addEventListener("mousemove", e => {
      if (S.dragging) {
        const ll = map.unproject(canvasPoint(e));
        const d = sub(toLocal(ll.lng, ll.lat), S.dragging.from);
        if (!S.dragging.moved) {
          if (len(d) < 0.05) return;
          pushUndo();
          S.dragging.moved = true;
        }
        [...S.selected].forEach((i, k) => { S.panels[i].ring = S.dragging.start[k].map(p => add(p, d)); });
        renderHandles();
        const src = map.getSource("live-layout");
        if (src) src.setData(featureCollection());
        return;
      }
      if (S.box) {
        const p = canvasPoint(e), a = S.box.start;
        Object.assign(S.box.el.style, { left: Math.min(a.x, p.x) + "px", top: Math.min(a.y, p.y) + "px",
          width: Math.abs(p.x - a.x) + "px", height: Math.abs(p.y - a.y) + "px" });
        S.box.end = p;
        return;
      }
    });

    window.addEventListener("mouseup", () => {
      if (S.dragging) {
        const dr = S.dragging;
        S.dragging = null;
        map.dragPan.enable();
        if (dr.moved && [...S.selected].some(i => !faceAt(centroid(S.panels[i].ring)))) {
          // dropped off the roof: put them back
          S.undo.pop();
          [...S.selected].forEach((i, k) => { S.panels[i].ring = dr.start[k]; });
          render();
        } else if (dr.moved) {
          // moved panels take the roof under them (see the header)
          for (const i of S.selected) {
            const sun = sunFor(S.panels[i].ring);
            if (sun) S.panels[i].sun = sun;
          }
          render();
        } else renderHandles();
      }
      if (S.box) {
        const { start: a, end: b, el } = S.box;
        S.box = null;
        el.remove();
        map.dragPan.enable();
        if (b) {
          const x0 = Math.min(a.x, b.x), x1 = Math.max(a.x, b.x), y0 = Math.min(a.y, b.y), y1 = Math.max(a.y, b.y);
          S.panels.forEach((p, i) => {
            const c = centroid(p.ring), q = map.project(toLngLat(c[0], c[1]));
            if (q.x >= x0 && q.x <= x1 && q.y >= y0 && q.y <= y1) S.selected.add(i);
          });
          render();
        }
      }
    });

    // Hover: highlight the panel under the pointer, or preview where a click
    // would add one.
    map.on("mousemove", ev => {
      if (!S.modeOn || !S.active || S.dragging || S.box) return;
      const idx = panelAt(ev.point);
      setHover(idx);
      if (idx >= 0) {
        renderGhost(null);
        map.getCanvas().style.cursor = S.selected.has(idx) ? "move" : "pointer";
        return;
      }
      const oe = ev.originalEvent || {};
      const ring = oe.shiftKey ? null : candidateAt(toLocal(ev.lngLat.lng, ev.lngLat.lat));
      renderGhost(ring);
      map.getCanvas().style.cursor = ring ? "copy" : "";
    });
    map.getCanvas().addEventListener("mouseleave", () => {
      if (!S.active) return;
      setHover(-1);
      renderGhost(null);
    });

    map.on("move", () => { if (S.active) renderHandles(); });
    document.addEventListener("keydown", e => {
      if (!S.modeOn || !S.active) return;
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if ((e.metaKey || e.ctrlKey) && (e.key === "z" || e.key === "Z")) { e.preventDefault(); undo(); return; }
      if (e.key === "Escape" && S.selected.size) { S.selected.clear(); render(); }
      if ((e.key === "Delete" || e.key === "Backspace") && S.selected.size) {
        e.preventDefault(); deleteSelected();
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "a") {
        e.preventDefault(); S.selected = new Set(S.panels.map((_, i) => i)); render();
      }
    });

    const btn = document.getElementById("toggle-edit-panels");
    if (btn) btn.addEventListener("click", toggleMode);
  }

  window.PanelEditor = {
    start, enter, toggleMode, onSelection, onCleared,
    modeOn: () => S.modeOn,
    isActive: () => S.active,
    buildingId: () => S.buildingId,
    _state: S, _candidateAt: candidateAt, _onRoof: onRoof, _toLocal: toLocal,
    _wire: wire,
  };
})();
