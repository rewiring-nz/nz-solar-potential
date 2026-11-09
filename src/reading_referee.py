"""The vision referee's pick of a roof's reading, as the reading it is built from.

The build draws several readings of a roof (its final faces, the LiDAR
partition, the precomputed selected reading, plain hip/gable forms) and
picks one by how well its planes explain the LiDAR returns. On the 130
drawn roofs that pick gets the roof lines right (line F >= 0.7 against the
markup) on 47; Claude Opus 5.5, shown each reading on the photo and on a
LiDAR relief image (tools/vision_referee.py), picks one that does on 60;
the best reading on offer does on 73. Its pick improved 35 roofs and made
13 worse ($1.79 for the 130, effort low).

Where the referee picked something other than the build's own final faces,
`tools/vision_referee.py apply` writes the picked faces to
data/referee_faces/<id>.json in the selected-faces schema (source
"referee", score 1.0). With SOLAR_READING_REFEREE=1 the build reads that
file before the repeat-roof and selected readings
(roof_partition.selected_doc), and at score 1.0 it is trusted: it does not
compete with the LiDAR path.

Off by default. The first A/B (markup_benchmark, blind, all drawn roofs,
picks from candidates dumped 24 Oct) showed no gain: line F 0.586 off,
0.582 on, 63 roofs right either way; on the 64 roofs it changed, 14 better
and 20 worse. The build's own pick had improved since the dump, so the
referee's picks need asking again from fresh candidates before another A/B.
"""
import json
import os
from pathlib import Path

REFEREE_FACES_DIR = Path(__file__).resolve().parent.parent / "data" / "referee_faces"
USE_READING_REFEREE = os.environ.get("SOLAR_READING_REFEREE", "0") == "1"


def path(building_id):
    return REFEREE_FACES_DIR / f"{building_id}.json"


def write(building_id, polygons, picked, model):
    """Write the picked reading's faces (shapely polygons in NZTM)."""
    faces = []
    for p in polygons:
        for g in getattr(p, "geoms", [p]):
            if g.geom_type == "Polygon" and not g.is_empty and g.area > 0.5:
                faces.append([[round(x, 2), round(y, 2)] for x, y in g.exterior.coords])
    if not faces:
        return None
    REFEREE_FACES_DIR.mkdir(parents=True, exist_ok=True)
    doc = {"source": "referee", "score": 1.0, "picked": picked, "model": model, "faces": faces}
    path(building_id).write_text(json.dumps(doc))
    return doc
