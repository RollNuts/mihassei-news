"""Small, local photo-motion clips bound to accepted story photographs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from .motion_clips import (WIDTH, HEIGHT, FPS, DURATION_SECONDS, MAX_CLIP_BYTES,
                               encode_clip, require_mac_not_stopped, mac_resource_guard,
                               MIN_FREE_GIB, MAX_TOTAL_BYTES, movie_bytes)
else:
    from motion_clips import (WIDTH, HEIGHT, FPS, DURATION_SECONDS, MAX_CLIP_BYTES,
                              encode_clip, require_mac_not_stopped, mac_resource_guard,
                              MIN_FREE_GIB, MAX_TOTAL_BYTES, movie_bytes)

FIELDS = {"kind", "path", "sha256", "bytes", "image_sha256", "created_at",
          "width", "height", "duration_ms", "fps", "codec", "pixel_format"}


def validate_story_motion(record, *, story_id, image_sha256, root):
    root = Path(root)
    if root.is_symlink():
        raise ValueError("Unsafe story motion root")
    root = root.resolve()
    if not isinstance(record, dict) or set(record) != FIELDS:
        raise ValueError("Invalid story motion record")
    expected = "assets/photo-story-clips/" + story_id + ".mp4"
    if (not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", story_id)
            or record["path"] != expected or record["kind"] != "photo-motion"
            or record["image_sha256"] != image_sha256
            or record["codec"] != "h264" or record["pixel_format"] != "yuv420p"
            or any(type(record[k]) is not int for k in ("width", "height", "fps", "duration_ms", "bytes"))
            or (record["width"], record["height"], record["fps"], record["duration_ms"]) !=
               (WIDTH, HEIGHT, FPS, DURATION_SECONDS * 1000)
            or not 1024 <= record["bytes"] <= MAX_CLIP_BYTES
            or not isinstance(record["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"])):
        raise ValueError("Story motion does not match its source or encoding")
    try:
        stamp = datetime.fromisoformat(record["created_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid story motion creation time") from exc
    path = root / expected
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError("Unsafe or missing story motion file")
    data = path.read_bytes()
    if (len(data) != record["bytes"] or data[4:8] != b"ftyp" or b"moov" not in data or b"mdat" not in data
            or hashlib.sha256(data).hexdigest() != record["sha256"]):
        raise ValueError("Story motion bytes changed")
    return {"url": "/photo/story-clips/" + path.name + "?v=" + record["sha256"][:12],
            "width": WIDTH, "height": HEIGHT,
            "durationSeconds": DURATION_SECONDS}


def copy_story_motion(stories, output, *, root):
    output = Path(output)
    if output.is_symlink():
        raise ValueError("Unsafe story motion output")
    output = output.resolve()
    for story in stories:
        motion = story.get("motion")
        if motion:
            target = output / motion["url"].split("?", 1)[0].lstrip("/")
            if any(p.is_symlink() for p in (target, *target.parents)):
                raise ValueError("Unsafe story motion output")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(root) / "assets/photo-story-clips" / target.name, target)


def generate(story_id, *, root):
    # Validate the actual published JPEG first; never borrow an article identity.
    from .photo_stories import load_photo_stories
    from .photo_issues import load_photo_issues
    root = Path(root)
    stories = load_photo_stories(root)
    selected = next(story for story in stories if story["id"] == story_id)
    catalog = load_photo_issues(root)
    creator_path = root / "content/photo_stories" / (selected["modelId"] + ".json")
    document_path = root / "content/photo_stories.json"
    if creator_path.is_file() and any(row["id"] == story_id for row in
                                      json.loads(creator_path.read_text(encoding="utf-8"))["stories"]):
        document_path = creator_path
    original = document_path.read_bytes()
    document = json.loads(original)
    raw = next(story for story in document["stories"] if story["id"] == story_id)
    if "motion" in raw:
        raise ValueError("Existing story motion must not be overwritten")
    issue = next(issue for issue in catalog["issues"] if issue["slug"] == raw["source"]["issue_slug"])
    image = root / issue["images"][raw["source"]["cut_id"]]["path"]
    relative = "assets/photo-story-clips/" + story_id + ".mp4"
    target = root / relative
    if target.exists() or any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError("Story motion output already exists or is unsafe")
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("Existing local FFmpeg and ffprobe required")
    guard = mac_resource_guard()

    def check_running(*, force=False):
        require_mac_not_stopped()
        if shutil.disk_usage(root).free < MIN_FREE_GIB * 1024**3:
            raise RuntimeError("Free storage reached the safety floor; movie deferred")
        if guard is not None:
            guard.check(force=force)

    check_running(force=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".story-motion-", dir=target.parent) as folder:
        staged = Path(folder) / target.name
        encode_clip(image, staged, ffmpeg=ffmpeg, ffprobe=ffprobe, check=check_running,
                    preserve_frame=True)
        size = staged.stat().st_size
        existing = movie_bytes(root) + sum(p.stat().st_size for p in target.parent.glob("*.mp4"))
        if existing + size > MAX_TOTAL_BYTES:
            raise RuntimeError("Total video budget reached; no clips deleted")
        raw["motion"] = {"kind": "photo-motion", "path": relative,
                         "sha256": hashlib.sha256(staged.read_bytes()).hexdigest(),
                         "bytes": size, "image_sha256": raw["source"]["image_sha256"],
                         "created_at": datetime.now(timezone.utc).isoformat(),
                         "width": WIDTH, "height": HEIGHT, "duration_ms": DURATION_SECONDS * 1000,
                         "fps": FPS, "codec": "h264", "pixel_format": "yuv420p"}
        staged_document = Path(folder) / "photo_stories.json"
        staged_document.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        check_running(force=True)
        if document_path.read_bytes() != original or target.exists():
            raise RuntimeError("Story changed during encoding; preserved newer data")
        os.replace(staged, target)
        validate_story_motion(raw["motion"], story_id=selected["id"],
                              image_sha256=raw["source"]["image_sha256"], root=root)
        os.replace(staged_document, document_path)
    return raw["motion"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("story_id")
    args = parser.parse_args()
    print(json.dumps(generate(args.story_id, root=Path(__file__).resolve().parents[1]), indent=2))
