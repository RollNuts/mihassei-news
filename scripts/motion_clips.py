#!/usr/bin/env python3
"""Optional, very small video clips from verified fictional-news JPEGs.

No model, API, installation, network call, cron or git push is performed.
The normal static build imports only the integrity checks (stdlib).
A local operator explicitly invokes --generate with an existing ffmpeg/ffprobe.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

try:
    from .generated_photos import (
        SAFE_SLUG, article_fingerprint, generated_scene_entry, usable_generated_scene
    )
except ImportError:
    from generated_photos import (
        SAFE_SLUG, article_fingerprint, generated_scene_entry, usable_generated_scene
    )

ROOT = Path(__file__).resolve().parents[1]
RELATIVE_DIR = Path("assets/clips")
MANIFEST_NAME = "manifest.json"
WIDTH, HEIGHT, FPS, DURATION_SECONDS = 640, 360, 24, 12
MOTION_STYLE_VERSION = 3
# Do not invalidate already-published four-second clips when a new encoder is
# released. Each style admits only its own exact duration/frame-rate pair.
CLIP_PROFILES = {1: (4000, 12), 2: (4000, 12), 3: (12000, 24)}
MAX_CLIP_BYTES = 450 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024
MAX_CLIPS = 48
MAX_BATCH = 2
MIN_FREE_GIB = 12
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def clip_url(article: dict) -> str:
    slug = article.get("slug")
    if not isinstance(slug, str) or not SAFE_SLUG.fullmatch(slug):
        raise ValueError("Unsafe clip article slug")
    return "/clips/" + slug + ".mp4"


def manifest_entries(root: Path = ROOT) -> dict:
    folder = root / RELATIVE_DIR
    path = folder / MANIFEST_NAME
    if folder.is_symlink() or path.is_symlink():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(document, dict) or document.get("version") != 1:
        return {}
    clips = document.get("clips")
    if not isinstance(clips, dict) or len(clips) > MAX_CLIPS:
        return {}
    # Invalid records must never accidentally open an unbounded media budget.
    total = 0
    for record in clips.values():
        if not isinstance(record, dict) or type(record.get("bytes")) is not int:
            return {}
        if not 0 <= record["bytes"] <= MAX_CLIP_BYTES:
            return {}
        total += record["bytes"]
        if total > MAX_TOTAL_BYTES:
            return {}
    return clips


def usable_clip(article: dict, *, root: Path = ROOT) -> dict | None:
    """Return vetted metadata only when the clip still matches the live image."""
    slug = article.get("slug")
    if not isinstance(slug, str) or not SAFE_SLUG.fullmatch(slug):
        return None
    item = manifest_entries(root).get(slug)
    if not isinstance(item, dict):
        return None
    source = generated_scene_entry(article, root=root)
    if not source or not usable_generated_scene(article, root=root):
        return None
    style = item.get("motion_style_version", 1)
    if (type(style) is not int
            or type(item.get("duration_ms")) is not int
            or type(item.get("fps")) is not int
            or CLIP_PROFILES.get(style) != (item["duration_ms"], item["fps"])):
        return None
    if (item.get("article_sha256") != article_fingerprint(article)
            or item.get("image_sha256") != source.get("sha256")
            or item.get("width") != WIDTH or item.get("height") != HEIGHT
            or item.get("origin") != "verified-still-motion"
            or type(item.get("bytes")) is not int
            or not 1024 <= item["bytes"] <= MAX_CLIP_BYTES
            or not isinstance(item.get("sha256"), str)
            or not DIGEST.fullmatch(item["sha256"])):
        return None
    try:
        created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            return None
    except (KeyError, TypeError, AttributeError, ValueError):
        return None
    folder = root / RELATIVE_DIR
    movie = folder / (slug + ".mp4")
    try:
        if folder.is_symlink() or movie.is_symlink() or movie.stat().st_size != item["bytes"]:
            return None
        data = movie.read_bytes()
    except OSError:
        return None
    # A cheap structural fail-closed gate; ffprobe does full codec checks
    # when the clip is created, never on every website build.
    if (len(data) < 16 or data[4:8] != b"ftyp"
            or b"moov" not in data or b"mdat" not in data
            or hashlib.sha256(data).hexdigest() != item["sha256"]):
        return None
    return item


def movie_bytes(root: Path) -> int:
    folder = root / RELATIVE_DIR
    if folder.is_symlink():
        raise RuntimeError("Unsafe clips directory symlink")
    if not folder.is_dir():
        return 0
    return sum(p.stat().st_size for p in folder.glob("*.mp4")
               if p.is_file() and not p.is_symlink())


def candidates(root: Path = ROOT, *, slug: str | None = None) -> list[dict]:
    articles = json.loads((root / "content/articles.json").read_text(encoding="utf-8"))
    if not isinstance(articles, list):
        raise ValueError("Article source is not a list")
    if slug and not SAFE_SLUG.fullmatch(slug):
        raise ValueError("Unsafe clip slug")
    articles.sort(key=lambda a: (a.get("published_at") or a["source"]["published_at"]), reverse=True)
    result = []
    for article in articles:
        if slug and article["slug"] != slug:
            continue
        # Explicit editor opt-in. A new photograph alone is not a reason to
        # spend CPU or put a looping video on every article.
        if article.get("broadcast_feature") is not True:
            continue
        clip = usable_clip(article, root=root)
        if (usable_generated_scene(article, root=root)
                and (clip is None or clip.get("motion_style_version") != MOTION_STYLE_VERSION)):
            result.append(article)
    return result


def require_mac_not_stopped() -> None:
    """Existing OFF switch is also an OFF switch for optional video generation."""
    if sys.platform == "darwin":
        try:
            from . import mac_scene_control
        except ImportError:
            import mac_scene_control
        mac_scene_control.check_allowed()


def encode_clip(image: Path, output: Path, *, ffmpeg: str, ffprobe: str) -> None:
    """Three four-second camera shots from one verified still, without AI APIs.

    Establish the scene, cut to a closer lateral pan, then pull back to the
    scene. Crop at four times the output resolution to keep slow movement
    smooth instead of jumping a whole output pixel at a time. Hard cuts at
    4s/8s preserve all 288 frames: no black/white transition frames or gaps.
    These are camera movements, not animation of the depicted people.
    """
    shot_frames = FPS * DURATION_SECONDS // 3
    last = shot_frames - 1
    close_progress = f"(on-{shot_frames})/{last}"
    ending_progress = f"(on-{shot_frames * 2})/{last}"
    zoom = (
        f"if(lt(on,{shot_frames}),1.02+0.16*on/{last},"
        f"if(lt(on,{shot_frames * 2}),1.45+0.04*{close_progress},"
        f"1.24-0.22*{ending_progress}))"
    )
    horizontal = (
        f"if(between(on,{shot_frames},{shot_frames * 2 - 1}),"
        f"(iw-iw/zoom)*(0.12+0.76*{close_progress}),(iw-iw/zoom)/2)"
    )
    filter_graph = (
        f"scale={WIDTH * 4}:{HEIGHT * 4}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH * 4}:{HEIGHT * 4},"
        f"zoompan=z='{zoom}':x='{horizontal}':y='(ih-ih/zoom)/2':"
        f"d=1:s={WIDTH}x{HEIGHT}:fps={FPS},format=yuv420p"
    )
    command = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-loop", "1", "-framerate", str(FPS), "-i", str(image),
        "-frames:v", str(FPS * DURATION_SECONDS),
        "-vf", filter_graph, "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
        "-maxrate", "240k", "-bufsize", "480k",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(output),
    ]
    subprocess.run(command, capture_output=True, text=True, timeout=120, check=True)
    if not output.is_file() or not 1024 <= output.stat().st_size <= MAX_CLIP_BYTES:
        raise RuntimeError("Clip over 450 KiB or empty; refusing to publish")
    probe = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format",
         "-of", "json", str(output)],
        capture_output=True, text=True, timeout=15, check=True,
    )
    info = json.loads(probe.stdout)
    streams = info.get("streams", [])
    if (len(streams) != 1 or streams[0].get("codec_type") != "video"
            or streams[0].get("codec_name") != "h264"
            or streams[0].get("width") != WIDTH
            or streams[0].get("height") != HEIGHT
            or streams[0].get("pix_fmt") != "yuv420p"
            or Fraction(streams[0].get("avg_frame_rate", "0/1")) != FPS
            or int(streams[0].get("nb_frames", 0)) != FPS * DURATION_SECONDS
            or not DURATION_SECONDS - 0.1 <= float(info["format"]["duration"]) <= DURATION_SECONDS + 0.1):
        raise RuntimeError("Invalid encoded clip codec, dimensions, audio, frame rate or duration")


def generate(root: Path = ROOT, *, limit: int = 1, slug: str | None = None,
             min_free_gib: int = MIN_FREE_GIB) -> list[str]:
    if not 1 <= limit <= MAX_BATCH:
        raise ValueError("Generate at most two clips per run")
    folder = root / RELATIVE_DIR
    registry = folder / MANIFEST_NAME
    if folder.is_symlink() or registry.is_symlink():
        raise RuntimeError("Unsafe clip directory or manifest symlink")
    # Never overwrite a damaged registry or silently disconnect past clips.
    # The read-only publishing path will simply ignore an invalid registry.
    if registry.exists():
        try:
            original = json.loads(registry.read_text(encoding="utf-8"))
            existing_records = manifest_entries(root)
            if (not isinstance(original, dict) or original.get("version") != 1
                    or not isinstance(original.get("clips"), dict)
                    or existing_records != original["clips"]):
                raise RuntimeError("Invalid video manifest; fix manually without losing clips")
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError("Unreadable video manifest; preserved without overwrite") from exc
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("Existing local ffmpeg and ffprobe required; nothing installed")
    require_mac_not_stopped()
    if shutil.disk_usage(root).free < min_free_gib * 1024**3:
        raise RuntimeError("Less than the safe free-disk floor; no movie created")
    current = candidates(root, slug=slug)
    if not current:
        return []
    folder.mkdir(parents=True, exist_ok=True)
    existing = manifest_entries(root)
    produced = []
    for article in current[:limit]:
        require_mac_not_stopped()
        src = generated_scene_entry(article, root=root)
        if src is None or not usable_generated_scene(article, root=root):
            continue
        image = root / "assets" / "generated" / (article["slug"] + ".jpg")
        movie = folder / (article["slug"] + ".mp4")
        if movie.is_symlink():
            raise RuntimeError("Unsafe existing clip symlink")
        if len(existing) >= MAX_CLIPS and article["slug"] not in existing:
            break
        with tempfile.TemporaryDirectory(prefix=".clip-", dir=folder) as temp:
            staged = Path(temp) / (article["slug"] + ".mp4")
            encode_clip(image, staged, ffmpeg=ffmpeg, ffprobe=ffprobe)
            old_size = movie.stat().st_size if movie.exists() else 0
            size = staged.stat().st_size
            if movie_bytes(root) - old_size + size > MAX_TOTAL_BYTES:
                raise RuntimeError("24 MiB total video budget reached; no clips deleted")
            record = {
                "origin": "verified-still-motion",
                "motion_style_version": MOTION_STYLE_VERSION,
                "article_sha256": article_fingerprint(article),
                "image_sha256": src["sha256"],
                "sha256": hashlib.sha256(staged.read_bytes()).hexdigest(),
                "bytes": size,
                "width": WIDTH, "height": HEIGHT, "fps": FPS,
                "duration_ms": DURATION_SECONDS * 1000,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            new_clips = {**existing, article["slug"]: record}
            staged_manifest = Path(temp) / MANIFEST_NAME
            staged_manifest.write_text(json.dumps({"version": 1, "clips": new_clips},
                                                   ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
            require_mac_not_stopped()
            os.replace(staged, movie)
            os.replace(staged_manifest, folder / MANIFEST_NAME)
            existing = new_clips
            produced.append(article["slug"])
    return produced


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Free local optional motion clips (no network or git writes)")
    parser.add_argument("--generate", action="store_true", help="Explicitly generate local clips")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--slug", default=None)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= MAX_BATCH:
        parser.error("limit must be 1–2")
    try:
        if not args.generate:
            print("Dry run; eligible approved stills:", [
                a["slug"] for a in candidates(ROOT, slug=args.slug)[:args.limit]
            ])
            return 0
        made = generate(ROOT, limit=args.limit, slug=args.slug)
        print("Local clips generated (not automatically pushed):", made)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print("Clip generation safely skipped: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
