"""Translate an already selected LINZ survey into experimental raster requests.

Mappings are explicit operator-supplied assertions, not inferred from titles.
This module does not itself change survey selection or enable a fetch path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from src.raster_acquisition_plan import RasterRequest, RasterSource, _bounds


@dataclass(frozen=True)
class LayerMapping:
    layer_id: int
    role: str
    collection_url: str
    collection_id: str
    asset_key: str
    evidence: str
    index_layer: int | None = None


def _validate_mappings(mappings: list[LayerMapping]) -> dict[tuple[int, str], LayerMapping]:
    if not isinstance(mappings, list):
        raise ValueError("Layer mappings must be a list")
    mapping_by_layer: dict[tuple[int, str], LayerMapping] = {}
    for mapping in mappings:
        if not isinstance(mapping, LayerMapping):
            raise ValueError("Invalid layer mapping")
        if type(mapping.layer_id) is not int or mapping.layer_id <= 0:
            raise ValueError("Mapping layer ID must be a positive integer")
        if mapping.role not in ("dsm", "imagery", "reference_imagery"):
            raise ValueError("Invalid mapping role")
        if mapping.index_layer is not None and (
                type(mapping.index_layer) is not int or mapping.index_layer <= 0):
            raise ValueError("Invalid mapping tile index layer")
        if not all(isinstance(value, str) and value.strip() for value in (
            mapping.collection_url, mapping.collection_id, mapping.asset_key, mapping.evidence
        )):
            raise ValueError("Mapping requires collection URL, ID, asset key and evidence")
        parsed = urlparse(mapping.collection_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Mapping collection URL must be an absolute HTTPS URL")
        key = (mapping.layer_id, mapping.role)
        if key in mapping_by_layer:
            raise ValueError(f"Ambiguous mappings for layer {mapping.layer_id} ({mapping.role})")
        mapping_by_layer[key] = mapping
    return mapping_by_layer


def load_layer_mappings(path: Path) -> list[LayerMapping]:
    """Load explicitly asserted LDS-to-STAC mappings; never infer equivalence."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read raster source mappings {path}: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"version", "mappings"} or data["version"] != 1 \
            or type(data["version"]) is not int or not isinstance(data["mappings"], list):
        raise ValueError(f"Invalid raster source mapping document: {path}")
    fields = {"layer_id", "role", "collection_url", "collection_id", "asset_key", "evidence"}
    mappings = []
    for entry in data["mappings"]:
        if not isinstance(entry, dict) or not fields <= set(entry) or set(entry) - fields - {"index_layer"}:
            raise ValueError(f"Invalid raster source mapping entry in {path}")
        mappings.append(LayerMapping(**entry))
    _validate_mappings(mappings)
    return mappings


def mappings_from_snapshot(snapshot: dict, regions: list[str]) -> list[LayerMapping]:
    """Reconstruct explicit mappings from a caller-verified approved snapshot."""
    from src.raster_plan_handoff import plan_for_regions

    batch = plan_for_regions(snapshot, regions)["batch"]
    if batch is None:
        return []
    by_layer = {}
    fallbacks = set()
    for item in batch["inputs"]:
        key = (item.get("layer_id"), item.get("role"))
        source = item.get("source")
        if source is None:
            fallbacks.add(key)
            continue
        fields = {"collection_url", "expected_collection_id", "asset_key"}
        if not isinstance(source, dict) or not fields <= set(source) or set(source) - fields - {"index_layer"}:
            raise ValueError("Invalid snapshot raster source")
        mapping = LayerMapping(key[0], key[1], source["collection_url"],
                               source["expected_collection_id"], source["asset_key"],
                               item.get("mapping_evidence"), source.get("index_layer"))
        previous = by_layer.get(key)
        if previous is not None and previous != mapping:
            raise ValueError(f"Conflicting snapshot mappings for layer {key[0]} ({key[1]})")
        by_layer[key] = mapping
    if fallbacks & by_layer.keys():
        raise ValueError("Snapshot has mapped and fallback inputs for the same layer and role")
    mappings = list(by_layer.values())
    _validate_mappings(mappings)
    return mappings


def survey_requests(region: str, footprints: list, survey: dict, *,
                    mappings: list[LayerMapping], include_imagery: bool = True) -> dict:
    """Keep selected LDS layers and explicitly report absent or fallback inputs."""
    if not isinstance(region, str) or not region:
        raise ValueError("A region is required")
    if not isinstance(footprints, (list, tuple)) or not footprints:
        raise ValueError("At least one acquisition footprint is required")
    bounds = tuple(_bounds(value, f"{region} footprint") for value in footprints)
    if not isinstance(survey, dict) or survey.get("elevation") not in ("lidar", "synthetic"):
        raise ValueError("Survey must specify lidar or synthetic elevation")
    if type(include_imagery) is not bool:
        raise ValueError("include_imagery must be boolean")
    mapping_by_layer = _validate_mappings(mappings)

    requests = []
    statuses = []
    roles = (("dsm", "dsm_layer"), ("imagery", "imagery_layer"),
             ("reference_imagery", "reference_imagery_layer"))
    for role, layer_field in roles:
        layer_id = survey.get(layer_field)
        if role == "dsm" and survey.get("raster_dsm_layer") is not None:
            if survey["elevation"] == "synthetic":
                raise ValueError("Synthetic elevation cannot also select a raster DSM layer")
            layer_id = survey["raster_dsm_layer"]
        if layer_id is not None and (type(layer_id) is not int or layer_id <= 0):
            raise ValueError(f"Invalid survey {layer_field}")
        if role == "dsm" and survey["elevation"] == "synthetic":
            if layer_id is not None:
                raise ValueError("Synthetic elevation cannot also select a DSM layer")
            statuses.append({"role": role, "state": "synthetic", "layer_id": None})
            continue
        if role != "dsm" and not include_imagery:
            statuses.append({"role": role, "state": "excluded", "layer_id": layer_id})
            continue
        if layer_id is None:
            if role == "dsm":
                raise ValueError("LiDAR elevation requires a DSM layer")
            statuses.append({"role": role, "state": "unavailable", "layer_id": None})
            continue
        if role == "reference_imagery" and layer_id == survey.get("imagery_layer"):
            statuses.append({"role": role, "state": "same_as_imagery", "layer_id": layer_id})
            continue
        survey_name = survey.get("name")
        if role == "dsm" and layer_id != survey.get("dsm_layer"):
            survey_name = f"LINZ raster layer {layer_id}" + (
                f"; reference survey {survey_name}" if survey_name else "")
        mapping = mapping_by_layer.get((layer_id, role))
        if mapping is None:
            reason = f"No explicit STAC mapping for {role} LINZ layer {layer_id}"
            requests.append(RasterRequest(region, role, bounds, None, layer_id, reason,
                                          survey_name=survey_name))
            statuses.append({"role": role, "state": "exports_fallback", "layer_id": layer_id,
                             "reason": reason})
        else:
            source = RasterSource(mapping.collection_url, mapping.asset_key, mapping.collection_id,
                                  mapping.index_layer)
            requests.append(RasterRequest(region, role, bounds, source, layer_id,
                                          survey_name=survey_name,
                                          mapping_evidence=mapping.evidence,
                                          allow_partial=bool(survey.get("imagery_partial"))
                                          if role == "imagery" else False))
            statuses.append({"role": role, "state": "mapped", "layer_id": layer_id,
                             "mapping_evidence": mapping.evidence})
    return {"requests": requests, "inputs": statuses, "survey_name": survey.get("name")}
