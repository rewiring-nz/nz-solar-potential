// Broken-text check for rooftop-solar.html, run in the browser console (or a
// browser automation tool) like tools/contrast_check.js.
//
// Lists visible text that shows a programming value instead of words or a
// figure: "undefined", "NaN", "null", "Infinity", "[object", or a
// workings "Doesn't add up" warning. Resilience read "3 people, undefined,
// undefined" and "Solar made a year" read "Doesn't add up" on every open
// roof before this existed.
//
// Run with a building open, the wide panel, every <details> open and each
// lightbox (A day in each season, Resilience, How this map is made) open.
// Expected output: "ok: no broken text".
(() => {
  const bad = [];
  const pat = /\b(undefined|NaN|null|Infinity)\b|\[object |Doesn.t add up/;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = n.textContent;
    if (!pat.test(t)) continue;
    const el = n.parentElement;
    if (!el || el.closest("script, style, noscript")) continue;
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    if (r.width < 1 || r.height < 1 || cs.visibility === "hidden" || cs.display === "none") continue;
    bad.push(`"${t.trim().slice(0, 80)}" in <${el.tagName.toLowerCase()}${el.className ? "." + String(el.className).split(" ")[0] : ""}>`);
  }
  document.querySelectorAll(".wk-bad").forEach(el => bad.push(`figure marked as not adding up: "${el.textContent.trim().slice(0, 40)}"`));
  return bad.length ? "FAIL\n" + bad.join("\n") : "ok: no broken text";
})();
