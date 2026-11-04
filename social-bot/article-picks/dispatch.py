#!/usr/bin/env python3
"""Buffer Free article queueing ranked by the site's observed page views.

Reads the same JST seven-date window as on-site recommendations. This is the
only automatic X publishing lane; retired original lanes are not dependencies.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT / "scripts"))
from publish import choose_x_channel, list_existing, queue_one  # noqa: E402
from social_text import article_hashtags, truncate_x_text, X_TEXT_LIMIT, x_weighted_length  # noqa: E402

SAFE_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
# Match canonical articles regardless of query parameters, extra prose or
# UTM links. Do not touch unknown external/shortened/manual-X URLs.
ARTICLE_URL = re.compile(
    r"https?://(?:www\.)?veripsa\.com/articles/([a-z0-9]+(?:-[a-z0-9]+)*)/?"
)
TEXT_PREFIX = "【未発生新聞】"
TRACKING_QUERY = "?utm_source=x&utm_medium=social&utm_campaign=article_auto"
METRIC_FIELDS = ("last7DaysPageViews", "todayPageViews")
METRICS_URL = "https://veripsa.com/api/engagement"
JST = timezone(timedelta(hours=9))
MAX_SAFE_INTEGER = 2**53 - 1
MAX_DAILY_X_POSTS = 7
CRISIS_PROMO_PATTERN = re.compile(
    r"地震|津波|台風|豪雨|洪水|土砂災害|山火事|戦争|武力衝突|空爆|侵攻|砲撃|"
    r"ミサイル攻撃|テロ|大量死傷|死者|死亡者|負傷者|被災者|避難者|人質"
)


def read_json(path: Path) -> dict:
    if path.is_symlink():
        raise RuntimeError("Unsafe symlink: " + str(path))
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Expected JSON object: " + str(path))
    return result


def validate_config(config: dict) -> None:
    if config.get("schema_version") != 1 or type(config.get("enabled")) is not bool:
        raise ValueError("Invalid article promotion switch")
    if not isinstance(config.get("channel_id"), str) or not config["channel_id"]:
        raise ValueError("Article X channel must be a pinned nonempty channel ID")
    caps = (
        ("max_queue_size", 3, 10),
        ("max_new_per_run", 1, 1),
        ("max_per_jst_day", 1, 7),
        ("min_page_views", 6, 100),
        ("fresh_minimum_age_hours", 1, 8),
        ("fresh_maximum_age_hours", 6, 24),
        ("minimum_article_age_hours", 8, 72),
        ("maximum_article_age_days", 7, 30),
        ("minimum_interval_hours", 2, 168),
    )
    for name, lower, upper in caps:
        value = config.get(name)
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(f"Unsafe article promotion config {name}")
    if config["fresh_minimum_age_hours"] >= config["fresh_maximum_age_hours"]:
        raise ValueError("Fresh article window is inverted")
    if config["fresh_maximum_age_hours"] > config["minimum_article_age_hours"] * 2:
        raise ValueError("Fresh article window overlaps too broadly with measured-PV lane")


def validate_state(state: dict) -> None:
    if state.get("schema_version") != 1 or not isinstance(state.get("queued"), dict):
        raise ValueError("Unsafe article post ledger")
    for slug, entry in state["queued"].items():
        if not SAFE_SLUG.fullmatch(slug) or not isinstance(entry, dict):
            raise ValueError("Unsafe article ID in permanent post ledger")
        if not isinstance(entry.get("buffer_id"), str) or not entry["buffer_id"]:
            raise ValueError("Incomplete Buffer ID in permanent post ledger")
        stamp = entry.get("queued_at_utc")
        if not isinstance(stamp, str):
            raise ValueError("Missing UTC timestamp in permanent post ledger")
        dt = datetime.fromisoformat(stamp)
        if dt.tzinfo is None:
            raise ValueError("Timezone missing in permanent post ledger")


def save_state(state: dict, path: Path = HERE / "state.json") -> None:
    validate_state(state)
    if path.is_symlink():
        raise RuntimeError("Unsafe article ledger symlink")
    tmp = path.with_suffix(path.suffix + ".tmp")
    if tmp.is_symlink():
        raise RuntimeError("Unsafe temporary article ledger symlink")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def article_slug_in_text(text: str) -> set[str]:
    return set(ARTICLE_URL.findall(text or ""))


def recover_from_buffer(state: dict, existing: list[dict], now: datetime,
                        known_slugs: set[str]) -> list[str]:
    """Reserve previously scheduled/sent article URLs before any new send."""
    validate_state(state)
    recovered = []
    for item in existing:
        if item.get("status") not in ("scheduled", "sent"):
            continue
        post_id = item.get("id")
        if not isinstance(post_id, str) or not post_id:
            continue
        for slug in article_slug_in_text(item.get("text", "")) & known_slugs:
            if slug not in state["queued"]:
                state["queued"][slug] = {
                    "buffer_id": post_id,
                    "queued_at_utc": now.astimezone(timezone.utc).isoformat(),
                    "source": "recovered_from_buffer",
                    "buffer_status_when_recovered": item.get("status"),
                    "text_sha256": hashlib.sha256(
                        (item.get("text", "") or "").encode("utf-8")
                    ).hexdigest(),
                }
                recovered.append(slug)
    return recovered


def publication_text(article: dict) -> str:
    """No misleading popularity ranking, analytics numbers or AI promo labels."""
    slug, title = article["slug"], article["title"]
    if not SAFE_SLUG.fullmatch(slug):
        raise ValueError("Unsafe article link")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Empty article title")
    tidy = re.sub(r"\s+", " ", title.strip())
    if len(tidy) > 68:
        tidy = tidy[:67].rstrip() + "…"
    url = "https://veripsa.com/articles/" + slug + "/" + TRACKING_QUERY
    hashtags = " ".join(article_hashtags(article))
    prefix = "【続報｜未発生新聞】" if article.get("followup_of") else TEXT_PREFIX
    suffix = ("\n\n" + hashtags if hashtags else "") + "\n\n" + url
    rendered = prefix + "\n" + tidy
    summary = article.get("summary")
    if isinstance(summary, str):
        summary = re.sub(r"\s+", " ", summary.strip())
        if summary:
            separator = "\n\n"
            budget = X_TEXT_LIMIT - x_weighted_length(rendered + separator + suffix)
            teaser = truncate_x_text(summary, budget)
            # Very short scraps do not give a reader enough context to earn
            # another line. Keep the stable headline/tags/link format instead.
            if x_weighted_length(teaser) >= 20:
                rendered += separator + teaser
    rendered += suffix
    if x_weighted_length(rendered) > X_TEXT_LIMIT:
        raise ValueError("Article X post text exceeds safe length")
    return rendered


def article_age(article: dict, now: datetime) -> timedelta | None:
    try:
        published = datetime.fromisoformat(article["published_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if published.tzinfo is None:
        return None
    return now - published


def is_crisis_article(article: dict) -> bool:
    """Automatic article promotion stays conservative around acute human harm."""
    searchable = " ".join(str(value or "") for value in (
        article.get("title"), article.get("summary"), article.get("source", {}).get("topic")
    ))
    return bool(CRISIS_PROMO_PATTERN.search(searchable))


def eligible_articles(articles: list[dict], now: datetime, config: dict) -> list[dict]:
    """Measured-PV lane uses real publication time, never fictional dates."""
    if now.tzinfo is None:
        raise ValueError("Current time must have timezone")
    eligible = []
    for article in articles:
        slug = article.get("slug")
        if not isinstance(slug, str) or not SAFE_SLUG.fullmatch(slug) or is_crisis_article(article):
            continue
        age = article_age(article, now)
        if age is None:
            continue
        if (timedelta(hours=config["minimum_article_age_hours"]) <= age <=
                timedelta(days=config["maximum_article_age_days"])):
            eligible.append(article)
    return eligible


def eligible_fresh_articles(articles: list[dict], now: datetime, config: dict) -> list[dict]:
    """Fresh lane exists to drive discovery before an article has enough PV to rank."""
    if now.tzinfo is None:
        raise ValueError("Current time must have timezone")
    result = []
    for article in articles:
        slug = article.get("slug")
        if not isinstance(slug, str) or not SAFE_SLUG.fullmatch(slug) or is_crisis_article(article):
            continue
        age = article_age(article, now)
        if age is None:
            continue
        if (timedelta(hours=config["fresh_minimum_age_hours"]) <= age <=
                timedelta(hours=config["fresh_maximum_age_hours"])):
            result.append(article)
    return result


def valid_page_views(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_SAFE_INTEGER


def page_view_window(data: dict, now: datetime) -> dict:
    """Validate observed coverage; an incomplete first window is not seven full days."""
    if now.tzinfo is None or not isinstance(data, dict):
        raise ValueError("Invalid page-view response or current time")
    tracking, week = data.get("pageViewTracking"), data.get("last7Days")
    if (not isinstance(tracking, dict) or tracking.get("unit") != "page-view"
            or tracking.get("timezone") != "Asia/Tokyo" or not isinstance(week, dict)):
        raise ValueError("Missing page-view tracking or period")
    try:
        started = datetime.fromisoformat(tracking["startedAtUtc"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Page-view measurement has not started") from exc
    if started.tzinfo is None or started > now:
        raise ValueError("Invalid page-view tracking start")
    today = now.astimezone(JST).date()
    first = today - timedelta(days=6)
    first_midnight = datetime.combine(first, datetime.min.time(), tzinfo=JST)
    measured_days = min(7, (today - started.astimezone(JST).date()).days + 1)
    if (not isinstance(data.get("today"), dict)
            or data["today"].get("dateJst") != today.isoformat()
            or week.get("startDateJst") != first.isoformat()
            or week.get("endDateJst") != today.isoformat()
            or not valid_page_views(week.get("pageViews"))
            or type(week.get("measuredDays")) is not int
            or week["measuredDays"] != measured_days
            or type(week.get("complete")) is not bool
            or week["complete"] != (started <= first_midnight)):
        raise ValueError("Inconsistent or stale page-view period")
    return {
        "unit": "page-view", "timezone": "Asia/Tokyo",
        "started_at_utc": started.astimezone(timezone.utc).isoformat(),
        "start_date_jst": first.isoformat(), "end_date_jst": today.isoformat(),
        "measured_days": measured_days, "complete": week["complete"],
    }


def read_page_view_metrics(slugs: list[str], now: datetime,
                           opener=urlopen) -> tuple[dict, dict]:
    """GET every candidate in API-sized batches, with one coherent measured window.

    Do not truncate to the daily snapshot's article limit or fall back to legacy
    browser/reaction counters. A missing batch or row invalidates the selection.
    """
    if not slugs or any(not isinstance(slug, str) or not SAFE_SLUG.fullmatch(slug)
                        for slug in slugs):
        raise ValueError("Invalid article metrics request")
    slugs = list(dict.fromkeys(slugs))
    metrics, window = {}, None
    for offset in range(0, len(slugs), 20):
        batch = slugs[offset:offset + 20]
        request = Request(METRICS_URL + "?" + urlencode({"articles": ",".join(batch)}),
                          headers={"Accept": "application/json", "Cache-Control": "no-cache",
                                   "User-Agent": "VeripsaArticlePicksReadOnly/1.0"}, method="GET")
        with opener(request, timeout=20) as response:
            data = json.load(response)
        observed_window = page_view_window(data, now)
        if window is not None and observed_window != window:
            raise ValueError("Page-view batches have different tracking periods")
        window = observed_window
        rows = data.get("articles")
        if not isinstance(rows, dict):
            raise ValueError("Missing article page views")
        for slug in batch:
            row = rows.get(slug)
            if (not isinstance(row, dict)
                    or any(not valid_page_views(row.get(key)) for key in METRIC_FIELDS)
                    or row["todayPageViews"] > row["last7DaysPageViews"]):
                raise ValueError("Missing or invalid article page views: " + slug)
            metrics[slug] = {key: row[key] for key in METRIC_FIELDS}
    return metrics, window


def rank(articles: list[dict], counts: dict, state: dict, blocked: set[str],
         config: dict) -> list[tuple[dict, dict, int]]:
    """Rank observed seven-date PV, today's PV, then real publication time."""
    validate_state(state)
    result = []
    for article in articles:
        slug = article["slug"]
        if slug in state["queued"] or slug in blocked:
            continue
        data = counts.get(slug)
        if (not isinstance(data, dict)
                or any(not valid_page_views(data.get(key)) for key in METRIC_FIELDS)
                or data["todayPageViews"] > data["last7DaysPageViews"]
                or data["last7DaysPageViews"] < config["min_page_views"]):
            continue
        result.append((article, data, data["last7DaysPageViews"]))
    result.sort(key=lambda row: (row[2], row[1]["todayPageViews"],
                                 datetime.fromisoformat(row[0]["published_at"])),
                reverse=True)
    return result


