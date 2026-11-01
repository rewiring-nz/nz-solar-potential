"""Synthetic tests for actual-cache and combined-output layer catalogues."""

import http.server
import json
import sqlite3
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import laspy
import numpy as np
import rasterio
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import combine_regions
from src.map_layer_catalog import build_local_catalog, build_static_catalog


def _assert_contract(catalogue: dict) -> None:
    assert catalogue["version"] == 1
    assert isinstance(catalogue["warnings"], list)
    for layer in catalogue["layers"]:
        assert set((
            "id", "label", "group", "representation", "colour", "description",
            "regions", "bounds", "defaultVisible",
        )) <= layer.keys()
        assert layer["group"] in {"source", "derived", "analysis"}
        assert layer["representation"] in {"footprint", "vector", "vector-tiles", "raster-tiles"}
        assert layer["colour"].startswith("#")
        assert len(layer["bounds"]) == 4
        assert layer["defaultVisible"] is False
        endpoints = sum(key in layer for key in ("data", "url", "tiles"))
        assert endpoints == 1
        if "data" in layer:
            assert layer["data"]["type"] == "FeatureCollection"
            for feature in layer["data"]["features"]:
                properties = feature["properties"]
                assert properties["layer_id"] == layer["id"]
                assert properties["status"] in {"cached", "cached_subset", "built", "emitted"}
                path = properties.get("cached_path", "")
                assert not path.startswith("/") and ".." not in path.split("/")
        elif layer["representation"] == "vector":
            assert not layer["url"].startswith(("/", "http:", "https:"))
            assert ".." not in layer["url"].split("/")
        elif layer["representation"] == "vector-tiles":
            assert layer["sourceLayer"] and layer["kind"]
        else:
            assert layer["representation"] == "raster-tiles"
            assert all(key in layer for key in ("tileSize", "minzoom", "maxzoom"))


def _write_laz(path: Path, xs: list[float], ys: list[float]) -> None:
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales = np.array([0.01, 0.01, 0.01])
    las = laspy.LasData(header)
    las.x = np.array(xs)
    las.y = np.array(ys)
    las.z = np.full(len(xs), 100.0)
    uncompressed = path.with_suffix(".las")
    las.write(uncompressed)
    uncompressed.replace(path)


def _write_raster(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path, "w", driver="GTiff", width=4, height=3, count=1, dtype="uint8",
        crs="EPSG:2193", transform=from_origin(1250000, 5001000, 10, 10),
    ) as dataset:
        dataset.write(np.ones((1, 3, 4), dtype=np.uint8))


def _cells(path: Path, tile: str = "12/2553/1536/home") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({tile: {"n": 1}}), encoding="utf-8")


