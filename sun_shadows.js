// SUN SIMULATION: real cast shadows on the map for a chosen date and time.
//
// Nothing here is precomputed. The 3D mode's height tiles are the LINZ 1 m
// DSM -- ground, trees AND roofs -- so a shadow is just geometry: a pixel is
// in shade when something between it and the sun rises above the line to the
// sun. Each map tile is computed on request by a custom protocol
// ("sunshade://z/x/y?az=..&alt=..") in a worker and handed back as a PNG.
//
// HOW ONE TILE IS COMPUTED (a sweep, not a ray per pixel)
// For one sun direction the shadow height field S obeys
//     S(p) = max( H(p), S(p + one step toward the sun) - drop )
// where drop = step length (m) * tan(sun altitude). Walking the grid from the
// sun's side, each pixel reads ONE interpolated value from the row before it,
// so a 768 x 768 block (the tile and its 8 neighbours) costs ~0.6 M simple
// steps however long the shadows are. A pixel is shaded by how far the
// incoming shadow height sits above its own surface.
//
// FAR AWAY HILLS. A z17 block is only ~650 m across, and in Queenstown in
// winter the shade that matters most comes from hills kilometres away. So the
// same sweep is first run on coarser tiles (z-3, z-6, ... down to z8, each 8x
// wider), and each finer sweep takes its incoming shadow heights at the block
// edge from the coarser one. Coarse blocks are shared by every fine tile in
// view and cached per sun position.
//
// Where the survey has no tile (404) nothing casts and nothing is drawn, so a
// hill outside the surveyed area casts no shadow -- said in the panel note.
//
// Wiring in preview.html is three lines: this script tag, the
// #toggle-sun button in #corner-actions, and SunShadows.init(map, ...) in
// map.on("load"). Everything else lives here, to keep that file's diff small.
(function () {
  "use strict";

  // ------------------------------------------------------------------
  // Solar position. The standard low-precision formulas (as in SunCalc,
  // BSD-2, Vladimir Agafonkin), ~0.01 deg -- far finer than the 10-minute
  // slider step needs.
  // ------------------------------------------------------------------
  const RAD = Math.PI / 180, DAY_MS = 864e5, J1970 = 2440588, J2000 = 2451545;
  const OBLIQ = RAD * 23.4397;
  const toDays = ms => ms / DAY_MS - 0.5 + J1970 - J2000;
  const fromJulian = j => (j + 0.5 - J1970) * DAY_MS;
  const meanAnomaly = d => RAD * (357.5291 + 0.98560028 * d);
  function eclipticLongitude(M) {
    const C = RAD * (1.9148 * Math.sin(M) + 0.02 * Math.sin(2 * M) + 0.0003 * Math.sin(3 * M));
    return M + C + RAD * 102.9372 + Math.PI;
  }
  const declination = L => Math.asin(Math.sin(OBLIQ) * Math.sin(L));
  const rightAscension = L => Math.atan2(Math.sin(L) * Math.cos(OBLIQ), Math.cos(L));

  // azimuth: compass degrees (0 = north, 90 = east); altitude: degrees
  function sunPosition(ms, lat, lng) {
    const lw = -RAD * lng, phi = RAD * lat, d = toDays(ms);
    const L = eclipticLongitude(meanAnomaly(d));
    const dec = declination(L), ra = rightAscension(L);
    const H = RAD * (280.16 + 360.9856235 * d) - lw - ra;
    const az = Math.atan2(Math.sin(H), Math.cos(H) * Math.sin(phi) - Math.tan(dec) * Math.cos(phi));
    const alt = Math.asin(Math.sin(phi) * Math.sin(dec) + Math.cos(phi) * Math.cos(dec) * Math.cos(H));
    return { azimuth: ((az / RAD + 180) % 360 + 360) % 360, altitude: alt / RAD };
  }

  // sunrise / sunset (upper limb on the horizon, with refraction) around ms
  function sunTimes(ms, lat, lng) {
    const J0 = 0.0009, lw = -RAD * lng, phi = RAD * lat, d = toDays(ms);
    const n = Math.round(d - J0 - lw / (2 * Math.PI));
    const ds = J0 + lw / (2 * Math.PI) + n;
    const M = meanAnomaly(ds), L = eclipticLongitude(M), dec = declination(L);
    const transit = (a) => J2000 + a + 0.0053 * Math.sin(M) - 0.0069 * Math.sin(2 * L);
    const jNoon = transit(ds);
    const cosW = (Math.sin(-0.833 * RAD) - Math.sin(phi) * Math.sin(dec)) / (Math.cos(phi) * Math.cos(dec));
    if (cosW < -1 || cosW > 1) return { noon: fromJulian(jNoon), rise: null, set: null };
    const w = Math.acos(cosW);
    const jSet = transit(J0 + (w + lw) / (2 * Math.PI) + n);
    return { noon: fromJulian(jNoon), rise: fromJulian(jNoon - (jSet - jNoon)), set: fromJulian(jSet) };
  }

  // ------------------------------------------------------------------
  // New Zealand local time (Pacific/Auckland, daylight saving included).
  // ------------------------------------------------------------------
  const NZ_FMT = new Intl.DateTimeFormat("en-NZ", {
    timeZone: "Pacific/Auckland", hourCycle: "h23", year: "numeric", month: "2-digit",
    day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
  const pad2 = v => String(v).padStart(2, "0");
  function nzParts(ms) {
    const p = {};
    for (const x of NZ_FMT.formatToParts(new Date(ms))) p[x.type] = x.value;
    return { y: +p.year, mo: +p.month, d: +p.day, h: (+p.hour) % 24, mi: +p.minute };
  }
  function nzOffsetMin(ms) {
    const p = nzParts(ms);
    return Math.round((Date.UTC(p.y, p.mo - 1, p.d, p.h, p.mi) - ms) / 60000);
  }
  function nzToUtc(dateStr, minutes) {
    const [y, mo, d] = dateStr.split("-").map(Number);
    const naive = Date.UTC(y, mo - 1, d, 0, minutes);
    let utc = naive - nzOffsetMin(naive - 12 * 3600e3) * 60000;
    return naive - nzOffsetMin(utc) * 60000;
  }
  const nzMinutes = ms => { const p = nzParts(ms); return p.h * 60 + p.mi; };
  const nzToday = () => { const p = nzParts(Date.now()); return `${p.y}-${pad2(p.mo)}-${pad2(p.d)}`; };
  function clockLabel(minutes) {
    const h = Math.floor(minutes / 60) % 24, m = minutes % 60;
    return `${(h % 12) || 12}:${pad2(m)} ${h < 12 ? "am" : "pm"}`;
  }
  const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
  const compassName = az => COMPASS[Math.round(az / 22.5) % 16];

  // ------------------------------------------------------------------
  // The shadow engine. Self-contained so it can be stringified into a
  // worker; it runs on the main thread only if workers can't draw.
  // ------------------------------------------------------------------
  function SunCore(scope) {
    const T = 256, W = 3 * T, NODATA = -1e9;
    // "nothing casts from here". Finite on purpose: -Infinity minus
    // -Infinity in an interpolation is NaN, and NaN blanked whole tiles.
    const NONE = -1e6;
    const EARTH = 40075016.686;
    let base = "", ver = "";

    const makeCanvas = (w, h) => {
      if (typeof OffscreenCanvas !== "undefined") return new OffscreenCanvas(w, h);
      const c = document.createElement("canvas"); c.width = w; c.height = h; return c;
    };
    const encode = c => (c.convertToBlob ? c.convertToBlob({ type: "image/png" })
      : new Promise(r => c.toBlob(r, "image/png"))).then(b => b.arrayBuffer());

    // decoded height tiles, LRU (a Float32Array tile is 256 KB)
    const dsm = new Map(), DSM_MAX = 160;
    function getDSM(z, x, y) {
      const n = 1 << z;
      if (y < 0 || y >= n) return Promise.resolve(null);
      x = ((x % n) + n) % n;
      const k = z + "/" + x + "/" + y;
      let p = dsm.get(k);
      if (p) { dsm.delete(k); dsm.set(k, p); return p; }
      p = fetch(`${base}data/terrain/${z}/${x}/${y}.png?v=${ver}`)
        .then(r => {
          if (r.status === 404 || r.status === 403) return null;   // outside the survey
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.blob();
        })
        .then(b => b && createImageBitmap(b, { colorSpaceConversion: "none", premultiplyAlpha: "none" }))
        .then(bmp => {
          if (!bmp) return null;
          const c = makeCanvas(T, T), ctx = c.getContext("2d", { willReadFrequently: true });
          ctx.drawImage(bmp, 0, 0);
          if (bmp.close) bmp.close();
          const d = ctx.getImageData(0, 0, T, T).data, h = new Float32Array(T * T);
          for (let i = 0, j = 0; i < h.length; i++, j += 4) h[i] = -10000 + (d[j] * 65536 + d[j + 1] * 256 + d[j + 2]) * 0.1;
          return h;
        })
        .catch(e => { dsm.delete(k); console.warn("sun shadows: height tile " + k + " failed", e); return null; });   // a network blip is retried next time
      dsm.set(k, p);
      while (dsm.size > DSM_MAX) dsm.delete(dsm.keys().next().value);
      return p;
    }

    // the 3x3 block of height tiles centred on (z, cx, cy)
    async function getBlock(z, cx, cy) {
      const tiles = [];
      for (let j = -1; j <= 1; j++) for (let i = -1; i <= 1; i++) tiles.push(getDSM(z, cx + i, cy + j));
      const got = await Promise.all(tiles);
      if (!got[4] && got.every(t => !t)) return null;
      const H = new Float32Array(W * W).fill(NODATA);
      got.forEach((t, k) => {
        if (!t) return;
        const ox = (k % 3) * T, oy = Math.floor(k / 3) * T;
        for (let r = 0; r < T; r++) H.set(t.subarray(r * T, r * T + T), (oy + r) * W + ox);
      });
      return H;
    }

    // One sweep over a block. ox, oy: the block's top-left in global pixels
    // at its zoom. edge(gx, gy): incoming shadow height for a point outside
    // the block (from the coarser level), or NONE.
    function sweep(H, ox, oy, mpp, az, tanAlt, edge, wantExcess) {
      const S = new Float32Array(W * W).fill(NONE);
      const E = wantExcess ? new Float32Array(T * T) : null;
      // direction toward the sun in pixel space (x east, y south)
      const dx = Math.sin(az * Math.PI / 180), dy = -Math.cos(az * Math.PI / 180);
      const majorX = Math.abs(dx) >= Math.abs(dy);
      const dA = majorX ? Math.sign(dx) || 1 : Math.sign(dy) || 1;     // step along the major axis
      const t = majorX ? dy / Math.abs(dx) : dx / Math.abs(dy);        // minor offset per step
      const drop = Math.sqrt(1 + t * t) * mpp * tanAlt;
      const idx = majorX ? (a, b) => b * W + a : (a, b) => a * W + b;
      // Lines downstream of the centre tile cannot shade it, so the sweep
      // stops there (a margin is kept on coarse levels, which finer blocks
      // sample just outside their centre tile).
      const margin = wantExcess ? 0 : 40;
      const a0 = dA > 0 ? W - 1 : 0, a1 = dA > 0 ? T - 1 - margin : 2 * T + margin;
      for (let a = a0; a !== a1; a -= dA) {
        const ap = a + dA;                     // the line one step toward the sun
        const inside = ap >= 0 && ap < W;
        for (let b = 0; b < W; b++) {
          const bp = b + t;
          let sIn;
          if (inside && bp >= 0 && bp <= W - 1) {
            const b0 = Math.floor(bp), f = bp - b0;
            const s0 = S[idx(ap, b0)];
            sIn = f > 0 ? s0 + (S[idx(ap, b0 + 1)] - s0) * f : s0;
          } else {
            sIn = majorX ? edge(ox + ap + 0.5, oy + bp + 0.5) : edge(ox + bp + 0.5, oy + ap + 0.5);
          }
          sIn = sIn > NONE ? sIn - drop : NONE;
          const k = idx(a, b), h = H[k];
          if (h === NODATA) { S[k] = sIn; continue; }
          S[k] = h > sIn ? h : sIn;
          if (E) {
            const x = majorX ? a : b, y = majorX ? b : a;
            if (x >= T && x < 2 * T && y >= T && y < 2 * T) E[(y - T) * T + (x - T)] = sIn - h;
          }
        }
      }
      return { S, E };
    }

    const mppAt = (z, ty) => {
      const n = Math.PI - 2 * Math.PI * (ty + 0.5) / (1 << z);
      const lat = Math.atan(Math.sinh(n));
      return EARTH * Math.cos(lat) / (T * (1 << z));
    };

    // bilinear sample of a level's S at global pixel coords of a finer zoom
    function sampler(level, zFine) {
      if (!level) return () => NONE;
      const f = 1 << (zFine - level.z), S = level.S;
      return (gx, gy) => {
        const u = gx / f - 0.5 - level.ox, v = gy / f - 0.5 - level.oy;
        if (!(u >= 0 && v >= 0 && u <= W - 1 && v <= W - 1)) return NONE;
        const i = Math.floor(u), j = Math.floor(v), fu = u - i, fv = v - j;
        const i1 = Math.min(i + 1, W - 1), j1 = Math.min(j + 1, W - 1);
        const a = S[j * W + i], b = S[j * W + i1], c = S[j1 * W + i], d = S[j1 * W + i1];
        return (a + (b - a) * fu) * (1 - fv) + (c + (d - c) * fu) * fv;
      };
    }

    // coarse levels, shared by every fine tile in view for one sun position
    const levels = new Map(), LEVEL_MAX = 10;
    const yieldNow = () => new Promise(r => setTimeout(r, 0));
    const keyOf = (az, alt) => az.toFixed(3) + "," + alt.toFixed(3);
    let alive = () => true;
    const parentZ = z => (z > 8 ? Math.max(8, z - 3) : null);
    function getLevel(z, cx, cy, az, tanAlt, sunKey) {
      const k = `${z}/${cx}/${cy}|${sunKey}`;
      let p = levels.get(k);
      if (p) { levels.delete(k); levels.set(k, p); return p; }
      p = (async () => {
        const pz = parentZ(z);
        const [H, parent] = await Promise.all([
          getBlock(z, cx, cy),
          pz === null ? null : getLevel(pz, cx >> (z - pz), cy >> (z - pz), az, tanAlt, sunKey),
        ]);
        if (!H) return parent ? { z: parent.z, ox: parent.ox, oy: parent.oy, S: parent.S } : null;
        // Dragging the slider queues a level for every sun position passed
        // through. Let the cancellations in first, and skip the sweep if no
        // tile still wants this sun position.
        await yieldNow();
        if (!alive(sunKey)) throw new Error("cancelled");
        const ox = (cx - 1) * T, oy = (cy - 1) * T;
        const { S } = sweep(H, ox, oy, mppAt(z, cy), az, tanAlt, sampler(parent, z), false);
        return { z, ox, oy, S };
      })();
      levels.set(k, p);
      p.catch(() => { if (levels.get(k) === p) levels.delete(k); });
      while (levels.size > LEVEL_MAX) levels.delete(levels.keys().next().value);
      return p;
    }

    let blankPng = null;
    const blank = () => blankPng || (blankPng = (() => { const c = makeCanvas(T, T); c.getContext("2d"); return encode(c); })());

    async function render(job) {
      base = job.base; ver = job.ver;
      const t0 = Date.now();
      const { z, x, y } = job;
      const c = makeCanvas(T, T), ctx = c.getContext("2d");
      if (job.down) {
        // sun below the horizon: the whole view dimmed, no shadow geometry
        const img = ctx.createImageData(T, T);
        for (let i = 3; i < img.data.length; i += 4) img.data[i] = 200;
        ctx.putImageData(img, 0, 0);
        return { buf: await encode(c), ms: Date.now() - t0 };
      }
      const tanAlt = Math.tan(job.alt * Math.PI / 180);
      const sunKey = keyOf(job.az, job.alt);
      const pz = parentZ(z);
      const [H, parent] = await Promise.all([
        getBlock(z, x, y),
        pz === null ? null : getLevel(pz, x >> (z - pz), y >> (z - pz), job.az, tanAlt, sunKey),
      ]);
      if (H) await yieldNow();
      if (job.cancelled && job.cancelled()) return null;
      if (!H) return { buf: (await blank()).slice(0), ms: Date.now() - t0, blank: true };
      const mpp = mppAt(z, y);
      const { E } = sweep(H, (x - 1) * T, (y - 1) * T, mpp, job.az, tanAlt, sampler(parent, z), true);
      // soft edge: full shade once the shadow is a little above the surface
      const ramp = Math.min(4, Math.max(0.35, 0.5 * mpp));
      const img = ctx.createImageData(T, T), d = img.data;
      for (let r = 0; r < T; r++) {
        for (let col = 0; col < T; col++) {
          const i = r * T + col;
          if (H[(r + T) * W + col + T] === NODATA) continue;
          const e = E[i];
          if (e <= 0) continue;
          const a = e >= ramp ? 1 : e / ramp;
          d[i * 4] = 6; d[i * 4 + 1] = 10; d[i * 4 + 2] = 28; d[i * 4 + 3] = Math.round(255 * a);
        }
      }
      ctx.putImageData(img, 0, 0);
      return { buf: await encode(c), ms: Date.now() - t0 };
    }

    const api = { render };
    if (scope && typeof document === "undefined") {
      // live jobs, and how many of them want each sun position
      const live = new Map(), perKey = new Map();
      const drop = (id) => {
        const j = live.get(id);
        if (!j) return;
        live.delete(id);
        perKey.set(j.key, perKey.get(j.key) - 1);
        if (!perKey.get(j.key)) perKey.delete(j.key);
      };
      alive = key => perKey.has(key);
      scope.onmessage = async (ev) => {
        const m = ev.data;
        if (m.cancel != null) { drop(m.cancel); return; }
        const key = m.down ? "down" : keyOf(m.az, m.alt);
        live.set(m.id, { key });
        perKey.set(key, (perKey.get(key) || 0) + 1);
        m.cancelled = () => !live.has(m.id);
        try {
          const out = await render(m);
          if (!out || !live.has(m.id)) { scope.postMessage({ id: m.id, cancelled: true }); return; }
          scope.postMessage({ id: m.id, buf: out.buf, ms: out.ms }, [out.buf]);
        } catch (e) {
          scope.postMessage({ id: m.id, error: String(e && e.message || e) });
        } finally {
          drop(m.id);
        }
      };
    }
    return api;
  }

  // ------------------------------------------------------------------
  // Worker pool + the MapLibre protocol.
  // ------------------------------------------------------------------
  const stats = { tiles: 0, totalMs: 0, maxMs: 0, last: [] };
  let opts = { dataBase: "", dataVersion: "1" };
  let workers = null, rr = 0, jobSeq = 0, mainCore = null;
  const pending = new Map();

  function startWorkers() {
    if (workers !== null) return;
    workers = [];
    if (typeof Worker === "undefined" || typeof OffscreenCanvas === "undefined") return;
    try {
      const url = URL.createObjectURL(new Blob([`(${SunCore.toString()})(self);`], { type: "text/javascript" }));
      // two workers: tiles alternate, so a screenful computes in parallel
      const n = Math.min(2, Math.max(1, (navigator.hardwareConcurrency || 2) - 1));
      for (let i = 0; i < n; i++) {
        const w = new Worker(url);
        w.onmessage = (ev) => {
          const m = ev.data, p = pending.get(m.id);
          if (!p) return;
          pending.delete(m.id);
          if (m.buf) { record(m.ms); p.resolve({ data: m.buf }); }
          else p.reject(new Error(m.error || "cancelled"));
        };
        workers.push(w);
      }
    } catch (e) {
      workers = [];
      console.warn("sun shadows: no worker, computing on the main thread", e);
    }
  }
  function record(ms) {
    stats.tiles++; stats.totalMs += ms; stats.maxMs = Math.max(stats.maxMs, ms);
    stats.last.push(ms); if (stats.last.length > 50) stats.last.shift();
  }

  function protocolHandler(params, abortController) {
    const m = /^sunshade:\/\/(\d+)\/(\d+)\/(\d+)\?(.*)$/.exec(params.url);
    if (!m) return Promise.reject(new Error("bad sunshade url"));
    const q = new URLSearchParams(m[4]);
    const job = {
      z: +m[1], x: +m[2], y: +m[3], az: +q.get("az"), alt: +q.get("alt"), down: q.get("down") === "1",
      base: new URL(opts.dataBase || "", location.href).href, ver: opts.dataVersion,   // a blob worker cannot resolve a relative URL
    };
    startWorkers();
    if (!workers.length) {
      mainCore = mainCore || SunCore(null);
      job.cancelled = () => abortController.signal.aborted;
      return mainCore.render(job).then(out => {
        if (!out) throw new Error("cancelled");
        record(out.ms); return { data: out.buf };
      });
    }
    const id = ++jobSeq;
    // a tile and its neighbours go to the same worker more often than not,
    // which keeps that worker's decoded-tile cache warm
    const w = workers[((job.x >> 1) + (job.y >> 1)) % workers.length];
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      abortController.signal.addEventListener("abort", () => {
        if (pending.has(id)) { w.postMessage({ cancel: id }); }
      });
      w.postMessage(Object.assign({ id }, job));
    });
  }

  // ------------------------------------------------------------------
  // Two sources, double-buffered: a new time loads into the hidden one and
  // swaps in when it is complete, so dragging the slider never blanks the
  // shadows (setTiles on a single source clears it first).
  // ------------------------------------------------------------------
  const SHADE_OPACITY = 0.55, MIN_Z = 11, MAX_Z = 17;
  // A swap cross-fades over FADE_MS instead of flipping in one frame: during
  // a drag the frames came 70 ms apart and every flip read as a flicker
  // (Josh, 21 Oct: "a bit glitchy when dragging").
  const FADE_MS = 140;
  const hideTimers = {};
  const IDS = ["sun-shade-a", "sun-shade-b"];
  let gen = 0, nextUrl = null;
  let map = null, front = 0, backUrl = null, frontUrl = null, swapTimer = null, swapWait = null;
  let layersAdded = false;

  function beforeId() {
    // above imagery and the heat map, under buildings, panels and outlines
    for (const id of ["buildings-fill", "layout-probe"]) if (map.getLayer(id)) return id;
    return undefined;
  }
  function addLayers(url) {
    for (const id of IDS) {
      if (!map.getSource(id)) {
        map.addSource(id, { type: "raster", tiles: [url], tileSize: 256, minzoom: MIN_Z, maxzoom: MAX_Z });
      }
      if (!map.getLayer(id)) {
        map.addLayer({
          id, type: "raster", source: id, minzoom: MIN_Z,
          layout: { visibility: id === IDS[front] ? "visible" : "none" },
          paint: { "raster-opacity": id === IDS[front] ? SHADE_OPACITY : 0, "raster-fade-duration": 0,
                   "raster-opacity-transition": { duration: FADE_MS, delay: 0 },
                   "raster-resampling": "linear" },
        }, beforeId());
      }
    }
    layersAdded = true;
    frontUrl = url;
  }
  function removeLayers() {
    gen++;
    clearSwap();
    if (typeof shadeTimer !== "undefined" && shadeTimer) { clearTimeout(shadeTimer); shadeTimer = null; }
    for (const k of Object.keys(hideTimers)) { if (hideTimers[k]) clearTimeout(hideTimers[k]); hideTimers[k] = null; }
    for (const id of IDS) { if (map.getLayer(id)) map.removeLayer(id); if (map.getSource(id)) map.removeSource(id); }
    layersAdded = false; frontUrl = null; backUrl = null; nextUrl = null;
  }
  function clearSwap() {
    if (swapTimer) { clearTimeout(swapTimer); swapTimer = null; }
    if (swapWait) { map.off("sourcedata", swapWait); map.off("idle", swapWait); swapWait = null; }
  }
  function showUrl(url) {
    if (!layersAdded) { addLayers(url); return; }
    if (url === frontUrl && !backUrl) { nextUrl = null; return; }
    // One time loading at once. While it loads, later requests just replace
    // nextUrl, and the newest one starts when it lands: a fast drag shows
    // every frame the machine can actually draw and none it can't. (Setting
    // new tiles on a loading source does not abort the old requests -- they
    // all ran, and a long drag took seconds to catch up.)
    if (backUrl) { nextUrl = url === backUrl ? null : url; return; }
    nextUrl = null;
    const backId = IDS[1 - front];
    backUrl = url;
    clearSwap();
    // a listener already queued in the current event dispatch can still
    // run after clearSwap; the generation stops it swapping twice
    const my = ++gen;
    if (hideTimers[backId]) { clearTimeout(hideTimers[backId]); hideTimers[backId] = null; }
    map.getSource(backId).setTiles([url]);
    map.setPaintProperty(backId, "raster-opacity", 0);
    map.setLayoutProperty(backId, "visibility", "visible");
    const swap = () => {
      if (my !== gen || backUrl !== url) return;
      gen++;
      clearSwap();
      const oldFront = IDS[front];
      front = 1 - front;
      map.setPaintProperty(IDS[front], "raster-opacity", SHADE_OPACITY);
      map.setPaintProperty(oldFront, "raster-opacity", 0);
      // hidden once it has faded out, unless it is reused before then
      hideTimers[oldFront] = setTimeout(() => {
        hideTimers[oldFront] = null;
        if (IDS[front] !== oldFront && map.getLayer(oldFront) && backUrl === null) {
          map.setLayoutProperty(oldFront, "visibility", "none");
        }
      }, FADE_MS + 40);
      frontUrl = backUrl; backUrl = null;
      map.triggerRepaint();
      if (nextUrl) { const u = nextUrl; nextUrl = null; showUrl(u); }
      if (!busy()) onSwapped();
    };
    // isSourceLoaded() is already true in the moment after setTiles, before
    // a single new tile has been asked for -- swapping then blanked the
    // shadows. So wait for a new tile to arrive first.
    let sawTile = map.getZoom() < MIN_Z;
    swapWait = (e) => {
      if (e && e.type === "sourcedata") {
        if (e.sourceId !== backId) return;
        if (e.tile) sawTile = true;
      }
      if (sawTile && map.isSourceLoaded(backId)) swap();
    };
    map.on("sourcedata", swapWait);
    map.on("idle", swapWait);
    swapTimer = setTimeout(swap, 5000);   // never stall on one slow tile
  }
  let swapListeners = [];
  function onSwapped() { const l = swapListeners; swapListeners = []; l.forEach(f => f()); }
  const busy = () => backUrl !== null || nextUrl !== null;

  // ------------------------------------------------------------------
  // State, controls.
  // ------------------------------------------------------------------
  const state = { on: false, date: nzToday(), minutes: 12 * 60, playing: false, lat: -41, lng: 174 };
  let ui = null, playTimer = null, lastSun = null;

  function dayInfo() {
    const noonUtc = nzToUtc(state.date, 12 * 60);
    const t = sunTimes(noonUtc, state.lat, state.lng);
    const rise = t.rise ? nzMinutes(t.rise) : 6 * 60, set = t.set ? nzMinutes(t.set) : 18 * 60;
    // one step either side of daylight, so the ends of the slider show the
    // sun down (view dimmed) rather than stopping just short of it
    return { rise, set, min: Math.floor(rise / 10) * 10 - 10, max: Math.ceil(set / 10) * 10 + 10 };
  }
  function currentSun() {
    return sunPosition(nzToUtc(state.date, state.minutes), state.lat, state.lng);
  }
  function tileUrl(sun) {
    if (sun.altitude <= 0.25) return "sunshade://{z}/{x}/{y}?down=1";
    // rounded so a tiny pan does not refetch every tile
    return `sunshade://{z}/{x}/{y}?az=${sun.azimuth.toFixed(1)}&alt=${sun.altitude.toFixed(2)}`;
  }

  function refresh() {
    if (!ui) return;
    const info = dayInfo();
    const s = ui.slider;
    s.min = info.min; s.max = info.max;
    if (state.minutes < info.min) state.minutes = info.min;
    if (state.minutes > info.max) state.minutes = info.max;
    s.value = state.minutes;
    s.style.setProperty("--fill", ((state.minutes - info.min) / Math.max(1, info.max - info.min) * 100).toFixed(1));
    ui.time.textContent = clockLabel(state.minutes);
    ui.date.value = state.date;
    const md = state.date.slice(5);
    ui.quick.forEach(b => b.classList.toggle("on", b.dataset.md === md));
    const sun = currentSun();
    lastSun = sun;
    ui.meta.innerHTML = sun.altitude > 0
      ? `Sun <b>${sun.altitude.toFixed(0)}&deg;</b> high, from the <b>${compassName(sun.azimuth)}</b> (${sun.azimuth.toFixed(0)}&deg;)`
        + ` &middot; up ${clockLabel(info.rise)}&ndash;${clockLabel(info.set)}`
      : `<b>Sun is down</b> &middot; up ${clockLabel(info.rise)}&ndash;${clockLabel(info.set)}`;
    ui.play.innerHTML = state.playing ? "&#10074;&#10074;" : "&#9654;";
    ui.play.setAttribute("aria-label", state.playing ? "Pause" : "Play through the day");
    if (state.on && map) shadeSoon(tileUrl(sun));
  }
  // While the slider is dragged the shadows follow at most every SHADE_EVERY_MS
  // (the labels follow every step); the last position always lands.
  const SHADE_EVERY_MS = 140;
  let lastShade = 0, shadeTimer = null, shadeUrl = null;
  function shadeSoon(url, now) {
    shadeUrl = url;
    const wait = now ? 0 : SHADE_EVERY_MS - (performance.now() - lastShade);
    if (wait <= 0) {
      if (shadeTimer) { clearTimeout(shadeTimer); shadeTimer = null; }
      lastShade = performance.now();
      showUrl(shadeUrl);
    } else if (!shadeTimer) {
      shadeTimer = setTimeout(() => { shadeTimer = null; lastShade = performance.now(); showUrl(shadeUrl); }, wait);
    }
  }

  function setPlaying(on) {
    state.playing = on;
    if (playTimer) { clearTimeout(playTimer); playTimer = null; }
    if (on) {
      const info = dayInfo();
      if (state.minutes >= info.max) state.minutes = info.min;
      const tick = () => {
        if (!state.playing) return;
        // advance only once the last frame is on screen, so a slow machine
        // plays slower instead of skipping frames it never drew
        if (busy()) { swapListeners.push(() => { playTimer = setTimeout(tick, 120); }); return; }
        const inf = dayInfo();
        state.minutes += 10;
        if (state.minutes > inf.max) state.minutes = inf.min;
        refresh();
        playTimer = setTimeout(tick, 250);
      };
      playTimer = setTimeout(tick, 150);
    }
    refresh();
  }

  function setOn(on) {
    state.on = on;
    const btn = document.getElementById("toggle-sun");
    if (btn) btn.classList.toggle("active", on);
    if (ui) ui.panel.style.display = on ? "" : "none";
    if (!on) { setPlaying(false); if (map) removeLayers(); return; }
    const c = map.getCenter();
    state.lat = c.lat; state.lng = c.lng;
    refresh();
  }

  function injectCss() {
    const css = `
  #sun-panel { position: absolute; left: 12px; bottom: 24px; z-index: 5; width: 300px; box-sizing: border-box;
    background: rgba(20,20,20,0.92); color: var(--ink, #f2f4f7); border: 1px solid #555; border-radius: 8px;
    padding: 9px 11px 8px; font-size: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.45); }
  #sun-panel.in-corner { position: static; margin-bottom: 2px; }
  #sun-panel .sun-head { display: flex; justify-content: space-between; align-items: center; font-weight: 700;
    font-size: 12.5px; margin-bottom: 6px; }
  #sun-panel .sun-head span { color: var(--accent, #ffd24a); }
  #sun-panel .sun-x { background: none; border: none; color: #aaa; font-size: 17px; line-height: 1; cursor: pointer; padding: 0 2px; }
  #sun-panel .sun-x:hover { color: #fff; }
  #sun-panel .sun-row { display: flex; align-items: center; gap: 8px; }
  #sun-panel .sun-play { flex: none; width: 30px; height: 30px; border-radius: 50%; border: 1px solid rgba(255,210,74,0.55);
    background: #252a31; color: var(--accent, #ffd24a); font-size: 12px; cursor: pointer; padding: 0; line-height: 1; }
  #sun-panel .sun-play:hover { background: #2f3640; }
  #sun-panel .sun-time { flex: none; min-width: 58px; text-align: right; font-weight: 700; font-size: 14px;
    font-variant-numeric: tabular-nums; }
  #sun-panel input[type=range] { flex: 1; min-width: 0; -webkit-appearance: none; appearance: none; height: 6px; border-radius: 3px;
    background: linear-gradient(to right, #ff9b3d 0%, #ffd24a calc(var(--fill, 50) * 1%), #3a3a3a calc(var(--fill, 50) * 1%), #3a3a3a 100%);
    outline: none; margin: 0; }
  #sun-panel input[type=range]::-webkit-slider-thumb { -webkit-appearance: none; appearance: none; width: 16px; height: 16px;
    border-radius: 50%; background: #ffd24a; border: 2px solid #1c1c1c; cursor: pointer; }
  #sun-panel input[type=range]::-moz-range-thumb { width: 13px; height: 13px; border-radius: 50%; background: #ffd24a;
    border: 2px solid #1c1c1c; cursor: pointer; }
  #sun-panel .sun-dates { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; align-items: center; }
  #sun-panel .sun-dates button { background: rgba(255,255,255,0.07); border: 1px solid rgba(255,255,255,0.18); color: #ddd;
    border-radius: 11px; padding: 3px 8px; font-size: 11px; cursor: pointer; font-family: inherit; }
  #sun-panel .sun-dates button:hover { background: rgba(255,255,255,0.15); }
  #sun-panel .sun-dates button.on { border-color: rgba(255,210,74,0.7); color: var(--accent, #ffd24a); }
  #sun-panel input[type=date] { background: #2e3440; color: #fff; border: 1px solid #555; border-radius: 6px;
    font-size: 11px; padding: 2px 4px; font-family: inherit; color-scheme: dark; flex: 1 1 110px; min-width: 0; }
  #sun-panel .sun-meta { margin-top: 7px; color: var(--ink-2, #b6bdc9); font-size: 11px; line-height: 1.4; }
  #sun-panel .sun-meta b { color: var(--ink, #f2f4f7); }
  #sun-panel .sun-note { margin-top: 4px; color: var(--ink-3, #7e8794); font-size: 10.5px; line-height: 1.35; }
  @media (max-width: 700px) {
    #sun-panel { left: 8px; right: 8px; width: auto; bottom: 112px; z-index: 7; }
    #sun-panel.in-corner { width: calc(100vw - 24px); max-width: 380px; }
    #sun-panel .sun-play { width: 34px; height: 34px; }
    #sun-panel .sun-dates button { padding: 5px 9px; }
  }`;
    const st = document.createElement("style");
    st.id = "sun-shadows-css";
    st.textContent = css;
    document.head.appendChild(st);
  }

  function buildPanel() {
    injectCss();
    const p = document.createElement("div");
    p.id = "sun-panel";
    p.style.display = "none";
    p.setAttribute("role", "region");
    p.setAttribute("aria-label", "Sun simulation");
    p.innerHTML = `
      <div class="sun-head"><span>&#9728;&#65039; Sun simulation</span>
        <button class="sun-x" aria-label="Close sun simulation" title="Close">&times;</button></div>
      <div class="sun-row">
        <button class="sun-play" aria-label="Play through the day">&#9654;</button>
        <input type="range" step="10" aria-label="Time of day (NZ time)">
        <span class="sun-time"></span>
      </div>
      <div class="sun-dates">
        <button data-md="06-21" title="21 June">Mid-winter</button>
        <button data-md="03-21" title="21 March">Equinox</button>
        <button data-md="12-21" title="21 December">Mid-summer</button>
        <input type="date" aria-label="Date">
      </div>
      <div class="sun-meta"></div>
      <div class="sun-note">Shadows from the LiDAR survey of roofs, trees and ground, in NZ time.
        Hills outside the survey cast no shadow.</div>`;
    // above the corner buttons, so the switch that closes it stays in reach
    const host = document.getElementById("corner-actions");
    if (host) { p.classList.add("in-corner"); host.insertBefore(p, host.firstChild); }
    else document.body.appendChild(p);
    ui = {
      panel: p, slider: p.querySelector("input[type=range]"), time: p.querySelector(".sun-time"),
      play: p.querySelector(".sun-play"), date: p.querySelector("input[type=date]"),
      quick: Array.from(p.querySelectorAll(".sun-dates button")), meta: p.querySelector(".sun-meta"),
    };
    ui.slider.addEventListener("input", () => { state.minutes = +ui.slider.value; refresh(); });
    // on release, the exact time at once
    ui.slider.addEventListener("change", () => { if (state.on && map && lastSun) shadeSoon(tileUrl(lastSun), true); });
    ui.play.addEventListener("click", () => setPlaying(!state.playing));
    ui.quick.forEach(b => b.addEventListener("click", () => {
      state.date = state.date.slice(0, 4) + "-" + b.dataset.md; refresh();
    }));
    ui.date.addEventListener("change", () => {
      if (/^\d{4}-\d\d-\d\d$/.test(ui.date.value)) { state.date = ui.date.value; refresh(); }
    });
    p.querySelector(".sun-x").addEventListener("click", () => setOn(false));
  }

  function init(m, o) {
    if (map) return;
    map = m;
    opts = Object.assign(opts, o || {});
    maplibregl.addProtocol("sunshade", protocolHandler);
    buildPanel();
    // start at the current NZ time if the sun is up, otherwise midday
    const now = nzMinutes(Date.now());
    const c = map.getCenter(); state.lat = c.lat; state.lng = c.lng;
    const info = dayInfo();
    state.minutes = now >= info.min && now <= info.max ? Math.round(now / 10) * 10 : 12 * 60;
    const btn = document.getElementById("toggle-sun");
    if (btn) btn.addEventListener("click", () => setOn(!state.on));
    // sunrise and the sun's bearing depend (a little) on where you look
    map.on("moveend", () => {
      if (!state.on) return;
      const cc = map.getCenter();
      if (Math.abs(cc.lat - state.lat) > 0.05 || Math.abs(cc.lng - state.lng) > 0.05) {
        state.lat = cc.lat; state.lng = cc.lng; refresh();
      }
    });
  }

  window.SunShadows = {
    init,
    toggle: on => setOn(on === undefined ? !state.on : !!on),
    set: (o) => { if (o.date) state.date = o.date; if (o.minutes != null) state.minutes = o.minutes; refresh(); },
    state: () => Object.assign({ sun: lastSun, day: dayInfo(), busy: busy() }, state),
    stats: () => ({ tiles: stats.tiles, avgMs: stats.tiles ? +(stats.totalMs / stats.tiles).toFixed(1) : 0,
                    maxMs: stats.maxMs, last: stats.last.slice() }),
    sunPosition, sunTimes, nzToUtc,
    // diagnosis: one tile's PNG bytes, exactly as the map gets it
    _tile: (z, x, y, az, alt) => protocolHandler(
      { url: `sunshade://${z}/${x}/${y}?az=${az}&alt=${alt}` }, new AbortController()).then(r => r.data),
  };
})();
