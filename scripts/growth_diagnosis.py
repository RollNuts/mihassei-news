#!/usr/bin/env python3
"""Explain a live traffic spike with read-only first-party metrics.

The public engagement API already records page views, coarse session-acquisition
sources, and hourly article counters. This helper finds today's busiest measured
hour, asks the API to replay that specific hour, and compares it with actual X
article-send timestamps saved by the existing article-picks lane.

It reports evidence, not causation. No tracking event or external paid API is used.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
API = "https://veripsa.com/api/engagement"
JST = ZoneInfo("Asia/Tokyo")
SOURCE_KEYS = ("x", "search", "external", "unattributed")
MEASURED_COVERAGE = {"partial", "complete", "in-progress"}


def _count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _aware_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def fetch_json(url: str, opener=urlopen) -> dict:
    request = Request(
        url,
        headers={
            "User-Agent": "MihasseiGrowthDiagnosis/1.0",
            "Accept": "application/json",
            "Cache-Control": "no-cache",
        },
        method="GET",
    )
    with opener(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError("Audience API returned HTTP " + str(response.status))
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("Audience API returned a non-object payload")
    return payload


def peak_hour(payload: dict) -> dict:
    hourly = payload.get("hourly")
    rows = hourly.get("today") if isinstance(hourly, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Live payload has no hourly.today series")
    measured = [
        row for row in rows
        if isinstance(row, dict)
        and row.get("pageViewsCoverage") in MEASURED_COVERAGE
        and _count(row.get("pageViews")) is not None
        and isinstance(row.get("hourJst"), str)
    ]
    if not measured:
        raise ValueError("No measured hour is available")
    return max(measured, key=lambda row: (_count(row["pageViews"]) or 0, row["hourJst"]))


def load_titles(path: Path = ROOT / "content" / "articles.json") -> dict[str, str]:
    articles = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(articles, list):
        raise ValueError("content/articles.json must contain a list")
    return {
        item["slug"]: item["title"]
        for item in articles
        if isinstance(item, dict)
        and isinstance(item.get("slug"), str)
        and isinstance(item.get("title"), str)
    }


def load_x_state(path: Path = ROOT / "social-bot" / "article-picks" / "state.json") -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("queued"), dict):
        raise ValueError("article-picks state is invalid")
    return data


def sent_x_posts(state: dict, date_jst: str) -> list[dict]:
    posts = []
    for slug, item in state.get("queued", {}).items():
        if not isinstance(item, dict) or item.get("buffer_status_at_accept") != "sent":
            continue
        sent = _aware_datetime(item.get("buffer_sent_at_at_accept"))
        if sent is None:
            continue
        sent_jst = sent.astimezone(JST)
        if sent_jst.date().isoformat() != date_jst:
            continue
        posts.append({
            "slug": slug,
            "sent_at_jst": sent_jst.isoformat(timespec="seconds"),
            "logical_slot_jst": item.get("slot_jst"),
            "x_url": item.get("buffer_external_link_at_accept"),
        })
    posts.sort(key=lambda row: row["sent_at_jst"])
    return posts


def _source_summary(value: object) -> dict | None:
    if not isinstance(value, dict) or set(value) != set(SOURCE_KEYS):
        return None
    if any(_count(value[key]) is None for key in SOURCE_KEYS):
        return None
    total = sum(value[key] for key in SOURCE_KEYS)
    return {
        "counts": {key: value[key] for key in SOURCE_KEYS},
        "attributed_page_views": total,
        "shares": {
            key: round(value[key] / total, 3) if total else None
            for key in SOURCE_KEYS
        },
    }


def build_report(live: dict, selected: dict, state: dict, titles: dict[str, str]) -> dict:
    peak = peak_hour(live)
    hour_jst = peak["hourJst"]
    selected_hour = selected.get("selectedHour")
    if not isinstance(selected_hour, dict) or selected_hour.get("hourJst") != hour_jst:
        raise ValueError("Selected-hour payload does not match the peak hour")

    date_jst = hour_jst[:10]
    peak_start = datetime.fromisoformat(hour_jst + ":00+09:00")
    near_start = peak_start - timedelta(minutes=60)
    near_end = peak_start + timedelta(hours=1)

    top_container = selected.get("topArticles")
    top_rows = top_container.get("selectedHour") if isinstance(top_container, dict) else None
    if not isinstance(top_rows, list):
        raise ValueError("Selected-hour top article ranking is missing")
    top_articles = []
    for row in top_rows:
        if not isinstance(row, dict):
            continue
        slug = row.get("article")
        views = _count(row.get("pageViews"))
        if not isinstance(slug, str) or views is None:
            continue
        top_articles.append({
            "slug": slug,
            "title": titles.get(slug),
            "page_views": views,
        })

    peak_views = _count(selected_hour.get("pageViews"))
    if peak_views is None:
        raise ValueError("Selected peak hour has no measured page views")
    top_share = (
        round(top_articles[0]["page_views"] / peak_views, 3)
        if peak_views and top_articles else None
    )

    source = _source_summary(selected_hour.get("pageViewSources"))
    x_share = source["shares"]["x"] if source else None

    x_posts = sent_x_posts(state, date_jst)
    near_posts = []
    top_slugs = {row["slug"] for row in top_articles}
    for post in x_posts:
        sent = _aware_datetime(post["sent_at_jst"])
        if sent is None or not (near_start <= sent <= near_end):
            continue
        near_posts.append({
            **post,
            "title": titles.get(post["slug"]),
            "minutes_from_peak_start": round(
                (sent - peak_start).total_seconds() / 60, 1
            ),
            "is_peak_top_article": post["slug"] in top_slugs,
        })

    matched_post = next((post for post in near_posts if post["is_peak_top_article"]), None)
    if x_share is not None and x_share >= 0.5 and matched_post is not None:
        diagnosis = "x_distribution_strong_signal"
    elif matched_post is not None:
        diagnosis = "x_timing_article_match"
    elif top_share is not None and top_share >= 0.5:
        diagnosis = "single_article_breakout"
    elif x_share is not None and x_share >= 0.5:
        diagnosis = "x_distribution_without_article_match"
    else:
        diagnosis = "mixed_or_unattributed"

    today = live.get("today") if isinstance(live.get("today"), dict) else {}
    yesterday = live.get("yesterday") if isinstance(live.get("yesterday"), dict) else {}
    today_pv = _count(today.get("pageViews"))
    yesterday_pv = _count(yesterday.get("pageViews"))
    partial_vs_previous = (
        round(today_pv / yesterday_pv, 2)
        if today_pv is not None and yesterday_pv else None
    )

    return {
        "schema_version": 1,
        "date_jst": date_jst,
        "today_page_views_so_far": today_pv,
        "today_page_views_coverage": today.get("pageViewsCoverage"),
        "previous_day_page_views": yesterday_pv,
        "previous_day_page_views_coverage": yesterday.get("pageViewsCoverage"),
        "today_so_far_vs_previous_full_day_ratio": partial_vs_previous,
        "peak_hour": {
            "hour_jst": hour_jst,
            "page_views": peak_views,
            "page_views_coverage": selected_hour.get("pageViewsCoverage"),
            "source_coverage": selected_hour.get("pageViewSourcesCoverage"),
            "source_mix": source,
            "top_article_share": top_share,
            "top_articles": top_articles,
        },
        "x_posts_near_peak": near_posts,
        "diagnosis": diagnosis,
        "note": (
            "This is descriptive evidence, not proof of causation. X timing is "
            "stronger evidence only when the peak hour is also X-majority in "
            "session-acquisition page-view sources. Partial/in-progress hours and "
            "source tracking that began mid-hour must be interpreted as incomplete."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=API)
    parser.add_argument("--state", default=str(ROOT / "social-bot" / "article-picks" / "state.json"))
    parser.add_argument("--articles", default=str(ROOT / "content" / "articles.json"))
    args = parser.parse_args()

    live = fetch_json(args.api)
    peak = peak_hour(live)
    selected_url = args.api + ("&" if "?" in args.api else "?") + urlencode(
        {"hour": peak["hourJst"][:13]}
    )
    selected = fetch_json(selected_url)
    report = build_report(
        live,
        selected,
        load_x_state(Path(args.state)),
        load_titles(Path(args.articles)),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
