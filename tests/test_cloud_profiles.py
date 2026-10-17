"""Validation and safety tests for local cloud profiles and command setup."""

from __future__ import annotations

import json
import importlib
import io
import os
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    def test_quickstart_has_no_manual_preview_identity_or_cost_flags(self) -> None:
        for removed_option in (
            "--estimate-nzd", "--estimate-basis", "--share-preview", "--preview-user",
        ):
            with patch.object(sys, "stderr", io.StringIO()):
                with self.assertRaises(SystemExit):
                    cloud_pipeline._argument_parser("frankton_arm_lake").parse_args(
                        [removed_option, "test"]
                    )

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
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as log_dir:
            path = self._write({"test": _profile_values()}, directory)
            with (
                patch.object(sys, "argv", [
                    "pipeline.py", "frankton_arm_lake", "--mode", "cloud",
                    "--profile", "test", "--profiles-file", str(path),
                ]),
                patch.object(cloud_pipeline, "CLOUD_RUNS", Path(log_dir)),
                patch.object(cloud_pipeline.shutil, "which", return_value="/usr/bin/git"),
                patch.object(cloud_pipeline, "_gcloud", return_value="/usr/bin/gcloud"),
                patch.object(cloud_pipeline, "_cloud_preflight", return_value="verified"),
                patch.object(sys.stdin, "isatty", return_value=True),
                patch("builtins.input", return_value=""),
                patch.object(cloud_pipeline, "_cloud_action_preflight") as action_preflight,
                patch.object(cloud_pipeline, "_dispatch_cloud_action") as dispatch,
            ):
                self.assertEqual(cloud_pipeline.main(), 2)
            log = next(Path(log_dir).glob("*.log")).read_text(encoding="utf-8")
        self.assertIn("[QS-01] PASS Check local checkout and cloud profile", log)
        self.assertIn("[QS-02] PASS Check or install Google Cloud CLI", log)
        self.assertIn("[QS-03] PASS Check Google authentication and cloud target", log)
        self.assertIn("[QS-04] DECLINED Confirm estimated cloud cost", log)
        action_preflight.assert_not_called()
        dispatch.assert_not_called()

    def test_cost_confirmation_approval_dispatches_cloud_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as log_dir:
            path = self._write({"test": _profile_values()}, directory)
            with (
                patch.object(sys, "argv", [
                    "pipeline.py", "frankton_arm_lake", "--mode", "cloud",
                    "--profile", "test", "--profiles-file", str(path),
                ]),
                patch.object(cloud_pipeline, "CLOUD_RUNS", Path(log_dir)),
                patch.object(cloud_pipeline.shutil, "which", return_value="/usr/bin/git"),
                patch.object(cloud_pipeline, "_gcloud", return_value="/usr/bin/gcloud"),
                patch.object(cloud_pipeline, "_cloud_preflight", return_value="verified"),
                patch.object(cloud_pipeline, "_cloud_action_preflight", return_value="ready"),
                patch.object(sys.stdin, "isatty", return_value=True),
                patch("builtins.input", return_value="yes"),
                patch.object(cloud_pipeline, "_dispatch_cloud_action", return_value=0) as dispatch,
            ):
                self.assertEqual(cloud_pipeline.main(), 0)
            log = next(Path(log_dir).glob("*.log")).read_text(encoding="utf-8")
        self.assertIn("[QS-04] PASS Confirm estimated cloud cost", log)
        self.assertIn("[QS-05] PASS Check VM state and SSH readiness", log)
        self.assertIn("[QS-06] PASS Run cloud action: run", log)
        dispatch.assert_called_once()

    def test_cloud_preflight_does_not_request_cost_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as log_dir:
            path = self._write({"test": _profile_values()}, directory)
            with (
                patch.object(sys, "argv", [
                    "pipeline.py", "frankton_arm_lake", "--mode", "cloud",
                    "--profile", "test", "--profiles-file", str(path), "--cloud", "preflight",
                ]),
                patch.object(cloud_pipeline, "CLOUD_RUNS", Path(log_dir)),
                patch.object(cloud_pipeline.shutil, "which", return_value="/usr/bin/git"),
                patch.object(cloud_pipeline, "_gcloud", return_value="/usr/bin/gcloud"),
                patch.object(cloud_pipeline, "_cloud_preflight", return_value="verified"),
                patch.object(cloud_pipeline, "_confirm_cloud_cost") as confirm,
                patch.object(cloud_pipeline, "_cloud_action_preflight", return_value="not needed"),
                patch.object(cloud_pipeline, "_dispatch_cloud_action", return_value=0) as dispatch,
            ):
                self.assertEqual(cloud_pipeline.main(), 0)
            log = next(Path(log_dir).glob("*.log")).read_text(encoding="utf-8")
        confirm.assert_not_called()
        dispatch.assert_called_once()
        self.assertIn("[QS-04] PASS Confirm estimated cloud cost: This action does not", log)

    def test_missing_gcloud_can_be_installed_from_homebrew(self) -> None:
        with tempfile.TemporaryDirectory() as log_dir:
            with (
                patch.object(cloud_pipeline.platform, "system", return_value="Darwin"),
                patch.object(cloud_pipeline, "CLOUD_RUNS", Path(log_dir)),
                patch.object(cloud_pipeline.shutil, "which", side_effect=[None, "/brew", "/gcloud"]),
                patch.object(sys.stdin, "isatty", return_value=True),
                patch("builtins.input", return_value="yes"),
                patch.object(
                    cloud_pipeline.subprocess,
                    "run",
                    return_value=cloud_pipeline.subprocess.CompletedProcess(
                        args=["/brew", "install", "--cask", "gcloud-cli"],
                        returncode=0, stdout="", stderr="",
                    ),
                ) as install,
            ):
                log = cloud_pipeline.CloudQuickstartLog()
                self.assertEqual(cloud_pipeline._gcloud(log), "/gcloud")
            install.assert_called_once_with(
                ["/brew", "install", "--cask", "gcloud-cli"],
                cwd=cloud_pipeline.ROOT,
                check=False,
                text=True,
                capture_output=True,
            )
            text = log.path.read_text(encoding="utf-8")
        self.assertIn("[QS-02] gcloud is missing; supported package manager available (Homebrew", text)
        self.assertIn("[QS-02] Installing Google Cloud CLI: Homebrew", text)

    def test_missing_gcloud_can_be_installed_with_apt_in_wsl_ubuntu(self) -> None:
        with tempfile.TemporaryDirectory() as log_dir:
            with (
                patch.object(cloud_pipeline.platform, "system", return_value="Linux"),
                patch.object(cloud_pipeline, "_linux_environment", return_value=("ubuntu", True)),
                patch.object(cloud_pipeline, "CLOUD_RUNS", Path(log_dir)),
                patch.object(cloud_pipeline.shutil, "which", side_effect=[None, "/usr/bin/apt-get", "/usr/bin/gcloud"]),
                patch.object(sys.stdin, "isatty", return_value=True),
                patch("builtins.input", return_value="yes"),
                patch.object(
                    cloud_pipeline.subprocess,
                    "run",
                    return_value=cloud_pipeline.subprocess.CompletedProcess(
                        args=["bash", "-c", "installer"],
                        returncode=0, stdout="installed", stderr="",
                    ),
                ) as install,
            ):
                log = cloud_pipeline.CloudQuickstartLog()
                self.assertEqual(cloud_pipeline._gcloud(log), "/usr/bin/gcloud")
            install_args = install.call_args.args[0]
            self.assertEqual(install_args[:2], ["bash", "-c"])
            self.assertIn("packages.cloud.google.com/apt", install_args[2])
            self.assertIn("google-cloud-cli", install_args[2])
            self.assertTrue(install_args[2].startswith("set -euo pipefail"))
            text = log.path.read_text(encoding="utf-8")
        self.assertIn("[QS-02] Installing Google Cloud CLI: Ubuntu in WSL", text)
        self.assertIn("[QS-02] installed", text)

    def test_cloud_mode_without_profile_uses_configured_shared_build_defaults(self) -> None:
        defaults = cloud_pipeline._load_shared_cloud_config()
        preview_url = (
            "https://storage.googleapis.com/rewiring-solar-previews/"
            "devel/alice/preview.html"
        )
        with tempfile.TemporaryDirectory() as log_dir:
            with (
                patch.object(sys, "argv", ["pipeline.py", "--mode", "cloud"]),
                patch.dict(os.environ, {"SOLAR_CLOUD_PROFILE": ""}),
                patch.object(cloud_pipeline, "CLOUD_RUNS", Path(log_dir)),
                patch.object(cloud_pipeline.shutil, "which", return_value="/usr/bin/git"),
                patch.object(cloud_pipeline, "_load_shared_cloud_config", return_value=defaults),
                patch.object(cloud_pipeline, "_gcloud", return_value="/usr/bin/gcloud"),
                patch.object(cloud_pipeline, "_shared_cloud_preflight", return_value="target verified"),
                patch.object(cloud_pipeline, "_shared_vm_readiness", return_value="ready"),
                patch.object(cloud_pipeline, "_github_alias", return_value="alice"),
                patch.object(
                    cloud_pipeline, "_run_shared_cloud_build",
                    return_value=("build and preview completed", preview_url),
                ) as run_build,
                patch.object(sys.stdin, "isatty", return_value=True),
                patch("builtins.input", return_value="yes"),
                patch.object(cloud_pipeline.webbrowser, "open", return_value=True) as open_browser,
            ):
                self.assertEqual(cloud_pipeline.main(), 0)
            log = next(Path(log_dir).glob("*.log")).read_text(encoding="utf-8")
        run_args = run_build.call_args.args
        self.assertEqual(run_args[0]["project"], defaults["project"])
        self.assertEqual(run_args[0]["preview_user"], "alice")
        self.assertEqual(run_args[1], "frankton_arm_lake")
        self.assertIsInstance(run_args[2], cloud_pipeline.CloudQuickstartLog)
        self.assertIn("[QS-01] PASS Check local checkout and shared cloud defaults", log)
        self.assertIn("preview alias alice", log)
        self.assertIn("[QS-04] PASS Confirm estimated cloud cost: Human approval received.", log)
        self.assertIn("[QS-06] PASS Build, gate, and publish developer preview", log)
        open_browser.assert_called_once_with(preview_url)

    def test_shared_build_publishes_and_opens_preview_by_default(self) -> None:
        preview_url = (
            "https://storage.googleapis.com/rewiring-solar-previews/"
            "devel/alice/preview.html"
        )
        with tempfile.TemporaryDirectory() as log_dir:
            with (
                patch.object(
                    sys,
                    "argv",
                    ["pipeline.py", "--mode", "cloud"],
                ),
                patch.dict(os.environ, {"SOLAR_CLOUD_PROFILE": ""}),
                patch.object(cloud_pipeline, "CLOUD_RUNS", Path(log_dir)),
                patch.object(cloud_pipeline.shutil, "which", return_value="/usr/bin/git"),
                patch.object(
                    cloud_pipeline, "_load_shared_cloud_config",
                    return_value=cloud_pipeline._load_shared_cloud_config(),
                ),
                patch.object(cloud_pipeline, "_gcloud", return_value="/usr/bin/gcloud"),
                patch.object(cloud_pipeline, "_shared_cloud_preflight", return_value="verified"),
                patch.object(cloud_pipeline, "_shared_vm_readiness", return_value="ready"),
                patch.object(cloud_pipeline, "_github_alias", return_value="alice"),
                patch.object(
                    cloud_pipeline, "_run_shared_cloud_build",
                    return_value=("build and preview completed", preview_url),
                ) as run_build,
                patch.object(sys.stdin, "isatty", return_value=True),
                patch("builtins.input", return_value="yes"),
                patch.object(cloud_pipeline.webbrowser, "open", return_value=True) as open_browser,
            ):
                self.assertEqual(cloud_pipeline.main(), 0)
        self.assertEqual(run_build.call_args.args[0]["preview_user"], "alice")
        open_browser.assert_called_once_with(preview_url)

    def test_shared_build_passes_github_alias_to_remote_publisher(self) -> None:
        config = cloud_pipeline._load_shared_cloud_config()
        config["preview_user"] = "alice"
        expected_url = (
            "https://storage.googleapis.com/rewiring-solar-previews/"
            "devel/alice/preview.html"
        )
        process = MagicMock()
        process.stdout = io.StringIO(f"PREVIEW_URL={expected_url}\n")
        process.wait.return_value = 0
        with tempfile.TemporaryDirectory() as log_dir:
            with (
                patch.object(cloud_pipeline, "CLOUD_RUNS", Path(log_dir)),
                patch.object(cloud_pipeline.subprocess, "Popen", return_value=process) as popen,
            ):
                log = cloud_pipeline.CloudQuickstartLog()
                result = cloud_pipeline._run_shared_cloud_build(
                    config, config["regions"], log,
                )
        command = popen.call_args.args[0]
        self.assertIn("--preview-user", command)
        self.assertEqual(command[command.index("--preview-user") + 1], "alice")
        self.assertEqual(result[1], expected_url)
        self.assertIn("preview was published", result[0])

    def test_shared_cloud_config_has_expected_project_vm_and_calculated_cost(self) -> None:
        values = cloud_pipeline._load_shared_cloud_config()
        self.assertEqual(values["project"], "downloads-417521")
        self.assertEqual(values["vm"], "claude-doing-things")
        self.assertEqual(values["zone"], "australia-southeast1-b")
        self.assertEqual(values["ssh_user"], "j")
        self.assertEqual(values["regions"], "frankton_arm_lake")
        self.assertEqual(values["ref"], "main")
        estimate, basis = cloud_pipeline._shared_preview_cost(values)
        self.assertEqual(estimate, Decimal("5.00"))
        self.assertIn("compute NZD $1.92 (8 hours)", basis)
        self.assertIn("estimated public downloads NZD $2.40 (20 GB)", basis)

    def test_shared_cost_confirmation_shows_calculated_estimate(self) -> None:
        config = cloud_pipeline._load_shared_cloud_config()
        with tempfile.TemporaryDirectory() as log_dir:
            with (
                patch.object(cloud_pipeline, "CLOUD_RUNS", Path(log_dir)),
                patch.object(sys.stdin, "isatty", return_value=True),
                patch("builtins.input", return_value="yes"),
            ):
                log = cloud_pipeline.CloudQuickstartLog()
                cloud_pipeline._confirm_shared_cloud_cost(config, log)
            content = log.path.read_text(encoding="utf-8")
        self.assertIn("Calculated estimate: NZD $5.00", content)
        self.assertIn("publish a public preview", content)
        self.assertIn("not a spending cap", content)

    def test_github_alias_uses_authenticated_cli_login(self) -> None:
        completed = cloud_pipeline.subprocess.CompletedProcess(
            args=["gh", "api", "user"], returncode=0, stdout="Alice\n", stderr="",
        )
        with (
            patch.object(cloud_pipeline.shutil, "which", return_value="/usr/bin/gh"),
            patch.object(cloud_pipeline.subprocess, "run", return_value=completed) as run,
        ):
            self.assertEqual(cloud_pipeline._github_alias(), "alice")
        self.assertEqual(run.call_args.args[0], [
            "/usr/bin/gh", "api", "user", "--jq", ".login",
        ])

    def test_github_alias_requires_authenticated_cli(self) -> None:
        with patch.object(cloud_pipeline.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "GitHub CLI.*gh auth login"):
                cloud_pipeline._github_alias()

    def test_unsupported_linux_gcloud_error_has_official_install_instructions(self) -> None:
        with (
            patch.object(cloud_pipeline.platform, "system", return_value="Linux"),
            patch.object(cloud_pipeline, "_linux_environment", return_value=("fedora", False)),
            patch.object(cloud_pipeline.shutil, "which", side_effect=[None, None]),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Google Cloud CLI.*https://cloud.google.com/sdk/docs/install-sdk",
            ):
                cloud_pipeline._gcloud()

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
