"""Validate the emitted contract and support files for one isolated map preview."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.output_contract import validate_region

REQUIRED_DATA_FILES = (
    "buildings.pmtiles",
    "panel_layouts.pmtiles",
    "building_cells.pmtiles",
    "assumptions.json",
    "build_summary.json",
    "addresses.json",
    "building_detail/index.json",
    "heatmap_tiles/meta.json",
    "seasonal_curves/index.json",
    "markup_lines.geojson",
)


def validate(region_out: Path, map_data: Path) -> list[str]:
    errors = []
    try:
        errors.extend(validate_region(region_out))
    except Exception as exc:
        errors.append(f"regional output contract could not be decoded: {type(exc).__name__}: {exc}")
    data = map_data
    for rel in REQUIRED_DATA_FILES:
        path = data / rel
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"map data missing or empty: {path}")
    json_files = ("assumptions.json", "build_summary.json", "addresses.json",
                  "building_detail/index.json", "heatmap_tiles/meta.json",
                  "seasonal_curves/index.json", "markup_lines.geojson", "terrain/meta.json")
    for rel in json_files:
        path = data / rel
        if path.is_file():
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"invalid JSON {path}: {exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region-out", type=Path, required=True)
    parser.add_argument("--map-data", type=Path, required=True,
                        help="combine destination containing buildings.pmtiles and related map files")
    args = parser.parse_args()
    errors = validate(args.region_out, args.map_data)
    if errors:
        print("MAP OUTPUT VALIDATION FAILED:")
        for error in errors:
            print(f"  - {error}")
        return 1
    summary = json.loads((args.region_out / "summary.json").read_text())
    print(f"map output contract valid: {summary['n']} buildings, "
          f"{summary['panel_count']} panels, "
          f"{summary.get('heatmap_tiles', 0)} heatmap tiles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
