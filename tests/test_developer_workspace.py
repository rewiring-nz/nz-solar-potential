"""Tests for isolated developer checkouts and shared source caching."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import developer_workspace


class DeveloperWorkspaceTests(unittest.TestCase):
    def test_checkouts_and_run_roots_are_separate_and_commit_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            origin = root / "origin.git"
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            (repo / "source.txt").write_text("pinned\n", encoding="utf-8")
            subprocess.run(["git", "add", "source.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "source"], cwd=repo, check=True)
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
            subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
            subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=repo, check=True)
            subprocess.run(["git", "push", "-q", "-u", "origin", "HEAD:main"], cwd=repo, check=True)

            first_root, first_checkout = developer_workspace.create_workspace(
                repo, root / "developers", "Alice", "main", commit,
                "20261018T010101Z-00000001",
            )
            other_root, other_checkout = developer_workspace.create_workspace(
                repo, root / "developers", "Bob", "main", commit,
                "20261018T010101Z-00000002",
            )
            second_root, second_checkout = developer_workspace.create_workspace(
                repo, root / "developers", "Alice", "main", commit,
                "20261018T010101Z-00000003",
            )

            self.assertNotEqual(first_checkout, other_checkout)
            self.assertNotEqual(first_checkout, second_checkout)
            self.assertNotEqual(first_root, other_root)
            self.assertNotEqual(first_root, second_root)
            for checkout in (first_checkout, other_checkout, second_checkout):
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=checkout, check=True,
                    capture_output=True, text=True,
                ).stdout.strip()
                self.assertEqual(result, commit)
                self.assertEqual((checkout / "source.txt").read_text(), "pinned\n")
            self.assertEqual(
                (root / "developers/alice/checkout/source.txt").read_text(),
                "pinned\n",
            )
            self.assertEqual(
                (root / "developers/bob/checkout/source.txt").read_text(),
                "pinned\n",
            )

    def test_shared_cache_reuse_keeps_each_runs_outputs_separate(self) -> None:
        config = SimpleNamespace(
            REGIONS={"test_region": [1.0, 2.0, 3.0, 4.0]},
            LINZ_DEM_WIDE_LAYER=51768,
            DEM_WIDE_BBOX=[1, 2, 3, 4],
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_data = root / "source"
            source_file = source_data / "regions/test_region/building_outlines.geojson"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                json.dumps({"type": "FeatureCollection", "features": []}),
                encoding="utf-8",
            )
            cache_root = root / "cache"
            run_a, run_b = root / "run-a/data", root / "run-b/data"
            production_file = source_data / "solar_potential.geojson"
            production_file.write_text("preserve-production", encoding="utf-8")
            before = production_file.read_bytes()

            with patch.object(
                developer_workspace, "_load_code_config",
                return_value=(config, lambda bbox, name=None: {"name": "test"}),
            ):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [
                        pool.submit(
                            developer_workspace.prepare_sources,
                            source_data, run_data, cache_root, root, ["test_region"],
                        )
                        for run_data in (run_a, run_b)
                    ]
                    for future in futures:
                        future.result()

            a_input = run_a / "regions/test_region/building_outlines.geojson"
            b_input = run_b / "regions/test_region/building_outlines.geojson"
            self.assertTrue(a_input.is_symlink())
            self.assertTrue(b_input.is_symlink())
            self.assertEqual(a_input.resolve(), b_input.resolve())
            self.assertNotEqual(a_input.parent.parent, b_input.parent.parent)
            self.assertEqual(production_file.read_bytes(), before)
            self.assertFalse((run_a / "solar_potential.geojson").exists())
            self.assertFalse((run_b / "solar_potential.geojson").exists())

    def test_incomplete_download_is_not_promoted_to_shared_cache(self) -> None:
        config = SimpleNamespace(
            REGIONS={"test_region": [1.0, 2.0, 3.0, 4.0]},
            LINZ_DEM_WIDE_LAYER=51768,
            DEM_WIDE_BBOX=[1, 2, 3, 4],
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_data = root / "run/data"
            downloaded = run_data / "regions/test_region/building_outlines.geojson"
            downloaded.parent.mkdir(parents=True)
            downloaded.write_text("{incomplete", encoding="utf-8")
            cache_root = root / "cache"
            with patch.object(
                developer_workspace, "_load_code_config",
                return_value=(config, lambda bbox, name=None: {"name": "test"}),
            ):
                with self.assertRaisesRegex(RuntimeError, "incomplete or invalid"):
                    developer_workspace.promote_sources(
                        run_data, cache_root, root, ["test_region"],
                    )
            self.assertFalse(
                list(cache_root.rglob("*.complete.json")),
            )

    def test_concurrent_complete_downloads_promote_one_valid_cache_entry(self) -> None:
        config = SimpleNamespace(
            REGIONS={"test_region": [1.0, 2.0, 3.0, 4.0]},
            LINZ_DEM_WIDE_LAYER=51768,
            DEM_WIDE_BBOX=[1, 2, 3, 4],
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runs = [root / "run-a/data", root / "run-b/data"]
            for run_data in runs:
                outlines = run_data / "regions/test_region/building_outlines.geojson"
                outlines.parent.mkdir(parents=True)
                outlines.write_text(
                    json.dumps({"type": "FeatureCollection", "features": []}),
                    encoding="utf-8",
                )
            cache_root = root / "cache"
            with patch.object(
                developer_workspace, "_load_code_config",
                return_value=(config, lambda bbox, name=None: {"name": "test"}),
            ):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [
                        pool.submit(
                            developer_workspace.promote_sources,
                            run_data, cache_root, root, ["test_region"],
                        )
                        for run_data in runs
                    ]
                    for future in futures:
                        future.result()

            cached_inputs = [
                run_data / "regions/test_region/building_outlines.geojson"
                for run_data in runs
            ]
            self.assertTrue(all(path.is_symlink() for path in cached_inputs))
            self.assertEqual(cached_inputs[0].resolve(), cached_inputs[1].resolve())
            self.assertTrue(developer_workspace._cached(cached_inputs[0].resolve()))

    def test_synthetic_surface_is_not_promoted_as_downloaded_source(self) -> None:
        config = SimpleNamespace(
            REGIONS={"test_region": [1.0, 2.0, 3.0, 4.0]},
            LINZ_DEM_WIDE_LAYER=51768,
            DEM_WIDE_BBOX=[1, 2, 3, 4],
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(
                developer_workspace, "_load_code_config",
                return_value=(config, lambda bbox, name=None: {"elevation": "synthetic"}),
            ):
                items = developer_workspace._source_items(
                    root, root, root / "cache", root, ["test_region"],
                )
            self.assertNotIn(
                root / "regions/test_region/dsm_mosaic.tif",
                [run_path for _, run_path, _ in items],
            )

    def test_packed_pointcloud_subsets_are_not_reused_as_full_tiles(self) -> None:
        config = SimpleNamespace(
            REGIONS={"test_region": [1.0, 2.0, 3.0, 4.0]},
            LINZ_DEM_WIDE_LAYER=51768,
            DEM_WIDE_BBOX=[1, 2, 3, 4],
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pointcloud = root / "source/pointcloud"
            pointcloud.mkdir(parents=True)
            (pointcloud / ".packed.json").write_text(
                json.dumps(["subset.laz"]), encoding="utf-8",
            )
            (pointcloud / "subset.laz").write_bytes(b"partial tile")
            with patch.object(
                developer_workspace, "_load_code_config",
                return_value=(config, lambda bbox, name=None: {"name": "test"}),
            ):
                items = developer_workspace._source_items(
                    root / "source", root / "run/data", root / "cache",
                    root, ["test_region"],
                )
                developer_workspace.prepare_sources(
                    root / "source", root / "run/data", root / "cache",
                    root, ["test_region"],
                )
            self.assertNotIn(
                root / "run/data/pointcloud/subset.laz",
                [run_path for _, run_path, _ in items],
            )
            self.assertFalse((root / "run/data/pointcloud/subset.laz").exists())
            self.assertEqual(
                json.loads((root / "run/data/pointcloud/.packed.json").read_text()),
                ["subset.laz"],
            )


if __name__ == "__main__":
    unittest.main()