def test_local_catalog_uses_cached_headers_deduplicates_and_marks_subsets():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        regions = data / "regions"
        for region in ("alpha", "beta"):
            (regions / region).mkdir(parents=True)
            (regions / region / "pointcloud_tiles.txt").write_text(
                "shared.laz\npartial.laz\nmissing.laz\n", encoding="utf-8",
            )

        pointcloud = data / "pointcloud"
        pointcloud.mkdir()
        _write_laz(pointcloud / "shared.copc.laz", [1250000, 1250100], [5000000, 5000100])
        _write_laz(pointcloud / "partial.laz", [1250010, 1250020], [5000010, 5000020])
        _write_laz(pointcloud / "packed-copc.copc.laz", [1250030, 1250040], [5000030, 5000040])
        (pointcloud / "broken.laz").write_bytes(b"not a LAS file")
        (pointcloud / ".packed.json").write_text(
            '["partial.laz","packed-copc.copc.laz"]', encoding="utf-8",
        )
        for region in ("alpha", "beta"):
            pack = regions / region / "pack"
            (pack / "points").mkdir(parents=True)
            _write_laz(pack / "points" / "partial.laz", [1250012, 1250018], [5000012, 5000018])
            (pack / "pack.json").write_text('{"tiles":["partial.laz"]}', encoding="utf-8")

        _write_raster(regions / "alpha" / "dsm_mosaic.tif")
        (regions / "beta" / "dsm_mosaic.tif").symlink_to(regions / "alpha" / "dsm_mosaic.tif")
        bad_raster = regions / "beta" / "imagery_mosaic.tif"
        bad_raster.write_text("not a raster", encoding="utf-8")

        gpkg_dir = data / "sources" / "linz" / "layer-7" / "council"
        gpkg_dir.mkdir(parents=True)
        gpkg = gpkg_dir / "building_outlines.gpkg"
        with sqlite3.connect(gpkg) as connection:
            connection.execute(
                "CREATE TABLE gpkg_contents (table_name TEXT, data_type TEXT, srs_id INTEGER, "
                "min_x REAL, min_y REAL, max_x REAL, max_y REAL)"
            )
            connection.execute(
                "INSERT INTO gpkg_contents VALUES ('buildings','features',2193,1250000,5000000,1250100,5000100)"
            )
        (gpkg_dir / "manifest.json").write_text(json.dumps({
            "layer": 7, "territorial_authority": "Council", "features": 12,
        }), encoding="utf-8")
        for region in ("alpha", "beta"):
            (regions / region / "locality.json").write_text(json.dumps({
                "linz": {"territorial_authority": "Council"},
            }), encoding="utf-8")

        _cells(data / "out" / "alpha" / "cells.json")
        (data / "out" / "alpha" / "summary.json").write_text(
            '{"region":"alpha"}', encoding="utf-8",
        )
        _cells(data / "out" / "beta" / "cells.json")
        (data / "out" / "beta" / "summary.json").write_text(
            '{"region":"beta"}', encoding="utf-8",
        )
        vector = regions / "alpha" / "solar_potential.geojson"
        vector.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        (data / "build_summary.json").write_text(
            '{"regions":["alpha"]}', encoding="utf-8",
        )
        (data / "buildings.pmtiles").write_bytes(b"combined buildings")
        (data / "panel_layouts.pmtiles").write_bytes(b"combined layouts")
        heatmap = data / "heatmap_tiles"
        (heatmap / "9" / "1" / "2.png").parent.mkdir(parents=True)
        (heatmap / "9" / "1" / "2.png").write_bytes(b"png")
        (heatmap / "meta.json").write_text(
            '{"minzoom":9,"maxzoom":17,"tileSize":256}', encoding="utf-8",
        )

        catalogue = build_local_catalog(data)
        layers = {layer["id"]: layer for layer in catalogue["layers"]}
        _assert_contract(catalogue)
        assert catalogue["version"] == 1
        assert catalogue["availability"] == {"source": "local", "local_cache": True}
        assert "source-lidar" in layers
        lidar = layers["source-lidar"]["data"]["features"]
        full = [feature for feature in lidar if feature["properties"]["subset"] is False]
        subsets = [feature for feature in lidar if feature["properties"]["subset"] is True]
        assert len(full) == 1, (lidar, catalogue["warnings"])
        assert full[0]["properties"]["format"] == "COPC LAZ"
        assert full[0]["properties"]["regions"] == ["alpha", "beta"]
        assert len(subsets) == 2
        assert all(feature["properties"]["regions"] in (["alpha"], ["beta"]) for feature in subsets)
        assert all("missing.laz" not in feature["properties"]["name"] for feature in lidar)
        assert all("packed-copc" not in feature["properties"]["name"] for feature in lidar)

        dsm = layers["source-dsm"]["data"]["features"]
        assert len(dsm) == 1
        assert dsm[0]["properties"]["regions"] == ["alpha", "beta"]
        outlines = layers["source-council-outlines"]["data"]["features"]
        assert len(outlines) == 1
        assert outlines[0]["properties"]["regions"] == ["alpha", "beta"]
        assert outlines[0]["properties"]["linz_layer"] == 7
        assert outlines[0]["properties"]["extent_kind"] == "gpkg_metadata_extent"
        assert {feature["properties"]["status"] for feature in subsets} == {"cached_subset"}
        derived = layers["derived-solar-potential:alpha"]
        assert derived["representation"] == "vector"
        assert derived["url"] == "data/regions/alpha/solar_potential.geojson"
        assert layers["analysis-panels"]["url"] == "data/panel_layouts.pmtiles"
        assert layers["analysis-panels"]["sourceLayer"] == "layout"
        assert layers["analysis-panels"]["kind"] == "panel"
        assert layers["analysis-facets"]["kind"] == "facet"
        assert layers["analysis-facets"]["order"] < layers["analysis-obstructions"]["order"]
        assert "pitch" in layers["analysis-facets"]["description"]
        assert layers["analysis-outlines"]["url"] == "data/buildings.pmtiles"
        assert layers["analysis-outlines"]["sourceLayer"] == "buildings"
        assert layers["analysis-outlines"]["kind"] == "building"
        assert layers["analysis-heatmap"]["tiles"] == "data/heatmap_tiles/{z}/{x}/{y}.png"
        assert layers["analysis-heatmap"]["tileSize"] == 256
        assert layers["analysis-heatmap"]["minzoom"] == 9
        assert layers["analysis-heatmap"]["maxzoom"] == 17
        analysis = [layer for layer in catalogue["layers"] if layer["group"] == "analysis"]
        assert all(layer["regions"] == ["alpha"] for layer in analysis)
        assert [layer["order"] for layer in analysis] == sorted(
            (layer["order"] for layer in analysis), reverse=True,
        )
        assert any("imagery_mosaic.tif" in warning for warning in catalogue["warnings"])
        assert any("broken.laz" in warning for warning in catalogue["warnings"])


