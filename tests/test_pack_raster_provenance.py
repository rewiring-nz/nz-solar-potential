"""Packed raster lineage is retained without treating subsets as native tiles."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from src import pack_region, publish_region
from src.raster_provenance import file_sha256, fingerprint, verify_mosaic

REAL_PACK_IMAGERY = pack_region._pack_imagery
REAL_COPY_RASTER = pack_region._copy_raster


class PackRasterProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.region_dir = root / "region"
        self.region_dir.mkdir()
        self.points = root / "pointcloud"
        self.points.mkdir()
        self.paths = {
            "dir": self.region_dir, "outlines": self.region_dir / "outlines.geojson",
            "dsm": self.region_dir / "dsm_mosaic.tif",
            "imagery": self.region_dir / "imagery_mosaic.tif",
        }
        for role in ("dsm", "imagery"):
            self.paths[role].write_bytes(f"original {role}".encode())
            content = {
                "schema_version": 1, "region": "fixture", "role": role,
                "layer_id": 42, "sources": [{"sha256": "a" * 64}],
                "processing": {"crs": "EPSG:2193"},
                "output": {"name": self.paths[role].name,
                           "sha256": file_sha256(self.paths[role])},
            }
            pack_region._sidecar(self.paths[role]).write_text(json.dumps({
                **content, "fingerprint": fingerprint(content),
            }))
        self.patches = [
            patch.object(pack_region, "area_paths", return_value=self.paths),
            patch.object(pack_region, "POINTCLOUD_DIR", self.points),
            patch.object(pack_region, "PACKED_REGISTRY", self.points / ".packed.json"),
            patch.object(pack_region, "_boxes", return_value=np.array([[0, 0, 2, 2]])),
            patch.object(pack_region, "_pack_imagery", side_effect=self._subset),
            patch.object(pack_region, "_copy_raster", side_effect=self._reencode),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    @staticmethod
    def _subset(src, boxes, dest):
        dest.write_bytes(Path(src).read_bytes() + b" subset")
        return dest.stat().st_size

    @staticmethod
    def _reencode(src, dest):
        dest.write_bytes(Path(src).read_bytes() + b" reencoded")
        return dest.stat().st_size

    def _remove_originals(self):
        for role in ("dsm", "imagery"):
            self.paths[role].unlink()
            pack_region._sidecar(self.paths[role]).unlink()

    def test_round_trip_marks_reencoded_and_subset_files(self):
        packed = pack_region.pack("fixture")
        pack = self.region_dir / "pack"
        for role, name, operation in (
            ("dsm", "dsm_pack.tif", "pack_full_dsm_reencode"),
            ("imagery", "imagery_pack.tif", "pack_imagery_building_subset"),
        ):
            raster = pack / name
            result = verify_mosaic(raster, pack_region._sidecar(raster))
            self.assertEqual(packed["raster_provenance"][role], result["fingerprint"])
            self.assertEqual(result["derivations"][-1]["operation"], operation)
            self.assertEqual(result["sources"][0]["sha256"], "a" * 64)
        self._remove_originals()
        pack_region.restore("fixture")
        pack_region.validate_pack_sources("fixture")
        for role in ("dsm", "imagery"):
            restored = verify_mosaic(self.paths[role], pack_region._sidecar(self.paths[role]))
            self.assertEqual(restored["derivations"][-1]["operation"], "restore_pack_copy")
            self.assertEqual(restored["derivations"][0]["operation"],
                             "pack_full_dsm_reencode" if role == "dsm"
                             else "pack_imagery_building_subset")

    def test_missing_or_corrupt_pack_provenance_refuses_restore(self):
        pack_region.pack("fixture")
        self._remove_originals()
        pack = self.region_dir / "pack"
        sidecar = pack_region._sidecar(pack / "dsm_pack.tif")
        original = sidecar.read_bytes()
        sidecar.unlink()
        with self.assertRaisesRegex(ValueError, "missing dsm pack provenance"):
            pack_region.restore("fixture")
        self.assertFalse(self.paths["dsm"].exists())
        sidecar.write_bytes(original)
        (pack / "dsm_pack.tif").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "does not match"):
            pack_region.restore("fixture")
        self.assertFalse(self.paths["imagery"].exists())

    def test_untracked_legacy_pack_restores_without_inventing_provenance(self):
        for role in ("dsm", "imagery"):
            pack_region._sidecar(self.paths[role]).unlink()
        pack_region.pack("fixture")
        manifest_path = self.region_dir / "pack" / "pack.json"
        manifest = json.loads(manifest_path.read_text())
        del manifest["raster_provenance"]
        manifest_path.write_text(json.dumps(manifest))
        for role in ("dsm", "imagery"):
            self.paths[role].unlink()
        pack_region.restore("fixture")
        for role in ("dsm", "imagery"):
            self.assertTrue(self.paths[role].exists())
            self.assertFalse(pack_region._sidecar(self.paths[role]).exists())

    def test_invalid_source_provenance_preserves_previous_pack(self):
        pack = self.region_dir / "pack"
        pack.mkdir()
        (pack / "keep").write_text("previous pack")
        self.paths["dsm"].write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "does not match"):
            pack_region.pack("fixture")
        self.assertEqual((pack / "keep").read_text(), "previous pack")

    def test_restore_refuses_existing_orphan_sidecar(self):
        pack_region.pack("fixture")
        self._remove_originals()
        sidecar = pack_region._sidecar(self.paths["dsm"])
        sidecar.write_text("existing")
        with self.assertRaisesRegex(SystemExit, "refusing to overwrite"):
            pack_region.restore("fixture")
        self.assertEqual(sidecar.read_text(), "existing")
        self.assertFalse(self.paths["imagery"].exists())

    def test_existing_pack_cannot_authorize_deleting_changed_input(self):
        pack_region.pack("fixture")
        pack_region.validate_pack_sources("fixture")
        self.paths["dsm"].write_bytes(b"new source")
        content = json.loads(pack_region._sidecar(self.paths["dsm"]).read_text())
        content["output"]["sha256"] = file_sha256(self.paths["dsm"])
        content.pop("fingerprint")
        content["fingerprint"] = fingerprint(content)
        pack_region._sidecar(self.paths["dsm"]).write_text(json.dumps(content))
        with self.assertRaisesRegex(ValueError, "does not match current dsm"):
            pack_region.validate_pack_sources("fixture")

    def test_existing_legacy_pack_cannot_authorize_deleting_tracked_input(self):
        pack = self.region_dir / "pack"
        pack.mkdir()
        (pack / "pack.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "lacks dsm provenance"):
            pack_region.validate_pack_sources("fixture")

    def test_source_change_during_pack_is_not_recorded_as_success(self):
        def mutate_source(src, dest):
            result = self._reencode(src, dest)
            Path(src).write_bytes(b"changed while packing")
            return result

        with patch.object(pack_region, "_copy_raster", side_effect=mutate_source):
            with self.assertRaisesRegex(ValueError, "does not match"):
                pack_region.pack("fixture")
        self.assertFalse((self.region_dir / "pack" / "pack.json").exists())

    def test_corrupted_restore_copy_is_not_given_provenance(self):
        pack_region.pack("fixture")
        self._remove_originals()
        real_copy = shutil.copy2

        def corrupt_dsm(src, dest):
            result = real_copy(src, dest)
            if Path(src).name == "dsm_pack.tif":
                Path(dest).write_bytes(b"damaged copy")
            return result

        with patch.object(pack_region.shutil, "copy2", side_effect=corrupt_dsm):
            with self.assertRaisesRegex(ValueError, "restored dsm does not match"):
                pack_region.restore("fixture")
        self.assertFalse(pack_region._sidecar(self.paths["dsm"]).exists())

    def test_real_rasters_preserve_derivation_through_pack_and_restore(self):
        import rasterio
        from rasterio.transform import from_origin

        for role in ("dsm", "imagery"):
            path = self.paths[role]
            with rasterio.open(path, "w", driver="GTiff", count=1, width=64, height=64,
                               dtype="uint8", crs="EPSG:2193",
                               transform=from_origin(0, 64, 1, 1)) as raster:
                raster.write(np.ones((1, 64, 64), dtype="uint8"))
            sidecar = pack_region._sidecar(path)
            content = json.loads(sidecar.read_text())
            content["output"]["sha256"] = file_sha256(path)
            content.pop("fingerprint")
            sidecar.write_text(json.dumps({**content, "fingerprint": fingerprint(content)}))
        with patch.object(pack_region, "_pack_imagery", new=REAL_PACK_IMAGERY), \
             patch.object(pack_region, "_copy_raster", new=REAL_COPY_RASTER):
            pack_region.pack("fixture")
        self._remove_originals()
        pack_region.restore("fixture")
        with rasterio.open(self.paths["dsm"]) as raster:
            np.testing.assert_array_equal(raster.read(1), np.ones((64, 64), dtype="uint8"))
        with rasterio.open(self.paths["imagery"]) as raster:
            pixels = raster.read(1)
            self.assertEqual(pixels[0, 63], 0)
            self.assertEqual(pixels[63, 0], 1)
        for role in ("dsm", "imagery"):
            verify_mosaic(self.paths[role], pack_region._sidecar(self.paths[role]))

    def test_publishing_deletes_inputs_only_after_matching_pack_upload(self):
        pack_region.pack("fixture")
        root = self.region_dir.parent
        out_root = root / "out"
        out = out_root / "fixture"
        out.mkdir(parents=True)
        (out / "summary.json").write_text("{}")
        (out / "solar_potential.geojson").write_text('{"features": []}')
        uploads = {}

        def fake_upload(local, remote, dry):
            uploads[remote] = publish_region._sizes_local(local)

        with patch.object(publish_region, "OUT_ROOT", out_root), \
             patch.object(publish_region, "SEL", root / "readings"), \
             patch.object(publish_region, "PC", self.points), \
             patch.object(publish_region, "REGIONS_DIR", root / "regions"), \
             patch.object(publish_region, "area_paths", return_value=self.paths), \
             patch.object(publish_region, "area_bbox_wgs84", return_value=[0, 0, 1, 1]), \
             patch.object(publish_region, "url", return_value="gs://fixture/region"), \
             patch.object(publish_region, "_rsync", side_effect=fake_upload), \
             patch.object(publish_region, "_sizes_remote", side_effect=uploads.__getitem__), \
             patch.object(publish_region.region_ids, "list_regions", return_value=[]), \
             patch("src.surveys.survey_for", return_value={"name": "fixture"}):
            publish_region.publish("fixture")
        pack_upload = uploads["gs://fixture/region/pack"]
        self.assertIn("dsm_pack.tif.source.json", pack_upload)
        self.assertIn("imagery_pack.tif.source.json", pack_upload)
        for role in ("dsm", "imagery"):
            self.assertFalse(self.paths[role].exists())
            self.assertFalse(pack_region._sidecar(self.paths[role]).exists())


if __name__ == "__main__":
    unittest.main()
