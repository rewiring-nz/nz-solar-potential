"""Offline static documentation rendering and publication contract."""

from pathlib import Path
from html.parser import HTMLParser
import sys
import tempfile
import unittest
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.build_static_site import build_site
from tools.site_assets import FRONTEND_FILES
from tools.render_docs import render_docs


class DocumentationTests(unittest.TestCase):
    def test_real_page_resources_are_in_public_manifest(self):
        resources = []

        class ResourceParser(HTMLParser):
            def handle_starttag(self, tag, attributes):
                attrs = dict(attributes)
                if tag == "script" and attrs.get("src"):
                    resources.append(attrs["src"])
                elif tag == "link" and attrs.get("rel") == "stylesheet":
                    resources.append(attrs["href"])

        for name in FRONTEND_FILES:
            if name.endswith(".html"):
                ResourceParser().feed((ROOT / name).read_text(encoding="utf-8"))
        # Files the page's scripts load by a literal fetch("name.json") are
        # public too: methodology.json was fetched by methodology.js but not
        # published, so the live diagram could not load.
        import re
        for name in FRONTEND_FILES:
            if name.endswith(".js") or name.endswith(".html"):
                text = (ROOT / name).read_text(encoding="utf-8")
                resources += re.findall(r"""fetch\(\s*["']([A-Za-z0-9_./-]+\.(?:json|js|csv))["']""", text)
        for url in resources:
            parsed = urlsplit(url)
            if not parsed.scheme and not parsed.netloc:
                self.assertIn(parsed.path, (*FRONTEND_FILES, "site-config.js"), url)

    def fixture(self, root):
        (root / "docs" / "images").mkdir(parents=True)
        (root / "docs" / "researchers").mkdir()
        (root / "docs" / "guide.md").write_text(
            "# Next steps\n\n[Home](index.md#map-users)\n", encoding="utf-8")
        (root / "docs" / "researchers" / "how-a-roof-estimate-is-made.md").write_text(
            "# How a roof estimate is made\n\n## Cost, savings and payback\n",
            encoding="utf-8")
        (root / "docs" / "images" / "roof.png").write_bytes(b"image")
        (root / "source.py").write_text("not published")
        (root / ".env").write_text("do not publish")
        (root / "docs" / "unused.png").write_bytes(b"not referenced")
        (root / "docs" / "index.md").write_text(
            "# Map users\n\n[Guide](guide.md?mode=read#next-steps)\n\n"
            "![Roof](images/roof.png)\n\n[Source](../source.py)\n\n"
            "![Secret](../.env)\n\n<script>alert('x')</script>\n\n"
            "| A | B |\n| --- | --- |\n| one | two |\n\n"
            "[Method](researchers/how-a-roof-estimate-is-made.md#cost-savings-and-payback)\n\n"
            "# Map users\n", encoding="utf-8")

    def test_links_images_headings_and_manifest(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix="docs-test-") as work:
            root = Path(work)
            self.fixture(root)
            manifest = render_docs(root)
            page = (root / "docs" / "index.html").read_text()
            self.assertIn('href="guide.html?mode=read#next-steps"', page)
            self.assertIn(
                'href="researchers/how-a-roof-estimate-is-made.html#cost-savings-and-payback"',
                page)
            self.assertIn('src="images/roof.png"', page)
            self.assertIn('id="map-users"', page)
            self.assertIn('id="map-users-1"', page)
            self.assertIn("<table>", page)
            self.assertNotIn("<script>", page)
            self.assertIn("&lt;script&gt;", page)
            self.assertIn("github.com/rewiring-nz/nz-solar-potential/blob/main/source.py", page)
            self.assertEqual(manifest, (
                "docs/guide.html", "docs/images/roof.png", "docs/index.html",
                "docs/researchers/how-a-roof-estimate-is-made.html"))
            self.assertIn('href="../rooftop-solar.html"', page)
            self.assertIn('href="../data-explorer.html"', page)
            nav = page.split("</nav>", 1)[0]
            self.assertIn(">Docs</a>", nav)
            self.assertNotIn(">Documentation</a>", nav)
            self.assertNotIn(">Method</a>", nav)
            mtime = (root / "docs" / "index.html").stat().st_mtime_ns
            self.assertEqual(render_docs(root), manifest)
            self.assertEqual((root / "docs" / "index.html").stat().st_mtime_ns, mtime)

    def test_symlinks_traversal_and_unsafe_urls(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix="docs-test-") as work:
            root = Path(work)
            self.fixture(root)
            (root / "docs" / "leak.md").symlink_to(root / ".env")
            (root / "docs" / "images" / "leak.png").symlink_to(root / ".env")
            with (root / "docs" / "index.md").open("a") as stream:
                stream.write(
                    "\n![Leak](images/leak.png)\n[Outside](../../AGENTS.md)\n"
                    "[Hidden](../%2eenv)\n[File](file:///etc/passwd)\n")
            manifest = render_docs(root)
            page = (root / "docs" / "index.html").read_text()
            self.assertNotIn("docs/leak.html", manifest)
            self.assertNotIn("docs/images/leak.png", manifest)
            self.assertNotIn("do not publish", page)
            self.assertNotIn('href="file:///etc/passwd"', page)
            self.assertNotIn('href="../../AGENTS.md"', page)
            output = root / "docs" / "guide.html"
            output.unlink()
            output.symlink_to(root / ".env")
            with self.assertRaises(ValueError):
                render_docs(root)
            self.assertEqual((root / ".env").read_text(), "do not publish")

    def test_pages_artifact_is_allowlisted_and_complete(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix="docs-test-") as work:
            root = Path(work)
            self.fixture(root)
            for name in (*FRONTEND_FILES, "index.html", "site-config.js"):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("public")
            output = build_site(root)
            self.assertTrue((output / "docs" / "index.html").is_file())
            self.assertTrue((output / "docs" / "images" / "roof.png").is_file())
            self.assertTrue((output / "rooftop-solar.html").is_file())
            self.assertTrue((output / "preview.html").is_file())
            self.assertTrue((output / "data-explorer.html").is_file())
            self.assertTrue(
                (output / "docs" / "researchers" /
                 "how-a-roof-estimate-is-made.html").is_file())
            self.assertFalse((output / "method.html").exists())
            self.assertTrue((output / ".nojekyll").is_file())
            self.assertFalse((output / ".env").exists())
            self.assertFalse((output / "source.py").exists())
            self.assertFalse((output / "docs" / "index.md").exists())
            self.assertFalse((output / "docs" / "unused.png").exists())
            (output / "stale.txt").touch()
            build_site(root)
            self.assertFalse((output / "stale.txt").exists())


if __name__ == "__main__":
    unittest.main()
