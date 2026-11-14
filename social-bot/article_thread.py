"""Deterministic X article threads and exact, reviewed image evidence.

No Buffer requests or writes here. Preparing a thread downloads only the
immutable public image, after local review and committed-byte validation.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from article_comics import approved_comic_asset, CAPTION_LINK, MAX_COMIC_BYTES
from editorial_copy import reader_copy
from generated_photos import article_fingerprint, generated_scene_entry, RELATIVE_DIR, share_page_url
from media_policy import usable_cached_photo
from social_cards import reviewed_scene_png
from social_text import x_share_lead, truncate_x_text, x_weighted_length, X_TEXT_LIMIT
from visual_review import reviewed_generated_scene

TRACKING_QUERY = "?utm_source=x&utm_medium=social&utm_campaign=article_auto"
RAW_BASE = "https://raw.githubusercontent.com/RollNuts/mihassei-news/"
SAFE_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
IMAGE_PATH = re.compile(r"(?:assets/(?:generated|photos)/[a-z0-9-]+\.jpg|assets/editorial-scenes/[a-z0-9-]+\.png|social-bot/assets/article-comic-[a-z0-9-]+\.png)\Z")
# Asset.source is the original file URL in Buffer's public schema. Require an
# exact match; a provider rewrite is an unresolved audit, never permission to retry.
THREAD_FIELDS = "assets { source mimeType } metadata { ... on TwitterPostMetadata { thread { text assets { source mimeType } } } }"


class ArticleImageUnavailable(RuntimeError):
    """No verified parent image; no Buffer submission is permitted."""


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_url(slug):
    if not isinstance(slug, str) or not SAFE_SLUG.fullmatch(slug):
        raise ValueError("Unsafe article slug")
    return "https://veripsa.com/articles/" + slug + "/"


def is_article_reply_url(url, slug, *, allow_legacy=False):
    base = canonical_url(slug)
    if allow_legacy and url == base:
        return True
    return isinstance(url, str) and re.fullmatch(
        re.escape(base + TRACKING_QUERY) + r"(?:&image=[0-9a-f]{12})?", url) is not None


def article_reply_url(article, *, root=ROOT):
    # Use the article's existing share image version even when a separately
    # approved comic is attached to the X parent.
    url = share_page_url(article, canonical_url(article.get("slug")) + TRACKING_QUERY, root=Path(root))
    if not is_article_reply_url(url, article["slug"]):
        raise ValueError("Unsafe tracked article reply URL")
    return url


def parent_text(article, caption=None):
    """Always retain the title and one short existing hook; no new copy model."""
    canonical_url(article.get("slug"))
    title = article.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Missing article title")
    title = re.sub(r"\s+", " ", reader_copy(title.strip()))
    if CAPTION_LINK.search(title) or "#" in title or "＃" in title:
        raise ValueError("Article title contains a URL or hashtag")
    title = truncate_x_text(title, 174)
    hook = caption or x_share_lead(article, "\n\n" + title)
    if hook is None:
        hook = article.get("summary")
    if not isinstance(hook, str) or not hook.strip():
        raise ValueError("Missing article hook")
    hook = re.sub(r"\s+", " ", reader_copy(hook.strip()))
    if CAPTION_LINK.search(hook) or "#" in hook or "＃" in hook:
        raise ValueError("Article hook contains a URL or hashtag")
    hook = truncate_x_text(hook, min(100, X_TEXT_LIMIT - x_weighted_length(title + "\n\n")))
    if x_weighted_length(hook) < 12:
        raise ValueError("Article hook is too short")
    return title + "\n\n" + hook


def thread_digest(thread):
    return digest(json.dumps(thread, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def validate_thread(thread, slug):
    if not isinstance(thread, dict) or thread.get("schema_version") != 1:
        raise ValueError("Invalid article thread evidence")
    parent, reply, image = thread.get("parent_text"), thread.get("reply_text"), thread.get("image")
    if (not isinstance(parent, str) or not parent.strip() or CAPTION_LINK.search(parent)
            or "#" in parent or "＃" in parent or x_weighted_length(parent) > X_TEXT_LIMIT
            or not is_article_reply_url(reply, slug, allow_legacy=True) or not SHA256.fullmatch(str(thread.get("article_sha256", "")))
            or not isinstance(image, dict) or not IMAGE_PATH.fullmatch(str(image.get("path", "")))
            or not SHA256.fullmatch(str(image.get("sha256", "")))
            or not re.fullmatch(r"[0-9a-f]{40}", str(image.get("revision", "")))
            or image.get("url") != RAW_BASE + image["revision"] + "/" + image["path"]):
        raise ValueError("Invalid article thread body/image binding")
    return thread


def prepare_article_thread(article, comic_review=None, *, root=ROOT):
    """Fail closed on absent/stale images or a public download/hash mismatch."""
    root = Path(root).resolve()
    slug = article.get("slug")
    canonical_url(slug)
    caption = None
    try:
        if comic_review is not None:
            comic = approved_comic_asset(article, comic_review, root)
            if comic is None:
                raise ArticleImageUnavailable("Reviewed article comic is unavailable")
            path, caption = comic["path"], comic["caption"]
        elif article.get("visual_strategy", "cpu-photo") != "illustration" and reviewed_generated_scene(article, root=root):
            generation = generated_scene_entry(article, root=root)
            if generation is None:
                raise ArticleImageUnavailable("Article photo review changed")
            path = (RELATIVE_DIR / (slug + ".jpg")).as_posix()
        elif reviewed_scene_png(article, root=root) is not None:
            path = "assets/editorial-scenes/" + slug + ".png"
        elif article.get("visual_strategy", "cpu-photo") != "illustration" and usable_cached_photo(article, root=root):
            path = "assets/photos/" + slug + ".jpg"
        else:
            raise ArticleImageUnavailable("No current reviewed article raster")
        local = root
        for part in Path(path).parts:
            local = local / part
            if local.is_symlink():
                raise ArticleImageUnavailable("Unsafe article image path")
        data = local.read_bytes()
        if not 24 <= len(data) <= MAX_COMIC_BYTES:
            raise ArticleImageUnavailable("Invalid article image size")
        image_sha = hashlib.sha256(data).hexdigest()
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                  text=True, check=True, timeout=10).stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ArticleImageUnavailable("Missing committed image revision")
        committed = subprocess.run(["git", "show", revision + ":" + path], cwd=root,
                                   capture_output=True, check=True, timeout=10).stdout
        if hashlib.sha256(committed).hexdigest() != image_sha:
            raise ArticleImageUnavailable("Article image differs from committed bytes")
        url = RAW_BASE + revision + "/" + path
        request = Request(url, headers={"User-Agent": "veripsa-article-image/1.0"})
        expected_type = "image/png" if path.endswith(".png") else "image/jpeg"
        with urlopen(request, timeout=20) as response:
            if response.status != 200 or response.headers.get("Content-Type", "").split(";", 1)[0] != expected_type:
                raise ArticleImageUnavailable("Article image download is not a raster")
            downloaded = response.read(MAX_COMIC_BYTES + 1)
        if len(downloaded) != len(data) or hashlib.sha256(downloaded).hexdigest() != image_sha:
            raise ArticleImageUnavailable("Article image download does not match reviewed bytes")
        thread = {"schema_version": 1, "article_sha256": article_fingerprint(article),
                  "parent_text": parent_text(article, caption), "reply_text": article_reply_url(article, root=root),
                  "image": {"path": path, "sha256": image_sha, "revision": revision, "url": url}}
        return validate_thread(thread, slug)
    except ArticleImageUnavailable:
        raise
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ArticleImageUnavailable("Article image preparation failed; nothing submitted") from exc


def thread_items(post):
    metadata = post.get("metadata") if isinstance(post, dict) else None
    items = metadata.get("thread") if isinstance(metadata, dict) else None
    return items if isinstance(items, list) else []


def post_texts(post):
    return [text for text in [post.get("text"), *[
        item.get("text") for item in thread_items(post) if isinstance(item, dict)
    ]] if isinstance(text, str)]


def remote_thread_mismatch(post, thread):
    """Match one Buffer receipt to the exact two texts and parent image source."""
    items = thread_items(post)
    if len(items) != 2 or any(not isinstance(item, dict) for item in items):
        return "thread_items_missing_or_partial"
    if (post.get("text") != thread["parent_text"] or items[0].get("text") != thread["parent_text"]
            or items[1].get("text") != thread["reply_text"]):
        return "thread_text_mismatch"
    assets = items[0].get("assets")
    if (not isinstance(assets, list) or len(assets) != 1 or not isinstance(assets[0], dict)
            or assets[0].get("source") != thread["image"]["url"]
            or assets[0].get("mimeType") not in ("image/jpeg", "image/png")
            or items[1].get("assets") != []):
        return "thread_image_source_mismatch"
    return None


def image_sources(post):
    """Only public provider asset source observations, never inferred hashes."""
    sources = []
    for item in [post, *thread_items(post)]:
        if not isinstance(item, dict) or not isinstance(item.get("assets"), list):
            continue
        sources.extend(asset["source"] for asset in item["assets"]
                       if isinstance(asset, dict) and isinstance(asset.get("source"), str))
    return sources


def image_article_slugs(post):
    pattern = re.compile(re.escape(RAW_BASE) + r"[0-9a-f]{40}/assets/(generated|photos|editorial-scenes)/([a-z0-9]+(?:-[a-z0-9]+)*)\.(jpg|png)\Z")
    return {match[2] for source in image_sources(post)
            if (match := pattern.fullmatch(source))
            and (match[1] == "editorial-scenes") == (match[3] == "png")}
