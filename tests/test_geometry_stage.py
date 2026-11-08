"""A face-refining stage may improve a roof but never lose one."""
import sys
from pathlib import Path

from shapely.geometry import Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.build_layout_geojson as blg


def _faces():
    return [dict(geometry=box(0, 0, 5, 5), area_m2=25.0), dict(geometry=box(5, 0, 10, 5), area_m2=25.0)]


def test_empty_face_is_dropped():
    def stage(facets):
        return [facets[0], dict(facets[1], geometry=Polygon())]
    out = blg._geometry_stage(1, stage, _faces())
    assert len(out) == 1 and out[0]["geometry"].area == 25.0


def test_raising_stage_leaves_faces_alone():
    def stage(facets):
        raise ValueError("TopologyException")
    faces = _faces()
    assert blg._geometry_stage(1, stage, faces) is faces


def test_invalid_face_is_repaired():
    bowtie = Polygon([(0, 0), (4, 4), (4, 0), (0, 4)])
    out = blg._geometry_stage(1, lambda f: [dict(f[0], geometry=bowtie)], _faces())
    assert len(out) == 1 and out[0]["geometry"].is_valid and out[0]["geometry"].area > 0
    assert abs(out[0]["area_m2"] - out[0]["geometry"].area) < 1e-9


def test_all_empty_keeps_the_input():
    faces = _faces()
    assert blg._geometry_stage(1, lambda f: [dict(x, geometry=Polygon()) for x in f], faces) is faces


def test_worker_closes_aliased_rasters_once():
    from unittest.mock import Mock, patch
    dsm, photo = Mock(), Mock()
    with patch.dict(blg._CTX, {"dsm_ds": dsm, "imagery_ds": photo, "photo_ds": photo},
                    clear=True):
        blg._close_worker_rasters()
        blg._close_worker_rasters()
        dsm.close.assert_called_once_with()
        photo.close.assert_called_once_with()
        assert not blg._CTX


def test_worker_registers_raster_cleanup_before_interpreter_teardown():
    import os
    import tempfile
    from unittest.mock import Mock, patch
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        paths = {key: directory / key for key in
                 ("dsm", "imagery", "reference_imagery", "outlines")}
        for path in paths.values():
            path.touch()
        paths["dir"] = directory
        datasets = [Mock(), Mock(), Mock()]
        registered = []
        real_finalize = blg.Finalize

        def register(*args, **kwargs):
            finalizer = real_finalize(*args, **kwargs)
            registered.append(finalizer)
            return finalizer

        with (patch.dict(blg._CTX, {}, clear=True),
              patch.dict(os.environ, {"SOLAR_PIPELINE_PHOTO": "map"}),
              patch.object(blg, "area_paths", return_value=paths),
              patch.object(blg.rasterio, "open", side_effect=datasets),
              patch.object(blg, "_hz_load_far_dem", return_value=(None, None, None)),
              patch.object(blg.gpd, "read_file"),
              patch.object(blg, "fitted_outlines"),
              patch.object(blg, "PointCloudSource"),
              patch.object(blg, "Finalize", side_effect=register) as finalize):
            try:
                blg._init_worker("test", Mock())
                finalize.assert_called_once_with(None, blg._close_worker_rasters, exitpriority=10)
                registered[0]()
                for dataset in datasets:
                    dataset.close.assert_called_once_with()
            finally:
                for finalizer in registered:
                    finalizer()


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("  pass ", name)
            except AssertionError as e:
                fails += 1
                print("  FAIL ", name, e)
    sys.exit(1 if fails else 0)
