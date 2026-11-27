// Problem reports from the map. Each report is posted to the inbox function
// (services/report_inbox/, URL in site-config.js `reportUrl`) and filed as a
// public GitHub issue labelled user-report within about 15 minutes
// (.github/workflows/user-reports.yml). Reports on one building collect in
// one issue.
//
// A report carries what diagnosis needs and nothing personal: the note, the
// building and address being looked at, the map view and settings, the data
// version, and a small JPEG of the map as it was on screen.
(function () {
  "use strict";
  const LIST = "https://github.com/rewiring-nz/nz-solar-potential/issues?q=label%3Auser-report";
  const SHOT_WIDTH = 900, SHOT_QUALITY = 0.72, SHOT_MAX = 390000;

  // MapLibre 5 clears the WebGL canvas after each frame (its top-level
  // preserveDrawingBuffer option is gone), so the canvas is read inside a
  // render event, while the frame is still there.
  function grab(map) {
    return new Promise(done => {
      const t = setTimeout(() => done(null), 1500);
      map.once("render", () => { clearTimeout(t); done(screenshot(map.getCanvas())); });
      map.triggerRepaint();
    });
  }

  function screenshot(canvas) {
    if (!canvas || !canvas.width) return null;
    try {
      const k = Math.min(1, SHOT_WIDTH / canvas.width);
      const c = document.createElement("canvas");
      c.width = Math.round(canvas.width * k);
      c.height = Math.round(canvas.height * k);
      c.getContext("2d").drawImage(canvas, 0, 0, c.width, c.height);
      for (let q = SHOT_QUALITY; q >= 0.4; q -= 0.12) {
        const url = c.toDataURL("image/jpeg", q);
        if (url.length * 0.75 < SHOT_MAX) return url;
      }
    } catch (e) { /* a tainted canvas cannot be read; send without */ }
    return null;
  }

  // r: {kind: "note"|"flag"|"correction", note, building_id, address, view,
  //     context, map}. Resolves to {ok, id} or {ok: false, error}.
  async function send(r) {
    const url = (window.SITE || {}).reportUrl;
    if (!/^https?:\/\//.test(url || "")) return { ok: false, error: "reporting is not set up on this copy of the map" };
    const hp = document.getElementById("bug-website");
    const body = {
      kind: r.kind, note: r.note, building_id: r.building_id || null,
      address: r.address || null, view: r.view || null, context: r.context || "",
      data_version: (window.SITE || {}).dataVersion || null,
      page: location.pathname,
      website: hp ? hp.value : "",
      screenshot: r.map ? await grab(r.map) : null,
    };
    try {
      const res = await fetch(url, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const out = await res.json().catch(() => ({}));
      if (res.ok && out.ok) return out;
      return { ok: false, error: out.error || `the report service answered ${res.status}` };
    } catch (e) {
      return { ok: false, error: "could not reach the report service" };
    }
  }

  window.Report = { send, LIST };
})();
