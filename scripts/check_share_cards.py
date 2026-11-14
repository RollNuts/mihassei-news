#!/usr/bin/env python3
"""Read-only, zero-API-cost verification of real X/Twitterbot link cards.

This verifies what a claimed Twitterbot user-agent can retrieve from the
public site. It cannot reproduce X's internal cache or a real crawler IP.
Never calls engagement endpoints, posts traffic, changes data or uses paid APIs.
"""
from __future__ import annotations

import json
import re
import struct
import sys
import urllib.error
import urllib.request
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

if __package__:
    from .serials import public_serials
    from .photo_issues import (load_photo_issues, published_issues,
                              index_url as photo_index_url, issue_url as photo_issue_url)
else:
    from serials import public_serials
    from photo_issues import (load_photo_issues, published_issues,
                             index_url as photo_index_url, issue_url as photo_issue_url)

SITE = "https://veripsa.com"
ROOT = Path(__file__).resolve().parents[1]
BOT = "Twitterbot/1.0"
MAX_HTML = 2 * 1024 * 1024
MAX_IMAGE = 6 * 1024 * 1024


def png_card_dimensions(image_url: str) -> tuple[int, int]:
    parsed = urlsplit(image_url)
    version = parse_qs(parsed.query).get("v", [""])
    scene = (parsed.path.startswith("/og/articles/")
             and len(version) == 1 and re.fullmatch(r"[0-9a-f]{12}", version[0]))
    return 1200, 675 if scene else 630


class CardParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.canonicals = []

    def handle_starttag(self, tag, attrs):
        details = dict(attrs)
        if tag == "meta":
            name = details.get("property") or details.get("name")
            if name and details.get("content"):
                self.meta.setdefault(name, details["content"].strip())
        elif tag == "link" and details.get("rel", "").lower() == "canonical":
            self.canonicals.append(details.get("href", ""))


def inspect_markup(markup: str, path: str) -> tuple[str, str]:
    """Check image/social/canonical contract; return (image URL, MIME)."""
    p = CardParser()
    p.feed(markup)
    expected = SITE + path
    if p.canonicals != [expected]:
        raise RuntimeError(path + ": missing/wrong canonical URL: " + repr(p.canonicals))
    for name in ("og:url", "og:title", "og:description", "og:image",
                 "og:image:secure_url", "og:image:alt", "og:image:type",
                 "twitter:title", "twitter:description", "twitter:image",
                 "twitter:image:alt", "twitter:card"):
        if not p.meta.get(name):
            raise RuntimeError(path + ": missing social metadata " + name)
    if p.meta["og:url"] != expected:
        raise RuntimeError(path + ": OpenGraph URL differs from canonical")
    if p.meta["twitter:card"] != "summary_large_image":
        raise RuntimeError(path + ": X large-card mode missing")
    image = p.meta["og:image"]
    if image != p.meta["og:image:secure_url"] or image != p.meta["twitter:image"]:
        raise RuntimeError(path + ": X and OpenGraph images disagree")
    parsed = urlsplit(image)
    weekly_image = (re.fullmatch(r"/photo/weekly/[a-z0-9]+(?:-[a-z0-9]+)*/(?:0[1-9]|10)\.jpg", parsed.path)
                    and re.fullmatch(r"v=[0-9a-f]{12}", parsed.query) and not parsed.fragment)
    if (parsed.scheme != "https" or parsed.netloc != "veripsa.com"
            or not (parsed.path.startswith(("/og/", "/photos/", "/generated/")) or weekly_image)):
        raise RuntimeError(path + ": image must be first-party raster: " + image)
    extension = parsed.path.rsplit(".", 1)[-1].lower()
    expected_mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(extension)
    if not expected_mime or p.meta["og:image:type"] != expected_mime:
        raise RuntimeError(path + ": wrong image MIME for " + image)
    weekly_issue = re.fullmatch(r"/photo/weekly/[a-z0-9]+(?:-[a-z0-9]+)*/", path)
    if weekly_issue and p.meta.get("og:type") != "article":
        raise RuntimeError(path + ": weekly photo issue must advertise article type")
    if path == photo_index_url() and p.meta.get("og:type") == "article":
        raise RuntimeError(path + ": weekly photo collection must not advertise article type")
    if path.startswith(("/articles/", "/serials/")) and path.count("/") > 2:
        if p.meta.get("og:type") != "article":
            # /serials/<series>/ is a collection page, not an episode.
            if path.startswith("/articles/") or path.count("/") > 3:
                raise RuntimeError(path + ": story/episode must advertise article type")
    if extension == "png":
        width, height = png_card_dimensions(image)
        if p.meta.get("og:image:width") != str(width) or p.meta.get("og:image:height") != str(height):
            raise RuntimeError(path + ": PNG share-card dimensions missing")
    return image, expected_mime


