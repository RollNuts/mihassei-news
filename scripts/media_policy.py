"""Free, conservative editorial photo gate used by build and photo caching.

An article may publish with no photo or a newly supplied photo immediately:
unless the photo was explicitly approved and locally verified, it receives the
deterministic original newspaper cover instead. No paid APIs or network calls.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
APPROVALS = ROOT / "content" / "approved_photos.json"
MIN_JPEG_BYTES = 16_000
MAX_JPEG_BYTES = 3 * 1024 * 1024


@lru_cache(maxsize=4)
def approved_sources(path: str = str(APPROVALS)) -> dict:
    """A missing or damaged review list must fail closed, not approve all photos."""
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        return {}
    approved = manifest.get("approved")
    if not isinstance(approved, dict):
        return {}
    return {
        slug: review["source"]
        for slug, review in approved.items()
        if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug)
        and isinstance(review, dict)
        and isinstance(review.get("source"), str)
        and review["source"].startswith("https://unsplash.com/photos/")
        and isinstance(review.get("reason"), str)
        and len(review["reason"].strip()) >= 15
    }


def photo_is_approved(article: dict, *, root: Path = ROOT) -> bool:
    photo = article.get("photo")
    if not isinstance(photo, dict):
        return False
    source = photo.get("source")
    if source != approved_sources(str(root / "content" / "approved_photos.json")).get(article.get("slug")):
        return False
    url = photo.get("url", "")
    try:
        image_url, source_url = urlsplit(url), urlsplit(source)
    except (TypeError, ValueError):
        return False
    if (image_url.scheme, image_url.netloc) != ("https", "images.unsplash.com"):
        return False
    if not image_url.path.startswith("/photo-"):
        return False
    if (source_url.scheme, source_url.netloc) != ("https", "unsplash.com"):
        return False
    return (photo.get("license") == "Unsplash"
            and isinstance(photo.get("credit"), str) and bool(photo["credit"].strip())
            and isinstance(photo.get("alt"), str) and bool(photo["alt"].strip()))


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read baseline/progressive JPEG dimensions without external image packages."""
    if not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9"):
        return None
    pos = 2
    while pos + 4 < len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if marker == 0xDA:
            break
        if pos + 2 > len(data):
            break
        length = int.from_bytes(data[pos:pos + 2], "big")
        if length < 2 or pos + length > len(data):
            break
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7):
            if length < 7:
                return None
            height = int.from_bytes(data[pos + 3:pos + 5], "big")
            width = int.from_bytes(data[pos + 5:pos + 7], "big")
            return width, height
        pos += length
    return None


def usable_cached_photo(article: dict, *, root: Path = ROOT) -> bool:
    if not photo_is_approved(article, root=root):
        return False
    path = root / "assets" / "photos" / (article["slug"] + ".jpg")
    try:
        size = path.stat().st_size
        if size < MIN_JPEG_BYTES or size > MAX_JPEG_BYTES:
            return False
        data = path.read_bytes()
    except OSError:
        return False
    dimensions = jpeg_dimensions(data)
    if not dimensions:
        return False
    width, height = dimensions
    # A tall portrait cropped into a 16:9 news thumbnail looks misleading.
    # Very small photos likewise become blurry in large article hero blocks.
    return width >= 900 and height >= 440 and 1.15 <= width / height <= 2.65
