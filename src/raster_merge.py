"""Merge raster files to an atomic, disk-backed GeoTIFF."""

import os
import tempfile
from contextlib import ExitStack
from pathlib import Path

import rasterio
from rasterio.merge import merge


def merge_to_file(paths, destination):
    destination = Path(destination)
    if not paths:
        raise ValueError(f"No rasters to merge for {destination}")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.stem}-",
                                         suffix=".part.tif", dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with ExitStack() as stack:
            sources = [stack.enter_context(rasterio.open(path)) for path in paths]
            merge(sources, dst_path=temporary, dst_kwds={"BIGTIFF": "IF_SAFER"})
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