def rank_fresh(articles: list[dict], state: dict, blocked: set[str],
               config: dict, now: datetime) -> list[dict]:
    """Prefer a genuine follow-up, then the newest still-fresh unpromoted story."""
    result = []
    for article in eligible_fresh_articles(articles, now, config):
        slug = article["slug"]
        if slug in state["queued"] or slug in blocked:
            continue
        result.append(article)
    result.sort(
        key=lambda item: (
            bool(item.get("followup_of")),
            datetime.fromisoformat(item["published_at"]),
        ),
        reverse=True,
    )
    return result


def primary_topic(article: dict) -> str:
    """Use the first published topic tag as a small diversity key."""
    tags = article.get("tags")
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, str) and tag.strip():
                return "tag:" + tag.strip().casefold()
    category = article.get("category")
    if isinstance(category, str) and category.strip():
        return "category:" + category.strip().casefold()
    return ""


def promoted_topics_today(state: dict, articles: list[dict], now: datetime) -> set[str]:
    """Topics already selected today; missing old metadata never blocks a post."""
    by_slug = {
        item.get("slug"): item for item in articles
        if isinstance(item, dict) and isinstance(item.get("slug"), str)
    }
    day = now.astimezone(JST).date()
    result = set()
    for slug, entry in state.get("queued", {}).items():
        try:
            queued_at = datetime.fromisoformat(entry["queued_at_utc"])
        except (KeyError, TypeError, ValueError):
            continue
        if not queued_at.tzinfo or queued_at.astimezone(JST).date() != day:
            continue
        topic = primary_topic(by_slug.get(slug, {}))
        if topic:
            result.add(topic)
    return result