def image_dimensions(data: bytes, mime: str) -> tuple[int, int]:
    """Read the actual raster header with only the Python standard library."""
    if mime == "image/png":
        if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("Social card is not a valid PNG")
        return struct.unpack(">II", data[16:24])
    if not data.startswith(b"\xff\xd8"):
        raise RuntimeError("Social card is not a valid JPEG")
    cursor = 2
    while cursor + 4 < len(data):
        if data[cursor] != 255:
            raise RuntimeError("Malformed JPEG marker")
        while cursor < len(data) and data[cursor] == 255:
            cursor += 1
        if cursor >= len(data):
            break
        marker = data[cursor]
        cursor += 1
        if marker in (0xD8, 0xD9, 0x01):
            continue
        if marker == 0xDA:
            break
        if cursor + 2 > len(data):
            break
        segment = struct.unpack(">H", data[cursor:cursor + 2])[0]
        if segment < 2 or cursor + segment > len(data):
            raise RuntimeError("Malformed JPEG segment")
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if segment < 7:
                raise RuntimeError("Malformed JPEG dimensions")
            height, width = struct.unpack(">HH", data[cursor + 3:cursor + 7])
            return width, height
        cursor += segment
    raise RuntimeError("JPEG has no readable frame dimensions")


def candidate_paths(root: Path = ROOT) -> list[str]:
    """Use the real published pages, never an unpublished/future placeholder."""
    articles = json.loads((root / "content/articles.json").read_text(encoding="utf-8"))
    newest = sorted(articles, key=lambda a: (
        a.get("published_at") or a["source"]["published_at"],
        a["source"]["published_at"]), reverse=True)
    paths = ["/"]
    paths.extend("/articles/" + article["slug"] + "/" for article in newest[:2])
    groups = Counter((a.get("published_at") or a["source"]["published_at"])[:10] for a in newest)
    substantial = sorted((day for day, n in groups.items() if n >= 3), reverse=True)
    if substantial:
        paths.append("/issues/" + substantial[0] + "/")
    series = public_serials(json.loads((root / "content/serials.json").read_text(encoding="utf-8")))
    if series:
        paths.append("/serials/")
    for serial in series:
        if serial["episodes"]:
            ep = serial["episodes"][-1]
            paths.append("/serials/" + serial["slug"] + "/" + ep["slug"] + "/")
    photos = published_issues(load_photo_issues(root), root=root)
    if photos:
        paths.extend((photo_index_url(), photo_issue_url(photos[0])))
    return paths


def fetch(url: str, accept: str, max_bytes: int) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={
        "User-Agent": BOT,
        "Accept": accept,
        "Cache-Control": "no-cache",
    })
    try:
        with urllib.request.urlopen(request, timeout=18) as response:
            if response.status != 200:
                raise RuntimeError("HTTP " + str(response.status) + " " + url)
            if urlsplit(response.geturl()).netloc != "veripsa.com":
                raise RuntimeError("Unexpected off-site redirect at " + url)
            data = response.read(max_bytes + 1)
            mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise RuntimeError("Bot cannot retrieve " + url + ": " + str(exc)) from exc
    if len(data) > max_bytes or len(data) < 120:
        raise RuntimeError("Invalid response size for " + url)
    return data, mime


def check():
    checked_images = set()
    for path in candidate_paths():
        body, mime = fetch(SITE + path, "text/html,application/xhtml+xml", MAX_HTML)
        if mime != "text/html":
            raise RuntimeError(path + ": crawler did not receive text/html")
        image_url, image_mime = inspect_markup(body.decode("utf-8"), path)
        if image_url not in checked_images:
            image, actual_mime = fetch(image_url, "image/png,image/jpeg", MAX_IMAGE)
            if actual_mime != image_mime:
                raise RuntimeError(path + ": image content-type is " + actual_mime)
            width, height = image_dimensions(image, image_mime)
            if ((image_mime == "image/png" and (width, height) != png_card_dimensions(image_url))
                    or (image_mime == "image/jpeg" and (width < 600 or height < 315))):
                raise RuntimeError(path + ": unsupported social raster dimensions " + str((width, height)))
            checked_images.add(image_url)
        print("OK X crawler:", path, "card", image_url)
    print("Verified", len(checked_images), "first-party share images with Twitterbot UA.")
    print("Note: real X IP, platform cache and actual feed UI are not observable by this test.")


if __name__ == "__main__":
    try:
        check()
    except (RuntimeError, ValueError, KeyError, UnicodeError) as exc:
        print("X share-card verification FAILED:", str(exc), file=sys.stderr)
        sys.exit(1)
