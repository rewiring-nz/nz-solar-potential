// PER-DEPLOYMENT settings. This file is deliberately NOT synced between the
// Queenstown and national repos -- it is the one place they are meant to differ
// on the frontend, so preview.html can stay byte-identical between them.
//
// Why it exists: preview.html was unified across both deploys on 31 Aug, and
// its DEFAULT_VIEW was hardcoded to Island Bay. That silently pointed the
// QUEENSTOWN site at Wellington on first load -- Josh found it on 1 Sep. A
// shared file cannot carry a per-site default, so the default moved here.
//
// Loaded synchronously before the map is constructed, so there is no visible
// jump from a wrong starting view to the right one.
window.SITE = {
  // Bumped on every DEPLOY of this site's data: the ?v= param is the only
  // thing that makes a browser re-fetch solar_potential.geojson and the
  // pmtiles, whose URLs are otherwise identical across builds.
  dataVersion: "34",
  name: "Queenstown",
  defaultView: { center: [168.6620, -45.0320], zoom: 15.5 },
  // Areas offered in the search box, ranked above street addresses.
  towns: [
    ["Queenstown", 168.6626, -45.0312, 14.5], ["Frankton", 168.7380, -45.0230, 14.5],
    ["Fernhill", 168.6350, -45.0400, 15], ["Sunshine Bay", 168.6210, -45.0430, 15],
    ["Arthurs Point", 168.6960, -44.9820, 14.5], ["Arrowtown", 168.8110, -44.9430, 14.5],
    ["Millbrook", 168.7950, -44.9500, 15], ["Lake Hayes", 168.8100, -44.9800, 13.8],
    ["Speargrass Flat", 168.7800, -44.9700, 14], ["Kelvin Heights", 168.7000, -45.0380, 14.5],
    ["Jacks Point", 168.7420, -45.0870, 14.5], ["Hanley's Farm", 168.7510, -45.0680, 14.8],
    ["Quail Rise", 168.7470, -45.0000, 15], ["Shotover Country", 168.7560, -44.9950, 14.8],
    ["Lake Hayes Estate", 168.7610, -44.9880, 14.8], ["Queenstown Airport", 168.7390, -45.0210, 15]
  ],
};
