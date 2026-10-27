"""Map artifacts uploaded by production and developer-preview publishers."""

SERVED_FILES = [
    "panel_layouts.pmtiles",
    "buildings.pmtiles",
    "building_cells.pmtiles",
    "addresses.json",
    "assumptions.json",
    "seasonal_curves.json",
    "build_summary.json",
    "markup_lines.geojson",
]
SERVED_DIRS = [
    "building_detail",
    "heatmap_tiles",
    "addresses",
    "seasonal_curves",
    "summaries",
    # each region's sunlight table, for the workings (src/workings_data.py)
    "sun",
]
# Fetched whole by the page, and compressible ~4x: stored gzip-encoded.
GZIP_DIRS = {"sun"}
# terrain_ground: the DSM with buildings pressed flat, under the 3D solids
STATIC_DIRS = ["terrain", "terrain_ground"]
