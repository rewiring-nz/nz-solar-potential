/* Video export: a recorded tour of the map that shows panels being added to
 * one kind of building while a counter adds up.
 *
 * The tour opens on a title over the view the map is on, flies in to
 * well-covered areas of the chosen building type, reveals each area's panels
 * in the roof's own fill order (the density slider's order) while that
 * group's MW counts up top left, flies back out and closes on the title with
 * each group's total for the starting view.
 *
 * The video is rendered a frame at a time: the map's own WebGL canvas copied
 * onto a 2D canvas with the counter drawn over it, encoded as H.264 by the
 * browser (WebCodecs) and written as an MP4 (mp4-muxer). Nothing leaves the
 * browser. The page supplies the map-specific parts as
 * hooks (rooftop-solar.html, "VIDEO EXPORT"), so this file knows nothing
 * about tiles or building types:
 *
 *   map              the MapLibre map
 *   groups           [[label, [type, ...]], ...] to offer
 *   detail           the same shape, optional: single types offered under
 *                    "One building type", each counted under its own label
 *   begin(types)     show only these types, panels hidden; returns once set
 *   end()            put the map back as it was
 *   cells()          [{lng, lat, lod, btype, panels, buildings}] for the areas in
 *                    view, one entry per area and building type
 *   bestRoof(lng, lat, types)  [lng, lat] of the roof at the middle of the
 *                    densest cluster of panels in view near this point (of
 *                    these types, when given), or null
 *   reveal(pct)      draw each roof's panels up to this fill rank (0-100);
 *                    null draws every roof's panels as the map normally does
 *   finestLod        the cell size a stop is flown to (smaller cells are refined to it)
 *   panelKw          kW per panel
 */
