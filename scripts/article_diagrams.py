"""Optional, authored explanatory diagrams beside the unchanged article photo.

Each SVG is its own review record: its article fingerprint binds the drawing to
the text that was inspected. Stale or malformed diagrams are omitted, while the
article and its independent photo review continue to work normally.
"""
from __future__ import annotations

import hashlib
import html
import re
import sys
from pathlib import Path
from xml.etree import ElementTree

if __package__:
    from .generated_photos import SAFE_SLUG, article_fingerprint
else:
    from generated_photos import SAFE_SLUG, article_fingerprint

ROOT = Path(__file__).resolve().parent.parent
RELATIVE_DIR = Path("assets/article-diagrams")
PUBLIC_DIR = "article-diagrams"
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
MAX_SVG_BYTES = 256 * 1024
ALLOWED_ELEMENTS = frozenset({
    "svg", "title", "desc", "g", "path", "rect", "circle", "ellipse",
    "line", "polyline", "polygon", "text", "tspan", "defs", "marker",
    "clippath", "lineargradient", "radialgradient", "stop", "use",
})
_reported_omissions: set[tuple[str, str, str]] = set()


def _omit(root: Path, slug: str, reason: str) -> None:
    """Report an existing but unusable diagram once per reason and build."""
    key = (str(root.resolve()), slug, reason)
    if key not in _reported_omissions:
        print(f"Article diagram omitted: {slug!r} ({reason})", file=sys.stderr)
        _reported_omissions.add(key)


def reviewed_article_diagram(article: dict, *, root: Path = ROOT) -> dict | None:
    """Read a small, self-contained SVG only when it matches the current text.

    The drawing is served through an img element, never injected as inline SVG.
    No diagram is expected for most articles; a missing optional file is silent.
    """
    slug = article.get("slug")
    if not isinstance(slug, str) or not SAFE_SLUG.fullmatch(slug):
        return None
    root = Path(root)
    directory = root / RELATIVE_DIR
    path = directory / f"{slug}.svg"
    if (directory.parent.is_symlink() or directory.is_symlink() or path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())):
        return _omit(root, slug, "unsafe asset path")
    try:
        if not path.exists():
            return None
        if path.stat().st_size > MAX_SVG_BYTES:
            return _omit(root, slug, "SVG exceeds 256 KiB")
        payload = path.read_bytes()
        source = payload.decode("utf-8")
        if "<!DOCTYPE" in source.upper() or "<!ENTITY" in source.upper():
            return _omit(root, slug, "XML declarations are not allowed")
        if any(target.lower() != "xml" for target in re.findall(r"<\?([\w:.-]+)", source)):
            return _omit(root, slug, "XML processing instructions are not allowed")
        svg = ElementTree.fromstring(source)
    except (OSError, UnicodeError, ElementTree.ParseError):
        return _omit(root, slug, "unreadable SVG")
    if svg.tag != f"{{{SVG_NAMESPACE}}}svg" or svg.get("role") != "img":
        return _omit(root, slug, "missing accessible SVG root")
    if svg.get("data-article-sha256") != article_fingerprint(article):
        return _omit(root, slug, "article fingerprint changed")
    viewbox = svg.get("viewBox", "").split()
    if (len(viewbox) != 4 or viewbox[:2] != ["0", "0"]
            or any(not re.fullmatch(r"[1-9][0-9]{0,3}", v) for v in viewbox[2:])):
        return _omit(root, slug, "invalid viewBox dimensions")
    width, height = map(int, viewbox[2:])
    if width > 4096 or height > 4096:
        return _omit(root, slug, "invalid viewBox dimensions")
    for name, expected in (("width", width), ("height", height)):
        if svg.get(name) is not None and svg.get(name) != str(expected):
            return _omit(root, slug, "dimensions disagree with viewBox")
    title_node = svg.find(f"{{{SVG_NAMESPACE}}}title")
    desc_node = svg.find(f"{{{SVG_NAMESPACE}}}desc")
    title = "" if title_node is None else "".join(title_node.itertext()).strip()
    description = "" if desc_node is None else "".join(desc_node.itertext()).strip()
    if not title or not description:
        return _omit(root, slug, "missing title or description")
    for node in svg.iter():
        if (not node.tag.startswith(f"{{{SVG_NAMESPACE}}}")
                or node.tag.rsplit("}", 1)[-1].lower() not in ALLOWED_ELEMENTS):
            return _omit(root, slug, "unsupported SVG element")
        for name, value in node.attrib.items():
            attribute = name.rsplit("}", 1)[-1].lower()
            # No embedded styles or CSS escapes: use ordinary SVG presentation
            # attributes. Local marker/gradient references remain available.
            if attribute.startswith("on") or attribute in {"style", "base"} or "\\" in value:
                return _omit(root, slug, "active or unsupported SVG attribute")
            if attribute == "href" and not re.fullmatch(r"#[A-Za-z_][\w:.-]*", value):
                return _omit(root, slug, "external SVG reference")
            references = re.findall(r"url\s*\((.*?)\)", value, flags=re.IGNORECASE)
            if any(not re.fullmatch(r"#[A-Za-z_][\w:.-]*", ref.strip(" \t\r\n\"'"))
                   for ref in references):
                return _omit(root, slug, "external SVG reference")
    digest = hashlib.sha256(payload).hexdigest()
    return {
        "path": path,
        "url": f"/{PUBLIC_DIR}/{slug}.svg?v={digest[:12]}",
        "svg_sha256": digest,
        "svg_bytes": payload,
        "width": width,
        "height": height,
        "title": title,
        "description": description,
    }


def render_article_diagram(diagram: dict | None) -> str:
    """Render the validated image with a useful alternative and short caption."""
    if diagram is None:
        return ""
    return (
        '<figure class="article-diagram">'
        f'<img src="{html.escape(diagram["url"], quote=True)}" '
        f'alt="{html.escape(diagram["description"], quote=True)}" '
        f'width="{diagram["width"]}" height="{diagram["height"]}" '
        'loading="lazy" decoding="async">'
        f'<figcaption>{html.escape(diagram["title"])}</figcaption></figure>'
    )


def publish_article_diagrams(articles: list[dict], out: Path, *, root: Path = ROOT) -> None:
    """Copy only validated bytes into the site's freshly cleared output tree."""
    for article in articles:
        diagram = reviewed_article_diagram(article, root=root)
        if diagram:
            destination = Path(out) / PUBLIC_DIR / (article["slug"] + ".svg")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(diagram["svg_bytes"])
