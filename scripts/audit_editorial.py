#!/usr/bin/env python3
"""Read-only editorial freshness audit. Warnings never block daily publication.

This is a consistency signal, not a judgement that an article is funny.
No API calls, extra services, paid resources, or mutation of article data.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")
FRESHNESS_HOURS = 12
# Dated observations can remain relevant beyond the breaking-news window.
# Missing or unknown kinds keep the existing news-freshness warning.
FRESHNESS_EXEMPT_KINDS = ("公開投稿", "生活・文化")
WINDOW_HOURS = 48
AI_PATTERN = re.compile(r"(?<![A-Za-z])AI(?![A-Za-z])|ロボット|人工知能|生成ＡＩ|生成AI", re.IGNORECASE)


def audit(articles: list[dict], now: datetime) -> list[tuple[str, str, str]]:
    """Return (code, slug, message); never invent freshness when dates are absent."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    cutoff = now.astimezone(JST) - timedelta(hours=WINDOW_HOURS)
    warnings: list[tuple[str, str, str]] = []
    recent: list[dict] = []
    for a in articles:
        slug = a.get("slug", "unknown")
        if not a.get("published_at"):
            warnings.append(("MISSING_PUBLICATION", slug, "本紙の現実掲載時刻が未記録。元ネタ時刻と混同しないでください"))
            continue
        try:
            published = datetime.fromisoformat(a["published_at"])
            source = datetime.fromisoformat(a["source"]["published_at"])
        except (KeyError, TypeError, ValueError):
            warnings.append(("INVALID_DATE", slug, "元ネタまたは本紙の日時を解析できません"))
            continue
        if published.tzinfo is None or source.tzinfo is None:
            warnings.append(("MISSING_TZ", slug, "日時にはタイムゾーンが必要です"))
            continue
        published = published.astimezone(JST)
        source = source.astimezone(JST)
        if published < cutoff or published > now.astimezone(JST) + timedelta(minutes=10):
            if published > now.astimezone(JST) + timedelta(minutes=10):
                warnings.append(("FUTURE_PUBLISHED", slug, "本紙の掲載時刻が現実の未来になっています"))
            continue
        recent.append(a)
        lag_hours = (published - source).total_seconds() / 3600
        if lag_hours < -0.1:
            warnings.append(("SOURCE_IN_FUTURE", slug, "元ネタが本紙掲載時刻より後に発表されています"))
        elif (lag_hours > FRESHNESS_HOURS
              and a["source"].get("kind") not in FRESHNESS_EXEMPT_KINDS):
            warnings.append(("STALE_SOURCE", slug,
                f"本紙掲載時の元ネタが {lag_hours:.1f} 時間前。推奨上限 {FRESHNESS_HOURS} 時間を超過"))
        if not a["source"].get("url", "").startswith("https://"):
            warnings.append(("INVALID_SOURCE_URL", slug, "元ネタのHTTPSリンクを確認してください"))

    if len(recent) >= 6:
        counts = Counter(a.get("category") for a in recent)
        primary, number = counts.most_common(1)[0]
        if number / len(recent) >= 0.6:
            warnings.append(("CATEGORY_BIAS", "-", f"最近{len(recent)}本のうち{primary}が{number}本。ジャンルの偏りを確認"))
        themed = sum(bool(AI_PATTERN.search(a.get("title", ""))) for a in recent)
        if themed / len(recent) >= 0.6:
            warnings.append(("AI_TOPIC_BIAS", "-", f"最近{len(recent)}本のうちAI・ロボット関連見出しが{themed}本。題材の幅を確認"))
    return warnings


def main() -> None:
    articles = json.loads((ROOT / "content/articles.json").read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    warnings = audit(articles, now)
    for code, slug, message in warnings:
        # GitHub annotation; this never fails the publish/validation job.
        print(f"::warning title=Editorial {code} ({slug})::{message}")
    print(f"Editorial audit: {len(articles)} articles, {len(warnings)} warning(s);"
          " advisory only, publishing not blocked.")


if __name__ == "__main__":
    main()