def prefer_topic_diversity(items: list, used_topics: set[str], *,
                           article_of=lambda item: item, lookahead: int = 3):
    """Prefer a different topic within the leading candidates, never skip all.

    A genuine follow-up keeps editorial priority because repeating its topic is
    intentional. For ordinary posts this only reorders the first few already
    eligible candidates; it never lowers the daily cap or invents a candidate.
    """
    if not items:
        return None
    first_article = article_of(items[0])
    if first_article.get("followup_of"):
        return items[0]
    if not used_topics:
        return items[0]
    for item in items[:lookahead]:
        article = article_of(item)
        topic = primary_topic(article)
        if not topic or topic not in used_topics:
            return item
    return items[0]


def promotions_today(state: dict, now: datetime) -> int:
    day = now.astimezone(JST).date()
    count = 0
    for entry in state["queued"].values():
        try:
            queued_at = datetime.fromisoformat(entry["queued_at_utc"])
        except (KeyError, TypeError, ValueError):
            continue
        if queued_at.tzinfo and queued_at.astimezone(JST).date() == day:
            count += 1
    return count


def x_posts_today(existing: list[dict], now: datetime) -> int:
    """Count all observed Buffer X posts on the JST day, across every lane."""
    day = now.astimezone(JST).date()
    seen = set()
    for row in existing:
        if row.get("status") not in ("scheduled", "sent"):
            continue
        value = row.get("sentAt") if row.get("status") == "sent" else row.get("dueAt")
        if not value:
            value = row.get("dueAt") or row.get("sentAt")
        if not value:
            continue
        try:
            observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            continue
        if observed.tzinfo and observed.astimezone(JST).date() == day:
            seen.add(row.get("id") or ("anonymous", row.get("text"), value))
    return len(seen)


