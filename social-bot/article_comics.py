"""Offline checks for an explicitly reviewed comic attached to an article."""
import hashlib
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from generated_photos import article_fingerprint, review_timestamp, SAFE_SLUG, SHA256

MAX_COMIC_BYTES = 2_000_000
COMIC_PATH = re.compile(r"social-bot/assets/article-comic-[a-z0-9]+(?:-[a-z0-9]+)*\.png\Z")


def approved_comic_asset(article, review, root, now=None):
    """Return a filename and immutable revision, or omit the optional image.

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
            or review.get("article_sha256") != article_fingerprint(article)):
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
        if (len(data) > MAX_COMIC_BYTES or data[:8] != b"\x89PNG\r\n\x1a\n"
                or data[12:16] != b"IHDR"
                or not 0 < int.from_bytes(data[16:20], "big")
                < int.from_bytes(data[20:24], "big") <= 4096
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
    return Path(path).name, revision
