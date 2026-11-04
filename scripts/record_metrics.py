#!/usr/bin/env python3
"""Capture public page-view and anonymous-browser counters once per JST day.

Only GETs our own free Cloudflare API. No visits, likes, comments, or tracking events
are generated. Page displays and deduplicated browser activity stay separate;
unavailable history is never converted to zero or reconstructed from browser counts.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
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


def valid_count(value) -> bool:
    return type(value) is int and value >= 0


def validate_page_views(day: str, total, daily, coverage, tracking: dict) -> None:
    """Reject a partial start day mislabeled as a complete day, and invented zeros."""
    if not isinstance(tracking, dict) or tracking.get("timezone") != "Asia/Tokyo":
        raise ValueError("Page-view tracking timezone missing or invalid")
    if "startedAtUtc" not in tracking:
        raise ValueError("Page-view tracking start is missing")
    started = tracking["startedAtUtc"]
    if started is None:
        if total is not None or daily is not None or coverage != "unmeasured":
            raise ValueError("Unstarted page-view tracking must not invent counts")
        return
    if not isinstance(started, str):
        raise ValueError("Invalid page-view tracking start")
    try:
        start = datetime.fromisoformat(started)
    except ValueError as exc:
        raise ValueError("Invalid page-view tracking start") from exc
    if start.tzinfo is None or start.utcoffset() != timedelta(0):
        raise ValueError("Page-view tracking start must be an aware UTC timestamp")
    if not valid_count(total):
        raise ValueError("Invalid cumulative page views")
    local_start = start.astimezone(TOKYO)
    reported_date = date.fromisoformat(day)
    if reported_date < local_start.date():
        if daily is not None or coverage != "unmeasured":
            raise ValueError("Pre-start page views must be unmeasured, not zero")
        return
    expected = "complete"
    if (reported_date == local_start.date() and
            any((local_start.hour, local_start.minute, local_start.second,
                 local_start.microsecond))):
        expected = "partial"
    if coverage != expected or not valid_count(daily) or daily > total:
        raise ValueError("Page-view count or completed-day coverage is inconsistent")


def read_page_view_totals(payload: dict, counts: dict, day: str) -> dict:
    """An old Worker has no PV fields; a partly updated/corrupt payload is an error."""
    fields = ("pageViews" in payload, "pageViewTracking" in payload,
              "pageViews" in counts, "pageViewsCoverage" in counts)
    if not any(fields):
        return {}
    if not all(fields):
        raise ValueError("Incomplete page-view metrics from public API")
    validate_page_views(day, payload["pageViews"], counts["pageViews"],
                        counts["pageViewsCoverage"], payload["pageViewTracking"])
    return {
        "pageViews": counts["pageViews"],
        "pageViewsCoverage": counts["pageViewsCoverage"],
        "pageViewsCumulative": payload["pageViews"],
        "pageViewTracking": payload["pageViewTracking"],
    }


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
            if "pageViews" in item:
                # This is a separate display counter, never item["readers"].
                value = item["pageViews"]
                if value is not None and not valid_count(value):
                    raise ValueError("Invalid article page views for " + slug)
                extra["page_views_cumulative"] = value
            metrics[slug] = {
                "readers": item["readers"],
                "likes": item["likes"],
                "share_clicks": item["shareClicks"],
                "continued_readers": item["continuedReaders"],
                **extra,
            }
    return visitors, metrics, channels


def read_day_totals(day: str, opener=urlopen) -> dict:
    """GET the requested completed JST day, not a lifetime counter or today's partial total."""
    request = Request(
        API + "?" + urlencode({"day": day}),
        headers={
            "User-Agent": "MihasseiDailyReadOnlySnapshot/1.0",
            "Accept": "application/json",
            "Cache-Control": "no-cache",
        },
        method="GET",
    )
    with opener(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError("Audience day API returned HTTP " + str(response.status))
        payload = json.load(response)
    counts = payload.get("day")
    if (payload.get("unit") != "anonymous-browser" or not isinstance(counts, dict)
            or counts.get("dateJst") != day
            or any(type(counts.get(key)) is not int or counts[key] < 0
                   for key in ("visitors", "articleReads"))):
        raise ValueError("Day-scoped audience counters missing or invalid for " + day)
    return {"visitors": counts["visitors"], "articleReads": counts["articleReads"],
            **read_page_view_totals(payload, counts, day)}


def build_snapshot(articles: list[dict], now: datetime, visitors: int, counts: dict,
                   daily_totals: dict, channels: dict | None = None) -> dict:
    day = report_date(now)
    if (not isinstance(daily_totals, dict) or
        any(type(daily_totals.get(key)) is not int or daily_totals[key] < 0
            for key in ("visitors", "articleReads"))):
        raise ValueError("No trustworthy completed-day audience totals")
    recent = recent_articles(articles)
    published_on_day = [a for a in articles if real_published_date(a) == day]
    for a in recent:
        if a["slug"] not in counts:
            raise ValueError("Missing read-only count: " + a["slug"])
    snapshot = {
        "schema_version": 3,
        "date_jst": day,
        "measured_at_utc": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "unit": "anonymous-browser",
        "daily_visitors": daily_totals["visitors"],
        "daily_article_reads": daily_totals["articleReads"],
        "daily_note": (
            "Target is a completed JST date; this alone does not establish full-day "
            "coverage. A browser counts once per day for daily_visitors; "
            "a browser+article pair counts once per day for daily_article_reads. "
            "Neither is people, sessions or PV. Legacy snapshots without these "
            "fields are missing, not zero."
        ),
        "page_views_available": "pageViews" in daily_totals,
        "page_views_unit": "page-view",
        "daily_page_views": daily_totals.get("pageViews"),
        "daily_page_views_coverage": daily_totals.get("pageViewsCoverage"),
        "page_views_cumulative": daily_totals.get("pageViewsCumulative"),
        "page_views_started_at_utc": daily_totals.get("pageViewTracking", {}).get("startedAtUtc"),
        "page_views_timezone": "Asia/Tokyo",
        "page_views_note": (
            "JavaScript-visible page displays, including repeat loads, with request "
            "retries deduplicated by view ID. Not people or completed article reads. "
            "Daily coverage is partial on the activation date unless tracking began "
            "at JST midnight. Pre-start dates are null/unmeasured. If the API lacks "
            "PV fields, available is false and counts/coverage are null. Cumulative "
            "site/article totals are measured at capture time, not the report date's "
            "midnight. Existing snapshots are never backfilled or overwritten."
        ),
        "note": "visitors_cumulative and article readers are cumulative distinct browser identifiers, NOT people or PV. These browser counters are a point-in-time sample, not daily visitor totals.",
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
    validate_snapshot(snapshot, day)
    return snapshot


def validate_snapshot(data: dict, day: str) -> None:
    """Check saved v2/v3 data without rewriting historical snapshots."""
    if (not isinstance(data, dict) or data.get("schema_version") not in (2, 3)
            or data.get("date_jst") != day
            or any(not valid_count(data.get(key))
                   for key in ("daily_visitors", "daily_article_reads",
                               "articles_published_on_date"))):
        raise ValueError("Completed-day audience counters missing for " + day)
    if data["schema_version"] == 2:
        return
    required = ("page_views_available", "page_views_unit", "daily_page_views",
                "daily_page_views_coverage", "page_views_cumulative",
                "page_views_started_at_utc", "page_views_timezone")
    if any(key not in data for key in required):
        raise ValueError("Page-view snapshot metadata is missing")
    if (type(data["page_views_available"]) is not bool
            or data["page_views_unit"] != "page-view"
            or data["page_views_timezone"] != "Asia/Tokyo"):
        raise ValueError("Page-view snapshot metadata is invalid")
    if not data["page_views_available"]:
        if any(data[key] is not None for key in (
                "daily_page_views", "daily_page_views_coverage",
                "page_views_cumulative", "page_views_started_at_utc")):
            raise ValueError("Unavailable page-view metrics must remain null")
    else:
        validate_page_views(day, data["page_views_cumulative"], data["daily_page_views"],
                            data["daily_page_views_coverage"], {
                                "startedAtUtc": data["page_views_started_at_utc"],
                                "timezone": data["page_views_timezone"],
                            })
    for item in data.get("recent_articles", []):
        if ("page_views_cumulative" in item and item["page_views_cumulative"] is not None
                and not valid_count(item["page_views_cumulative"])):
            raise ValueError("Invalid per-article page views in snapshot")


def main() -> None:
    now = datetime.now(timezone.utc)
    day = report_date(now)
    output = ROOT / "analytics" / "daily" / (day + ".json")
    if "--check-only" in sys.argv:
        if not output.exists():
            sys.exit("::error::Missing audience snapshot for " + day)
        data = json.loads(output.read_text(encoding="utf-8"))
        try:
            validate_snapshot(data, day)
        except ValueError as exc:
            sys.exit("::error::" + str(exc))
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
    daily_totals = read_day_totals(day)
    snapshot = build_snapshot(articles, now, visitors, counts, daily_totals, channels)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Recorded", output.relative_to(ROOT),
          "daily_visitors=", snapshot["daily_visitors"],
          "daily_article_reads=", snapshot["daily_article_reads"],
          "daily_page_views=", snapshot["daily_page_views"],
          "pv_coverage=", snapshot["daily_page_views_coverage"],
          "cumulative_browser_ids=", visitors,
          "published_on_date=", snapshot["articles_published_on_date"],
          "article_metrics=", len(counts))


if __name__ == "__main__":
    main()

