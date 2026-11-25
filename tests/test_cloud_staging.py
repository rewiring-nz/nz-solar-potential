"""Private cloud staging: immutable, resumable uploads verified without GCS credentials."""

import json
import base64
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.artifact_common import ArtifactError
from tools.cloud_staging import CloudStaging
from tools.regional_staging import StagingStore


class FakeGcloud:
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.fail_once = False

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        args = command[1:]
        if args[:2] == ["storage", "cp"]:
            uri = args[-1]
            if self.fail_once and uri.endswith("summary.json"):
                self.fail_once = False
                return subprocess.CompletedProcess(command, 1, "", "interrupted")
            if uri in self.objects:
                return subprocess.CompletedProcess(command, 1, "", "already exists")
            local = Path(args[-2])
            content = local.read_bytes()
            metadata = next(arg.split("=", 2)[2] for arg in args if arg.startswith("--custom-metadata="))
            self.objects[uri] = {"bytes": content, "metadata": {"sha256": metadata},
                                 "md5Hash": base64.b64encode(hashlib.md5(content).digest()).decode()}
            return subprocess.CompletedProcess(command, 0, "", "")
        if args[:3] == ["storage", "objects", "describe"]:
            assert "--raw" in args
            uri = args[-1]
            item = self.objects.get(uri)
            if item is None:
                return subprocess.CompletedProcess(command, 1, "", "not found")
            return subprocess.CompletedProcess(command, 0, json.dumps({
                "size": str(len(item["bytes"])), "metadata": item["metadata"],
                "md5Hash": item["md5Hash"]}), "")
        if args[:2] == ["storage", "ls"]:
            assert args[-1].endswith("/**")
            prefix = args[-1].removesuffix("**")
            rows = sorted(uri for uri in self.objects if uri.startswith(prefix))
            return subprocess.CompletedProcess(command, 0, "".join(row + "\n" for row in rows), "")
        if args[:2] == ["storage", "cat"]:
            item = self.objects.get(args[-1])
            if item is None:
                return subprocess.CompletedProcess(command, 1, "", "not found")
            return subprocess.CompletedProcess(command, 0, item["bytes"].decode(), "")
        return subprocess.CompletedProcess(command, 1, "", f"unexpected command: {command}")


def _fixture(tmp):
    data = Path(tmp) / "data"
    source = data / "out" / "pilot"
    source.mkdir(parents=True)
    (source / "summary.json").write_text('{"region":"pilot"}')
    (source / "panel.pmtiles").write_bytes(b"fixture tile bytes")
    staging = StagingStore(data)
    staged = staging.stage("pilot")
    runner = FakeGcloud()
    cloud = CloudStaging(staging, "gs://private-test-bucket", {
        "egress_per_gb_nzd": "0.12", "storage_monthly_per_gb_nzd": "0.03",
        "round_up_nzd": "0.50",
    }, runner=runner, gcloud="/fake/gcloud")
    return staging, staged["staging_id"], cloud, runner


def test_quote_is_bound_to_verified_staged_inputs_and_cost():
    with tempfile.TemporaryDirectory() as tmp:
        _, identifier, cloud, _ = _fixture(tmp)
        quote = cloud.estimate([identifier])
        assert quote["bucket"] == "gs://private-test-bucket"
        assert quote["staging"][0]["region_id"] == "pilot"
        assert quote["bytes"] > 0 and quote["cost_nzd"] == "0.50"
        assert "first 30 days" in quote["cost_basis"]
        assert "not a spending cap" in quote["cost_warning"]
        cloud.verify_quote([identifier], quote["quote_id"], quote["cost_nzd"])
        try:
            cloud.verify_quote([identifier], quote["quote_id"], "9.99")
        except ArtifactError as exc:
            assert "estimate the cloud push again" in str(exc)
        else:
            raise AssertionError("a changed cost was accepted")


