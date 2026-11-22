#!/usr/bin/env python3
"""Inspect an experimental regional raster plan without downloading raster data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.raster_cache import CachePolicy, RasterCache, RasterCacheError
from src.raster_cache_config import load_cache_config
from src.raster_disk_plan import DiskAllocation, DiskRequirements, forecast_disk
from src.raster_region_plan import plan_regions
from src.raster_source_selection import load_layer_mappings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("regions", nargs="+", help="Prepared region IDs")
    parser.add_argument("--mappings", type=Path, help="Explicit LDS-to-STAC mapping JSON")
    parser.add_argument("--no-imagery", action="store_true")
    parser.add_argument("--cache-policy", type=Path, help="Experimental cache configuration JSON")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--cache-max-bytes", type=int)
    parser.add_argument("--unknown-asset-bytes", type=int)
    parser.add_argument("--min-free-bytes", type=int)
    parser.add_argument("--work-dir", type=Path, help="Existing preparation/scratch filesystem directory")
    parser.add_argument("--output-dir", type=Path, help="Existing output filesystem directory")
    parser.add_argument("--mosaic-bytes", type=int, help="Conservative mosaic allocation bound")
    parser.add_argument("--scratch-bytes", type=int, help="Conservative scratch allocation bound")
    parser.add_argument("--output-bytes", type=int, help="Conservative build output allocation bound")
    parser.add_argument("--export-bytes", type=int, help="Conservative bound for each export fallback")
    parser.add_argument("--extra-allocation", nargs=3, action="append", default=[],
                        metavar=("NAME", "DIRECTORY", "BYTES"),
                        help="Additional named filesystem allocation; repeat as needed")
    args = parser.parse_args(argv)
    if args.cache_policy is not None and any(value is not None for value in (
            args.cache_dir, args.cache_max_bytes, args.unknown_asset_bytes, args.min_free_bytes)):
        parser.error("--cache-policy cannot be combined with individual cache options")
    cache_values = (args.cache_dir, args.cache_max_bytes, args.unknown_asset_bytes)
    if any(value is not None for value in cache_values) and any(value is None for value in cache_values):
        parser.error("--cache-dir, --cache-max-bytes and --unknown-asset-bytes must be supplied together")
    forecast_values = (args.work_dir, args.output_dir, args.mosaic_bytes,
                       args.scratch_bytes, args.output_bytes)
    forecast_requested = (any(value is not None for value in forecast_values)
                          or args.export_bytes is not None or bool(args.extra_allocation))
    if forecast_requested and (any(value is None for value in forecast_values)
                               or (args.cache_dir is None and args.cache_policy is None)):
        parser.error("disk forecast requires cache options, --work-dir, --output-dir, "
                     "--mosaic-bytes, --scratch-bytes and --output-bytes")
    try:
        config, sources = load_cache_config(args.cache_policy) if args.cache_policy else (None, None)
        cache = (config.cache() if config is not None else
                 RasterCache(args.cache_dir, CachePolicy(args.cache_max_bytes,
                                                        args.min_free_bytes or 0,
                                                        args.unknown_asset_bytes))
                 if args.cache_dir is not None else None)
        mappings = load_layer_mappings(args.mappings) if args.mappings else []
        result = plan_regions(args.regions, mappings=mappings,
                              include_imagery=not args.no_imagery)
        if config is not None:
            result["cache_policy"] = config.describe(sources)
        if cache is not None and result["batch"] is not None:
            result["batch"] = cache.inspect_plan(result["batch"])
        if forecast_requested:
            if result["batch"] is None:
                raise ValueError("No raster batch inputs to forecast")
            requirements = DiskRequirements(cache.directory, args.work_dir, args.output_dir,
                                            args.mosaic_bytes, args.scratch_bytes, args.output_bytes,
                                            cache.policy.min_free_bytes, args.export_bytes,
                                            tuple(DiskAllocation(name, Path(directory), int(size))
                                                  for name, directory, size in args.extra_allocation))
            result["disk_forecast"] = forecast_disk(result["batch"], requirements)
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    except (OSError, ValueError, LookupError, RasterCacheError) as exc:
        print(f"Raster planning failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
