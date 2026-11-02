#!/usr/bin/env python3
"""Capture public, anonymous-browser audience counters once per JST day.

Only GETs our own free Cloudflare API. No visits, likes, comments, or tracking events
are generated. Data is an aggregate reference value, NOT people, PV, or daily visits.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
API = "https://veripsa.com/api/engagement"
TOKYO = ZoneInfo("Asia/Tokyo")
MAX_ARTICLES = 100
BATCH = 20
CHANNELS = ("x", "search", "external", "unattributed")


def report_date(now: datetime) -> str:
    """Each early-morning execution reports the *completed* previous JST day."""
    return (now.astimezone(TOKYO).date() - timedelta(days=1)).isoformat()


def real_published_date(article: dict) -> str | None:
    """Never interpret fictional dates or source publication as our publication."""
    value = article.get("published_at")
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("published_at must have a timezone: " + article["slug"])
    return dt.astimezone(TOKYO).date().isoformat()


def recent_articles(articles: list[dict]) -> list[dict]:
    """Bound requests and repository growth while retaining newest stories."""
    return sorted(
        articles,
        key=lambda a: a.get("published_at") or a["source"]["published_at"],
        reverse=True,
    )[:MAX_ARTICLES]


def read_public_metrics(slugs: list[str], opener=urlopen) -> tuple[int, dict, dict | None]:
    if not slugs:
        raise ValueError("No published article slugs available")
    unique = list(dict.fromkeys(slugs))
    visitors = None
    metrics = {}
    channels = None
    for index in range(0, len(unique), BATCH):
        batch = unique[index:index + BATCH]
        request = Request(
            API + "?" + urlencode({"articles": ",".join(batch)}),
            headers={
                "User-Agent": "MihasseiDailyReadOnlySnapshot/1.0",
                "Accept": "application/json",
                "Cache-Control": "no-cache",
            },
            method="GET",
        )
        with opener(request, timeout=20) as response:
            if response.status != 200:
                raise RuntimeError("Audience API returned HTTP " + str(response.status))
            payload = json.load(response)
        if payload.get("unit") != "anonymous-browser":
            raise ValueError("Audience API unit changed; refusing to guess")
        count = payload.get("visitors")
        if type(count) is not int or count < 0:
            raise ValueError("Audience visitor counter is invalid")
        if visitors is None:
            visitors = count
            source_counts = payload.get("channels")
            if source_counts is not None:
                if not isinstance(source_counts, dict) or set(source_counts) != set(CHANNELS):
                    raise ValueError("Unexpected audience attribution categories")
                if any(type(source_counts[key]) is not int or source_counts[key] < 0 for key in CHANNELS):
                    raise ValueError("Invalid audience attribution counters")
                channels = {key: source_counts[key] for key in CHANNELS}
        details = payload.get("articles")
        if not isinstance(details, dict):
            raise ValueError("Audience per-article data is missing")
        for slug in batch:
            item = details.get(slug)
            if not isinstance(item, dict):
                raise ValueError("Missing article statistics: " + slug)
            for key in ("readers", "likes", "shareClicks", "continuedReaders"):
                if type(item.get(key)) is not int or item[key] < 0:
                    raise ValueError("Invalid metric " + key + " for " + slug)
            # New per-story attribution only exists after activation.
            # An older Worker lacking the field must NOT be interpreted as
            # zero X/search arrivals or used to reconstruct earlier traffic.
            arrivals = item.get("arrivals")
            extra = {}
            if arrivals is not None:
                if (not isinstance(arrivals, dict) or
                    set(arrivals) != {"x","search"} or
                    any(type(arrivals[k]) is not int or arrivals[k] < 0
                        for k in ("x","search"))):
                    raise ValueError("Invalid per-article arrivals for " + slug)
                extra = {"x_arrivals":arrivals["x"],"search_arrivals":arrivals["search"]}
            metrics[slug] = {
                "readers": item["readers"],
                "likes": item["likes"],
                "share_clicks": item["shareClicks"],
                "continued_readers": item["continuedReaders"],
                **extra,
            }
    return visitors, metrics, channels


def build_snapshot(articles: list[dict], now: datetime, visitors: int, counts: dict,
                   channels: dict | None = None) -> dict:
    day = report_date(now)
    recent = recent_articles(articles)
    published_on_day = [a for a in articles if real_published_date(a) == day]
    for a in recent:
        if a["slug"] not in counts:
            raise ValueError("Missing read-only count: " + a["slug"])
    snapshot = {
        "schema_version": 1,
        "date_jst": day,
        "measured_at_utc": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "unit": "anonymous-browser",
        "note": "Cumulative distinct browser identifiers, NOT people or PV. Counts are a point-in-time sample, not daily visitor totals.",
        "continuation_note": (
            "continued_readers is cumulative unique browser IDs that arrived at a DIFFERENT "
            "article from this source article via a same-origin referrer path. "
            "One per browser and source; referrer-stripping undercounts. "
            "This is not an article-to-article click total or a conversion rate."
        ),
        "per_article_arrival_note": (
            "x_arrivals/search_arrivals are distinct anonymous browser identifiers "
            "per specific article and channel, counted only since that metric was enabled. "
            "A browser can be in both channels and several articles. "
            "They are NOT people, page views, clicks, daily visits or proof of X posts. "
            "Older snapshots without these fields must not be backfilled as zeros."
        ),
        "visitors_cumulative": visitors,
        "articles_published_total": len(articles),
        "articles_published_on_date": len(published_on_day),
        "published_categories": dict(sorted(Counter(a["category"] for a in published_on_day).items())),
        "recent_articles_sampled": len(recent),
        "recent_articles": [
            {
                "slug": a["slug"],
                "category": a["category"],
                "published_at": a.get("published_at"),
                **counts[a["slug"]],
            }
            for a in recent
        ],
    }
    if channels is not None:
        snapshot["channels_cumulative"] = channels
        snapshot["channels_note"] = (
            "Cumulative browser IDs per observed incoming category since activation. "
            "Channels may overlap and are not people or page views. "
            "Source attribution is approximate; unattributed is not necessarily direct."
        )
    return snapshot


def main() -> None:
    now = datetime.now(timezone.utc)
    day = report_date(now)
    output = ROOT / "analytics" / "daily" / (day + ".json")
    if "--check-only" in sys.argv:
        if not output.exists():
            sys.exit("::error::Missing audience snapshot for " + day)
        data = json.loads(output.read_text(encoding="utf-8"))
        published = data["articles_published_on_date"]
        if published == 0:
            sys.exit("::error::No new newspaper articles published on " + day + " JST")
        print("Verified", published, "article(s) published on", day, "JST")
        return
    if output.exists():
        print("Snapshot already exists, no overwrite:", output.relative_to(ROOT))
        return
    articles = json.loads((ROOT / "content" / "articles.json").read_text(encoding="utf-8"))
    if not isinstance(articles, list) or not articles:
        raise ValueError("No article data")
    recent = recent_articles(articles)
    visitors, counts, channels = read_public_metrics([a["slug"] for a in recent])
    snapshot = build_snapshot(articles, now, visitors, counts, channels)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Recorded", output.relative_to(ROOT), "visitors=", visitors,
          "published_on_date=", snapshot["articles_published_on_date"],
          "article_metrics=", len(counts))


if __name__ == "__main__":
    main()
