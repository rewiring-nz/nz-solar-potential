# Static site and documentation

The canonical map is `rooftop-solar.html`. `preview.html` is a compatibility
redirect preserving the query string and fragment. `data-explorer.html` is
the separate dataset explorer. All three workflows link to `docs/index.html`.

## Render locally

Markdown under `docs/` is the source of truth. Install the normal pipeline
requirements (which include `requirements-docs.txt`), or just the renderer:

```sh
.venv/bin/python -m pip install -r requirements-docs.txt
.venv/bin/python tools/render_docs.py
.venv/bin/python tools/pipeline_serve.py --root . --port 8765
```

Open `http://127.0.0.1:8765/docs/index.html`. The pipeline preview server and
web panel render documentation at startup, including developer-VM profile
serves. Plain static servers can serve the same generated files after the
explicit render command. Restart or render again after editing Markdown.
Generated HTML is ignored by Git and must not be edited by hand.

The build-time Markdown renderer escapes raw HTML, supports tables and code
fences, adds heading anchors, and rewrites links to other documentation
from `.md` to `.html`, preserving queries and fragments. Local documentation
images keep their relative paths. Mermaid fences remain readable diagram
source; there is no runtime Markdown renderer or external script dependency.
Repository source links outside `docs/` point to GitHub. Hidden paths,
symlinks, unsupported image types and files outside the checkout are not
included in the documentation publication manifest.

## GitHub Pages

[pages.yml](https://github.com/rewiring-nz/nz-solar-potential/blob/main/.github/workflows/pages.yml)
renders documentation on
pushes to `main` and manual dispatches, then uploads an allowlisted
`site-build/` artifact through the official GitHub Pages actions. Set the
repository's **Settings → Pages → Build and deployment → Source** to
**GitHub Actions** to activate this workflow; do not retain a parallel
branch-based deployment. This repository's separate build-and-publish
workflow builds map **data** on the VM, not documentation.

Reproduce the Pages artifact locally:

```sh
.venv/bin/python tools/build_static_site.py
.venv/bin/python tools/pipeline_serve.py --root site-build --port 8765
```

The site builder copies only `tools/site_assets.py`'s frontend allowlist, `site-config.js`, the
index/method pages, and generated documentation with referenced images.
It does not copy raw Markdown, local data, credentials, Python source, or
the checkout history. Production map data remains at `site-config.js`'s
configured data URL; no cloud operation is needed to render or build the site.

## Developer previews

`tools/publish_preview.py` renders the same documentation source and includes
the resulting manifest in the developer preview's public static assets.
The preview's `site-config.js` still points to that preview's immutable map
dataset. Production data publication remains separate from the Pages site
deployment; documentation changes alone do not require rebuilding roof data.
