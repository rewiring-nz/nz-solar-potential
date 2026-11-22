"""Content-verified provenance for an experimental prepared raster."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(content: dict) -> str:
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_mosaic(mosaic: Path, manifest_path: Path) -> dict:
    """Verify a sidecar and its output; source tiles need not still be cached."""
    manifest_path = Path(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read raster provenance: {manifest_path}") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"Invalid raster provenance: {manifest_path}")
    recorded = manifest.get("fingerprint")
    content = {key: value for key, value in manifest.items() if key != "fingerprint"}
    if (manifest.get("schema_version") != 1
            or not isinstance(recorded, str)
            or not re.fullmatch(r"[a-f0-9]{64}", recorded)
            or fingerprint(content) != recorded):
        raise ValueError(f"Invalid raster provenance fingerprint: {manifest_path}")
    output = manifest.get("output")
    mosaic = Path(mosaic)
    if (not isinstance(output, dict) or output.get("name") != mosaic.name
            or not isinstance(output.get("sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", output["sha256"])):
        raise ValueError(f"Invalid raster output provenance: {manifest_path}")
    try:
        actual = file_sha256(mosaic)
    except OSError as exc:
        raise ValueError(f"Cannot read provenance-tracked mosaic: {mosaic}") from exc
    if actual != output["sha256"]:
        raise ValueError(f"Mosaic content does not match provenance: {mosaic}")
    return manifest


def derived_manifest(parent: dict, output: Path, operation: str) -> dict:
    """Describe a derived raster without claiming it is the original mosaic."""
    if not isinstance(operation, str) or not operation:
        raise ValueError("A raster derivation operation is required")
    lineage = parent.get("derivations", [])
    if not isinstance(lineage, list):
        raise ValueError("Invalid raster derivation history")
    content = {key: value for key, value in parent.items()
               if key not in ("fingerprint", "output", "derivations")}
    content["derivations"] = [*lineage, {
        "operation": operation,
        "parent_fingerprint": parent["fingerprint"],
        "parent_output": parent["output"],
    }]
    output = Path(output)
    content["output"] = {"name": output.name, "sha256": file_sha256(output)}
    return {**content, "fingerprint": fingerprint(content)}


def tracked_raster_inputs(paths: dict[str, Path], *, region: str | None = None) -> dict[str, str]:
    """Return scientific identities for verified sidecars, omitting legacy inputs."""
    tracked = {}
    for role, value in paths.items():
        path = Path(value)
        sidecar = path.with_name(path.name + ".source.json")
        if not sidecar.exists() and not sidecar.is_symlink():
            continue
        manifest = verify_mosaic(path, sidecar)
        if manifest.get("role") != role or (region is not None and role != "wide_dem"
                                             and manifest.get("region") != region):
            raise ValueError(f"Raster provenance does not match {region} {role}: {sidecar}")
        sources = manifest.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError(f"Raster provenance has no source identities: {sidecar}")
        identities = []
        for source in sources:
            if not isinstance(source, dict) or any(
                not isinstance(source.get(key), str) or not source[key]
                for key in ("collection_url", "collection_id", "item_id", "asset_key")
            ) or not isinstance(source.get("sha256"), str) or not re.fullmatch(
                r"[a-f0-9]{64}", source["sha256"]
            ):
                raise ValueError(f"Invalid source identity in raster provenance: {sidecar}")
            identities.append({key: source[key] for key in
                               ("collection_url", "collection_id", "item_id", "asset_key", "sha256")})
        processing = manifest.get("processing")
        if not isinstance(processing, dict):
            raise ValueError(f"Invalid raster processing provenance: {sidecar}")
        content = {
            "role": role, "layer_id": manifest.get("layer_id"),
            "footprint_wgs84": manifest.get("footprint_wgs84"),
            "processing": processing, "sources": sorted(
                identities, key=lambda source: (source["collection_url"], source["collection_id"],
                                                source["item_id"], source["asset_key"])),
            "output_sha256": manifest["output"]["sha256"],
        }
        if "footprints_wgs84" in manifest:
            content["footprints_wgs84"] = manifest["footprints_wgs84"]
        tracked[role] = fingerprint(content)
    return tracked