def cooldown_active(state: dict, now: datetime, hours: int) -> bool:
    """Keep article promotions separated even when several are allowed per day."""
    for entry in state["queued"].values():
        t = datetime.fromisoformat(entry["queued_at_utc"])
        if t.tzinfo and timedelta(0) <= now - t < timedelta(hours=hours):
            return True
    return False


def decide(articles: list[dict], counts: dict, state: dict, existing: list[dict],
           config: dict, now: datetime
           ) -> tuple[dict | None, dict | None, int]:
    """Evaluate only after recovering remote article URLs and saved Buffer IDs."""
    known = {a["slug"] for a in articles if isinstance(a.get("slug"), str)}
    recover_from_buffer(state, existing, now, known)
    queued = [p for p in existing if p.get("status") == "scheduled"]
    remote_slugs = set().union(
        *(article_slug_in_text(p.get("text", "")) for p in existing
          if p.get("status") in ("scheduled", "sent"))
    ) if existing else set()
    today_count = promotions_today(state, now)
    if (x_posts_today(existing, now) >= MAX_DAILY_X_POSTS
            or len(queued) >= config["max_queue_size"]
            or today_count >= config["max_per_jst_day"]
            or cooldown_active(state, now, config["minimum_interval_hours"])):
        return None, None, len(queued)
    used_topics = promoted_topics_today(state, articles, now)
    fresh = rank_fresh(articles, state, remote_slugs, config, now)
    if fresh:
        selected = prefer_topic_diversity(fresh, used_topics)
        return selected, None, len(queued)
    candidates = rank(eligible_articles(articles, now, config), counts,
                      state, remote_slugs, config)
    if not candidates:
        return None, None, len(queued)
    selected = prefer_topic_diversity(
        candidates, used_topics, article_of=lambda row: row[0]
    )
    item, metrics, _score = selected
    return item, metrics, len(queued)


