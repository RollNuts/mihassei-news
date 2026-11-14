"""Offline checks for an explicitly reviewed comic attached to an article."""
import hashlib
import re
import subprocess
import sys
import unicodedata
import zlib
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from generated_photos import article_fingerprint, review_timestamp, SAFE_SLUG, SHA256

MAX_COMIC_BYTES = 4_000_000
MAX_CAPTION_CHARACTERS = 100
COMIC_PATH = re.compile(r"social-bot/assets/article-comic-[a-z0-9]+(?:-[a-z0-9]+)*\.png\Z")
CAPTION_LINK = re.compile(r"[a-z][a-z0-9+.-]*://|www\.|[a-z0-9-]+\.[a-z]{2,}(?:(?a:\b)|/)", re.I)


def valid_caption(caption, digest):
    if (not isinstance(caption, str) or caption != caption.strip()
            or not 1 <= len(caption) <= MAX_CAPTION_CHARACTERS
            or len(caption.splitlines()) != 1
            or any(unicodedata.category(ch).startswith("C") for ch in caption)
            or any(ch in caption for ch in "#＃`[]<>")
            or CAPTION_LINK.search(caption)
            or not any(ch.isalnum() for ch in caption)
            or not 1 <= len([part for part in re.split(r"[。！？!?]+|\.(?:\s|$)", caption)
                             if part.strip()]) <= 2):
        return False
    return hashlib.sha256(caption.encode("utf-8")).hexdigest() == digest


def valid_comic_png(data):
    """Check bounded PNG chunk structure/CRCs, without decoding image pixels."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return False
    offset, has_pixels = 8, False
    while offset + 12 <= len(data):
        size = int.from_bytes(data[offset:offset + 4], "big")
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + size
        if end > len(data):
            return False
        chunk = data[offset + 4:end - 4]
        if zlib.crc32(chunk) & 0xffffffff != int.from_bytes(data[end - 4:end], "big"):
            return False
        if offset == 8:
            if (kind != b"IHDR" or size != 13
                    or not 0 < int.from_bytes(data[16:20], "big")
                    < int.from_bytes(data[20:24], "big") <= 4096):
                return False
        elif kind == b"IHDR":
            return False
        if kind == b"IDAT" and size:
            has_pixels = True
        if kind == b"IEND":
            return size == 0 and end == len(data) and has_pixels
        offset = end
    return False


def approved_comic_asset(article, review, root, now=None):
    """Return the reviewed image/caption and revision, or omit the image.

    The owner record binds the current article, local PNG bytes and the same
    bytes committed at HEAD. No download, approval creation or publishing here.
    """
    if not isinstance(article, dict) or not isinstance(review, dict):
        return None
    if not isinstance(article.get("slug"), str) or not SAFE_SLUG.fullmatch(article["slug"]):
        return None
    now = now or datetime.now(timezone.utc)
    reviewed = review_timestamp(review.get("reviewed_at"))
    if (review.get("approved_by") != "owner" or reviewed is None or reviewed > now
            or review.get("article_sha256") != article_fingerprint(article)
            or not valid_caption(review.get("caption"), review.get("caption_sha256"))):
        return None
    image_sha = review.get("image_sha256")
    path = review.get("path")
    if (not isinstance(image_sha, str) or not SHA256.fullmatch(image_sha)
            or not isinstance(path, str) or not COMIC_PATH.fullmatch(path)):
        return None
    root = Path(root).resolve()
    try:
        local = root
        for part in Path(path).parts:
            local = local / part
            if local.is_symlink():
                return None
        if not local.is_file() or not 24 <= local.stat().st_size <= MAX_COMIC_BYTES:
            return None
        data = local.read_bytes()
        if (len(data) > MAX_COMIC_BYTES or not valid_comic_png(data)
                or hashlib.sha256(data).hexdigest() != image_sha):
            return None
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
            text=True, check=True, timeout=10,
        ).stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            return None
        committed = subprocess.run(
            ["git", "show", revision + ":" + path], cwd=root,
            capture_output=True, check=True, timeout=10,
        ).stdout
        if hashlib.sha256(committed).hexdigest() != image_sha:
            return None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return {"filename": Path(path).name, "revision": revision,
            **{key: review[key] for key in ("article_sha256", "image_sha256", "path",
                                           "caption", "caption_sha256")}}
