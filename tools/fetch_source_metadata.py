"""
Record what LINZ publishes about every data layer the build reads.

The methodology diagram on the map (methodology.json, built by
src/methodology.py) states each source's title, capture dates and accuracy.
Those figures are LINZ's, not ours, so they are read from the LINZ Data
Service layer metadata and stored in data/source_metadata.json, never typed
by hand. Area-specific survey layers are discovered and cached at runtime;
this committed file covers only stable national layers.

Accuracy is parsed from the layer's own description text; where LINZ states
none, the field is null and the diagram says "not stated by LINZ".

Run (needs the LINZ key, from the environment or Secret Manager):
    python tools/fetch_source_metadata.py
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

OUT = ROOT / "data" / "source_metadata.json"
API = "https://data.linz.govt.nz/services/api/v1.x/layers/{}/"
def layer_ids() -> dict[int, list[str]]:
    """Stable national LINZ layer IDs, with where each is named."""
    ids: dict[int, list[str]] = {}
    for name in ("LINZ_BUILDING_OUTLINES_LAYER", "LINZ_SUBURBS_LOCALITIES_LAYER",
                 "LINZ_WIDE_DEM_LAYER"):
        ids.setdefault(int(getattr(config, name)), []).append(f"config.{name}")
    return ids


def _sentences(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"(?<=\.)\s+(?=[A-Z])", text) if x.strip()]


def parse(desc: str) -> dict:
    """LINZ's own statements, kept verbatim, plus the figures read from them.

    Accuracy is LINZ's specification sentence (LiDAR: "Vertical Accuracy
    Specification is +/- 0.2m (95%)"; imagery: "spatial accuracy of +/-0.15 m
    @ 95% ..."). Numbers are read from that sentence and nowhere else."""
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", desc)).strip()
    sents = _sentences(t)
    acc = [x for x in sents if re.search(r"accuracy", x, re.I)]
    cap = next((x for x in sents if re.search(r"captured .*\b(between|in the)\b", x, re.I)
                and re.search(r"\d{4}", x)), None)
    gsd = re.search(r"\(([\d.]+)\s*m GSD\)", t)

    def figure(pattern):
        for x in acc:
            m = re.search(pattern, x, re.I)
            if m:
                return float(m.group(1))
        return None

    return {
        "accuracy_statements": [x[:240] for x in acc][:3],
        "vertical_accuracy_m": figure(r"vertical accuracy[^.]*?\+/-\s*([\d.]+)\s*m"),
        "horizontal_accuracy_m": figure(r"(?:horizontal|spatial) accuracy[^.]*?(?:\+/-|-/\+|±)\s*([\d.]+)\s*m"),
        "confidence_pct": figure(r"(\d+)\s*%"),
        "ground_resolution_m": float(gsd.group(1)) if gsd else None,
        "captured": cap[:240] if cap else None,
    }


def fetch(lid: int, key: str) -> dict:
    req = urllib.request.Request(API.format(lid), headers={"Authorization": "key " + key})
    d = json.load(urllib.request.urlopen(req, timeout=60))
    desc = d.get("description_html") or d.get("description") or ""
    return {"title": d.get("title"), "url": f"https://data.linz.govt.nz/layer/{lid}/",
            **parse(desc)}


def main() -> int:
    from src.project_secrets import get
    key = get("LINZ_API_KEY")
    if not key:
        print("no LINZ key", file=sys.stderr)
        return 1
    out = {}
    for lid, where in sorted(layer_ids().items()):
        m = fetch(lid, key)
        m["named_in"] = where
        out[str(lid)] = m
        print(lid, m["title"], "| v", m["vertical_accuracy_m"], "h", m["horizontal_accuracy_m"],
              "@", m["confidence_pct"], "| gsd", m["ground_resolution_m"])
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"{len(out)} layers -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