def main() -> int:
    config = read_json(HERE / "config.json")
    validate_config(config)
    if not config["enabled"]:
        print("Article X promotions OFF; no automatic X post is sent.")
        return 0

    state = read_json(HERE / "state.json")
    validate_state(state)
    now = datetime.now(timezone.utc)
    articles = json.loads((ROOT / "content" / "articles.json").read_text(encoding="utf-8"))
    if not isinstance(articles, list):
        raise ValueError("Invalid article archive")

    # Fresh articles can be promoted before PV has had time to accumulate.
    # Mature candidates still use the site's actual PV period, never legacy
    # browser counts, cumulative reactions or estimated snapshot changes.
    candidates = eligible_articles(articles, now, config)
    fresh_candidates = eligible_fresh_articles(articles, now, config)
    if not candidates and not fresh_candidates:
        print("No fresh or measured-interest articles; no Buffer write.")
        return 0
    token = os.environ.get("BUFFER_API_KEY", "").strip()
    if not token:
        raise RuntimeError("Buffer secret unavailable; cannot promote articles")
    metrics, window = ({}, None)
    if candidates:
        metrics, window = read_page_view_metrics([a["slug"] for a in candidates], now)
    org_id, channel_id = choose_x_channel(token)
    if channel_id != config["channel_id"]:
        raise RuntimeError("Connected X channel differs from pinned Veripsa account")
    existing = list_existing(token, org_id, channel_id, include_due_at=True)
    # Automatic X publishing is article-only; retired lanes do not gate slots.
    eligible, evidence, scheduled = decide(articles, metrics, state, existing,
                                            config, now)
    # Persist remote recovery before any further POST. The workflow also
    # commits both ledgers even after a partial API/network failure.
    save_state(state)
    if eligible is None:
        print("No eligible article for this check; Buffer scheduled:", scheduled)
        return 0
    text = publication_text(eligible)
    buffer_id = queue_one(token, channel_id, {"kind": "text", "text": text})
    entry = {
        "buffer_id": buffer_id,
        "queued_at_utc": now.isoformat(),
        "source": "fresh_article" if evidence is None else "verified_article_page_views",
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "published_at": eligible["published_at"],
        "primary_topic": primary_topic(eligible) or None,
    }
    if evidence is not None:
        entry["metrics_at_queue"] = {**window,
                                     "last_7_days_page_views": evidence["last7DaysPageViews"],
                                     "today_page_views": evidence["todayPageViews"]}
    state["queued"][eligible["slug"]] = entry
    save_state(state)
    print("Article accepted by Buffer (not verified X publication):",
          eligible["slug"], buffer_id, "scheduled_before=", scheduled,
          "selection=", entry["source"], "page_views=", evidence,
          "measurement_window=", window)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError) as exc:
        print("Safe article promotion skip:", exc, file=sys.stderr)
        sys.exit(1)