def test_invalid_packed_registry_fails_closed_for_shared_tiles():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        pointcloud = data / "pointcloud"
        pointcloud.mkdir(parents=True)
        _write_laz(pointcloud / "possibly-packed.laz", [1250000, 1250010], [5000000, 5000010])
        (pointcloud / ".packed.json").write_text("{", encoding="utf-8")

        catalogue = build_local_catalog(data)
        assert "source-lidar" not in {layer["id"] for layer in catalogue["layers"]}
        assert any("invalid packed-subset registry" in warning for warning in catalogue["warnings"])


def test_static_catalog_is_restricted_to_selected_emitted_regions_and_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        out = root / "out"
        served = root / "served"
        served.mkdir()
        _cells(out / "selected" / "cells.json")
        _cells(out / "not-selected" / "cells.json", "12/2554/1536/home")
        for name in ("buildings.pmtiles", "panel_layouts.pmtiles"):
            (served / name).write_bytes(b"fixture")
        (served / "heatmap_tiles" / "12" / "0" / "0.png").parent.mkdir(parents=True)
        (served / "heatmap_tiles" / "12" / "0" / "0.png").write_bytes(b"png")
        (served / "heatmap_tiles" / "meta.json").write_text(
            '{"minzoom":12,"maxzoom":16,"tileSize":256}', encoding="utf-8",
        )

        catalogue = build_static_catalog(["selected"], out, served)
        layers = {layer["id"]: layer for layer in catalogue["layers"]}
        _assert_contract(catalogue)
        assert catalogue["availability"] == {"source": "static", "local_cache": False}
        assert layers["derived-building-coverage"]["regions"] == ["selected"]
        assert layers["analysis-outlines"]["url"] == "data/buildings.pmtiles"
        assert layers["analysis-panels"]["url"] == "data/panel_layouts.pmtiles"
        assert layers["analysis-heatmap"]["tiles"] == "data/heatmap_tiles/{z}/{x}/{y}.png"
        assert "analysis-building-cells" not in layers
        assert all(layer["regions"] == ["selected"] for layer in layers.values())
        assert all("source-" not in layer["id"] for layer in layers.values())
        assert all(
            not layer.get("url", "").startswith(("../", "/"))
            for layer in layers.values()
        )
        assert all(not layer.get("tiles", "").startswith(("../", "/")) for layer in layers.values())


def test_combine_writes_static_catalog_for_its_region_selection():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        out = root / "out"
        dest = root / "data"
        dest.mkdir()
        selected = "catalog_test_selected"
        omitted = "catalog_test_omitted"
        for region in (selected, omitted):
            region_dir = out / region
            region_dir.mkdir(parents=True)
            (region_dir / "summary.json").write_text(json.dumps({
                "region": region, "assumptions": {}, "n": 1, "n_est": 1,
                "panel_count": 1, "kwp": 1, "kwh": 1,
            }), encoding="utf-8")
            _cells(region_dir / "cells.json")

        def fake_join(_inputs, target, _workdir):
            Path(target).write_bytes(b"pmtiles")

        def fake_cells(_regions, _out_root, work):
            (Path(work) / "building_cells.pmtiles").write_bytes(b"pmtiles")
            return 1

        def fake_heatmap(_regions, _out_root, work):
            heatmap = Path(work) / "heatmap_tiles"
            tile = heatmap / "9" / "1" / "2.png"
            tile.parent.mkdir(parents=True)
            tile.write_bytes(b"png")
            (heatmap / "meta.json").write_text(
                '{"minzoom":9,"maxzoom":17,"tileSize":256}', encoding="utf-8",
            )
            return 1, 0

        fake_cells.density_p95 = 0.0
        fake_cells.density_p95_by_lod = {}

        with (
            patch.object(combine_regions, "_tile_join", side_effect=fake_join),
            patch.object(combine_regions, "_combine_cells", new=fake_cells),
            patch.object(combine_regions, "_combine_detail", return_value=0),
            patch.object(combine_regions, "_combine_heatmap", side_effect=fake_heatmap),
            patch.object(combine_regions, "_combine_addresses", return_value=(0, 0)),
            patch.object(combine_regions, "_combine_curves", return_value=0),
        ):
            combine_regions.combine(
                [selected], out_root=out, dest=dest, build_markup_lines=False,
            )

        catalogue = json.loads((dest / "map_layers.json").read_text(encoding="utf-8"))
        layers = {layer["id"]: layer for layer in catalogue["layers"]}
        _assert_contract(catalogue)
        assert layers["derived-building-coverage"]["regions"] == [selected]
        assert layers["analysis-outlines"]["regions"] == [selected]
        assert layers["analysis-panels"]["regions"] == [selected]
        assert layers["analysis-heatmap"]["regions"] == [selected]
        assert layers["analysis-outlines"]["url"] == "data/buildings.pmtiles"
        assert layers["analysis-panels"]["url"] == "data/panel_layouts.pmtiles"
        assert layers["analysis-heatmap"]["tiles"] == "data/heatmap_tiles/{z}/{x}/{y}.png"
        assert omitted not in json.dumps(catalogue)


