#!/usr/bin/env python3
"""Read-only publication gate for explicitly recorded visual reviews.

JPEG integrity alone is not a visual review. This module checks stored decisions
against the current article and image bytes; it does not judge image relevance,
approve images, generate assets, or change generation-worker state.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

try:
    from .generated_photos import (
        ROOT, SAFE_SLUG, article_fingerprint, generated_scene_entry,
        usable_generated_scene, visually_held_scene,
    )
except ImportError:
    from generated_photos import (
        ROOT, SAFE_SLUG, article_fingerprint, generated_scene_entry,
        usable_generated_scene, visually_held_scene,
    )

REVIEW_FILE = Path("content/visual_reviews.json")
STATUSES = ("accepted", "pending", "rejected", "missing")


def _review_records(root: Path) -> dict:
    try:
        payload = json.loads((root / REVIEW_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}
    if (not isinstance(payload, dict)
            or type(payload.get("version")) is not int
            or payload["version"] != 1):
        return {}
    reviews = payload.get("reviews")
    return reviews if isinstance(reviews, dict) else {}


def _timezone_qualified_iso(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        timestamp = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
        return timestamp.tzinfo is not None and timestamp.utcoffset() is not None
    except (ValueError, OverflowError):
        return False


def _matching_review(article: dict, generation: dict, root: Path) -> dict | None:
    review = _review_records(root).get(article["slug"])
    if not isinstance(review, dict):
        return None
    note = review.get("note")
    if (review.get("decision") not in ("accepted", "rejected")
            or review.get("article_sha256") != article_fingerprint(article)
            or review.get("image_sha256") != generation["sha256"]
            or not _timezone_qualified_iso(review.get("reviewed_at"))
            or not isinstance(note, str) or not note.strip()):
        return None
    return review


def _scene_state(article: dict, *, root: Path) -> dict:
    slug = article.get("slug") if isinstance(article, dict) else None
    row = {"slug": slug, "status": "missing", "reason": "no_current_verified_jpeg"}
    if not isinstance(slug, str) or not SAFE_SLUG.fullmatch(slug):
        return {**row, "reason": "invalid_article_slug"}
    if visually_held_scene(article, root=root):
        return {**row, "status": "rejected", "reason": "current_visual_hold"}
    generation = generated_scene_entry(article, root=root)
    if generation is None or not usable_generated_scene(article, root=root):
        return row
    # A concurrent replacement must not inherit the integrity result for the
    # previous JPEG. A later read can reconsider the complete new generation.
    if generated_scene_entry(article, root=root) != generation:
        return {**row, "reason": "generation_changed_during_check"}
    review = _matching_review(article, generation, root)
    if review is None:
        return {**row, "status": "pending", "reason": "no_current_valid_visual_review"}
    return {
        **row,
        "status": review["decision"],
        "reason": "matching_visual_review_record",
        "article_sha256": review["article_sha256"],
        "image_sha256": review["image_sha256"],
        "reviewed_at": review["reviewed_at"],
        "note": review["note"],
    }


def reviewed_generated_scene(article: dict, *, root: Path = ROOT) -> bool:
    """Allow publication only for a current, intact, visually accepted JPEG."""
    return _scene_state(article, root=Path(root))["status"] == "accepted"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="保存済みの画像目視レビュー記録を照合する、読み取り専用の一覧。"
    )
    parser.add_argument("--root", type=Path, default=ROOT, help="対象リポジトリのディレクトリ")
    args = parser.parse_args(argv)
    try:
        articles = json.loads((args.root / "content/articles.json").read_text(encoding="utf-8"))
        if not isinstance(articles, list) or any(
            not isinstance(article, dict)
            or not isinstance(article.get("slug"), str)
            or not SAFE_SLUG.fullmatch(article["slug"])
            for article in articles
        ):
            raise ValueError("記事の配列と安全なslugが必要です")
        if len({article["slug"] for article in articles}) != len(articles):
            raise ValueError("記事のslugが重複しています")
    except (OSError, UnicodeError, ValueError) as error:
        print(json.dumps({"error": "記事データを読み込めません", "detail": str(error)},
                         ensure_ascii=False))
        return 2
    rows = [_scene_state(article, root=args.root)
            for article in sorted(articles, key=lambda article: article["slug"])]
    print(json.dumps({
        "version": 1,
        "basis": "保存済みの目視レビュー記録とJPEGの整合性を照合。画像の意味適合は自動判定しません。",
        "counts": {status: sum(row["status"] == status for row in rows) for status in STATUSES},
        "images": rows,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
