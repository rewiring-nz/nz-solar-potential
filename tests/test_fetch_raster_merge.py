"""Regional raster merges preserve rasterio's first-valid-pixel behavior."""

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import rasterio
import rasterio.merge
from rasterio.merge import merge
from rasterio.transform import from_origin

from src import fetch_data, fetch_regions
from src import raster_merge


class FetchRasterMergeTests(unittest.TestCase):
    def test_merge_matches_in_memory_reference_and_writes_to_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / "first.tif", root / "second.tif"]
            values = [
                np.array([[1, 0, 3], [4, 5, 6]], dtype="uint8"),
                np.array([[7, 8, 9], [10, 11, 12]], dtype="uint8"),
            ]
            for path, data, x in zip(paths, values, (0, 2)):
                with rasterio.open(path, "w", driver="GTiff", width=3, height=2,
                                   count=1, dtype="uint8", crs="EPSG:2193", nodata=0,
                                   transform=from_origin(x, 2, 1, 1)) as dst:
                    dst.write(data, 1)
            with rasterio.open(paths[0]) as first, rasterio.open(paths[1]) as second:
                expected, expected_transform = merge([first, second])
            destination = root / "merged.tif"
            with patch.object(raster_merge, "merge", wraps=merge) as merge_call:
                fetch_regions._merge_footprints(paths, destination)
            temporary = merge_call.call_args.kwargs["dst_path"]
            self.assertEqual(temporary.parent, root)
            self.assertTrue(temporary.name.endswith(".part.tif"))
            with rasterio.open(destination) as result:
                np.testing.assert_array_equal(result.read(), expected)
                self.assertEqual(result.transform, expected_transform)
                self.assertEqual(result.crs.to_epsg(), 2193)
                self.assertEqual(result.nodata, 0)
            self.assertFalse(temporary.exists())

    def test_large_merge_is_subdivided_instead_of_allocated_whole(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / "first.tif", root / "second.tif"]
            for path in paths:
                with rasterio.open(path, "w", driver="GTiff", width=8001, height=8001,
                                   count=1, dtype="uint8", crs="EPSG:2193", nodata=0,
                                   transform=from_origin(0, 8001, 1, 1), tiled=True,
                                   compress="deflate", SPARSE_OK="TRUE"):
                    pass
            with patch("rasterio.merge.subdivide", wraps=rasterio.merge.subdivide) as subdivide:
                fetch_regions._merge_footprints(paths, root / "merged.tif")
            subdivide.assert_called_once()
            with rasterio.open(root / "merged.tif") as result:
                self.assertEqual((result.width, result.height), (8001, 8001))

    def test_failed_merge_leaves_no_temporary_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / "first.tif", root / "second.tif"]
            for path in paths:
                with rasterio.open(path, "w", driver="GTiff", width=1, height=1,
                                   count=1, dtype="uint8", crs="EPSG:2193",
                                   transform=from_origin(0, 1, 1, 1)) as dst:
                    dst.write(np.ones((1, 1), dtype="uint8"), 1)
            with patch.object(raster_merge, "merge", side_effect=RuntimeError("failed")):
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    fetch_regions._merge_footprints(paths, root / "merged.tif")
            self.assertFalse((root / "merged.tif").exists())
            self.assertEqual(list(root.glob("*.part.tif")), [])

    def test_failed_merge_preserves_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.tif"
            with rasterio.open(source, "w", driver="GTiff", width=1, height=1,
                               count=1, dtype="uint8", crs="EPSG:2193",
                               transform=from_origin(0, 1, 1, 1)) as dst:
                dst.write(np.ones((1, 1), dtype="uint8"), 1)
            destination = root / "merged.tif"
            destination.write_bytes(b"previous output")
            with patch.object(raster_merge, "merge", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    raster_merge.merge_to_file([source], destination)
            self.assertEqual(destination.read_bytes(), b"previous output")
            self.assertEqual(list(root.glob("*.part.tif")), [])

    def test_export_mosaics_are_merged_to_disk_with_matching_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / "one.tif", root / "two.tif"]
            for index, path in enumerate(paths):
                values = np.full((3, 2, 3), 10 + index, dtype="uint8")
                with rasterio.open(path, "w", driver="GTiff", width=3, height=2,
                                   count=3, dtype="uint8", crs="EPSG:2193", nodata=0,
                                   transform=from_origin(index * 2, 2, 1, 1)) as dst:
                    dst.write(values)
            with rasterio.open(paths[0]) as first, rasterio.open(paths[1]) as second:
                expected, transform = merge([first, second])
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as output:
                for path in paths:
                    output.write(path, path.name)
            stale_dir = root / "imagery"
            stale_dir.mkdir()
            with rasterio.open(stale_dir / "stale.tif", "w", driver="GTiff", width=1,
                               height=1, count=3, dtype="uint8", crs="EPSG:2193", nodata=0,
                               transform=from_origin(100, 2, 1, 1)) as dst:
                dst.write(np.full((3, 1, 1), 255, dtype="uint8"))
            response = MagicMock(status_code=200)
            response.json.return_value = {"id": 1, "url": "https://example.test/job",
                                          "state": "complete",
                                          "download_url": "https://example.test/archive"}
            download = MagicMock()
            download.__enter__.return_value = download
            download.iter_content.return_value = [archive.getvalue()]
            with patch.object(fetch_data.requests, "post", return_value=response), \
                 patch.object(fetch_data.requests, "get", return_value=download), \
                 patch.object(raster_merge, "merge", wraps=merge) as merge_call:
                result = fetch_data.fetch_raster([168, -45, 168.1, -44.9], "test-key", 123,
                                                "imagery", out_dir=root, format_key="raster")
            self.assertIn("dst_path", merge_call.call_args.kwargs)
            with rasterio.open(result) as mosaic:
                np.testing.assert_array_equal(mosaic.read(), expected)
                self.assertEqual(mosaic.transform, transform)
                self.assertEqual(mosaic.crs.to_epsg(), 2193)
                self.assertEqual(mosaic.nodata, 0)
            self.assertFalse((root / "imagery_export.zip").exists())


if __name__ == "__main__":
    unittest.main()
