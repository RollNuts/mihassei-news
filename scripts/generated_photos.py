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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:
    from .cpu_photo_model import (
        MODEL as CPU_MODEL, LEGACY_MODEL, INFERENCE as CPU_INFERENCE,
        LCM_INFERENCE, ORIGIN as CPU_ORIGIN, REFERENCE_ORIGIN,
        REFERENCE_INFERENCE, reference_metadata_valid,
    )
    from .media_policy import jpeg_dimensions, MIN_JPEG_BYTES, MAX_JPEG_BYTES
except ImportError:
    from cpu_photo_model import (
        MODEL as CPU_MODEL, LEGACY_MODEL, INFERENCE as CPU_INFERENCE,
        LCM_INFERENCE, ORIGIN as CPU_ORIGIN, REFERENCE_ORIGIN,
        REFERENCE_INFERENCE, reference_metadata_valid,
    )
    from media_policy import jpeg_dimensions, MIN_JPEG_BYTES, MAX_JPEG_BYTES

ROOT = Path(__file__).resolve().parent.parent
RELATIVE_DIR = Path("assets/generated")
MANIFEST_NAME = "manifest.json"
SAFE_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
# v3 makes documentary/news-photo realism explicit for new generations.
# Keep v2 readable so already-reviewed photographs do not disappear merely
# because the prompt policy improved; new writes always use PROMPT_VERSION.
PROMPT_VERSION = 3
SUPPORTED_PROMPT_VERSIONS = frozenset((2, PROMPT_VERSION))
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
    elif origin in ("github-cpu-lcm", CPU_ORIGIN, REFERENCE_ORIGIN):
        # Model weights run inside a GitHub-hosted CPU job, without image APIs.
        if model not in (LEGACY_MODEL, CPU_MODEL):
            return None
        if model == LEGACY_MODEL and origin != "github-cpu-lcm":
            return None
        expected = (REFERENCE_INFERENCE if origin == REFERENCE_ORIGIN else
                    CPU_INFERENCE if origin == CPU_ORIGIN else LCM_INFERENCE)
        if model == CPU_MODEL and record.get("inference") != expected:
            return None
        if origin == REFERENCE_ORIGIN:
            # Record immutable input provenance; the historical SVG need not
            # remain current after a reviewed photo replaces its article copy.
            if not reference_metadata_valid(record, article_sha256=article_fingerprint(article)):
                return None
            # Import lazily: the local writer also imports this integrity gate.
            try:
                from .local_scene_photos import stable_seed
            except ImportError:
                from local_scene_photos import stable_seed
            if type(record.get("seed")) is not int or record["seed"] != stable_seed(article):
                return None
    elif origin == "chatgpt-imagegen":
        # A manually imported ChatGPT-native generation, not a CPU/Mac result.
        # Require the exact source marker and generation receipt, plus the
        # independent fingerprint/hash and human-review gates below.
        generation_id = record.get("generation_id")
        if (model != "ChatGPT ImageGen"
                or not isinstance(generation_id, str)
                or not re.fullmatch(
                    r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", generation_id)):
            return None
    else:
        return None
    if (record.get("article_sha256") != article_fingerprint(article)
            or record.get("prompt_version") not in SUPPORTED_PROMPT_VERSIONS
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


def generated_url(article: dict, *, root: Path = ROOT) -> str:
    """Keep image URLs stable until verified generation bytes change."""
    slug = article["slug"]
    if not SAFE_SLUG.fullmatch(slug):
        raise ValueError("Invalid article slug")
    path = "/generated/" + slug + ".jpg"
    record = generated_scene_entry(article, root=root)
    return path + ("?v=" + record["sha256"][:12] if record else "")


def share_page_url(article: dict, url: str, *, root: Path = ROOT) -> str:
    """Version shared pages for their validated raster, keeping SEO identity."""
    try:
        from .visual_review import reviewed_generated_scene
    except ImportError:
        from visual_review import reviewed_generated_scene
    record = generated_scene_entry(article, root=root)
    version = None
    if (article.get("visual_strategy", "cpu-photo") != "illustration"
            and record and reviewed_generated_scene(article, root=root)
            and generated_scene_entry(article, root=root) == record):
        version = record["sha256"][:12]
    if version is None:
        try:
            from .social_cards import reviewed_scene_version
        except ImportError:
            from social_cards import reviewed_scene_version
        version = reviewed_scene_version(article, root=root)
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if not version and not any(key == "image" for key, _value in query):
        return url
    query = [(key, value) for key, value in query if key != "image"]
    if version:
        query.append(("image", version))
    return urlunsplit((*parts[:3], urlencode(query), parts.fragment))
