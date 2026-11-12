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
from xml.etree import ElementTree

try:
    from .editorial_covers import SVG_NAMESPACE, reviewed_story_scene
    from .generated_photos import (
        ROOT, SAFE_SLUG, SHA256, article_fingerprint, generated_scene_entry, generated_url,
        usable_generated_scene, visually_held_scene,
        native_text_revision_review,
    )
    from .media_policy import usable_cached_photo
except ImportError:
    from editorial_covers import SVG_NAMESPACE, reviewed_story_scene
    from generated_photos import (
        ROOT, SAFE_SLUG, SHA256, article_fingerprint, generated_scene_entry, generated_url,
        usable_generated_scene, visually_held_scene,
        native_text_revision_review,
    )
    from media_policy import usable_cached_photo

REVIEW_FILE = Path("content/visual_reviews.json")
STATUSES = ("accepted", "pending", "rejected", "missing")
PUBLICATION_KINDS = ("generated_jpeg", "editorial_svg", "stock_photo", "generic_fallback")


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
    if "reuse" in review and native_text_revision_review(article, generation, root=root) is None:
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


def _editorial_scene_state(article: dict, *, root: Path) -> dict:
    """Explain an authored SVG's gate result without changing its approval."""
    relative = Path("assets/editorial-scenes") / (article["slug"] + ".svg")
    path = root / relative
    state = {"path": relative.as_posix(), "status": "absent", "reason": "no_editorial_scene"}
    if path.parent.is_symlink() or path.is_symlink():
        return {**state, "status": "invalid", "reason": "unsafe_scene_path"}
    if not path.exists():
        return state
    if reviewed_story_scene(article, root=root) is not None:
        return {**state, "status": "current", "reason": "matching_reviewed_scene"}
    # The publication validator above remains authoritative. Parsing here only
    # distinguishes an article edit from other invalid assets for the operator.
    try:
        svg = path.read_text(encoding="utf-8")
        if "<!DOCTYPE" in svg.upper() or "<!ENTITY" in svg.upper():
            raise ValueError("Unsupported SVG declaration")
        scene = ElementTree.fromstring(svg)
        fingerprint = scene.get("data-article-sha256", "")
        if (scene.tag == f"{{{SVG_NAMESPACE}}}svg" and SHA256.fullmatch(fingerprint)
                and fingerprint != article_fingerprint(article)):
            return {**state, "status": "stale", "reason": "article_fingerprint_changed"}
    except (OSError, UnicodeError, ValueError, ElementTree.ParseError):
        pass
    return {**state, "status": "invalid", "reason": "scene_validation_failed"}


def _publication_row(article: dict, *, root: Path) -> dict:
    """Report build selection, not a claim that those bytes are deployed live."""
    row = _scene_state(article, root=root)
    strategy = article.get("visual_strategy", "cpu-photo")
    scene = _editorial_scene_state(article, root=root)
    # Match build.editorial_image() and uses_stock_photo() priority, using the
    # same validators. Importing build here would create a publication-gate cycle.
    kind, url = "generic_fallback", "/images/" + article["slug"] + ".svg"
    if strategy == "illustration":
        if scene["status"] == "current":
            kind = "editorial_svg"
    elif row["status"] == "accepted":
        kind, url = "generated_jpeg", generated_url(article, root=root)
    elif scene["status"] == "current":
        kind = "editorial_svg"
    elif usable_cached_photo(article, root=root):
        kind, url = "stock_photo", "/photos/" + article["slug"] + ".jpg"
    result = {**row, "visual_strategy": strategy,
              "publication": {"kind": kind, "url": url}, "editorial_scene": scene}
    if kind == "editorial_svg":
        # This is the same byte/proof gate used by the build. Keep the import
        # local: preparation also consults this module's JPEG approval gate.
        if __package__:
            from .social_cards import reviewed_scene_png
        else:
            from social_cards import reviewed_scene_png
        relative = Path("assets/editorial-scenes") / (article["slug"] + ".png")
        path = root / relative
        current = reviewed_scene_png(article, root=root) is not None
        status = "current" if current else "invalid" if path.exists() or path.is_symlink() else "missing"
        result["editorial_social_png"] = {"path": relative.as_posix(), "status": status}
    return result


def _attention(row: dict) -> list[dict]:
    """Include incomplete share artwork without treating it as CPU backlog."""
    work = []
    if row["editorial_scene"]["status"] in ("stale", "invalid"):
        work.append((
            row["editorial_scene"]["status"] + "_editorial_scene",
            "現在の本文と専用SVGを実物で再確認し、必要なら修正する。指紋だけで承認しない。",
        ))
    if row["status"] == "pending" and row.get("visual_strategy") != "illustration":
        work.append(("pending_jpeg", "現在のJPEGを本文と目視照合し、採否を記録する。"))
    paired = row.get("editorial_social_png")
    if paired and paired["status"] != "current":
        work.append((
            paired["status"] + "_editorial_social_png",
            "共有PNGを python3 scripts/social_cards.py --prepare-scenes " + row["slug"]
            + " で準備し、実表示する。CPU再生成は不要。",
        ))
    if row["publication"]["kind"] == "generic_fallback" and not work:
        if row.get("visual_strategy") == "illustration":
            work.append((
                "illustration_strategy_without_reviewed_scene",
                "CPU生成へ戻さず、本文のオチに合う専用SVG等を用意して実物確認する。",
            ))
        else:
            work.append((
                "generic_fallback",
                "本文の具体的な場面に合う素材を用意し、実物確認する。保留解除だけで復旧しない。",
            ))
    return [{"slug": row["slug"], "publication": row["publication"],
             "reason": reason, "next_action": action} for reason, action in work]


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
            or article.get("visual_strategy", "cpu-photo") not in ("cpu-photo", "illustration")
            for article in articles
        ):
            raise ValueError("記事の配列・安全なslug・有効なvisual_strategyが必要です")
        if len({article["slug"] for article in articles}) != len(articles):
            raise ValueError("記事のslugが重複しています")
    except (OSError, UnicodeError, ValueError) as error:
        print(json.dumps({"error": "記事データを読み込めません", "detail": str(error)},
                         ensure_ascii=False))
        return 2
    rows = [_publication_row(article, root=args.root)
            for article in sorted(articles, key=lambda article: article["slug"])]
    print(json.dumps({
        "version": 1,
        "basis": "保存済みの目視レビュー記録とJPEGの整合性を照合。画像の意味適合は自動判定しません。",
        "publication_basis": "現在のファイルからビルドが選ぶ素材。本番配信を確認した結果ではありません。",
        "counts": {status: sum(row["status"] == status for row in rows) for status in STATUSES},
        "publication_counts": {
            kind: sum(row["publication"]["kind"] == kind for row in rows)
            for kind in PUBLICATION_KINDS
        },
        "strategy_counts": {
            strategy: sum(row["visual_strategy"] == strategy for row in rows)
            for strategy in ("cpu-photo", "illustration")
        },
        "attention": [item for row in rows for item in _attention(row)],
        "images": rows,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

