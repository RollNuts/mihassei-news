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
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from .generated_photos import article_fingerprint
except ImportError:
    from generated_photos import article_fingerprint

ROOT = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")
READER_FIELDS = ("title", "summary", "paragraphs")


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


def load_baseline(ref: str, *, root: Path = ROOT) -> tuple[str, list[dict]]:
    """Resolve a real commit and read its articles; unavailable is never empty."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--verify", "--end-of-options", ref + "^{commit}"],
            cwd=root, check=True, capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        snapshot = subprocess.run(
            ["git", "show", commit + ":content/articles.json"],
            cwd=root, check=True, capture_output=True, text=True, timeout=15,
        ).stdout
        articles = json.loads(snapshot)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read article baseline {ref!r}; review scope is unknown") from exc
    if not isinstance(articles, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("slug"), str)
        for item in articles
    ):
        raise ValueError(f"Invalid article baseline {ref!r}; review scope is unknown")
    return commit, articles


def review_digest(
    articles: list[dict],
    *,
    baseline: list[dict] | None = None,
    base_commit: str | None = None,
    slugs: list[str] | None = None,
    limit: int | None = None,
    full_text: bool = False,
) -> dict:
    """List text changes plus carried-over slugs, retaining every deferred item.

    These are review candidates, not automated approvals. Explicit slugs let the
    next run resume pending work even after its comparison baseline has advanced.
    """
    if limit is not None and limit < 1:
        raise ValueError("limit must be >= 1")
    current = {item["slug"]: item for item in articles}
    requested = set(slugs or [])
    missing = requested - current.keys()
    if missing:
        raise ValueError("Unknown article slug(s): " + ", ".join(sorted(missing)))
    candidates = set(requested)
    if baseline is not None:
        previous = {item["slug"]: item for item in baseline}
        for slug, item in current.items():
            old = previous.get(slug)
            if old is None or any(item.get(key) != old.get(key) for key in READER_FIELDS):
                candidates.add(slug)
    dated = []
    for slug in candidates:
        item = current[slug]
        published = _parse_published(item)
        if published is None:
            raise ValueError(f"{slug}: invalid published_at; cannot order review candidates")
        dated.append((published, slug, item))
    # Carry-over work has already waited; new changes cannot displace it.
    dated.sort(key=lambda item: (item[1] not in requested, item[0], item[1]))
    ordered = [item for _, _, item in dated]
    selected = ordered if limit is None else ordered[:limit]
    deferred = [] if limit is None else ordered[limit:]
    rows = blind_rows(selected, full_text=full_text)
    for row, item in zip(rows, selected):
        row["article_sha256"] = article_fingerprint(item)
    return {
        "base_commit": base_commit,
        "total": len(ordered),
        "selected_count": len(selected),
        "pending_count": len(deferred),
        "selected": rows,
        "pending": [
            {"slug": item["slug"], "article_sha256": article_fingerprint(item)}
            for item in deferred
        ],
    }


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
                *([f"ARTICLE SHA256: {row['article_sha256']}"] if "article_sha256" in row else []),
                f"TITLE: {row['title']}",
                f"SUMMARY: {row['summary']}",
                *body,
            ])
        )
    return "\n\n".join(blocks)


def render_review_digest(digest: dict) -> str:
    lines = [
        "Review candidates (not review completion): "
        f"total={digest['total']} selected={digest['selected_count']} pending={digest['pending_count']}",
    ]
    if digest["base_commit"]:
        lines.append("BASE COMMIT: " + digest["base_commit"])
    lines.extend(["", render_text(digest["selected"])])
    if digest["pending"]:
        lines.extend(["", "Pending: carry these slugs into the next run with --slug."])
        lines.extend(
            f"PENDING: {item['slug']} ARTICLE SHA256: {item['article_sha256']}"
            for item in digest["pending"]
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--day", help="JST date YYYY-MM-DD")
    group.add_argument("--days", type=int, help="Recent N JST publication days ending on the latest article date")
    group.add_argument("--since-ref", help="Review new or changed reader-facing text since this Git commit/ref")
    parser.add_argument("--slug", action="append", default=[], help="Resume this pending slug even without text changes; repeatable, optionally with --since-ref")
    parser.add_argument("--limit", type=int, help="Date mode: latest N; change/slug mode: pending slugs first, then oldest N, with all remaining slugs reported")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of plain text")
    parser.add_argument("--full-text", action="store_true", help="Second pass: include reader-facing paragraphs, still hiding sources and editorial rationale")
    args = parser.parse_args(argv)
    if args.slug and (args.day is not None or args.days is not None):
        parser.error("--slug cannot be combined with --day or --days; use --since-ref or --slug alone")

    articles = json.loads(
        (ROOT / "content" / "articles.json").read_text(encoding="utf-8")
    )
    if args.since_ref is not None or args.slug:
        try:
            commit, baseline = load_baseline(args.since_ref, root=ROOT) if args.since_ref is not None else (None, None)
            digest = review_digest(
                articles, baseline=baseline, base_commit=commit, slugs=args.slug,
                limit=args.limit, full_text=args.full_text,
            )
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps(digest, ensure_ascii=False, indent=2) if args.json else render_review_digest(digest))
        return
    selected = select_articles(articles, day=args.day, days=args.days, limit=args.limit)
    rows = blind_rows(selected, full_text=args.full_text)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(render_text(rows))


if __name__ == "__main__":
    main()
