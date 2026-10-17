"""Validation and safety tests for local cloud profiles and command setup."""

from __future__ import annotations

import json
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.pipeline as cloud_pipeline
from cloud_profile import load_profile


def _profile_values() -> dict[str, object]:
    return {
        "project": "solar-demo-12345",
        "region": "australia-southeast1",
        "zone": "australia-southeast1-b",
        "instance_name": "solar-quickstart",
        "machine_type": "e2-standard-4",
        "disk_gb": 100,
        "image_project": "debian-cloud",
        "image_family": "debian-12",
        "service_account": None,
        "network": "default",
        "ssh_source_ranges": ["192.0.2.40/32"],
        "bucket": None,
        "build_prefix": None,
        "tiles_bucket": None,
        "preview_port": 8765,
        "cost_estimate_nzd": "3.00",
        "cost_estimate_basis": "small region, up to 8 VM-hours",
    }


class CloudProfileTests(unittest.TestCase):
    def _write(self, profiles: dict[str, object], directory: str) -> Path:
        path = Path(directory) / "cloud-profiles.json"
        path.write_text(json.dumps({"profiles": profiles}), encoding="utf-8")
        return path

    def test_loads_safe_profile_and_builds_exact_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write({"test": _profile_values()}, directory)
            profile = load_profile("test", path)
        self.assertEqual(profile.target, "solar-demo-12345/australia-southeast1-b/solar-quickstart")
        self.assertEqual(profile.values["ssh_source_ranges"], ["192.0.2.40/32"])

    def test_rejects_zone_region_mismatch(self) -> None:
        values = _profile_values()
        values["zone"] = "us-central1-a"
        with tempfile.TemporaryDirectory() as directory:
            path = self._write({"test": values}, directory)
            with self.assertRaisesRegex(ValueError, "not in region"):
                load_profile("test", path)

    def test_rejects_invalid_ssh_cidr(self) -> None:
        values = _profile_values()
        values["ssh_source_ranges"] = ["0.0.0.0/33"]
        with tempfile.TemporaryDirectory() as directory:
            path = self._write({"test": values}, directory)
            with self.assertRaisesRegex(ValueError, "invalid ssh source CIDR"):
                load_profile("test", path)

    def test_rejects_global_ssh_source_range(self) -> None:
        values = _profile_values()
        values["ssh_source_ranges"] = ["0.0.0.0/0"]
        with tempfile.TemporaryDirectory() as directory:
            path = self._write({"test": values}, directory)
            with self.assertRaisesRegex(ValueError, "global SSH source ranges"):
                load_profile("test", path)

    def test_rejects_unknown_profile_fields(self) -> None:
        values = _profile_values()
        values["password"] = "not allowed"
        with tempfile.TemporaryDirectory() as directory:
            path = self._write({"test": values}, directory)
            with self.assertRaisesRegex(ValueError, "unknown field"):
                load_profile("test", path)

    def test_cost_estimate_and_basis_must_be_valid_and_paired(self) -> None:
        values = _profile_values()
        values.pop("cost_estimate_basis")
        with tempfile.TemporaryDirectory() as directory:
            path = self._write({"test": values}, directory)
            with self.assertRaisesRegex(ValueError, "must be set together"):
                load_profile("test", path)
        values = _profile_values()
        values["cost_estimate_nzd"] = "-1"
        with tempfile.TemporaryDirectory() as directory:
            path = self._write({"test": values}, directory)
            with self.assertRaisesRegex(ValueError, "non-negative amount"):
                load_profile("test", path)

    def test_vm_creation_has_no_attached_service_account_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write({"test": _profile_values()}, directory)
            profile = load_profile("test", path)
        options = cloud_pipeline._profile_options(profile)
        self.assertIn("--no-service-account", options)
        self.assertIn("--boot-disk-auto-delete", options)
        self.assertNotIn("--scopes=https://www.googleapis.com/auth/cloud-platform", options)

    def test_firewall_rule_must_match_the_profile_before_reuse_or_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write({"test": _profile_values()}, directory)
            profile = load_profile("test", path)
        rule = {
            "description": f"{cloud_pipeline.FIREWALL_MARKER}: test",
            "direction": "INGRESS",
            "disabled": False,
            "network": "https://compute.googleapis.com/compute/v1/projects/solar-demo-12345/global/networks/default",
            "sourceRanges": ["192.0.2.40/32"],
            "targetTags": [cloud_pipeline._target_tag(profile)],
            "allowed": [{"IPProtocol": "tcp", "ports": ["22"]}],
        }
        self.assertTrue(cloud_pipeline._firewall_matches_profile(profile, rule))
        rule["sourceRanges"] = ["0.0.0.0/0"]
        self.assertFalse(cloud_pipeline._firewall_matches_profile(profile, rule))

    def test_wrong_confirmation_fails_before_cloud_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write({"test": _profile_values()}, directory)
            profile = load_profile("test", path)
        with patch.object(cloud_pipeline, "_cloud_preflight") as preflight:
            with self.assertRaisesRegex(RuntimeError, "requires --confirm"):
                cloud_pipeline._create(profile, "another/project/vm")
        preflight.assert_not_called()

    def test_cost_confirmation_decline_blocks_cloud_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write({"test": _profile_values()}, directory)
            with (
                patch.object(sys, "argv", [
                    "pipeline.py", "frankton_arm_lake", "--mode", "cloud",
                    "--profile", "test", "--profiles-file", str(path),
                ]),
                patch.object(sys.stdin, "isatty", return_value=True),
                patch("builtins.input", return_value=""),
                patch.object(cloud_pipeline, "_dispatch_cloud_action") as dispatch,
            ):
                self.assertEqual(cloud_pipeline.main(), 2)
        dispatch.assert_not_called()

    def test_cost_confirmation_approval_dispatches_cloud_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write({"test": _profile_values()}, directory)
            with (
                patch.object(sys, "argv", [
                    "pipeline.py", "frankton_arm_lake", "--mode", "cloud",
                    "--profile", "test", "--profiles-file", str(path),
                ]),
                patch.object(sys.stdin, "isatty", return_value=True),
                patch("builtins.input", return_value="yes"),
                patch.object(cloud_pipeline, "_dispatch_cloud_action", return_value=0) as dispatch,
            ):
                self.assertEqual(cloud_pipeline.main(), 0)
        dispatch.assert_called_once()

    def test_cleanup_refuses_unowned_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write({"test": _profile_values()}, directory)
            profile = load_profile("test", path)
        instance = {
            "labels": {
                "purpose": "solar-quickstart",
                "created-by": "nz-solar-potential",
                "cloud-profile": "another-profile",
            }
        }
        with patch.object(cloud_pipeline, "_existing_instance", return_value=instance):
            with self.assertRaisesRegex(RuntimeError, "refusing to delete"):
                cloud_pipeline._delete(profile, profile.target)

    def test_queue_and_publisher_use_selected_profile_buckets(self) -> None:
        values = _profile_values()
        values["bucket"] = "gs://solar-test-data"
        values["build_prefix"] = "quickstart/build"
        values["tiles_bucket"] = "gs://solar-test-tiles"
        with tempfile.TemporaryDirectory() as directory:
            path = self._write({"test": values}, directory)
            with patch.dict(os.environ, {
                "SOLAR_CLOUD_PROFILE": "test",
                "SOLAR_CLOUD_PROFILES_FILE": str(path),
            }, clear=True):
                queue = importlib.import_module("src.gcs_queue")
                publisher = importlib.import_module("tools.publish_served")
                queue = importlib.reload(queue)
                publisher = importlib.reload(publisher)
                self.assertEqual(queue.BUCKET, "gs://solar-test-data")
                self.assertEqual(queue.PREFIX, "quickstart/build")
                self.assertEqual(publisher.BUCKET, "gs://solar-test-tiles")
                self.assertEqual(publisher.PUBLIC_BASE,
                                 "https://storage.googleapis.com/solar-test-tiles")


if __name__ == "__main__":
    unittest.main()
