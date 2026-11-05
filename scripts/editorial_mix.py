#!/usr/bin/env python3
"""Editorial lane metadata, daily 3/2/2 guidance, and duplicate guards.

This module is intentionally offline and dependency-free. It validates metadata
that the publishing automation writes into content/articles.json. It does not
fetch trends, rank article quality, or publish anything by itself.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")

LANE_TARGETS = {
    "trend": 3,
    "social_life": 2,
    "original_satire": 2,
}
LANE_PRIORITY = tuple(LANE_TARGETS)
TREND_SOURCE_KINDS = {
    "google_trends",
    "x_official",
    "x_public_aggregator",
}
ROLLOUT_AT = datetime.fromisoformat("2026-11-05T20:00:00+09:00")
TREND_MAX_AGE = timedelta(hours=30)
DUPLICATE_WINDOW = timedelta(days=7)

_ws = re.compile(r"\s+")


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _norm(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return _ws.sub("", value).casefold().strip()


def article_day(article: dict) -> str | None:
    published = _parse_dt(article.get("published_at"))
    if published is None:
        return None
    return published.astimezone(JST).date().isoformat()


def lane_counts(articles: list[dict], day_jst: str) -> Counter:
    counts: Counter = Counter()
    for article in articles:
        if article_day(article) != day_jst:
            continue
        lane = article.get("editorial_lane")
        if lane in LANE_TARGETS:
            counts[lane] += 1
    return counts


def day_story_count(articles: list[dict], day_jst: str) -> int:
    return sum(article_day(article) == day_jst for article in articles)


def next_preferred_lane(counts: Counter) -> str | None:
    """Return the lane with the largest remaining target deficit."""
    best_lane = None
    best_deficit = 0
    for lane in LANE_PRIORITY:
        deficit = LANE_TARGETS[lane] - int(counts.get(lane, 0))
        if deficit > best_deficit:
            best_lane = lane
            best_deficit = deficit
    return best_lane


def _validate_signature(article: dict, slug: str) -> list[str]:
    errors: list[str] = []
    signature = article.get("editorial_signature")
    if not isinstance(signature, dict):
        return [f"{slug}: editorial_signature is required after rollout"]
    for key in ("target", "action", "outcome"):
        value = signature.get(key)
        if not isinstance(value, str) or not 4 <= len(value.strip()) <= 220:
            errors.append(f"{slug}: editorial_signature.{key} must be 4-220 chars")
    return errors


def _validate_trend(article: dict, slug: str, published: datetime) -> list[str]:
    errors: list[str] = []
    trend = article.get("trend_observation")
    if not isinstance(trend, dict):
        return [f"{slug}: trend_observation is required for trend lane"]

    source_kind = trend.get("source_kind")
    if source_kind not in TREND_SOURCE_KINDS:
        errors.append(
            f"{slug}: trend_observation.source_kind must be one of "
            + ", ".join(sorted(TREND_SOURCE_KINDS))
        )

    term = trend.get("term")
    if not isinstance(term, str) or not 1 <= len(term.strip()) <= 120:
        errors.append(f"{slug}: trend_observation.term must be 1-120 chars")

    observed = _parse_dt(trend.get("observed_at"))
    if observed is None:
        errors.append(f"{slug}: trend_observation.observed_at must be timezone-aware ISO")
    else:
        age = published.astimezone(JST) - observed.astimezone(JST)
        if age < -timedelta(minutes=10):
            errors.append(f"{slug}: trend observation is later than publication")
        elif age > TREND_MAX_AGE:
            errors.append(
                f"{slug}: trend observation is {age.total_seconds()/3600:.1f}h old; "
                f"max is {TREND_MAX_AGE.total_seconds()/3600:.0f}h"
            )

    trend_url = trend.get("url")
    if not isinstance(trend_url, str) or not trend_url.startswith("https://"):
        errors.append(f"{slug}: trend_observation.url must be HTTPS")

    verification_urls = trend.get("verification_urls")
    if not isinstance(verification_urls, list) or not verification_urls:
        errors.append(f"{slug}: trend_observation.verification_urls needs >=1 URL")
    else:
        bad = [
            url for url in verification_urls
            if not isinstance(url, str) or not url.startswith("https://")
        ]
        if bad:
            errors.append(f"{slug}: every verification URL must be HTTPS")
        source_url = article.get("source", {}).get("url")
        if (
            isinstance(source_url, str)
            and source_url.startswith("https://")
            and source_url not in verification_urls
        ):
            errors.append(f"{slug}: source.url must also appear in verification_urls")
    return errors


def validate_articles(articles: list[dict]) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors block malformed metadata/duplicates."""
    errors: list[str] = []
    warnings: list[str] = []

    recent_trends: list[tuple[datetime, str, str, str | None]] = []
    recent_signatures: list[tuple[datetime, tuple[str, str, str], str, str | None]] = []

    for article in articles:
        slug = str(article.get("slug") or "unknown")
        published = _parse_dt(article.get("published_at"))
        if published is None or published < ROLLOUT_AT:
            continue

        lane = article.get("editorial_lane")
        if lane not in LANE_TARGETS:
            errors.append(
                f"{slug}: editorial_lane required after rollout; expected "
                + ", ".join(LANE_TARGETS)
            )
            continue

        errors.extend(_validate_signature(article, slug))
        followup_of = (
            article.get("followup_of")
            if isinstance(article.get("followup_of"), str)
            else None
        )

        signature = article.get("editorial_signature")
        if isinstance(signature, dict):
            normalized = tuple(
                _norm(signature.get(key))
                for key in ("target", "action", "outcome")
            )
            if all(normalized):
                recent_signatures.append((published, normalized, slug, followup_of))

        if lane == "trend":
            errors.extend(_validate_trend(article, slug, published))
            trend = article.get("trend_observation")
            if isinstance(trend, dict):
                term = _norm(trend.get("term"))
                source_kind = str(trend.get("source_kind") or "")
                if term:
                    recent_trends.append((published, term, slug, followup_of))
                if source_kind == "x_public_aggregator":
                    warnings.append(
                        f"{slug}: X trend is from a public aggregator; "
                        "do not describe it as an official X ranking"
                    )

    recent_trends.sort(key=lambda item: item[0])
    for i, (published, term, slug, followup_of) in enumerate(recent_trends):
        if followup_of:
            continue
        for prior_published, prior_term, prior_slug, _ in recent_trends[:i]:
            if published - prior_published > DUPLICATE_WINDOW:
                continue
            if term == prior_term:
                errors.append(
                    f"{slug}: duplicate trend term '{term}' already used by "
                    f"{prior_slug} within {DUPLICATE_WINDOW.days} days"
                )
                break

    recent_signatures.sort(key=lambda item: item[0])
    for i, (published, signature, slug, followup_of) in enumerate(recent_signatures):
        if followup_of:
            continue
        for prior_published, prior_signature, prior_slug, _ in recent_signatures[:i]:
            if published - prior_published > DUPLICATE_WINDOW:
                continue
            if signature == prior_signature:
                errors.append(
                    f"{slug}: editorial_signature duplicates {prior_slug} "
                    f"within {DUPLICATE_WINDOW.days} days"
                )
                break

    return errors, warnings


