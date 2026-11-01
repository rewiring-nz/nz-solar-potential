"""Canonical map URLs and the compatibility redirect preserve view state."""

from pathlib import Path
import json
import re
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.pipeline_webui.region_catalog import preview_url
from tools.pipeline_webui.remote_catalog import LIVE_SITE, _with_view


class MapLinkTests(unittest.TestCase):
    def test_local_and_published_links_use_canonical_page(self):
        self.assertEqual(preview_url(168.67, -45.03, 15.5),
                         "/rooftop-solar.html?lat=-45.0300000&lng=168.6700000&z=15.5")
        self.assertTrue(LIVE_SITE.endswith("/rooftop-solar.html"))
        self.assertIn("/rooftop-solar.html?", _with_view(LIVE_SITE, [168.67, -45.03]))

    @unittest.skipUnless(shutil.which("node"), "Node is required to execute the redirect")
    def test_actual_legacy_redirect_preserves_query_and_fragment(self):
        page = (ROOT / "preview.html").read_text(encoding="utf-8")
        script = re.search(r"<script>(.*?)</script>", page, flags=re.DOTALL).group(1)
        command = (
            "const vm=require('node:vm');let target;"
            "vm.runInNewContext(" + json.dumps(script) + ","
            "{location:{search:'?lat=-45.03&lng=168.67&z=15.5&layers=panels',"
            "hash:'#building=123',replace:value=>target=value}});"
            "process.stdout.write(target);"
        )
        result = subprocess.run(["node", "-e", command], capture_output=True,
                                text=True, check=True, cwd=ROOT)
        self.assertEqual(result.stdout,
                         "rooftop-solar.html?lat=-45.03&lng=168.67&z=15.5&layers=panels#building=123")


if __name__ == "__main__":
    unittest.main()
