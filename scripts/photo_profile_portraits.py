"""Validate accepted profile portraits without publishing their private sources."""
from __future__ import annotations

import hashlib
import io
import re
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from .photo_issues import ROOT, _local, _supplied_png_dimensions
    from .media_policy import jpeg_dimensions
else:
    from photo_issues import ROOT, _local, _supplied_png_dimensions
    from media_policy import jpeg_dimensions

SHA = re.compile(r"[0-9a-f]{64}\Z")
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
ZERO_UUID = "00000000-0000-0000-0000-000000000000"
FIELDS = {"path", "sha256", "bytes", "width", "height", "source_png", "source", "review"}
MAX_JPEG_BYTES = 700 * 1024
MAX_PNG_BYTES = 32 * 1024 * 1024


def _digest(value):
    return isinstance(value, str) and SHA.fullmatch(value)


def _read_bound(root, record, limit):
    if Path(root).is_symlink():
        raise ValueError("Profile portrait root symlinks are not permitted")
    if (not isinstance(record, dict) or not _digest(record.get("sha256"))
            or type(record.get("bytes")) is not int or not 33 <= record["bytes"] <= limit):
        raise ValueError("Invalid profile portrait file binding")
    path = _local(root, record.get("path"))
    if not path.is_file() or path.stat().st_size != record["bytes"]:
        raise ValueError("Profile portrait file bytes mismatch")
    data = path.read_bytes()
    if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise ValueError("Profile portrait file SHA mismatch")
    return data


def _decoded_dimensions(data, expected_format):
    # Pillow is already part of this repository's free local/CI image tooling.
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(data)) as image:
            dimensions = image.size
            if (image.format != expected_format or min(dimensions) < 320
                    or max(dimensions) > 8192 or dimensions[0] * dimensions[1] > 32_000_000):
                raise ValueError("Invalid profile portrait format/dimensions")
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()  # Reject truncated pixels even when the container header parses.
        return dimensions
    except (OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid profile portrait image bytes") from exc


def validate_profile_portrait(model, *, root=ROOT, now=None):
    """Return an allowlisted public binding, or None when no portrait is assigned."""
    if "profile_portrait" not in model:
        return None
    record = model["profile_portrait"]
    model_id = model.get("id")
    if (not isinstance(model_id, str) or not SLUG.fullmatch(model_id)
            or model.get("fictional") is not True or type(model.get("age")) is not int
            or not 20 <= model["age"] <= 99
            or not isinstance(record, dict) or set(record) != FIELDS
            or not _digest(record.get("sha256"))
            or record.get("path") != f'assets/profile-portraits/{model_id}-{record["sha256"][:12]}.jpg'
            or any(type(record.get(key)) is not int for key in ("width", "height"))):
        raise ValueError("Invalid model profile portrait identity/path")
    source_png, source, review = record["source_png"], record["source"], record["review"]
    if (not isinstance(source_png, dict) or set(source_png) != {"path", "sha256", "bytes"}
            or not _digest(source_png.get("sha256"))
            or source_png.get("path") != f'maintenance/photo-profile-sources/{model_id}/source-{source_png["sha256"]}.png'):
        raise ValueError("Invalid profile portrait original path")
    if (not isinstance(source, dict) or set(source) != {"kind", "chat_url", "turn_id", "gallery_index"}
            or source.get("kind") != "user-supplied-chatgpt-photo"
            or not re.fullmatch(r"https://chatgpt\.com/c/" + UUID, str(source.get("chat_url", "")))
            or source["chat_url"].endswith("/" + ZERO_UUID)
            or not re.fullmatch(UUID, str(source.get("turn_id", ""))) or source["turn_id"] == ZERO_UUID
            or type(source.get("gallery_index")) is not int or not 1 <= source["gallery_index"] <= 1000):
        raise ValueError("Invalid observed profile portrait source")
    if (not isinstance(review, dict) or set(review) != {"decision", "reviewed_at", "note"}
            or review.get("decision") != "accepted" or not isinstance(review.get("note"), str)
            or not review["note"].strip() or len(review["note"]) > 2000):
        raise ValueError("Profile portrait needs an accepted visual review")
    try:
        reviewed_at = datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("Invalid profile portrait review timestamp") from exc
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or reviewed_at.tzinfo is None or reviewed_at > now:
        raise ValueError("Profile portrait review needs a current timezone-aware timestamp")
    original = _read_bound(root, source_png, MAX_PNG_BYTES)
    source_dimensions = _supplied_png_dimensions(original)
    if _decoded_dimensions(original, "PNG") != source_dimensions:
        raise ValueError("Profile portrait PNG dimensions mismatch")
    jpeg = _read_bound(root, record, MAX_JPEG_BYTES)
    actual = jpeg_dimensions(jpeg)
    if actual is None or _decoded_dimensions(jpeg, "JPEG") != actual:
        raise ValueError("Invalid profile portrait JPEG")
    if ((record["width"], record["height"]) != actual
            or any(after > before for after, before in zip(actual, source_dimensions))
            or abs(source_dimensions[0] * actual[1] - source_dimensions[1] * actual[0]) > max(source_dimensions)):
        raise ValueError("Profile portrait dimensions/aspect mismatch")
    return {"path": record["path"], "url": "/" + record["path"] + "?v=" + record["sha256"][:12],
            "sha256": record["sha256"], "bytes": record["bytes"],
            "width": actual[0], "height": actual[1], "reviewed_at": review["reviewed_at"],
            "alt": model["name"] + "（架空の成人モデル）のAI生成プロフィール写真"}


def copy_profile_portrait(portrait, output, *, root=ROOT):
    """Copy only the validated JPEG; source PNG and provenance never enter dist."""
    if Path(output).is_symlink():
        raise ValueError("Profile portrait output symlinks are not permitted")
    data = _read_bound(root, portrait, MAX_JPEG_BYTES)
    destination = _local(Path(output), portrait["path"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.read_bytes() != data:
            raise ValueError("Profile portrait output conflicts with accepted bytes")
    else:
        destination.write_bytes(data)
