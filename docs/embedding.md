# Embedding the map

The map runs in an iframe on other sites (rewiring.nz/every-roof). It keeps
its view in its own URL: `?lat=-45.03123&lng=168.66201&z=18.0&b=4747641`
opens that place with roof 4747641's card open. The share button on a roof
card copies such a link.

A frame cannot read or change the address bar of the page around it, so a
host page needs the snippet below for shared links to point at the host page
instead of the map's own address. With it, the host page:

- passes its own query string to the map on load, so a shared
  `rewiring.nz/every-roof?lat=…&b=…` link opens that roof;
- mirrors the map's view into its own address bar as people move around;
- tells the map its address, which the share button then uses.

Without it the map still works, and the share button copies the map's own
address (`rewiring-nz.github.io/nz-solar-potential/rooftop-solar.html?…`).

## Snippet (replaces the existing embed)

```html
<iframe id="solar-map" src="https://rewiring-nz.github.io/nz-solar-potential/"
        style="width:100%; height:100vh; border:0;" title="Rooftop Solar Potential"
        allow="clipboard-write; fullscreen" allowfullscreen></iframe>
<script>
(function () {
  var MAP = "https://rewiring-nz.github.io";
  var frame = document.getElementById("solar-map");
  if (location.search) frame.src = MAP + "/nz-solar-potential/" + location.search;
  window.addEventListener("message", function (e) {
    if (e.origin !== MAP || e.source !== frame.contentWindow || !e.data) return;
    if (e.data.type === "rewiring-solar:view" && /^\?[\w.=&%-]*$/.test(e.data.query)) {
      history.replaceState(history.state, "", location.pathname + e.data.query + location.hash);
    } else if (e.data.type === "rewiring-solar:hello") {
      frame.contentWindow.postMessage(
        { type: "rewiring-solar:host", url: location.origin + location.pathname }, MAP);
    }
  });
})();
</script>
```

`allow="clipboard-write"` lets the share button write the clipboard from
inside the frame; without it the button falls back to an older copy method,
and failing that shows the link to copy by hand.

## Messages

| From | Type | Data |
| --- | --- | --- |
| map | `rewiring-solar:hello` | sent once at load |
| map | `rewiring-solar:view` | `query`: the map's current `?lat=…&lng=…&z=…[&b=…]` |
| host | `rewiring-solar:host` | `url`: the host page's address; accepted only from the parent frame and only if its origin matches the sender's |
