"""Resolve an accepted public profile photograph without native-image receipts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import unicodedata

if __package__:
    from .media_policy import jpeg_dimensions
    from .photo_issues import image_path, load_photo_issues, published_issues
    from .photo_stories import load_photo_stories
else:
    from media_policy import jpeg_dimensions
    from photo_issues import image_path, load_photo_issues, published_issues
    from photo_stories import load_photo_stories


PROFILE_ID = "shioba-rio"
PROFILE_NAME = "汐羽りお"
STORY_ID = "shioba-rio-introduction-20261115"
MAX_PORTRAIT = 700 * 1024
SHA = re.compile(r"[0-9a-f]{64}\Z")
JAPANESE = re.compile(r"[ぁ-んァ-ヶ一-龯]")


def _spoken_line(value):
    if value is None:
        return None
    if (not isinstance(value, str) or not 4 <= len(value) <= 28
            or value != value.strip() or not JAPANESE.search(value)
            or any(unicodedata.category(char).startswith("C") for char in value)
            or re.search(r"[<>&\[\]`*_#{}]|(?:https?://|www\.|javascript:|data:)", value, re.I)):
        raise ValueError("Profile speech needs 4–28 plain Japanese characters")
    return value


def _local(root, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Unsafe profile source path")
    path = root
    for part in Path(relative).parts:
        path /= part
        if path.is_symlink():
            raise ValueError("Profile source symlinks are not permitted")
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError("Profile source must be a local regular file")
    return path


def resolve_profile_input(profile_id, spoken_line=None, *, root):
    """Return a read-only profile source binding; no article or generated receipt."""
    if profile_id != PROFILE_ID:
        raise ValueError("Only the 29-year-old fictional Rio profile is enabled")
    spoken_line = _spoken_line(spoken_line)
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Profile root must be a regular repository directory")
    root = root.resolve(strict=True)
    _local(root, "content/photo_issues.json")
    stories_path = _local(root, "content/photo_stories.json")
    catalog = load_photo_issues(root)
    model = next((model for model in catalog["models"] if model["id"] == profile_id), None)
    if (not model or model.get("name") != PROFILE_NAME or model.get("fictional") is not True
            or type(model.get("age")) is not int or not 25 <= model["age"] <= 35
            or model["age"] != 29):
        raise ValueError("Profile must remain the 29-year-old fictional Rio")
    published = published_issues(catalog, root=root)
    posts = load_photo_stories(root, catalog=catalog, published=published)
    post = next((post for post in posts if post["id"] == STORY_ID), None)
    raw = json.loads(stories_path.read_text(encoding="utf-8"))
    story = next((story for story in raw["stories"] if story["id"] == STORY_ID), None)
    if (not post or post.get("modelId") != profile_id or not story
            or story.get("model_id") != profile_id):
        raise ValueError("Rio's current published introduction story is required")
    source = story["source"]
    issue = next((issue for issue in published if issue["slug"] == source["issue_slug"]), None)
    if not issue or issue.get("model_id") != profile_id or source.get("cut_id") != "01":
        raise ValueError("Profile story must use Rio's published cut 01")
    snapshot = issue.get("model_snapshot", {})
    if (snapshot.get("id") != profile_id or snapshot.get("name") != PROFILE_NAME
            or snapshot.get("fictional") is not True or snapshot.get("age") != 29):
        raise ValueError("Profile source model differs from Rio")
    image = issue["images"]["01"]
    review = image.get("review", {})
    if (image.get("path") != image_path(issue, "01")
            or image.get("sha256") != source.get("image_sha256")
            or not SHA.fullmatch(str(image.get("sha256", "")))
            or review.get("decision") != "accepted" or review.get("cut_id") != "01"
            or review.get("image_sha256") != image["sha256"]
            or review.get("issue_sha256") != issue["issue_sha256"]):
        raise ValueError("Profile source needs its matching accepted issue review")
    portrait = _local(root, image["path"])
    data = portrait.read_bytes()
    dimensions = jpeg_dimensions(data)
    if (not 1024 <= len(data) <= MAX_PORTRAIT
            or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9")
            or dimensions is None or min(dimensions) < 512 or max(dimensions) > 2048
            or dimensions != (image.get("width"), image.get("height"))
            or image.get("bytes") != len(data)
            or hashlib.sha256(data).hexdigest() != image["sha256"]):
        raise ValueError("Profile portrait JPEG bytes or dimensions changed")
    record = {"source_kind": "photo_profile", "profile_id": profile_id,
              "name": model["name"], "fictional": True, "intended_age": model["age"],
              "story_id": STORY_ID, "issue_slug": issue["slug"],
              "issue_sha256": issue["issue_sha256"], "cut_id": "01",
              "image_sha256": image["sha256"], "path": image["path"],
              "bytes": len(data), "dimensions": list(dimensions)}
    provenance = image.get("provenance", {})
    if provenance.get("kind") == "user_supplied_photo":
        receipt = json.loads(_local(root, provenance["receipt_path"]).read_text(encoding="utf-8"))
        original = receipt.get("source", {})
        if (original.get("format", "png") == "png"
                and SHA.fullmatch(str(original.get("sha256", "")))
                and original["sha256"] == image.get("source_sha256")):
            record["source_png_sha256"] = original["sha256"]
    context = {"source_kind": "photo_profile", "profile_id": profile_id,
               "profile_name": model["name"], "story_id": STORY_ID,
               "source_issue_slug": issue["slug"], "source_issue_sha256": issue["issue_sha256"],
               "source_cut_id": "01", "source_image_sha256": image["sha256"]}
    return {"portrait": portrait, "portrait_record": record,
            "context": context, "spoken_line": spoken_line}
