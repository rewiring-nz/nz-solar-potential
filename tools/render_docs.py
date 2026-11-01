"""Render committed documentation for plain static hosting, without runtime scripts.

Run with the pipeline interpreter: python tools/render_docs.py [--root REPO].
Only Markdown beneath docs/ is rendered; only referenced, non-hidden image
assets beneath docs/ enter the publication manifest.
"""

from __future__ import annotations

import argparse
import html
import os
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

SOURCE_BASE = "https://github.com/rewiring-nz/nz-solar-potential/blob/main/"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
PUBLIC_PAGES = {"rooftop-solar.html", "preview.html", "data-explorer.html",
                "pipeline.html", "index.html"}
STYLE = """
body{max-width:76ch;margin:2rem auto;padding:0 1rem;color:#202530;background:#fff;
font:17px/1.6 system-ui,sans-serif}a{color:#175b98}nav{display:flex;flex-wrap:wrap;
gap:1rem;border-bottom:1px solid #ccc;padding-bottom:1rem}img{max-width:100%;
height:auto}pre{overflow:auto;padding:1rem;background:#f3f4f6}code{font-size:.9em}
table{border-collapse:collapse;display:block;overflow:auto}th,td{border:1px solid
#ccc;padding:.4rem .6rem;text-align:left}blockquote{border-left:3px solid #ccc;
margin-left:0;padding-left:1rem}h1,h2,h3,h4{scroll-margin-top:1rem}
"""


def _safe_file(path: Path, boundary: Path) -> bool:
    try:
        relative = path.relative_to(boundary)
        if any(part.startswith(".") for part in relative.parts):
            return False
        current = boundary
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                return False
        return path.resolve().is_relative_to(boundary.resolve()) and path.is_file()
    except ValueError:
        return False


def render_docs(root: Path) -> tuple[str, ...]:
    """Generate HTML beside Markdown and return allowlisted relative assets.

    Raw HTML is escaped. Local links cannot read/include arbitrary files or
    follow symlinks; links to repository source outside docs use GitHub.
    """
    from markdown_it import MarkdownIt

    root = Path(root).resolve()
    docs = root / "docs"
    if not docs.exists():
        return ()
    if docs.is_symlink():
        raise ValueError("docs must not be a symlink")
    sources = {p for p in docs.rglob("*.md") if _safe_file(p, docs)}
    assets: set[str] = set()
    for source in sorted(sources):
        relative = source.relative_to(root)
        to_root = "../" * (len(relative.parts) - 1)
        renderer = MarkdownIt("commonmark", {"html": False}).enable("table")
        slugs: dict[str, int] = {}

        def heading(tokens, idx, options, env):
            title = "".join(t.content for t in tokens[idx + 1].children or []
                            if t.type in {"text", "code_inline"})
            slug = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
            count = slugs.get(slug, 0)
            slugs[slug] = count + 1
            tokens[idx].attrSet("id", f"{slug}-{count}" if count else slug)
            return renderer.renderer.renderToken(tokens, idx, options, env)

        def rewrite(url: str, *, image: bool = False) -> str:
            parsed = urlsplit(url)
            if parsed.scheme or parsed.netloc:
                return url if parsed.scheme in {"http", "https", "mailto"} else "#"
            if not parsed.path:
                return url
            decoded = unquote(parsed.path)
            if decoded.startswith("/") or "\\" in decoded:
                return "#"
            target = Path(source.parent / decoded)
            # Normalize ".." without resolving symlinks.
            target = Path(os.path.abspath(target))
            if not _safe_file(target, root):
                return "#"
            target_relative = target.relative_to(root).as_posix()
            if image:
                if not target.is_relative_to(docs) or target.suffix.lower() not in IMAGE_SUFFIXES:
                    return "#"
                assets.add(target_relative)
                return url
            if target in sources:
                return urlunsplit(("", "", parsed.path[:-3] + ".html", parsed.query, parsed.fragment))
            if target_relative in PUBLIC_PAGES:
                return url
            return SOURCE_BASE + quote(target_relative, safe="/") + (
                "#" + parsed.fragment if parsed.fragment else "")

        renderer.add_render_rule("heading_open", lambda self, *args: heading(*args))
        tokens = renderer.parse(source.read_text(encoding="utf-8"))

        def rewrite_tokens(items):
            for token in items:
                if token.type == "link_open":
                    token.attrSet("href", rewrite(token.attrGet("href") or ""))
                elif token.type == "image":
                    token.attrSet("src", rewrite(token.attrGet("src") or "", image=True))
                if token.children:
                    rewrite_tokens(token.children)

        rewrite_tokens(tokens)
        body = renderer.renderer.render(tokens, renderer.options, {})
        title = next((tokens[i + 1].content for i, token in enumerate(tokens)
                      if token.type == "heading_open"), source.stem)
        page = (
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(title)} · NZ solar documentation</title>"
            f"<style>{STYLE}</style><body><nav aria-label=\"Site\">"
            f'<a href="{to_root}rooftop-solar.html">Rooftop solar</a>'
            f'<a href="{to_root}data-explorer.html">Data explorer</a>'
            f'<a href="{to_root}pipeline.html">Pipeline</a>'
            f'<a href="{to_root}docs/index.html">Docs</a></nav><main>{body}</main>'
            "</body></html>\n"
        )
        output = source.with_suffix(".html")
        if output.is_symlink():
            raise ValueError(f"refusing symlink output: {output}")
        if not output.exists() or output.read_text(encoding="utf-8") != page:
            output.write_text(page, encoding="utf-8")
        assets.add(output.relative_to(root).as_posix())
    return tuple(sorted(assets))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(f"Rendered documentation: {len(render_docs(args.root))} public files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
