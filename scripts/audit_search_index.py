#!/usr/bin/env python3
"""Offline Google discoverability audit for the generated static newspaper.

This checks what the site publishes, not Google's actual index coverage.
No Search Console credentials, paid APIs, network calls or simulated visits.
"""
from __future__ import annotations

import json
import sys
from collections import deque
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit
from xml.etree import ElementTree as ET

SITE = "https://veripsa.com"
SITEMAP_NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.titles: list[str] = []
        self.meta: dict[str, str] = {}
        self.canonicals: list[str] = []
        self.links: list[str] = []
        self.ids: set[str] = set()
        self.schemas: list[dict] = []
        self._title_parts: list[str] | None = None
        self._schema_parts: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "title":
            self._title_parts = []
        elif tag == "meta":
            key = values.get("name") or values.get("property")
            if key:
                self.meta[key.lower()] = values.get("content", "")
        elif tag == "link" and values.get("rel", "").lower() == "canonical":
            self.canonicals.append(values.get("href", ""))
        elif tag == "a" and values.get("href"):
            self.links.append(values["href"])
        elif tag == "script" and values.get("type") == "application/ld+json":
            self._schema_parts = []

    def handle_data(self, data):
        if self._title_parts is not None:
            self._title_parts.append(data)
        if self._schema_parts is not None:
            self._schema_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "title" and self._title_parts is not None:
            self.titles.append("".join(self._title_parts).strip())
            self._title_parts = None
        elif tag == "script" and self._schema_parts is not None:
            raw = "".join(self._schema_parts)
            parsed = json.loads(raw)
            blocks = parsed if isinstance(parsed, list) else [parsed]
            self.schemas.extend(block for block in blocks if isinstance(block, dict))
            self._schema_parts = None


def local_page(dist: Path, path: str) -> Path:
    """Every indexed URL must map to an existing generated HTML route."""
    if path == "/":
        return dist / "index.html"
    if not path.startswith("/") or not path.endswith("/") or ".." in path.split("/"):
        raise ValueError("Non-canonical page path: " + path)
    return dist / path.lstrip("/") / "index.html"


def read_page(dist: Path, path: str) -> Page:
    target = local_page(dist, path)
    if not target.is_file():
        raise ValueError("Sitemap entry has no generated HTML: " + path)
    page = Page()
    page.feed(target.read_text(encoding="utf-8"))
    page.close()
    return page


def check_internal_links(dist: Path, pages: dict[str, Page]) -> tuple[list[str], int]:
    """Validate reader-facing links after static generation, never external URLs.

    The source document, not JavaScript or a crawler, determines whether a
    visitor can reach the article, section, local file or real fragment.
    Queries are presentation filters; fragments need actual HTML target ids.
    """
    errors: list[str] = []
    checked = 0
    root = dist.resolve()
    id_cache: dict[Path, set[str]] = {}

    for source_path, page in pages.items():
        for href in set(page.links):
            target_url = urlsplit(urljoin(SITE + source_path, href))
            if target_url.scheme not in ("http", "https") or target_url.hostname != "veripsa.com":
                continue  # Links to sources, X sharing, mail or other domains.
            checked += 1
            url_path = unquote(target_url.path or "/")
            # Urljoin normalizes normal "../"; guard against encoded traversal.
            if ".." in url_path.split("/"):
                errors.append("Unsafe internal link: " + source_path + " -> " + href)
                continue
            filename = url_path.lstrip("/")
            resolved = (root / filename).resolve()
            if not resolved.is_relative_to(root):
                errors.append("Internal link escapes output: " + source_path + " -> " + href)
                continue
            if url_path == "/" or url_path.endswith("/"):
                resolved = resolved / "index.html"
            elif resolved.is_dir():
                resolved = resolved / "index.html"
            if not resolved.is_file():
                errors.append("Broken internal link: " + source_path + " -> " + href)
                continue
            if target_url.fragment and resolved.suffix == ".html":
                if resolved not in id_cache:
                    # Reuse parsed sitemap pages, avoid repeatedly reading HTML.
                    if url_path in pages:
                        id_cache[resolved] = pages[url_path].ids
                    else:
                        target_page = Page()
                        target_page.feed(resolved.read_text(encoding="utf-8"))
                        id_cache[resolved] = target_page.ids
                anchor = unquote(target_url.fragment)
                if anchor and anchor not in id_cache[resolved]:
                    errors.append("Broken internal fragment: " + source_path + " -> " + href)
    return errors, checked


