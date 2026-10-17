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
]
STATIC_DIRS = ["terrain"]