def test_upload_is_idempotent_and_manifest_is_commit_marker():
    with tempfile.TemporaryDirectory() as tmp:
        _, identifier, cloud, runner = _fixture(tmp)
        quote = cloud.estimate([identifier])
        progress = []
        result = cloud.submit([identifier], lambda phase, **state: progress.append((phase, state)))
        for step in ("before_manifest", "final"):
            counts = [state["verified_artifacts"] for phase, state in progress
                      if phase == "verifying_remote" and state.get("verification_step") == step]
            assert counts[0] == 0 and counts[-1] == 2
        submission = result["submissions"][0]
        assert submission["status"] == "complete"
        manifest_uri = f"{cloud.root}/{identifier}/manifest.json"
        assert manifest_uri in runner.objects
        assert len(runner.objects) == 3
        uploads = sum(command[1:3] == ["storage", "cp"] for command in runner.calls)
        again = cloud.submit([identifier])
        assert again["submissions"][0]["submission_id"] == identifier
        assert sum(command[1:3] == ["storage", "cp"] for command in runner.calls) == uploads
        assert quote["staging"][0]["manifest_sha256"] == submission["source_manifest_sha256"]


def test_interrupted_upload_resumes_and_remote_mismatch_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        _, identifier, cloud, runner = _fixture(tmp)
        runner.fail_once = True
        try:
            cloud.submit([identifier])
        except ArtifactError as exc:
            assert "Cloud upload failed" in str(exc)
        else:
            raise AssertionError("interrupted upload unexpectedly completed")
        assert not any(uri.endswith("/manifest.json") for uri in runner.objects)
        assert len(runner.objects) == 1
        assert cloud.submit([identifier])["submissions"][0]["status"] == "complete"

        object_uri = next(uri for uri in runner.objects if uri.endswith("panel.pmtiles"))
        runner.objects[object_uri]["metadata"]["sha256"] = "0" * 64
        try:
            cloud.submit([identifier])
        except ArtifactError as exc:
            assert "SHA-256 or MD5 differs" in str(exc)
        else:
            raise AssertionError("remote checksum mismatch was accepted")


def test_local_corruption_prevents_any_upload():
    with tempfile.TemporaryDirectory() as tmp:
        staging, identifier, cloud, runner = _fixture(tmp)
        (staging.artifacts_path(identifier) / "panel.pmtiles").write_bytes(b"changed")
        try:
            cloud.submit([identifier])
        except ArtifactError as exc:
            assert "verification failed" in str(exc)
        else:
            raise AssertionError("corrupted local staging was submitted")
        assert runner.calls == []


def test_server_checksum_and_unexpected_objects_are_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        _, identifier, cloud, runner = _fixture(tmp)
        cloud.submit([identifier])
        object_uri = next(uri for uri in runner.objects if uri.endswith("panel.pmtiles"))
        runner.objects[object_uri]["md5Hash"] = "different"
        try:
            cloud.submit([identifier])
        except ArtifactError as exc:
            assert "verification failed" in str(exc)
        else:
            raise AssertionError("server checksum mismatch was accepted")
        runner.objects[object_uri]["md5Hash"] = base64.b64encode(
            hashlib.md5(runner.objects[object_uri]["bytes"]).digest()).decode()
        runner.objects[f"{cloud.root}/{identifier}/unexpected"] = {"bytes": b"", "metadata": {}}
        try:
            cloud.submit([identifier])
        except ArtifactError as exc:
            assert "unexpected objects" in str(exc)
        else:
            raise AssertionError("unexpected cloud object was accepted")


if __name__ == "__main__":
    test_quote_is_bound_to_verified_staged_inputs_and_cost()
    test_upload_is_idempotent_and_manifest_is_commit_marker()
    test_interrupted_upload_resumes_and_remote_mismatch_is_rejected()
    test_local_corruption_prevents_any_upload()
    test_server_checksum_and_unexpected_objects_are_rejected()
    print("cloud staging tests passed")