"use strict";
const N = typeof require === "function" ? require("../map_navigation.js") : globalThis.MapNavigation;
function assert(value, message) { if (!value) throw new Error(message); }
const map = {
  getBounds: () => ({ getWest: () => 172.68, getSouth: () => -43.52,
    getEast: () => 172.71, getNorth: () => -43.50 }),
  getBearing: () => 0, getPitch: () => 0,
  getCenter: () => ({ lng: 172.695, lat: -43.51 }), getZoom: () => 14,
};
const href = N.linkFor("http://localhost:8765/rooftop-solar.html?location=queenstown", map, "local");
const view = N.viewFromURL(href);
assert(JSON.stringify(view.bounds) === "[[172.68,-43.52],[172.71,-43.5]]", "extent lost");
assert(view.fitBoundsOptions.padding === 0, "unexpected map padding");
assert(new URL(href).searchParams.get("data") === "local", "local source lost");
assert(new URL(href).searchParams.get("location") === "queenstown", "existing query lost");
assert(!href.includes("token"), "authentication leaked into navigation");
const back = N.linkFor("http://localhost:8765/pipeline.html", map);
assert(JSON.stringify(N.viewFromURL(back)) === JSON.stringify(view), "return navigation changed extent");
assert(N.viewFromURL("https://example.org/rooftop-solar.html?lat=-43&lng=172") === null,
  "existing coordinate links overridden");
for (const value of ["1,2,3", "1,2,0,3", "1,-100,2,3", "no,2,3,4"]) {
  assert(N.viewFromURL("https://example.org/?mapbbox=" + value) === null, "bad bounds accepted");
}
const site = { dataBase: "https://storage.googleapis.com/tiles/v85/", dataVersion: "85" };
const local = N.dataSettings(site, href, 12345);
assert(local.local && local.base === "http://localhost:8765/" && local.version === "12345",
  "local map still uses published data or stale version");
for (const url of ["https://example.org/rooftop-solar.html?data=local",
  "http://localhost:8765/local-releases/release/site/rooftop-solar.html",
  "http://localhost:8765/rooftop-solar.html?data=published"]) {
  const settings = N.dataSettings(site, url);
  assert(!settings.local && settings.base === site.dataBase && settings.version === "85",
    "configured deployment changed");
}
let callback;
const link = { href: "http://localhost:8765/rooftop-solar.html", addEventListener() {} };
N.bindLinks({ ...map, on: (event, fn) => { assert(event === "moveend", "wrong event"); callback = fn; } },
  [link], () => "local");
callback();
assert(new URL(link.href).searchParams.get("mapbbox") === "172.68,-43.52,172.71,-43.5",
  "link did not update");
const unchanged = { href: "http://localhost:8765/pipeline.html", addEventListener() {} };
let zoom = 14;
const update = N.bindLinks({ ...map, getZoom: () => zoom, on() {} }, [unchanged], null,
  "http://localhost:8765/rooftop-solar.html?mapbbox=172.69,-43.515,172.70,-43.505");
assert(new URL(unchanged.href).searchParams.get("mapbbox") === "172.69,-43.515,172.7,-43.505",
  "unchanged map accumulated viewport margins");
zoom = 15;
update();
assert(new URL(unchanged.href).searchParams.get("mapbbox") === "172.68,-43.52,172.71,-43.5",
  "moved map retained stale incoming bounds");
console.log("map navigation tests passed");
globalThis.mapNavigationTestPassed = true;
