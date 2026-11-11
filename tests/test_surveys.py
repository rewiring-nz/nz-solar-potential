import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.surveys import survey_for


CHRISTCHURCH_CENTRAL = [172.610948, -43.541107, 172.653937, -43.518458]
DSM_ID = 123194
IMAGERY_ID = 123118


def _extent(bounds):
    west, south, east, north = bounds
    return {
        "type": "Polygon",
        "coordinates": [[
            [west, south], [east, south], [east, north],
            [west, north], [west, south],
        ]],
    }


def _metadata(layer_id, title, bounds, description_html=""):
    return {"id": layer_id, "title": title, "data": {"extent": _extent(bounds)},
            "description_html": description_html}


def _client(bounds=None):
    bounds = bounds or [172.38, -43.67, 172.82, -43.38]
    dsm = _metadata(
        DSM_ID, "Canterbury - Christchurch LiDAR 1m DSM (2024-2025)", bounds,
        '<p>The DEM is available as layer <a href="https://data.linz.govt.nz/layer/123193">'
        "Christchurch LiDAR 1m DEM (2024-2025)</a>.</p>"
        '<p>The Index Tiles are available as layer <a href="https://data.linz.govt.nz/layer/123195">'
        "Christchurch LiDAR Index Tiles (2024-2025)</a>.</p>",
    )
    imagery = _metadata(
        IMAGERY_ID, "Christchurch 0.075m Urban Aerial Photos (2025)", bounds,
    )
    layers = {
        DSM_ID: dsm,
        IMAGERY_ID: imagery,
    }
    calls = []

    def get_json(url):
        calls.append(url)
        if "/layers/?" in url:
            if "aerial" in url:
                return [{"id": IMAGERY_ID, "title": imagery["title"]}]
            if "DSM" in url:
                return [{"id": DSM_ID, "title": dsm["title"]}]
            return []
        layer_id = int(url.rstrip("/").rsplit("/", 1)[-1])
        return layers[layer_id]

    return get_json, calls


def test_discovers_covering_christchurch_sources_and_related_layers(tmp_path):
    get_json, _ = _client()
    result = survey_for(CHRISTCHURCH_CENTRAL, "christchurch-city/christchurch/christchurch-central",
                        data_dir=tmp_path, get_json=get_json, now=100)

    assert result["elevation"] == "lidar"
    assert result["dsm_layer"] == DSM_ID
    assert result["dem_layer"] == 123193
    assert result["lidar_tile_index_layer"] == 123195
    assert result["imagery_layer"] == IMAGERY_ID
    assert result["reference_imagery_layer"] == IMAGERY_ID
    cache = tmp_path / "sources" / "linz" / "survey_layer_catalog.json"
    assert json.loads(cache.read_text())["layers"][str(DSM_ID)]["metadata"]["title"].endswith(
        "(2024-2025)")


def test_rejects_layers_whose_extent_does_not_cover_the_whole_area(tmp_path):
    partial = [172.38, -43.67, 172.62, -43.38]
    get_json, _ = _client(partial)
    result = survey_for(CHRISTCHURCH_CENTRAL, "christchurch-central",
                        data_dir=tmp_path, get_json=get_json, now=100)

    assert result["elevation"] == "synthetic"
    assert result["dsm_layer"] is None


def test_pinned_sources_are_used_and_revalidated(tmp_path):
    get_json, calls = _client()
    layers = {
        "dsm_layer": DSM_ID,
        "dem_layer": 123193,
        "lidar_tile_index_layer": 123195,
        "imagery_layer": IMAGERY_ID,
        "reference_imagery_layer": IMAGERY_ID,
    }
    result = survey_for(CHRISTCHURCH_CENTRAL, "christchurch-central",
                        data_dir=tmp_path, get_json=get_json, now=100,
                        overrides=layers)
    assert result["dsm_layer"] == DSM_ID
    assert result["dem_layer"] == 123193
    assert result["lidar_tile_index_layer"] == 123195
    assert not any("/layers/?" in url for url in calls)

    partial_get_json, _ = _client([172.38, -43.67, 172.62, -43.38])
    try:
        survey_for(CHRISTCHURCH_CENTRAL, "christchurch-central",
                   data_dir=tmp_path / "bad-pin", get_json=partial_get_json, now=100,
                   overrides=layers)
    except LookupError as exc:
        assert "no longer covers" in str(exc)
    else:
        raise AssertionError("a pinned layer that no longer covers the area was accepted")


def test_catalogue_failures_are_reported(tmp_path):
    def fail(_url):
        raise LookupError("network unavailable")

    try:
        survey_for(CHRISTCHURCH_CENTRAL, "christchurch-central",
                   data_dir=tmp_path, get_json=fail, now=100)
    except LookupError as exc:
        assert str(exc) == "network unavailable"
    else:
        raise AssertionError("catalogue failure was hidden")


if __name__ == "__main__":
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn(Path("/tmp") / f"survey-test-{name}")
                print("  pass", name)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print("  FAIL", name, exc)
    sys.exit(1 if failures else 0)