const VideoExport = (() => {
  // A stop opens close, on about ten roofs, and drifts out to a few streets
  // while the panels are added.
  const CLOSE_ZOOM = 19.2, STOP_ZOOM = 17.6;
  // Big roofs (an area averaging more panels a building than this: shops,
  // sheds, schools) are framed this much wider, or one roof fills the frame.
  const BIG_ROOF_PANELS = 60, BIG_ROOF_ZOOM_OUT = 1.1;
  const REFINE_ZOOM = 11;      // where the finest cells are drawn
  // FLY_MS: the first zoom in and the last zoom out. Between two areas the
  // camera rises for HOP_UP_MS and comes down for HOP_DOWN_MS. REVEAL_MS is
  // how long an area's panels take to fill; the camera leaves after
  // STAY_SHARE of it, with the roofs still filling.
  const FLY_MS = 4000, HOP_UP_MS = 1500, HOP_DOWN_MS = 2100, REVEAL_MS = 5000, STAY_SHARE = 0.62;
  // the title screen at each end, and how fast it fades
  const INTRO_MS = 1600, INTRO_FADE_MS = 450, OUTRO_FADE_MS = 700, OUTRO_HOLD_MS = 3200;
  const LINE_FADE_FRAMES = 9;
  // A flight in to a stop ends this close to its zoom: the last stretch of a
  // flight barely moves (0.1 of a zoom level over most of a second), which
  // read as a pause before the panels started.
  const LAND_WITHIN_ZOOM = 0.12;
  const MAX_WIDTH = 1920, FPS = 30, BITRATE = 8e6;
  // EVERY FRAME WAITS FOR ITS TILES. A frame drawn before the photo tiles for
  // its zoom had arrived showed the blurred parent tile, and the sharp one
  // popped in a few frames later: the flights flickered between the two.
  // (The first version waited 250 ms a frame in flight.) These are caps for a
  // tile that never comes; a frame normally waits only as long as it needs.
  const FLIGHT_TILE_WAIT_MS = 5000, ARRIVE_TILE_WAIT_MS = 10000, REVEAL_TILE_WAIT_MS = 5000;
  // Writes the MP4 container around the browser's own H.264 encoder. Loaded
  // only when a video is exported.
  const MUXER_URL = "https://unpkg.com/mp4-muxer@5.2.2/build/mp4-muxer.js";
  const MUXER_SRI = "sha256-zE0wvSC5/+CxX1nPDePhkwYmzNRqUUbjbk1hnX/s9j0=";

  const ease = t => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);
  const even = n => Math.max(2, Math.round(n / 2) * 2);
  class Cancelled extends Error {}

  function kmBetween(a, b) {
    const R = 6371, d2r = Math.PI / 180;
    const dLat = (b.lat - a.lat) * d2r, dLng = (b.lng - a.lng) * d2r;
    const h = Math.sin(dLat / 2) ** 2 +
      Math.cos(a.lat * d2r) * Math.cos(b.lat * d2r) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  // MAJOR AREAS ONLY. A stop is scored by its neighbourhood, not by its own
  // cell: its panels plus half of those in the eight cells around it
  // (about 1.3 km across at the finest grid). One big shed in paddocks, or
  // the last street at the edge of town, has a strong cell and an empty
  // neighbourhood; a suburb of homes or a block of large roofs has both.
  // Scoring single cells sent some tours to places that looked out of place.
  function scoreAreas(cells) {
    const key = c => {
      const n = 2 ** c.lod;
      const x = Math.floor((c.lng + 180) / 360 * n);
      const y = Math.floor((1 - Math.asinh(Math.tan(c.lat * Math.PI / 180)) / Math.PI) / 2 * n);
      return [x, y];
    };
    const sums = new Map();
    const at = cells.map(c => {
      const [x, y] = key(c), k = `${c.lod}/${x}/${y}`;
      sums.set(k, (sums.get(k) || 0) + c.panels);
      return [x, y];
    });
    return cells.map((c, i) => {
      let around = 0;
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
        if (dx || dy) around += sums.get(`${c.lod}/${at[i][0] + dx}/${at[i][1] + dy}`) || 0;
      }
      return { ...c, score: c.panels + 0.5 * around };
    });
  }

  // A DIFFERENT TOUR EACH TIME, among the major areas: drawn at random from
  // those scoring at least SHORTLIST_SHARE of the best. The rest follow in
  // score order, used only when the major areas cannot fill the tour.
  // rng null takes the best.
  const SHORTLIST_SHARE = 0.6;
  function shortlist(cells, rng) {
    const sorted = scoreAreas(cells.filter(c => c.panels > 0)).sort((a, b) => b.score - a.score);
    if (!sorted.length) return sorted;
    const floor = sorted[0].score * SHORTLIST_SHARE;
    sorted.forEach(c => { c.major = c.score >= floor; });
    if (!rng) return sorted;
    const good = sorted.filter(c => c.major), rest = sorted.filter(c => !c.major);
    for (let i = good.length - 1; i > 0; i--) {
      const j = Math.floor(rng() * (i + 1));
      [good[i], good[j]] = [good[j], good[i]];
    }
    return good.concat(rest);
  }

  // Stops are kept minKm apart so each is a different part of the region.
  // When the major areas cannot fill the tour at that spacing, the spacing
  // halves (down to MIN_GAP_KM: another block of the same centre) before a
  // lesser area is taken: two stops in the one business district beat a
  // stop at a lone shed.
  const MIN_GAP_KM = 0.25;
  function fill(n, minKm, attempt) {
    let out = [];
    for (const lesser of [false, true]) {
      for (let gap = minKm; ; gap /= 2) {
        out = attempt(Math.max(gap, MIN_GAP_KM), lesser);
        if (out.length === n) return out;
        if (gap <= MIN_GAP_KM) break;
      }
    }
    return out;
  }
  const apart = (out, gap) => c => out.every(o => kmBetween(o, c) >= gap);
  const split = (cells, rng) => {
    const all = shortlist(cells, rng), major = all.filter(c => c.major);
    return lesser => (lesser ? all : major);
  };

  // n areas of one kind.
  function pickStops(cells, n, minKm, rng) {
    const pool = split(cells, rng);
    return fill(n, minKm, (gap, lesser) => {
      const out = [];
      for (const c of pool(lesser)) {
        if (apart(out, gap)(c)) out.push(c);
        if (out.length === n) break;
      }
      return out;
    });
  }

  // One stop per entry of `mix` in turn (each a list of building types), so a
  // tour of every building shows homes and businesses. A turn with no area
  // of its types left takes one of any type.
  function pickMixed(cells, n, minKm, mix, rng) {
    const pools = mix.map(want => split(cells.filter(c => want.includes(c.btype)), rng));
    const any = split(cells, rng);
    return fill(n, minKm, (gap, lesser) => {
      const out = [];
      for (let i = 0; i < n; i++) {
        const free = apart(out, gap);
        const c = pools[i % mix.length](lesser).find(free) || (lesser ? any(true).find(free) : null);
        if (!c) break;
        out.push({ ...c, want: mix[i % mix.length] });
      }
      return out;
    });
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  // WHAT IS DRAWN OVER THE MAP, sized in 1080p pixels and scaled to the frame:
  //   o.card (0-1)   the title screen: the map dimmed under the title, and
  //                  under that o.cardLines when given (the closing totals)
  //   o.lines        the section counters, top left over a dark corner:
  //                  [{label, kw, alpha, mw}], one under the other
  //   o.attribution  the map's attribution, bottom right
  // The yellow-to-orange is the page heading's (.rsp-heading).
  const TITLE = "Local Rooftop Solar Potential";
  function drawOverlay(ctx, W, H, o) {
    const s = Math.min(W / 1920, H / 1080) * (W < 1100 ? 1.5 : 1);
    const font = (px, weight) => `${weight} ${px * s}px system-ui, -apple-system, "Segoe UI", sans-serif`;
    const gold = (x0, x1) => {
      const g = ctx.createLinearGradient(x0, 0, x1, 0);
      g.addColorStop(0, "#ffe600"); g.addColorStop(1, "#ff8c00");
      return g;
    };
    // "Residential: " in white, the figure in the gradient; returns the width
    const figure = (l, x, y, px) => {
      ctx.font = font(px, 800);
      const label = l.label + ": ", value = l.mw ? (l.kw / 1000).toFixed(1) + " MW" : Math.round(l.kw).toLocaleString() + " kW";
      const wl = ctx.measureText(label).width, wv = ctx.measureText(value).width;
      if (x == null) return wl + wv;
      ctx.fillStyle = "#fff";
      ctx.fillText(label, x, y);
      ctx.fillStyle = gold(x + wl, x + wl + wv);
      ctx.fillText(value, x + wl, y);
      return wl + wv;
    };
    ctx.save();
    ctx.textBaseline = "alphabetic";
    ctx.shadowColor = "rgba(0,0,0,0.55)";
    ctx.shadowBlur = 8 * s;

    const lines = (o.lines || []).filter(l => l.alpha > 0);
    const corner = Math.max(0, ...lines.map(l => l.alpha)) * (1 - o.card);
    if (corner > 0) {
      // a dark corner that fades out, so the text reads over any roofscape
      const r = (900 + 110 * lines.length) * s;
      const g = ctx.createRadialGradient(0, 0, 0, 0, 0, r);
      g.addColorStop(0, `rgba(0,0,0,${0.86 * corner})`);
      g.addColorStop(0.55, `rgba(0,0,0,${0.5 * corner})`);
      g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.save();
      ctx.shadowColor = "transparent";
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, r, r);
      ctx.restore();
      lines.forEach((l, i) => {
        ctx.globalAlpha = l.alpha * (1 - o.card);
        figure(l, 56 * s, (104 + 82 * i) * s, 58);
      });
      ctx.globalAlpha = 1;
    }

    if (o.card > 0) {
      ctx.globalAlpha = o.card;
      ctx.save();
      ctx.shadowColor = "transparent";
      ctx.fillStyle = "rgba(0,0,0,0.62)";
      ctx.fillRect(0, 0, W, H);
      ctx.restore();
      const rows = o.cardLines || [];
      let px = 92;
      ctx.font = font(px, 800);
      const fit = W * 0.88 / ctx.measureText(TITLE).width;
      if (fit < 1) { px *= fit; ctx.font = font(px, 800); }
      const tw = ctx.measureText(TITLE).width;
      const rowH = 74 * s, blockH = px * s + (rows.length ? 46 * s + rowH * rows.length : 0);
      let y = (H - blockH) / 2 + px * s * 0.8;
      ctx.fillStyle = gold((W - tw) / 2, (W + tw) / 2);
      ctx.fillText(TITLE, (W - tw) / 2, y);
      y += 46 * s;
      rows.forEach(l => {
        y += rowH;
        figure(l, (W - figure(l, null, 0, 50)) / 2, y, 50);
      });
      ctx.globalAlpha = 1;
    }

    if (o.attribution) {
      ctx.shadowColor = "transparent";
      ctx.font = font(18, 500);
      const w = ctx.measureText(o.attribution).width;
      ctx.fillStyle = "rgba(255,255,255,0.78)";
      roundRect(ctx, W - w - 34 * s, H - 46 * s, w + 22 * s, 32 * s, 8 * s);
      ctx.fill();
      ctx.fillStyle = "#16181d";
      ctx.fillText(o.attribution, W - w - 23 * s, H - 24 * s);
    }
    ctx.restore();
  }

  function loadMuxer() {
    if (window.Mp4Muxer) return Promise.resolve();
    return new Promise((res, rej) => {
      const el = document.createElement("script");
      el.src = MUXER_URL; el.integrity = MUXER_SRI; el.crossOrigin = "anonymous";
      el.onload = res;
      el.onerror = () => rej(new Error("Could not load the video writer (mp4-muxer). Check the connection and try again."));
      document.head.append(el);
    });
  }

  async function run(h, opt, ui) {
    const map = h.map;
    let cancelled = false;
    ui.onCancel = () => { cancelled = true; };
    const check = () => { if (cancelled) throw new Cancelled(); };
    const sleep = ms => new Promise(r => setTimeout(r, ms)).then(check);
    const idle = (timeout = 12000) => new Promise(res => {
      const t = setTimeout(res, timeout);
      map.once("idle", () => { clearTimeout(t); res(); });
      map.triggerRepaint();
    }).then(check);

    const start = { center: map.getCenter(), zoom: map.getZoom(), bearing: map.getBearing(), pitch: map.getPitch() };
    const state = { card: 1, cardLines: null, lines: [],
                    attribution: (document.querySelector(".maplibregl-ctrl-attrib-inner")?.textContent || "").trim() };
    let enc = null, clockSet = false;
    let late = 0;
    try {

      ui.status("Preparing: finding the areas…");
      await loadMuxer();
      await h.begin(opt.types);
      h.reveal(0);
      await idle();
      const cells = h.cells();
      const total = cells.reduce((a, c) => ({ panels: a.panels + c.panels, buildings: a.buildings + c.buildings }),
                                 { panels: 0, buildings: 0 });
      if (!(total.panels > 0)) {
        throw new Error("No panels of this building type in view. Move the map to a town or region that has some, then try again.");
      }
      const b = map.getBounds();
      const diagKm = kmBetween({ lng: b.getWest(), lat: b.getSouth() }, { lng: b.getEast(), lat: b.getNorth() });
      const minKm = Math.max(0.8, diagKm * 0.12);
      const stops = opt.mix ? pickMixed(cells, opt.stops, minKm, opt.mix, Math.random)
                            : pickStops(cells, opt.stops, minKm, Math.random);

      // LOAD EACH STOP FIRST, so its tiles are in the map's cache when the
      // flight arrives. A stop picked from coarse cells is first narrowed to
      // its best fine cell.
      for (let i = 0; i < stops.length; i++) {
        ui.status(`Preparing: loading area ${i + 1} of ${stops.length}…`);
        if (stops[i].lod < h.finestLod) {
          map.jumpTo({ center: [stops[i].lng, stops[i].lat], zoom: REFINE_ZOOM });
          await idle();
          const want = stops[i].want;
          const fine = h.cells().filter(c => !want || want.includes(c.btype));
          const best = pickStops(fine, 1, 0)[0];
          if (best) stops[i] = { ...best, want };
        }
        const out = stops[i].panels / Math.max(1, stops[i].buildings) > BIG_ROOF_PANELS ? BIG_ROOF_ZOOM_OUT : 0;
        stops[i].close = CLOSE_ZOOM - out;
        stops[i].wide = STOP_ZOOM - out;
        map.jumpTo({ center: [stops[i].lng, stops[i].lat], zoom: stops[i].wide, bearing: 0, pitch: 0 });
        await idle();
        // the close-up opens on the densest cluster of roofs near the middle
        // of the area, not on the road or park at the middle of the cell
        const roof = h.bestRoof(stops[i].lng, stops[i].lat, stops[i].want);
        if (roof) [stops[i].lng, stops[i].lat] = roof;
        map.jumpTo({ center: [stops[i].lng, stops[i].lat], zoom: stops[i].close });
        await idle();
      }
      map.jumpTo(start);
      await idle();

      // THE VIDEO IS RENDERED A FRAME AT A TIME, NOT FILMED. The map's clock
      // is set by hand (maplibregl.setNow), each frame is drawn at once
      // (map.redraw), copied with the counter onto a 2D canvas and handed to
      // the encoder with its own timestamp. So the video is exactly FPS
      // frames a second however slow the machine is, a frame can wait for
      // its tiles, and a tab in the background records the same video.
      const src = map.getCanvas();
      const k = Math.min(1, MAX_WIDTH / src.width);
      const W = even(src.width * k), H = even(src.height * k);
      const canvas = ui.canvas;
      canvas.width = W; canvas.height = H;
      const ctx = canvas.getContext("2d");
      const config = { codec: "avc1.640033", width: W, height: H, bitrate: BITRATE, framerate: FPS };
      if (!(await VideoEncoder.isConfigSupported(config)).supported) {
        throw new Error("This browser cannot encode H.264 video at this size. Try Chrome or Edge.");
      }
      const muxer = new Mp4Muxer.Muxer({ target: new Mp4Muxer.ArrayBufferTarget(),
                                         video: { codec: "avc", width: W, height: H }, fastStart: "in-memory" });
      let encError = null;
      enc = new VideoEncoder({ output: (chunk, meta) => muxer.addVideoChunk(chunk, meta),
                               error: e => { encError = e; } });
      enc.configure(config);

      const totalFrames = Math.round(FPS / 1000 * (INTRO_MS + INTRO_FADE_MS + FLY_MS + stops.length * REVEAL_MS * STAY_SHARE + (stops.length - 1) * (HOP_UP_MS + HOP_DOWN_MS) + FLY_MS + OUTRO_FADE_MS + OUTRO_HOLD_MS));
      let clock = performance.now(), frameNo = 0;
      maplibregl.setNow(clock);
      clockSet = true;
      ui.recording(true);
      // wait (real time) for the tiles this frame needs, redrawing at the same instant
      const tiles = async maxMs => {
        const t0 = performance.now();
        while (!map.areTilesLoaded() && performance.now() - t0 < maxMs) {
          await sleep(15);
          map.redraw();
        }
        if (!map.areTilesLoaded()) late++;
      };
      const frame = async (waitMs = 0) => {
        check();
        if (encError) throw encError;
        maplibregl.setNow(clock);
        map.redraw();
        if (waitMs) await tiles(waitMs);
        tick();
        ctx.drawImage(src, 0, 0, W, H);
        drawOverlay(ctx, W, H, state);
        const vf = new VideoFrame(canvas, { timestamp: Math.round(frameNo * 1e6 / FPS), duration: Math.round(1e6 / FPS) });
        enc.encode(vf, { keyFrame: frameNo % (FPS * 2) === 0 });
        vf.close();
        frameNo++;
        clock += 1000 / FPS;
        // let the page breathe: tile responses, the encoder, the Cancel button
        if (frameNo % 3 === 0 || enc.encodeQueueSize > 4) {
          ui.status(`Rendering the video… ${Math.min(99, Math.round(100 * frameNo / totalFrames))}%`);
          await sleep(0);
          while (enc.encodeQueueSize > 4) await sleep(5);
        }
      };
      const hold = async ms => { for (let n = Math.round(ms * FPS / 1000); n > 0; n--) await frame(FLIGHT_TILE_WAIT_MS); };
      // EVERY CAMERA MOVE IS A GLIDE: the zoom runs from one level to another
      // while `anchor` (a place) slides between two screen offsets from the
      // middle, each in pixels at the zoom of its end. So a place being
      // flown to drifts to the centre as it grows, and one being left
      // drifts off as it shrinks, with no sideways whip at close zooms.
      // MapLibre's flyTo is not used: it rises before it descends, which
      // from the opening view read as a small zoom out before the zoom in,
      // and it always starts and ends at rest, which read as a pause.
      //   o.ms       how long        o.push   the speed it starts at, as a
      //   o.onFrame  after each frame          share of its average speed
      //   o.landed   true to stop early        (0 starts from rest)
      const merc = ll => maplibregl.MercatorCoordinate.fromLngLat(ll);
      const glide = async (anchor, fromPx, toPx, zFrom, zTo, o = {}) => {
        const a = merc(anchor), n = Math.max(1, Math.round((o.ms || FLY_MS) * FPS / 1000)), push = o.push || 0;
        for (let j = 1; j <= n; j++) {
          const t = j / n;
          // smoothstep, plus a term that sets the starting speed and dies away
          const e = t * t * (3 - 2 * t) + push * t * (1 - t) ** 2;
          const z = zFrom + (zTo - zFrom) * e, world = 512 * 2 ** z;
          const x = a.x - (fromPx[0] + (toPx[0] - fromPx[0]) * e) / world;
          const y = a.y - (fromPx[1] + (toPx[1] - fromPx[1]) * e) / world;
          map.jumpTo({ center: new maplibregl.MercatorCoordinate(x, y, 0).toLngLat(), zoom: z, bearing: 0, pitch: 0 });
          await frame(FLIGHT_TILE_WAIT_MS);
          if (o.onFrame) o.onFrame();
          if (o.landed && o.landed()) break;
        }
      };
      // where `anchor` sits from the middle of a view centred on `centre` at `zoom`, in pixels
      const offsetIn = (anchor, centre, zoom) => {
        const a = merc(anchor), c = merc(centre), world = 512 * 2 ** zoom;
        return [(a.x - c.x) * world, (a.y - c.y) * world];
      };
      const offsetAtStart = anchor => offsetIn(anchor, start.center, start.zoom);
      // The height a hop between two areas rises to: both on screen at once
      // (the gap between them 0.7 of the shorter side), never closer than
      // HOP_PEAK_MAX_ZOOM.
      const HOP_PEAK_MAX_ZOOM = 14.5;
      const peakZoom = (a, b) => {
        const A = merc(a), B = merc(b), d = Math.hypot(A.x - B.x, A.y - B.y) || 1e-9;
        const side = Math.min(map.getContainer().clientWidth, map.getContainer().clientHeight);
        return Math.min(HOP_PEAK_MAX_ZOOM, Math.log2(0.7 * side / (512 * d)));
      };

      // THE SECTION COUNTERS. Each building group in the video has a line,
      // top left, that appears as the camera first zooms to one of its
      // areas and counts up while that area is on screen: through the
      // panels being added and the flight back out, reaching its share
      // when the camera is high again. A group visited k times climbs a
      // k-th of its total each visit, so its last visit lands on the total
      // for the starting view.
      // a type no group lists counts with the last group, as on the dashboard
      const G = opt.groups || h.groups;
      const groupOf = t => { const i = G.findIndex(g => g[1].includes(t)); return i < 0 ? G.length - 1 : i; };
      const groups = G.map(([label, types]) => {
        const kw = cells.reduce((a, c) => a + (G[groupOf(c.btype)][1] === types ? c.panels : 0), 0) * h.panelKw;
        return { label, total: kw, mw: kw >= 1000, visits: 0, seen: 0, line: null };
      });
      stops.forEach(c => { c.group = groups[groupOf(c.btype)]; c.group.visits++; });
      const climbs = [];
      const fades = [];
      const tick = () => {
        for (const c of climbs) {
          c.k = Math.min(c.n, c.k + 1);
          c.line.kw = c.from + (c.to - c.from) * ease(c.k / c.n);
        }
        for (const l of fades) l.alpha = Math.min(1, l.alpha + 1 / LINE_FADE_FRAMES);
      };
      const showLine = g => {
        if (g.line) return;
        g.line = { label: g.label, kw: 0, alpha: 0, mw: g.mw };
        state.lines.push(g.line);
        fades.push(g.line);
      };
      const climb = (g, frames) => {
        const old = climbs.findIndex(c => c.line === g.line);
        if (old >= 0) { g.line.kw = climbs[old].to; climbs.splice(old, 1); }
        g.seen++;
        climbs.push({ line: g.line, from: g.line.kw, to: g.total * g.seen / g.visits, k: 0, n: frames });
      };
      const frames = ms => Math.round(ms * FPS / 1000);

      // THE TITLE SCREEN: the map dimmed under the title, then a quick fade.
      await hold(INTRO_MS);
      for (let n = frames(INTRO_FADE_MS), j = 1; j <= n; j++) { state.card = 1 - ease(j / n); await frame(FLIGHT_TILE_WAIT_MS); }
      state.card = 0;

      // AN AREA: land close, drift out slowly while the panels go on, and
      // leave while they are still going on. The drift runs at a steady
      // speed and the move away starts at that speed, so there is no pause
      // between "this roof is filling" and "off to the next"; the panels
      // keep filling until the camera is too high to see them.
      const revealN = frames(REVEAL_MS), stayN = Math.round(revealN * STAY_SHARE);
      let shown = 0, revealJ = 0, hidden = true;
      const revealStep = () => {
        if (hidden || revealJ >= revealN) return;
        revealJ++;
        const pct = Math.max(1, Math.round(100 * (1 - (1 - revealJ / revealN) ** 2)));
        if (pct !== shown) { shown = pct; h.reveal(pct); }
      };
      // the drift: eased in over its first fifth, then steady
      const drift = p => (p < 0.2 ? p * p / 0.4 : p - 0.1) / 0.9;
      for (let i = 0; i < stops.length; i++) {
        const g = stops[i].group, last = i === stops.length - 1;
        const here = [stops[i].lng, stops[i].lat];
        const arriving = {
          // the section's title comes up on the way down
          onFrame: () => { if (map.getZoom() > 16) showLine(g); },
          landed: () => map.getZoom() > stops[i].close - LAND_WITHIN_ZOOM,
        };
        if (i === 0) {
          await glide(here, offsetAtStart(here), [0, 0], start.zoom, stops[i].close, arriving);
        } else {
          // the second half of the hop: down from the peak, onto this area
          const prev = [stops[i - 1].lng, stops[i - 1].lat], zP = peakZoom(prev, here);
          const mid = [(prev[0] + here[0]) / 2, (prev[1] + here[1]) / 2];
          await glide(here, offsetIn(here, mid, zP), [0, 0], zP, stops[i].close, { ...arriving, ms: HOP_DOWN_MS });
        }
        showLine(g);
        // THE PANELS START AS THE ZOOM-IN LANDS: the first on the very next
        // frame (the reveal starts fast and eases off).
        h.reveal(0);
        shown = 0; revealJ = 0; hidden = false;
        const z0 = map.getZoom();
        const upN = frames(last ? FLY_MS : HOP_UP_MS);
        // the counter climbs until the camera is high again
        climb(g, stayN + upN);
        // A change to which panels show re-tiles the panel layer in the
        // background (the opacity is a per-panel expression); every frame
        // waits for its tiles, so it is drawn with the change in place.
        for (let j = 1; j <= stayN; j++) {
          revealStep();
          map.jumpTo({ zoom: z0 + (stops[i].wide - z0) * drift(j / stayN) });
          await frame(REVEAL_TILE_WAIT_MS);
        }
        // LEAVING, at the drift's speed: `push` is that speed as a share of
        // the coming move's average speed.
        const zNow = map.getZoom(), driftSpeed = (stops[i].wide - z0) / (0.9 * stayN);
        const leaving = zTo => ({
          ms: last ? FLY_MS : HOP_UP_MS,
          push: Math.max(0, Math.min(1.5, driftSpeed * upN / (zTo - zNow))),
          onFrame: () => {
            if (hidden) return;
            if (map.getZoom() >= 15) return revealStep();
            // too high to see single panels: for the last area every roof's
            // panels come back as the map draws them; otherwise they clear
            // for the next area
            hidden = true;
            h.reveal(last ? null : 0);
          },
        });
        if (last) {
          if (zNow > start.zoom) await glide(here, [0, 0], offsetAtStart(here), zNow, start.zoom, leaving(start.zoom));
          if (!hidden) h.reveal(null);
          map.jumpTo(start);
        } else {
          const next = [stops[i + 1].lng, stops[i + 1].lat], zP = peakZoom(here, next);
          const mid = [(here[0] + next[0]) / 2, (here[1] + next[1]) / 2];
          await glide(here, [0, 0], offsetIn(here, mid, zP), zNow, zP, leaving(zP));
          if (!hidden) { hidden = true; h.reveal(0); }
        }
      }
      climbs.forEach(c => { c.line.kw = c.to; });
      climbs.length = 0;
      await frame(ARRIVE_TILE_WAIT_MS);

      // THE CLOSING SCREEN: the title again, over every group's total for
      // the starting view (the groups the tour did not visit included).
      state.cardLines = groups.filter(g => g.total > 0).map(g => ({ label: g.label, kw: g.total, mw: g.mw }));
      for (let n = frames(OUTRO_FADE_MS), j = 1; j <= n; j++) { state.card = ease(j / n); await frame(FLIGHT_TILE_WAIT_MS); }
      state.card = 1;
      await hold(OUTRO_HOLD_MS);

      ui.status("Finishing the file…");
      await enc.flush();
      if (encError) throw encError;
      muxer.finalize();
      const blob = new Blob([muxer.target.buffer], { type: "video/mp4" });
      return { ok: true, blob, late,
               filename: `rooftop-solar-${opt.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}.mp4`, seconds: Math.round(frameNo / FPS), frames: frameNo, size: blob.size,
               width: W, height: H, total, stops: stops.length,
               at: stops.map(c => [+c.lng.toFixed(5), +c.lat.toFixed(5), c.btype]) };
    } finally {
      if (enc && enc.state !== "closed") enc.close();
      if (clockSet) maplibregl.restoreNow();
      ui.recording(false);
      map.stop();
      h.end();
      map.jumpTo(start);
    }
  }

  // The dialog: which buildings, how many stops, Start. While it records, the
  // recorded frame fills the window, so what is on screen is what is saved.
  let box = null;
  function open(h) {
    if (box) { box.hidden = false; return; }
    box = document.createElement("div");
    box.id = "video-export";
    box.innerHTML = `
      <style>
        #video-export { position: fixed; inset: 0; z-index: 40; display: flex; align-items: center; justify-content: center;
          background: rgba(0,0,0,0.55); font: 13px system-ui, -apple-system, "Segoe UI", sans-serif; }
        #video-export[hidden] { display: none; }
        #video-export .ve-card { width: min(360px, calc(100vw - 32px)); background: #1b2029; color: #eee;
          border: 1px solid rgba(255,255,255,0.18); border-radius: 10px; padding: 16px; box-shadow: 0 12px 40px rgba(0,0,0,0.6); }
        #video-export h3 { margin: 0 0 6px; font-size: 15px; }
        #video-export .ve-how { margin: 0 0 10px; color: rgba(255,255,255,0.72); line-height: 1.4; }
        #video-export .ve-how summary { cursor: pointer; color: rgba(255,255,255,0.85); }
        #video-export .ve-how ul { margin: 6px 0 0; padding-left: 18px; }
        #video-export .ve-how li { margin: 3px 0; }
        #video-export label { display: flex; justify-content: space-between; align-items: center; margin: 8px 0; }
        #video-export select { font: inherit; padding: 4px 6px; border-radius: 6px; background: #10141a; color: #eee;
          border: 1px solid rgba(255,255,255,0.25); }
        #video-export .ve-row { display: flex; gap: 8px; justify-content: flex-end; margin-top: 14px; }
        #video-export button { font: inherit; padding: 6px 14px; border-radius: 6px; cursor: pointer;
          border: 1px solid rgba(255,255,255,0.25); background: none; color: #eee; }
        #video-export button.go { background: linear-gradient(100deg, #ffe600, #ff8c00); color: #16181d; border: none; font-weight: 700; }
        #video-export .ve-status { min-height: 1.4em; margin-top: 10px; color: #ffd866; }
        #video-export a.ve-download { display: block; margin-top: 10px; padding: 9px 14px; border-radius: 6px; text-align: center;
          text-decoration: none; font-weight: 700; color: #16181d; background: linear-gradient(100deg, #ffe600, #ff8c00); }
        #video-export a.ve-download[hidden], #video-export.busy a.ve-download { display: none; }
        #video-export.busy .ve-form { display: none; }
        #video-export canvas { display: none; position: fixed; inset: 0; width: 100vw; height: 100vh; object-fit: contain; background: #000; }
        #video-export.rec canvas { display: block; }
        #video-export.rec .ve-card { position: fixed; top: 12px; right: 12px; width: auto; padding: 8px 12px; display: flex; gap: 12px; align-items: center; }
        #video-export.rec h3, #video-export.rec .ve-how { display: none; }
        #video-export.rec .ve-status { margin: 0; }
        #video-export.rec .ve-row { margin: 0; }
      </style>
      <canvas></canvas>
      <div class="ve-card">
        <h3>Export a video</h3>
        <details class="ve-how">
          <summary>How this works</summary>
          <ul>
            <li>We look at everything in your current map view</li>
            <li>The building types included in the video are selected by you</li>
            <li>A video is created showing the solar potential of those types of buildings in the area you are viewing.</li>
          </ul>
        </details>
        <div class="ve-form">
          <label>Buildings <select class="ve-type"></select></label>
          <label>Areas to visit <select class="ve-stops"><option>2</option><option selected>3</option><option>4</option><option>5</option></select></label>
        </div>
        <div class="ve-status"></div>
        <a class="ve-download" hidden>&#11015; Download video</a>
        <div class="ve-row"><button class="ve-cancel">Close</button><button class="go ve-form ve-start">Record</button></div>
      </div>`;
    document.body.append(box);
    const q = s => box.querySelector(s);
    const main = [...h.groups, ["All buildings", h.groups.flatMap(g => g[1])]];
    const detail = h.detail || [];
    const groups = [...main, ...detail];
    const option = ([label], i) => `<option value="${i}">${label}</option>`;
    q(".ve-type").innerHTML = main.map(option).join("") + (detail.length
      ? `<optgroup label="One building type">${detail.map((g, i) => option(g, main.length + i)).join("")}</optgroup>` : "");
    const ui = {
      canvas: q("canvas"), onCancel: null,
      status: t => { q(".ve-status").textContent = t; },
      recording: on => box.classList.toggle("rec", on),
    };
    let busy = false;
    // A finished video belongs to the view it was recorded from: closing the
    // dialog (the only way to move the map) drops it and its message, so the
    // dialog never offers a download for somewhere else.
    const forget = () => {
      const dl = q(".ve-download");
      if (dl.href) URL.revokeObjectURL(dl.href);
      dl.removeAttribute("href");
      dl.hidden = true;
      ui.status("");
    };
    q(".ve-cancel").addEventListener("click", () => {
      if (busy && ui.onCancel) ui.onCancel(); else { forget(); box.hidden = true; }
    });
    q(".ve-start").addEventListener("click", async () => {
      if (typeof VideoEncoder === "undefined" || !maplibregl.setNow) {
        ui.status("This browser cannot record video. Try a current Chrome, Edge or Safari.");
        return;
      }
      const [label, types] = groups[+q(".ve-type").value];
      const dl = q(".ve-download");
      forget();
      busy = true;
      box.classList.add("busy");
      q(".ve-cancel").textContent = "Cancel";
      try {
        // every building: the stops take the first two groups in turn
        // (homes, then businesses)
        const mix = label === "All buildings" ? h.groups.slice(0, 2).map(g => g[1]) : null;
        const one = +q(".ve-type").value >= main.length ? [[label, types]] : null;
        const r = await run(h, { types, mix, groups: one, stops: +q(".ve-stops").value,
                                 title: label === "All buildings" ? "Rooftop solar potential" : `${label} rooftop solar potential` }, ui);
        dl.href = URL.createObjectURL(r.blob);
        dl.download = r.filename;
        dl.hidden = false;
        ui.status(`Video ready: ${r.seconds} s, ${r.width}×${r.height}, ${(r.size / 1e6).toFixed(1)} MB MP4`);
        window.__lastVideoExport = r;
      } catch (e) {
        ui.status(e instanceof Cancelled ? "Cancelled." : e.message || String(e));
        if (!(e instanceof Cancelled)) console.error(e);
      } finally {
        busy = false;
        box.classList.remove("busy");
        q(".ve-cancel").textContent = "Close";
      }
    });
  }

  return { open, pickStops, pickMixed, kmBetween };
})();
if (typeof module !== "undefined") module.exports = VideoExport;
