"""Canonical map URLs and the compatibility redirect preserve view state."""

from pathlib import Path
from html.parser import HTMLParser
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
    def test_rooftop_navigation_links_to_build_data_and_docs(self):
        class NavigationParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_navigation = False
                self.links = []

            def handle_starttag(self, tag, attrs):
                attributes = dict(attrs)
                if tag == "nav" and attributes.get("aria-label") == "Pages":
                    self.in_navigation = True
                if tag == "a" and self.in_navigation:
                    self.links.append(attributes.get("href"))

            def handle_endtag(self, tag):
                if tag == "nav":
                    self.in_navigation = False

        parser = NavigationParser()
        parser.feed((ROOT / "rooftop-solar.html").read_text(encoding="utf-8"))
        self.assertEqual(parser.links,
                         ["wholesale.html", "pipeline.html", "docs/index.html"])
        # docs/index.html is generated from the maintained Markdown source.
        for path in ["wholesale.html", "pipeline.html", "docs/index.md"]:
            self.assertTrue((ROOT / path).is_file(), path)

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

    @unittest.skipUnless(shutil.which("node"), "Node is required to execute the redirect")
    def test_former_explorer_url_redirects_to_build_data(self):
        page = (ROOT / "data-explorer.html").read_text(encoding="utf-8")
        script = re.search(r"<script>(.*?)</script>", page, flags=re.DOTALL).group(1)
        command = (
            "const vm=require('node:vm');let target;"
            "vm.runInNewContext(" + json.dumps(script) + ","
            "{location:{search:'?source=local',hash:'#token=abc',"
            "replace:value=>target=value}});"
            "process.stdout.write(target);"
        )
        result = subprocess.run(["node", "-e", command], capture_output=True,
                                text=True, check=True, cwd=ROOT)
        self.assertEqual(result.stdout, "pipeline.html?source=local#token=abc")


if __name__ == "__main__":
    unittest.main()
