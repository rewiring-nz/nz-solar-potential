// Address matching for the map's search box (rooftop-solar.html), kept free
// of the page so tests/test_address_search.js can run it under node.
//
// Rules:
// - Query and label are compared as normalised words: lower case, macrons
//   and punctuation dropped, street-type abbreviations spelt out ("st" ->
//   "street", "rd" -> "road"), "New Zealand" and a trailing postcode dropped.
//   Google Maps and LINZ write the same address differently, and a pasted
//   "10 Elspeth St, Cromwell 9310, New Zealand" has to find
//   "10 Elspeth Street, Cromwell".
// - Every query word must start a different word of the label, in any order;
//   a house number must equal the label's number unless it is the last word
//   typed (still being typed: "1" finds 12).
// - A word naming a town or suburb may be absent from a label that carries
//   no town (indexes built before labels kept ", Suburb, Town"), unless it
//   is the first word of the street name ("Frankton Road").
// - One result per label: two outlines can carry the same address.
// - Results come out with exact-order matches first, then by street and
//   house number in numeric order (2 before 10).
// - When nothing matches, `suggest` offers streets and towns spelt nearly the
//   same: each street-name word typed must be within a small edit distance
//   (none up to 3 letters, 1 up to 6, 2 beyond) of a word of the street.
(function (root) {
  "use strict";

  const ABBREV = {
    st: "street", rd: "road", ave: "avenue", av: "avenue", dr: "drive",
    cres: "crescent", cr: "crescent", pl: "place", tce: "terrace", ter: "terrace",
    ln: "lane", hwy: "highway", sh: "state highway", ct: "court", crt: "court",
    gr: "grove", pde: "parade", cl: "close", blvd: "boulevard", sq: "square",
    hts: "heights", mt: "mount", esp: "esplanade", wy: "way", rise: "rise",
  };
  const STREET_TYPES = new Set(Object.values(ABBREV).concat([
    "street", "road", "avenue", "drive", "crescent", "place", "terrace", "lane",
    "highway", "court", "grove", "parade", "close", "boulevard", "square", "way",
    "rise", "heights", "esplanade", "track", "loop", "row", "view", "glen", "mews",
  ]).flatMap(s => s.split(" ")));
  const NOISE = new Set(["new", "zealand", "nz", "aotearoa"]);

  function fold(s) {
    return String(s).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  }

  // Words of a query or label. "6/60" -> "6", "60"; "9A" stays one word.
  function words(s, { query = false } = {}) {
    let w = fold(s).replace(/[^a-z0-9]+/g, " ").trim().split(" ").filter(Boolean);
    w = w.flatMap(x => (ABBREV[x] ? ABBREV[x].split(" ") : [x]));
    if (query) {
      // "New Zealand" is noise only as a pair: "new" alone starts street names.
      const out = [];
      for (let i = 0; i < w.length; i++) {
        if (w[i] === "new" && w[i + 1] === "zealand") { i++; continue; }
        if (w[i] === "nz" || w[i] === "aotearoa") continue;
        out.push(w[i]);
      }
      w = out;
      // a postcode: four digits after a word, at the end
      if (w.length > 1 && /^\d{4}$/.test(w[w.length - 1]) && w.some(x => /[a-z]/.test(x)))
        w.pop();
    }
    return w;
  }

  const isNum = w => /^\d/.test(w);

  // One searchable row. `addr` is the label shown; `at` / `town` / `coords`
  // are what picking it does (the page's business).
  function prepare(row) {
    const label = row.addr;
    const comma = label.indexOf(",");
    const head = comma < 0 ? label : label.slice(0, comma);
    const w = words(label);
    const hw = words(head);
    const num = hw.length && isNum(hw[0]) ? hw[0] : "";
    // the street: the head without its leading numbers ("6/60 X Road" -> "x road")
    let i = 0;
    while (i < hw.length && isNum(hw[i])) i++;
    const street = hw.slice(i).join(" ");
    row._w = w;
    row._n = w.join(" ");
    row._hasPlace = comma >= 0;
    row._num = parseInt(num, 10);
    row._street = street;
    row._place = comma < 0 ? "" : label.slice(comma + 1).trim();
    return row;
  }

  function matchRow(row, qw, places, firstAlpha) {
    if (!row._w) prepare(row);
    const used = new Array(row._w.length).fill(false);
    for (let k = 0; k < qw.length; k++) {
      const q = qw[k], last = k === qw.length - 1;
      let hit = -1;
      for (let j = 0; j < row._w.length; j++) {
        if (used[j]) continue;
        const r = row._w[j];
        if (isNum(q) ? (last ? r.startsWith(q) : r === q) : r.startsWith(q)) { hit = j; break; }
      }
      if (hit < 0) {
        // a town word may be missing from a label without a town, but not as
        // the first word of the street name: "Frankton Road" is a street
        if (!row._hasPlace && k !== firstAlpha && places.has(q)) continue;
        return false;
      }
      used[hit] = true;
    }
    return true;
  }

  function compareRows(a, b) {
    if (a._rank !== b._rank) return a._rank - b._rank;
    if (a.town && !b.town) return -1;
    if (b.town && !a.town) return 1;
    if (a._street !== b._street) return a._street < b._street ? -1 : 1;
    if (a._place !== b._place) return a._place < b._place ? -1 : 1;
    const an = isNaN(a._num) ? -1 : a._num, bn = isNaN(b._num) ? -1 : b._num;
    if (an !== bn) return an - bn;
    return a.addr < b.addr ? -1 : a.addr > b.addr ? 1 : 0;
  }

  // pools: arrays of rows; places: Set of town/suburb words.
  function search(pools, query, { limit = 8, places = new Set() } = {}) {
    const qw = words(query, { query: true });
    if (!qw.length) return [];
    const qn = qw.join(" ");
    // cheap filter first: the longest word typed must appear somewhere
    const key = qw.filter(w => !places.has(w)).sort((a, b) => b.length - a.length)[0] || qw[0];
    const firstAlpha = qw.findIndex(w => !isNum(w));
    const out = [];
    const seen = new Set();
    for (const pool of pools) {
      for (const row of pool) {
        if (!row._w) prepare(row);
        if (!row._n.includes(key)) continue;
        if (!matchRow(row, qw, places, firstAlpha)) continue;
        // one line per label: two outlines can carry the same address
        const id = row.addr;
        if (seen.has(id)) continue;
        seen.add(id);
        row._rank = row._n.startsWith(qn) ? 0 : 1;
        out.push(row);
      }
    }
    out.sort(compareRows);
    return out.slice(0, limit);
  }

  // Optimal string alignment distance, stopping early past `max`.
  function editDistance(a, b, max) {
    if (Math.abs(a.length - b.length) > max) return max + 1;
    let prev2 = null, prev = Array.from({ length: b.length + 1 }, (_, j) => j);
    for (let i = 1; i <= a.length; i++) {
      const cur = [i];
      let rowMin = i;
      for (let j = 1; j <= b.length; j++) {
        let v = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
        if (prev2 && i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1])
          v = Math.min(v, prev2[j - 2] + 1);
        cur.push(v);
        if (v < rowMin) rowMin = v;
      }
      if (rowMin > max) return max + 1;
      prev2 = prev; prev = cur;
    }
    return prev[b.length];
  }

  const allowed = w => (w.length <= 3 ? 0 : w.length <= 6 ? 1 : 2);

  // Closeness of one typed word to a set of words: 0 when it starts one,
  // else the best edit distance against the word (or the word cut to the
  // typed length, so a misspelt half-typed word still counts).
  function wordCost(q, targets) {
    let best = Infinity;
    const max = allowed(q);
    for (const t of targets) {
      if (t.startsWith(q)) return 0;
      const d = Math.min(editDistance(q, t, max),
                         t.length > q.length ? editDistance(q, t.slice(0, q.length), max) : max + 1);
      if (d < best) best = d;
    }
    return best <= max ? best : Infinity;
  }

  // Distinct streets (with their place) of a pool, cached on the pool.
  function streetsOf(pool) {
    if (pool._streets) return pool._streets;
    const m = new Map();
    for (const row of pool) {
      if (!row._w) prepare(row);
      if (row.town) {
        const k = "town|" + row.addr;
        if (!m.has(k)) m.set(k, { label: row.addr, words: words(row.addr.replace(/\s*\(area\)$/, "")),
                                  placeWords: [], row, town: true, nums: new Map() });
        continue;
      }
      if (!row._street) continue;
      const k = row._street + "|" + row._place;
      let s = m.get(k);
      if (!s) {
        const head = row.addr.split(",")[0];
        const name = head.replace(/^[\d/\-\s]*[a-z]?\s+(?=\D)/i, "").trim();
        s = { label: name + (row._place ? ", " + row._place : ""), name,
              words: row._street.split(" "), placeWords: words(row._place), nums: new Map() };
        m.set(k, s);
      }
      if (!isNaN(row._num)) {
        const n = row.addr.split(",")[0].split(/\s+/)[0].toLowerCase();
        if (!s.nums.has(n)) s.nums.set(n, row);
      }
    }
    pool._streets = [...m.values()];
    return pool._streets;
  }

  // Streets and towns spelt like the query, best first. A suggestion with
  // `row` opens that row directly (the number typed exists on that street);
  // otherwise `text` is what to search for instead.
  function suggest(pools, query, { limit = 5, places = new Set() } = {}) {
    const qw = words(query, { query: true });
    const numW = qw.find(isNum) || "";
    const alpha = qw.filter(w => !isNum(w));
    if (!alpha.length) return [];
    const scored = [];
    for (const pool of pools) {
      for (const s of streetsOf(pool)) {
        let cost = 0, nameHits = 0, ok = true;
        for (const q of alpha) {
          const c = wordCost(q, s.words);
          if (c !== Infinity) { cost += c; nameHits++; continue; }
          if (STREET_TYPES.has(q)) { cost += 1; continue; }   // "Elspeth Road" for a Street
          const p = s.placeWords.length ? wordCost(q, s.placeWords) : Infinity;
          if (p !== Infinity) { cost += p; continue; }
          if (places.has(q) || wordCost(q, places) !== Infinity) { cost += 0.5; continue; }
          ok = false; break;
        }
        if (!ok || !nameHits) continue;
        scored.push({ s, cost });
      }
    }
    scored.sort((a, b) => a.cost - b.cost || a.s.label.localeCompare(b.s.label));
    const out = [];
    const seen = new Set();
    for (const { s } of scored) {
      if (seen.has(s.label)) continue;
      seen.add(s.label);
      if (s.town) { out.push({ label: s.label, row: s.row }); }
      else {
        const row = numW && s.nums.get(numW);
        if (row) out.push({ label: row.addr, row });
        else out.push({ label: s.label, text: s.label.replace(/,/g, ""),
                        streetOnly: !!numW });
      }
      if (out.length >= limit) break;
    }
    return out;
  }

  // Words of the town and suburb names a deployment lists.
  function placeWords(names) {
    return new Set(names.flatMap(n => words(n)).filter(w => !STREET_TYPES.has(w) && !NOISE.has(w)));
  }

  const api = { words, prepare, search, suggest, editDistance, placeWords };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.AddressSearch = api;
})(globalThis);