def test_static_catalog_warns_when_selected_cell_file_is_malformed():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        path = out / "broken" / "cells.json"
        path.parent.mkdir(parents=True)
        path.write_text("{", encoding="utf-8")
        catalogue = build_static_catalog(["broken"], out, Path(tmp))
        assert not catalogue["layers"]
        assert any("could not read derived cell coverage" in warning for warning in catalogue["warnings"])


def test_local_catalog_does_not_infer_combined_layers_from_region_summaries():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        _cells(data / "out" / "built-but-not-combined" / "cells.json")
        (data / "out" / "built-but-not-combined" / "summary.json").write_text(
            '{"region":"built-but-not-combined"}', encoding="utf-8",
        )
        regional = data / "out" / "built-but-not-combined"
        (regional / "buildings.pmtiles").write_bytes(b"regional buildings")
        (regional / "panel_layouts.pmtiles").write_bytes(b"regional layouts")

        catalogue = build_local_catalog(data)
        assert not [layer for layer in catalogue["layers"] if layer["group"] == "analysis"]


def _get(url: str) -> tuple[int, dict, dict]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, dict(response.headers), json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), {}


def test_preview_server_computes_local_catalog_only_on_its_explicit_route():
    from tools.pipeline_serve import LOCAL_CATALOG_PATH, RangeHandler
    from tools.pipeline_webui.server import WebHandler

    assert WebHandler.local_catalog is False
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        gpkg_dir = root / "data" / "sources" / "linz" / "layer-7" / "council"
        gpkg_dir.mkdir(parents=True)
        with sqlite3.connect(gpkg_dir / "building_outlines.gpkg") as connection:
            connection.execute(
                "CREATE TABLE gpkg_contents (table_name TEXT, data_type TEXT, srs_id INTEGER, "
                "min_x REAL, min_y REAL, max_x REAL, max_y REAL)"
            )
            connection.execute(
                "INSERT INTO gpkg_contents VALUES ('b','features',2193,1250000,5000000,1250100,5000100)"
            )
        (gpkg_dir / "manifest.json").write_text('{"layer":7,"territorial_authority":"Council"}',
                                                encoding="utf-8")
        handler = lambda *a, **kw: RangeHandler(*a, directory=str(root), **kw)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            status, headers, body = _get(base + LOCAL_CATALOG_PATH + "?v=1")
            assert status == 200
            assert headers["Cache-Control"] == "no-store"
            _assert_contract(body)
            assert body["availability"] == {"source": "local", "local_cache": True}
            assert [layer["id"] for layer in body["layers"]] == ["source-council-outlines"]
            assert not (root / "data" / "local_map_layers.json").exists()
            assert _get(base + "/data/map_layers.json")[0] == 404
        finally:
            server.shutdown()
            server.server_close()


def test_publication_allowlists_ship_explorer_assets_but_never_local_catalogue():
    from tools.publish_preview import FRONTEND_FILES
    from tools.served_outputs import SERVED_FILES

    for name in ("rooftop-solar.html", "preview.html", "data-explorer.html",
                 "data_explorer.js", "map_layers.js", "map_layers.css"):
        assert name in FRONTEND_FILES
    assert "map_layers.json" in SERVED_FILES
    assert "local_map_layers.json" not in (*FRONTEND_FILES, *SERVED_FILES)


if __name__ == "__main__":
    test_local_catalog_uses_cached_headers_deduplicates_and_marks_subsets()
    test_invalid_packed_registry_fails_closed_for_shared_tiles()
    test_static_catalog_is_restricted_to_selected_emitted_regions_and_files()
    test_combine_writes_static_catalog_for_its_region_selection()
    test_static_catalog_warns_when_selected_cell_file_is_malformed()
    test_local_catalog_does_not_infer_combined_layers_from_region_summaries()
    test_preview_server_computes_local_catalog_only_on_its_explicit_route()
    test_publication_allowlists_ship_explorer_assets_but_never_local_catalogue()
    print("map layer catalogue tests passed")
