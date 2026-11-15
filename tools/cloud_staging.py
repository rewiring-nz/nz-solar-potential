"""Submit locally staged regions to immutable private cloud storage."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Callable

from tools.artifact_common import ArtifactError, artifact_id, artifact_lock
from tools.regional_staging import StagingStore, manifest_hash

SUBMISSION_PREFIX = "pipeline-staging/submissions"
SCHEMA = 1
_BUCKET = re.compile(r"gs://[a-z0-9][a-z0-9-]{1,61}[a-z0-9]")

Runner = Callable[[list[str]], subprocess.CompletedProcess]


class CloudStaging:
    def __init__(self, staging: StagingStore, bucket: str, cost_model: dict,
                 runner: Runner | None = None, gcloud: str | None = None):
        if not isinstance(bucket, str) or not _BUCKET.fullmatch(bucket):
            raise ArtifactError("Cloud artifact staging requires a valid private gs:// bucket")
        self.staging = staging
        self.bucket = bucket
        self.cost_model = cost_model
        self._runner = runner or subprocess.run
        self._gcloud = gcloud or shutil.which("gcloud")

    @property
    def root(self) -> str:
        return f"{self.bucket}/{SUBMISSION_PREFIX}"

    def estimate(self, staging_ids: list[str]) -> dict:
        identifiers = self._ids(staging_ids)
        rows = []
        total_bytes = 0
        total_objects = 0
        for identifier in identifiers:
            manifest = self.staging.verify(identifier)
            size = sum(item["bytes"] for item in manifest["artifacts"])
            total_bytes += size
            total_objects += len(manifest["artifacts"])
            rows.append({"staging_id": identifier, "region_id": manifest.get("region_id"),
                         "manifest_sha256": manifest_hash(self._directory(identifier)),
                         "artifact_count": len(manifest["artifacts"]), "bytes": size})

        gb = Decimal(total_bytes) / Decimal(1_000_000_000)
        transfer = gb * Decimal(self.cost_model["egress_per_gb_nzd"])
        storage = gb * Decimal(self.cost_model["storage_monthly_per_gb_nzd"])
        quantum = Decimal(self.cost_model["round_up_nzd"])
        cost = max(quantum, ((transfer + storage) / quantum).to_integral_value(
            rounding=ROUND_CEILING) * quantum)
        basis = (f"{total_bytes:,} bytes in {total_objects:,} objects; transfer is conservatively "
                 f"estimated at NZD {self.cost_model['egress_per_gb_nzd']}/GB and storage for the "
                 f"first 30 days at NZD {self.cost_model['storage_monthly_per_gb_nzd']}/GB-month, "
                 f"rounded up in NZD {quantum} increments. Uploads are ingress; this uses the "
                 "configured egress rate as a conservative transfer proxy. Ongoing storage, "
                 "retrieval, request charges, taxes and price changes may add cost.")
        quote = {"bucket": self.bucket, "destination": f"{self.root}/<staging-id>/",
                 "staging": rows, "bytes": total_bytes, "artifact_count": total_objects,
                 "cost_nzd": f"{cost:.2f}", "cost_basis": basis,
                 "cost_warning": "This is an estimate, not a spending cap; storage continues until removed."}
        encoded = json.dumps(quote, sort_keys=True, separators=(",", ":")).encode()
        quote["quote_id"] = hashlib.sha256(encoded).hexdigest()
        return quote

    def upload_summary(self, staging_ids: list[str]) -> dict:
        """Verify the current staged versions and report bytes and destination only."""
        identifiers = self._ids(staging_ids)
        with artifact_lock(self.staging.data):
            current = self.staging.current()["regions"]
            rows = []
            total_bytes = 0
            total_objects = 0
            for identifier in identifiers:
                manifest = self.staging.verify(identifier)
                region = manifest.get("region_id")
                if current.get(region) != identifier:
                    raise ArtifactError(
                        f"{region} is no longer the current local staged version; refresh the area list", 409)
                size = sum(item["bytes"] for item in manifest["artifacts"])
                total_bytes += size
                total_objects += len(manifest["artifacts"])
                rows.append({"staging_id": identifier, "region_id": region,
                             "artifact_count": len(manifest["artifacts"]), "bytes": size})
            return {"bucket": self.bucket, "destination": f"{self.root}/<staging-id>/",
                    "staging": rows, "bytes": total_bytes, "artifact_count": total_objects}

    def verify_quote(self, staging_ids: list[str], quote_id: str, cost_nzd: str) -> dict:
        quote = self.estimate(staging_ids)
        if quote.get("quote_id") != quote_id or str(quote["cost_nzd"]) != str(cost_nzd):
            raise ArtifactError("The staged selection or estimate changed; estimate the cloud push again", 409)
        return quote

    def submit(self, staging_ids: list[str], progress: Callable[..., None] | None = None,
               quote_id: str | None = None, cost_nzd: str | None = None,
               require_current: bool = False) -> dict:
        identifiers = self._ids(staging_ids)
        with artifact_lock(self.staging.data):
            if quote_id is not None or cost_nzd is not None:
                if not isinstance(quote_id, str) or not isinstance(cost_nzd, str):
                    raise ArtifactError("Cloud submission requires its approved estimate", 400)
                self.verify_quote(identifiers, quote_id, cost_nzd)
            if require_current:
                current = self.staging.current()["regions"]
                for identifier in identifiers:
                    region = self.staging.verify(identifier).get("region_id")
                    if current.get(region) != identifier:
                        raise ArtifactError(
                            f"{region} is no longer the current local staged version; refresh the area list", 409)
            results = []
            for identifier in identifiers:
                if progress:
                    progress("verifying_local", staging_id=identifier)
                source_manifest = self.staging.verify(identifier)
                source_hash = manifest_hash(self._directory(identifier))
                submission = self._submission(identifier, source_manifest, source_hash)
                result = self._submit_one(identifier, source_manifest, source_hash, submission, progress)
                results.append(result)
        return {"submissions": results}

    def _submit_one(self, identifier: str, source_manifest: dict, source_hash: str,
                    submission: dict, progress: Callable[..., None] | None) -> dict:
        if not self._gcloud:
            raise ArtifactError("gcloud is not installed or not on PATH", 503)
        root = f"{self.root}/{identifier}"
        directory = self.staging.artifacts_path(identifier)
        expected = {}
        for item in source_manifest["artifacts"]:
            path = directory / item["path"]
            sha256, md5_hash = self._local_checksums(path)
            if sha256 != item["sha256"] or path.stat().st_size != item["bytes"]:
                raise ArtifactError("Staging verification failed: artifact changed before upload", 409)
            expected[f"artifacts/{item['path']}"] = {**item, "md5_hash": md5_hash}
        manifest_bytes = (json.dumps(submission, indent=2, sort_keys=True) + "\n").encode()
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        manifest_md5 = base64.b64encode(hashlib.md5(manifest_bytes).digest()).decode("ascii")
        if progress:
            progress("uploading", staging_id=identifier, artifact_count=len(expected),
                     uploaded_artifacts=0)

        remote_manifest = self._cat(f"{root}/manifest.json", missing_ok=True)
        if remote_manifest is not None and remote_manifest != manifest_bytes:
            raise ArtifactError("Cloud submission ID already exists with different content", 409)

        existing = self._list(root)
        allowed = {f"{root}/{name}" for name in expected}
        if remote_manifest is not None:
            allowed.add(f"{root}/manifest.json")
        unexpected = sorted(set(existing) - allowed)
        if unexpected:
            raise ArtifactError("Cloud submission contains unexpected objects; refusing to overwrite", 409)

        uploaded = 0
        for object_name, item in expected.items():
            local = directory / item["path"]
            uri = f"{root}/{object_name}"
            remote = self._describe(uri)
            if remote is not None:
                self._match(remote, item["bytes"], item["sha256"], item["md5_hash"], uri)
            else:
                self._copy_create(local, uri, item["bytes"], item["sha256"], item["md5_hash"])
            uploaded += 1
            if progress:
                progress("uploading", staging_id=identifier, artifact_count=len(expected),
                         uploaded_artifacts=uploaded)

        if progress:
            progress("verifying_remote", staging_id=identifier, artifact_count=len(expected),
                     uploaded_artifacts=uploaded, verified_artifacts=0,
                     verification_step="before_manifest" if remote_manifest is None else "final")
        if remote_manifest is None:
            self._verify_artifacts(root, expected, progress, identifier)
            with tempfile.NamedTemporaryFile() as temp:
                temp.write(manifest_bytes)
                temp.flush()
                self._copy_create(Path(temp.name), f"{root}/manifest.json", len(manifest_bytes),
                                  manifest_sha, manifest_md5)
        self._verify_remote(root, expected, manifest_bytes, manifest_sha, progress, identifier)
        return {"submission_id": identifier, "staging_id": identifier,
                "region_id": source_manifest.get("region_id"), "status": "complete",
                "bucket_uri": f"{root}/", "source_manifest_sha256": source_hash,
                "artifact_count": len(expected), "bytes": sum(x["bytes"] for x in expected.values())}

    def _verify_remote(self, root: str, expected: dict, manifest_bytes: bytes,
                       manifest_sha: str, progress: Callable[..., None] | None = None,
                       identifier: str | None = None) -> None:
        names = {f"{root}/{name}" for name in expected} | {f"{root}/manifest.json"}
        actual = set(self._list(root))
        if actual != names:
            raise ArtifactError("Cloud verification failed: remote inventory differs", 409)
        self._verify_objects(root, expected, progress, identifier, "final")
        manifest_uri = f"{root}/manifest.json"
        remote = self._describe(manifest_uri)
        manifest_md5 = base64.b64encode(hashlib.md5(manifest_bytes).digest()).decode("ascii")
        self._match(remote, len(manifest_bytes), manifest_sha, manifest_md5, manifest_uri)
        if self._cat(manifest_uri) != manifest_bytes:
            raise ArtifactError("Cloud verification failed: submission manifest differs", 409)

    def _verify_artifacts(self, root: str, expected: dict,
                          progress: Callable[..., None] | None = None,
                          identifier: str | None = None) -> None:
        names = {f"{root}/{name}" for name in expected}
        if set(self._list(root)) != names:
            raise ArtifactError("Cloud verification failed: remote artifact inventory differs", 409)
        self._verify_objects(root, expected, progress, identifier, "before_manifest")

    def _verify_objects(self, root: str, expected: dict,
                        progress: Callable[..., None] | None = None,
                        identifier: str | None = None, step: str = "final") -> None:
        if progress:
            progress("verifying_remote", staging_id=identifier, artifact_count=len(expected),
                     verified_artifacts=0, verification_step=step)
        for index, (name, item) in enumerate(expected.items(), 1):
            uri = f"{root}/{name}"
            remote = self._describe(uri)
            self._match(remote, item["bytes"], item["sha256"], item["md5_hash"], uri)
            if progress:
                progress("verifying_remote", staging_id=identifier, artifact_count=len(expected),
                         verified_artifacts=index, verification_step=step)

    def _copy_create(self, local: Path, uri: str, size: int, sha256: str, md5_hash: str) -> None:
        result = self._run(["storage", "cp", "-q", "--if-generation-match=0",
                            f"--custom-metadata=sha256={sha256}", str(local), uri], check=False)
        if result.returncode == 0:
            return
        remote = self._describe(uri)
        if remote is not None:
            self._match(remote, size, sha256, md5_hash, uri)
            return
        detail = (result.stderr or "").strip()[-500:]
        raise ArtifactError(f"Cloud upload failed for {uri}: {detail or 'gcloud storage cp failed'}", 502)

    def _describe(self, uri: str) -> dict | None:
        result = self._run(["storage", "objects", "describe", "--raw", "--format=json", uri], check=False)
        if result.returncode != 0:
            return None
        try:
            value = json.loads(result.stdout)
        except (TypeError, ValueError) as exc:
            raise ArtifactError(f"Cloud returned invalid object metadata for {uri}", 502) from exc
        return value if isinstance(value, dict) else None

    @staticmethod
    def _match(remote: dict | None, size: int, sha256: str, md5_hash: str, uri: str) -> None:
        metadata = remote.get("metadata") if remote else None
        remote_md5 = remote.get("md5Hash") if remote else None
        try:
            remote_size = int(remote.get("size")) if remote else -1
        except (TypeError, ValueError):
            remote_size = -1
        if (remote_size != size or remote_md5 != md5_hash or not isinstance(metadata, dict)
                or metadata.get("sha256") != sha256):
            raise ArtifactError(f"Cloud verification failed: size, SHA-256 or MD5 differs for {uri}", 409)

    @staticmethod
    def _local_checksums(path: Path) -> tuple[str, str]:
        before = path.stat()
        sha256 = hashlib.sha256()
        md5 = hashlib.md5()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                sha256.update(block)
                md5.update(block)
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ArtifactError("Staged artifact changed while preparing cloud upload", 409)
        return sha256.hexdigest(), base64.b64encode(md5.digest()).decode("ascii")

    def _list(self, root: str) -> list[str]:
        result = self._run(["storage", "ls", f"{root}/**"], check=False)
        if result.returncode != 0:
            return []
        prefix = root + "/"
        return sorted(line.strip().rstrip("/") for line in result.stdout.splitlines()
                      if line.strip().startswith(prefix) and not line.strip().endswith("/"))

    def _cat(self, uri: str, missing_ok: bool = False) -> bytes | None:
        result = self._run(["storage", "cat", uri], check=False)
        if result.returncode != 0:
            if missing_ok:
                return None
            raise ArtifactError(f"Could not read cloud submission object {uri}", 502)
        return (result.stdout or "").encode() if isinstance(result.stdout, str) else result.stdout

    def _run(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        command = [self._gcloud or "gcloud", *args]
        try:
            result = self._runner(command, cwd=self.staging.data.parent, text=True,
                                  capture_output=True, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ArtifactError(f"Could not run gcloud storage: {type(exc).__name__}", 503) from exc
        if check and result.returncode:
            raise ArtifactError(f"gcloud storage failed: {(result.stderr or '').strip()[-500:]}", 502)
        return result

    def _submission(self, identifier: str, manifest: dict, source_hash: str) -> dict:
        return {"schema_version": SCHEMA, "submission_id": identifier, "staging_id": identifier,
                "region_id": manifest.get("region_id"), "staged_at": manifest.get("staged_at"),
                "source_manifest_sha256": source_hash, "source_manifest": manifest,
                "artifacts": manifest["artifacts"]}

    def _directory(self, identifier: str) -> Path:
        identifier = artifact_id(identifier)
        return self.staging.root / identifier

    @staticmethod
    def _ids(values: list[str]) -> list[str]:
        if not isinstance(values, list) or not values or len(values) > 100:
            raise ArtifactError("Select between one and 100 locally staged versions")
        identifiers = [artifact_id(value) for value in values]
        if len(set(identifiers)) != len(identifiers):
            raise ArtifactError("A staged version was selected more than once")
        return identifiers