def report_day(articles: list[dict], day_jst: str) -> str:
    counts = lane_counts(articles, day_jst)
    tracked = sum(counts.values())
    published = day_story_count(articles, day_jst)
    parts = [
        f"{lane}={counts.get(lane, 0)}/{target}"
        for lane, target in LANE_TARGETS.items()
    ]
    if published >= sum(LANE_TARGETS.values()) and tracked < sum(LANE_TARGETS.values()):
        suffix = "quality-only (7-story baseline already reached; rollout tracking partial)"
    else:
        preferred = next_preferred_lane(counts)
        suffix = preferred or "quality-only (3/2/2 target filled)"
    return (
        f"{day_jst}: published={published}; tracked={tracked}; "
        + ", ".join(parts)
        + f"; next={suffix}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero on metadata/duplicate errors",
    )
    parser.add_argument("--day", help="JST date YYYY-MM-DD for mix report")
    args = parser.parse_args()

    articles = json.loads(
        (ROOT / "content" / "articles.json").read_text(encoding="utf-8")
    )
    if not isinstance(articles, list):
        print("content/articles.json must contain a list")
        return 2

    errors, warnings = validate_articles(articles)
    for warning in warnings:
        print(f"::warning title=Editorial mix::{warning}")
    for error in errors:
        print(f"::error title=Editorial mix::{error}")

    day = args.day or datetime.now(JST).date().isoformat()
    print(report_day(articles, day))

    if args.check and errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
