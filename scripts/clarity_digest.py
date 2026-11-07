#!/usr/bin/env python3
"""Render recent articles for a blind first-read clarity review.

Only reader-facing fields needed for the first impression are emitted.
Sources, editorial metadata, tags, and analytics stay hidden so reviewers
cannot fill in missing context from the author's explanation. --full-text
reveals the body only for the second, separate review of the actual joke.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")


def _parse_published(article: dict) -> datetime | None:
    value = article.get("published_at")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(JST)


def select_articles(
    articles: list[dict],
    *,
    day: str | None = None,
    days: int | None = None,
    limit: int | None = None,
) -> list[dict]:
    dated: list[tuple[datetime, dict]] = []
    for article in articles:
        published = _parse_published(article)
        if published is not None:
            dated.append((published, article))

    if not dated:
        return []

    dated.sort(key=lambda item: item[0])
    if day is not None:
        target = datetime.fromisoformat(day + "T00:00:00+09:00").date()
        dated = [item for item in dated if item[0].date() == target]
    elif days is not None:
        if days < 1:
            raise ValueError("days must be >= 1")
        latest_day = dated[-1][0].date()
        start_day = latest_day - timedelta(days=days - 1)
        dated = [
            item for item in dated
            if start_day <= item[0].date() <= latest_day
        ]

    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        dated = dated[-limit:]

    return [article for _, article in dated]


def blind_rows(articles: list[dict], *, full_text: bool = False) -> list[dict]:
    rows = []
    for article in articles:
        row = {
            "slug": str(article.get("slug") or ""),
            "published_at": str(article.get("published_at") or ""),
            "title": str(article.get("title") or ""),
            "summary": str(article.get("summary") or ""),
        }
        if full_text:
            row["paragraphs"] = list(article.get("paragraphs") or [])
        rows.append(row)
    return rows


def render_text(rows: list[dict]) -> str:
    if not rows:
        return "No articles matched."
    blocks = []
    for row in rows:
        body = [f"BODY {i}: {paragraph}" for i, paragraph in
                enumerate(row.get("paragraphs", []), 1)]
        blocks.append(
            "\n".join([
                f"[{row['published_at']}] {row['slug']}",
                f"TITLE: {row['title']}",
                f"SUMMARY: {row['summary']}",
                *body,
            ])
        )
    return "\n\n".join(blocks)


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--day", help="JST date YYYY-MM-DD")
    group.add_argument("--days", type=int, help="Recent N JST publication days ending on the latest article date")
    parser.add_argument("--limit", type=int, help="Keep only the latest N matched articles")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of plain text")
    parser.add_argument("--full-text", action="store_true", help="Second pass: include reader-facing paragraphs, still hiding sources and editorial rationale")
    args = parser.parse_args()

    articles = json.loads(
        (ROOT / "content" / "articles.json").read_text(encoding="utf-8")
    )
    selected = select_articles(
        articles,
        day=args.day,
        days=args.days,
        limit=args.limit,
    )
    rows = blind_rows(selected, full_text=args.full_text)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(render_text(rows))


if __name__ == "__main__":
    main()
