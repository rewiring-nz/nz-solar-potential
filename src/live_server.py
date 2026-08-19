"""
Local server for the pilot: serves preview.html and data/ as static
files (same as `python3 -m http.server`), plus one API endpoint,
/api/refit, that recomputes segmentation/obstruction-detection/panel-
fitting for a single building on demand with overridable parameters --
so the sliders in preview.html can show what a parameter change actually
does to a real building's layout, live, instead of guessing and waiting
for a full ~90s pilot-wide rebuild.

All the heavy inputs (building outlines, DSM, imagery, the pvlib/NASA
POWER yield model) are loaded once at startup and reused across requests
-- a single-building refit is cheap (RANSAC + obstruction detection for
one footprint, tens of milliseconds), so this stays responsive as you
drag a slider.

Usage: python src/live_server.py [port]   (default port 8000)
"""

import http.server
import json
import sys
import urllib.parse
import warnings
from pathlib import Path

import geopandas as gpd
import pyproj
import rasterio
from shapely.ops import transform as shapely_transform

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from src.roof_segmentation import segment_building
from src.obstruction_detection import detect_obstructions
from src.panel_fitting import fit_panels_on_facet
from src.solar_model import SolarModel

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
# Serve from the repo root (one level up), not the project dir, so the URL
# stays http://localhost:8000/solar-map/preview.html -- consistent with
# every link already shared for this pilot rather than dropping the prefix.
SERVE_ROOT = PROJECT_DIR.parent

print("Loading shared data (buildings, DSM, imagery, solar model)...")
GDF = gpd.read_file(DATA_DIR / "building_outlines.geojson").set_index("building_id", drop=False)
DSM_DS = rasterio.open(DATA_DIR / "dsm_mosaic.tif")
IMAGERY_DS = rasterio.open(DATA_DIR / "imagery_mosaic.tif")
MODEL = SolarModel()
TO_WGS84 = pyproj.Transformer.from_crs("EPSG:2193", "EPSG:4326", always_xy=True).transform
print("Ready.")


def refit_building(building_id, setback, ransac_threshold, z_threshold):
    row = GDF.loc[building_id]
    facets = segment_building(DSM_DS, row.geometry, building_id, ransac_distance_threshold=ransac_threshold)

    features = []
    total_panels = total_kwp = total_ac_kwh_year = 0
    for f in facets:
        obstructions = detect_obstructions(IMAGERY_DS, f["geometry"], z_threshold=z_threshold,
                                            boundary_erode_m=setback)
        panels = fit_panels_on_facet(f, setback=setback, obstructions=obstructions)
        poa = MODEL.annual_poa_kwh_per_m2(f["slope_deg"], f["aspect_deg"])

        features.append({
            "type": "Feature",
            "geometry": shapely_transform(TO_WGS84, f["geometry"]).__geo_interface__,
            "properties": {"kind": "facet", "slope_deg": round(f["slope_deg"], 1),
                            "aspect_deg": round(f["aspect_deg"], 1), "poa_kwh_m2_yr": round(poa, 0)},
        })
        for o in obstructions:
            features.append({
                "type": "Feature",
                "geometry": shapely_transform(TO_WGS84, o).__geo_interface__,
                "properties": {"kind": "obstruction"},
            })
        if panels:
            y = MODEL.facet_yield(f, len(panels))
            total_panels += len(panels)
            total_kwp += y["kwp"]
            total_ac_kwh_year += y["ac_kwh_year"]
            for p in panels:
                features.append({
                    "type": "Feature",
                    "geometry": shapely_transform(TO_WGS84, p["geometry"]).__geo_interface__,
                    "properties": {"kind": "panel"},
                })

    return {
        "type": "FeatureCollection",
        "features": features,
        "summary": {
            "building_id": building_id,
            "facet_count": len(facets),
            "panel_count": total_panels,
            "kwp": round(total_kwp, 2),
            "ac_kwh_year": round(total_ac_kwh_year, 0),
        },
    }


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SERVE_ROOT), **kwargs)

    def do_GET(self):
        if self.path.startswith("/api/refit"):
            self.handle_refit()
        else:
            super().do_GET()

    def handle_refit(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        try:
            building_id = int(params["building_id"][0])
            setback = float(params.get("setback", [config.PANEL_EDGE_SETBACK_M])[0])
            ransac_threshold = float(params.get("ransac_threshold", [0.15])[0])
            z_threshold = float(params.get("z_threshold", [2.75])[0])
            result = refit_building(building_id, setback, ransac_threshold, z_threshold)
            body = json.dumps(result).encode()
            status = 200
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode()
            status = 400

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if "/api/refit" in (self.path or ""):
            print(f"{self.address_string()} - {fmt % args}")
        # stay quiet on ordinary static-file requests, same as before


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = http.server.ThreadingHTTPServer(("", port), Handler)
    print(f"Serving {SERVE_ROOT} on http://localhost:{port} (with /api/refit)")
    server.serve_forever()


if __name__ == "__main__":
    main()