def audit(dist: Path = DIST) -> dict:
    errors: list[str] = []
    sitemap = ET.parse(dist / "sitemap.xml").getroot()
    entries = sitemap.findall("s:url", SITEMAP_NS)
    routes: dict[str, tuple[str, str | None]] = {}
    for entry in entries:
        url = entry.findtext("s:loc", namespaces=SITEMAP_NS)
        lastmod = entry.findtext("s:lastmod", namespaces=SITEMAP_NS)
        if not url:
            errors.append("Missing sitemap <loc>")
            continue
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc != "veripsa.com"
                or parsed.query or parsed.fragment
                or not parsed.path.endswith("/")):
            errors.append("Non-canonical sitemap URL: " + url)
            continue
        path = parsed.path
        if path in routes:
            errors.append("Duplicate sitemap URL: " + url)
        routes[path] = (url, lastmod)
        if lastmod:
            try:
                datetime.fromisoformat(lastmod.replace("Z", "+00:00"))
            except ValueError:
                errors.append("Invalid <lastmod>: " + url + " " + lastmod)

    for path in ("/", "/archive/", "/issues/", "/serials/"):
        if path not in routes:
            errors.append("Missing primary discovery route: " + path)

    pages: dict[str, Page] = {}
    titles: dict[str, str] = {}
    article_count = 0
    serial_episode_count = 0
    for path, (url, lastmod) in routes.items():
        try:
            page = read_page(dist, path)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
            continue
        pages[path] = page
        if page.canonicals != [url]:
            errors.append("Missing, wrong or repeated canonical: " + path)
        if "noindex" in page.meta.get("robots", "").lower():
            errors.append("An indexed sitemap URL carries noindex: " + path)
        if len(page.titles) != 1 or not page.titles[0]:
            errors.append("Missing or repeated HTML <title>: " + path)
        else:
            title = page.titles[0]
            if title in titles:
                errors.append("Duplicate HTML title: " + path + " and " + titles[title])
            titles[title] = path
        if not page.meta.get("description", "").strip():
            errors.append("Missing meta description: " + path)

        og_image = page.meta.get("og:image", "")
        img_url = urlsplit(og_image)
        if img_url.scheme != "https" or img_url.netloc != "veripsa.com":
            errors.append("Missing or external OG image: " + path)
        elif not (dist / img_url.path.lstrip("/")).is_file():
            errors.append("OG image asset not generated: " + path + " -> " + og_image)

        if not any(schema.get("url") == url for schema in page.schemas):
            errors.append("Missing primary JSON-LD URL: " + path)

        if path.startswith("/articles/"):
            article_count += 1
            matching = [schema for schema in page.schemas
                        if schema.get("@type") in {"BlogPosting", "Article", "NewsArticle"}]
            if not matching:
                errors.append("Article lacks article structured data: " + path)
            elif (not matching[0].get("datePublished")
                  or matching[0].get("dateModified") != lastmod):
                errors.append("Article publication/update date differs from sitemap: " + path)
        elif path.startswith("/serials/") and path.count("/") >= 4:
            serial_episode_count += 1
            matching = [schema for schema in page.schemas if schema.get("@type") == "Article"]
            if not matching or matching[0].get("datePublished") != lastmod:
                errors.append("Serial episode publication date differs from sitemap: " + path)

    # A sitemap alone does not supply a reader or crawler navigation route.
    # Require every public indexed page to be reachable by HTML anchors from /.
    graph = {}
    for path, page in pages.items():
        destinations = set()
        for href in page.links:
            link = urlsplit(urljoin(SITE + path, href))
            if link.scheme == "https" and link.netloc == "veripsa.com":
                if link.path in routes:
                    destinations.add(link.path)
        graph[path] = destinations
    seen = set()
    pending = deque(["/"]) if "/" in pages else deque()
    while pending:
        here = pending.popleft()
        if here in seen:
            continue
        seen.add(here)
        pending.extend(sorted(graph.get(here, set()) - seen))
    for path in sorted(routes.keys() - seen):
        errors.append("Sitemap page not linked from homepage navigation: " + path)

    link_errors, checked_links = check_internal_links(dist, pages)
    errors.extend(link_errors)

    robots = (dist / "robots.txt").read_text(encoding="utf-8")
    if "Sitemap: " + SITE + "/sitemap.xml" not in robots:
        errors.append("robots.txt does not expose sitemap.xml")
    if any(line.strip() == "Disallow: /" for line in robots.splitlines()):
        errors.append("robots.txt appears to block all crawling")
    for path in ("/saved/", "/404.html"):
        target = dist / ("404.html" if path == "/404.html" else "saved/index.html")
        if target.is_file() and 'name="robots" content="noindex' not in target.read_text(encoding="utf-8"):
            errors.append("Non-content utility page missing noindex: " + path)
        if path in routes:
            errors.append("Non-content page included in sitemap: " + path)

    if errors:
        raise ValueError("Search discoverability audit failed:\n- " + "\n- ".join(errors))
    return {
        "sitemap_pages": len(routes),
        "reachable_pages": len(seen),
        "articles": article_count,
        "serial_episodes": serial_episode_count,
        "internal_links_checked": checked_links,
        "index_status": "unknown (requires Google Search Console)",
    }


def main() -> int:
    try:
        counts = audit()
    except (ValueError, OSError, ET.ParseError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Google discoverability audit OK (offline, not Google indexing confirmation):")
    print(json.dumps(counts, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
