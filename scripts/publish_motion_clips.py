#!/usr/bin/env python3
"""GitHub-only, opt-in single-run publisher of approved JPEG photo-motion.

The uploader deliberately lives in the GitHub workflow, not here. This script
never calls network services, commits, deploys, or changes Mac settings.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from .motion_clips import candidates, generate, readiness_report, usable_clip
    from .generated_photos import usable_generated_scene
    from .visual_review import reviewed_generated_scene
except ImportError:
    from motion_clips import candidates, generate, readiness_report, usable_clip
    from generated_photos import usable_generated_scene
    from visual_review import reviewed_generated_scene

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path(".github/motion-clips.json")
REPO = "RollNuts/mihassei-news"


def load_settings(root: Path = ROOT) -> dict:
    path = root / CONFIG
    if path.is_symlink():
        raise RuntimeError("Unsafe motion clip config symlink")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Missing/invalid motion clip config; leave automation OFF") from exc
    if (not isinstance(config, dict)
            or config.get("version") != 1
            or type(config.get("enabled")) is not bool
            or type(config.get("max_per_run")) is not int
            or config["max_per_run"] != 1
            or type(config.get("min_free_gib")) is not int
            or not 2 <= config["min_free_gib"] <= 12):
        raise RuntimeError("Invalid motion clip limits; refusing to start")
    return config


def require_trusted_runner(env: dict | None = None) -> None:
    e = os.environ if env is None else env
    if (e.get("GITHUB_ACTIONS") != "true"
            or e.get("GITHUB_REPOSITORY") != REPO
            or e.get("GITHUB_REF") != "refs/heads/main"):
        raise RuntimeError("Automatic publishing only runs inside the owner's main-branch GitHub Actions")


def pending_count(root: Path = ROOT) -> int:
    """Read the selected, approved backlog without encoding or installing anything."""
    if not load_settings(root)["enabled"]:
        return 0
    return len(candidates(root))


def plan(root: Path = ROOT) -> dict:
    """Report editorial waiting states separately from an empty or OFF queue."""
    enabled = load_settings(root)["enabled"]
    stories = readiness_report(root) if enabled else []
    return {"enabled": enabled,
            "pending": sum(story["state"] == "ready" for story in stories),
            "stories": stories}


def newsroom_health(root: Path = ROOT, *, now: datetime | None = None) -> dict:
    """Read-only newsroom freshness, based on article age, not MP4 encode time.

    An older article rendered into a new MP4 is not new programming. A reviewed
    still is a candidate for editorial review, never an automatic selection.
    """
    enabled = load_settings(root)["enabled"]
    if not enabled:
        return {"enabled": False, "status": "disabled"}
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Newsroom health needs a timezone-aware clock")
    articles = json.loads((root / "content/articles.json").read_text(encoding="utf-8"))
    if not isinstance(articles, list):
        raise ValueError("Article source is not a list")

    def published(article: dict) -> datetime:
        value = article.get("published_at") or article["source"]["published_at"]
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Article published_at must have an offset")
        return parsed

    ordered = sorted(articles, key=published, reverse=True)
    clips = []
    for article in ordered:
        if article.get("broadcast_feature") is True:
            clip = usable_clip(article, root=root)
            if clip:
                clips.append((article, clip))
    newest_story = clips[0][0] if clips else None
    latest_pub = published(newest_story) if newest_story else None
    is_stale = bool(ordered and (latest_pub is None or
                     (published(ordered[0]) > latest_pub and
                      now - latest_pub >= timedelta(hours=24))))
    reviewed_newer = []
    if is_stale:
        for article in ordered:
            if latest_pub and published(article) <= latest_pub:
                break
            if (article.get("broadcast_feature") is not True
                    and usable_generated_scene(article, root=root)
                    and reviewed_generated_scene(article, root=root)):
                reviewed_newer.append(article["slug"])
                if len(reviewed_newer) >= 3:
                    break
    pending = len(candidates(root))
    if pending:
        status = "selected_waiting_generation"
    elif is_stale and reviewed_newer:
        status = "selection_needed"
    elif is_stale:
        status = "awaiting_reviewed_source"
    else:
        status = "current"
    return {
        "enabled": True,
        "status": status,
        "latest_article_published_at": (ordered[0].get("published_at")
                                        or ordered[0]["source"]["published_at"]) if ordered else None,
        "latest_video_article_published_at": (newest_story.get("published_at")
                                               or newest_story["source"]["published_at"]) if newest_story else None,
        "latest_video_generated_at": max((clip["created_at"] for _, clip in clips), default=None),
        "selected_pending": pending,
        "reviewed_unselected_newer": reviewed_newer,
    }


def run(root: Path = ROOT, *, env: dict | None = None) -> list[str]:
    config = load_settings(root)
    if not config["enabled"]:
        return []
    require_trusted_runner(env)
    return generate(root, limit=config["max_per_run"],
                    min_free_gib=config["min_free_gib"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only status or bounded trusted-runner motion clip maker")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--status", action="store_true", help="Emit true/false for the durable switch")
    mode.add_argument("--pending", action="store_true", help="Count selected missing clips without encoding")
    mode.add_argument("--plan", action="store_true", help="Explain each selected story's readiness without encoding")
    mode.add_argument("--health", action="store_true", help="Report newsroom freshness and reviewed unselected sources")
    mode.add_argument("--generate", action="store_true", help="Generate at most one video, only on main Actions")
    args = parser.parse_args(argv)
    try:
        if args.status:
            print("true" if load_settings()["enabled"] else "false")
            return 0
        if args.pending:
            print(pending_count())
            return 0
        if args.plan:
            print(json.dumps(plan(), ensure_ascii=False))
            return 0
        if args.health:
            print(json.dumps(newsroom_health(), ensure_ascii=False))
            return 0
        produced = run()
        print("GitHub-only video generation:", produced or "nothing needed / disabled")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print("Motion clip publication halted safely: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
