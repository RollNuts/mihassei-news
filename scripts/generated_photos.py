"""Integrity gate for locally generated fictional comedy-news photographs.

Only validated first-party 1200x675 JPEGs with a matching manifest can replace
existing reviewed photos or the standard SVG fallback. The static site build
needs Python's standard library only, and never calls an image API.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

try:
    from .media_policy import jpeg_dimensions, MIN_JPEG_BYTES, MAX_JPEG_BYTES
except ImportError:
    from media_policy import jpeg_dimensions, MIN_JPEG_BYTES, MAX_JPEG_BYTES

ROOT = Path(__file__).resolve().parent.parent
RELATIVE_DIR = Path("assets/generated")
MANIFEST_NAME = "manifest.json"
SAFE_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
PROMPT_VERSION = 2
MAX_SCENE_BYTES = 700 * 1024
MAX_ARCHIVE_BYTES = 1536 * 1024**2
MAX_BATCH = 4
MIN_FREE_GIB = 12


def article_fingerprint(article: dict) -> str:
    """Body/punchline and visual-direction edits also invalidate old scenes."""
    fields = {k: article.get(k, "") for k in
              ("slug", "title", "summary", "category", "paragraphs", "scene_prompt")}
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def generated_manifest(root: Path = ROOT) -> dict:
    try:
        obj = json.loads((root / RELATIVE_DIR / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(obj, dict) or obj.get("version") != 1:
        return {}
    images = obj.get("images")
    return images if isinstance(images, dict) else {}


def visually_held_scene(article: dict, *, root: Path = ROOT) -> bool:
    """A human visual rejection overrides otherwise valid JPEG hashes.

    This is a hold for the exact article fingerprint. Once the scene direction
    or story changes, a fresh generated image can be reviewed again.
    """
    slug = article.get("slug")
    if not isinstance(slug, str) or not SAFE_SLUG.fullmatch(slug):
        return False
    try:
        payload = json.loads((root / RELATIVE_DIR / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return False
    rejected = payload.get("rejected")
    if not isinstance(rejected, dict):
        return False
    record = rejected.get(slug)
    return (isinstance(record, dict)
            and record.get("article_sha256") == article_fingerprint(article))


def generated_scene_entry(article: dict, *, root: Path = ROOT) -> dict | None:
    slug = article.get("slug")
    if not isinstance(slug, str) or not SAFE_SLUG.fullmatch(slug):
        return None
    if visually_held_scene(article, root=root):
        return None
    record = generated_manifest(root).get(slug)
    if not isinstance(record, dict):
        return None
    origin, model = record.get("origin"), record.get("model")
    if origin == "local-comfyui":
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+\.safetensors", model):
            return None
    elif origin == "github-cpu-lcm":
        # Model weights run inside a GitHub-hosted CPU job, without image APIs.
        if model != "SimianLuo/LCM_Dreamshaper_v7":
            return None
    else:
        return None
    if (record.get("article_sha256") != article_fingerprint(article)
            or record.get("prompt_version") != PROMPT_VERSION
            or not isinstance(record.get("sha256"), str)
            or not SHA256.fullmatch(record["sha256"])):
        return None
    size = record.get("bytes")
    if type(size) is not int or not MIN_JPEG_BYTES <= size <= MAX_SCENE_BYTES:
        return None
    return record


def usable_generated_scene(article: dict, *, root: Path = ROOT) -> bool:
    record = generated_scene_entry(article, root=root)
    if not record:
        return False
    slug = article["slug"]
    path = root / RELATIVE_DIR / (slug + ".jpg")
    try:
        if path.is_symlink() or (root / RELATIVE_DIR).is_symlink():
            return False
        if path.stat().st_size != record["bytes"]:
            return False
        data = path.read_bytes()
    except OSError:
        return False
    return (hashlib.sha256(data).hexdigest() == record["sha256"]
            and jpeg_dimensions(data) == (1200, 675))


def generated_url(article: dict) -> str:
    slug = article["slug"]
    if not SAFE_SLUG.fullmatch(slug):
        raise ValueError("Invalid article slug")
    return "/generated/" + slug + ".jpg"
