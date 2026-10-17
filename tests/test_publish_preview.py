"""Tests for public developer-preview publishing."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import publish_preview


class PublishPreviewTests(unittest.TestCase):
    def test_site_config_points_at_immutable_run_data(self) -> None:
        source = (
            'window.SITE = {\n  dataVersion: "48",\n'
            '  dataBase: "https://example.com/production/",\n'
            '  defaultView: { center: [168.6620, -45.0320], zoom: 15.5 },\n};\n'
        )
        result = publish_preview._replace_site_config(
            source,
            "https://storage.googleapis.com/rewiring-solar-previews/devel/alice/runs/20261017T120000Z-deadbeef/",
            "20261017T120000Z-deadbeef",
            "frankton_arm_lake",
        )
        self.assertIn('dataVersion: "20261017T120000Z-deadbeef"', result)
        self.assertIn(
            'dataBase: "https://storage.googleapis.com/rewiring-solar-previews/devel/alice/runs/20261017T120000Z-deadbeef/"',
            result,
        )
        self.assertIn("defaultView: { center: [168.677000, -45.034200], zoom:", result)
        self.assertNotIn("production", result)

    def test_rejects_site_config_without_unique_data_settings(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one"):
            publish_preview._replace_site_config(
                "window.SITE = {};", "https://example/", "v1", "frankton_arm_lake",
            )

    def test_publishes_immutable_data_then_stable_preview_pointer(self) -> None:
        run_id = "20261017T120000Z-deadbeef"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            data = root / "data"
            root.mkdir()
            data.mkdir()
            for name in publish_preview.CORE_DATA_FILES:
                (data / name).write_bytes(b"tile data")
            for name in publish_preview.FRONTEND_FILES:
                (root / name).write_text("<html></html>", encoding="utf-8")
            (root / "site-config.js").write_text(
                'window.SITE = {\n  dataVersion: "48",\n'
                '  dataBase: "https://example.com/production/",\n'
                '  defaultView: { center: [168.6620, -45.0320], zoom: 15.5 },\n};\n',
                encoding="utf-8",
            )
            commands: list[list[str]] = []
            uploaded_config: list[str] = []

            def fake_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                commands.append(command)
                if command[-1].endswith("/site-config.js"):
                    uploaded_config.append(Path(command[-2]).read_text(encoding="utf-8"))
                return subprocess.CompletedProcess(command, 0, "", "")

            url = publish_preview.publish(
                "Alice", "frankton_arm_lake", data_dir=data, root=root,
                run_id=run_id, runner=fake_runner,
            )

        self.assertEqual(
            url,
            "https://storage.googleapis.com/rewiring-solar-previews/devel/alice/preview.html",
        )
        self.assertTrue(
            any(
                f"/devel/alice/runs/{run_id}/data/panel_layouts.pmtiles" in command[-1]
                and "--cache-control=public,max-age=31536000,immutable" in command
                for command in commands
            )
        )
        self.assertEqual(commands[-1][-1], "gs://rewiring-solar-previews/devel/alice/site-config.js")
        self.assertIn(f"/runs/{run_id}/", uploaded_config[0])

    def test_configures_public_read_vm_upload_and_expiry(self) -> None:
        commands: list[list[str]] = []
        lifecycle_path: Path | None = None

        def fake_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            nonlocal lifecycle_path
            commands.append(command)
            if command[:4] == ["gcloud", "compute", "instances", "describe"]:
                return subprocess.CompletedProcess(
                    command, 0, json.dumps({"serviceAccounts": [{"email": "vm@example.iam.gserviceaccount.com"}]}), "",
                )
            if command[:4] == ["gcloud", "storage", "buckets", "describe", "gs://rewiring-solar-previews"][:4]:
                return subprocess.CompletedProcess(command, 0, json.dumps({"lifecycle": {"rule": []}}), "")
            lifecycle_arg = next(
                (item for item in command if item.startswith("--lifecycle-file=")),
                None,
            )
            if lifecycle_arg:
                lifecycle_path = Path(lifecycle_arg.partition("=")[2])
                self.assertEqual(json.loads(lifecycle_path.read_text(encoding="utf-8")), {
                    "rule": [{
                        "action": {"type": "Delete"},
                        "condition": {"age": 14, "matchesPrefix": ["devel/"]},
                    }],
                })
            return subprocess.CompletedProcess(command, 0, "", "")

        publish_preview.configure_bucket(runner=fake_runner)
        self.assertIn("--member=allUsers", commands[2])
        self.assertIn("--role=roles/storage.objectViewer", commands[2])
        self.assertIn("--member=serviceAccount:vm@example.iam.gserviceaccount.com", commands[3])
        self.assertIn("--role=roles/storage.objectUser", commands[3])
        self.assertIsNotNone(lifecycle_path)
        self.assertFalse(lifecycle_path.exists())

    def test_invalid_preview_alias_does_not_upload(self) -> None:
        with self.assertRaisesRegex(ValueError, "alphanumeric/hyphen alias"):
            publish_preview.publish(
                "../alice", "frankton_arm_lake",
                runner=lambda *_args, **_kwargs: self.fail("uploaded"),
            )


if __name__ == "__main__":
    unittest.main()
