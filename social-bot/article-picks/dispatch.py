#!/usr/bin/env python3
"""Exactly-once-ish Buffer Free queueing for genuinely observed reader-interest stories.

Uses the site's own GET-only anonymous-browser article counters, NEVER X
impressions or invented popularity. Shares the established X posting workflow
and its concurrency/queue budget with the original short-text lane.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(HERE.parent))
from record_metrics import read_public_metrics  # noqa: E402
from publish import choose_x_channel, list_existing, queue_one  # noqa: E402

SAFE_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
# Match canonical articles regardless of query parameters, extra prose or
# UTM links. Do not touch unknown external/shortened/manual-X URLs.
ARTICLE_URL = re.compile(
    r"https?://(?:www\.)?veripsa\.com/articles/([a-z0-9]+(?:-[a-z0-9]+)*)/?"
)
TEXT_PREFIX = "【未発生新聞・創作】"
MIN_EXTRA_SIGNALS = ("likes", "share_clicks", "continued_readers")
METRIC_FIELDS = ("readers", *MIN_EXTRA_SIGNALS)


def read_json(path: Path) -> dict:
    if path.is_symlink():
        raise RuntimeError("Unsafe symlink: " + str(path))
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Expected JSON object: " + str(path))
    return result


def validate_config(config: dict, original_x_config: dict) -> None:
    if config.get("schema_version") != 1 or type(config.get("enabled")) is not bool:
        raise ValueError("Invalid article promotion switch")
    if (config.get("channel_id") != original_x_config.get("channel_id")
            or not isinstance(config.get("channel_id"), str)
            or not config["channel_id"]):
        raise ValueError("Article X channel must equal the existing Veripsa X channel")
    caps = (
        ("max_queue_size", 3, 3),
        ("max_new_per_run", 1, 1),
        ("min_readers", 6, 100),
        ("minimum_article_age_hours", 12, 72),
        ("maximum_article_age_days", 7, 30),
        ("minimum_interval_hours", 72, 168),
    )
    for name, lower, upper in caps:
        value = config.get(name)
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(f"Unsafe article promotion config {name}")


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
    url = "https://veripsa.com/articles/" + slug + "/"
    rendered = TEXT_PREFIX + "\n" + tidy + "\n\n" + url
    if len(rendered) > 220:
        raise ValueError("Article X post text exceeds safe length")
    return rendered


def eligible_articles(articles: list[dict], now: datetime, config: dict) -> list[dict]:
    """Use real publication time; never 2031 fictional dates or source dates."""
    if now.tzinfo is None:
        raise ValueError("Current time must have timezone")
    eligible = []
    for article in articles:
        slug = article.get("slug")
        if not isinstance(slug, str) or not SAFE_SLUG.fullmatch(slug):
            continue
        try:
            published = datetime.fromisoformat(article["published_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if published.tzinfo is None:
            continue
        age = now - published
        if (timedelta(hours=config["minimum_article_age_hours"]) <= age <=
                timedelta(days=config["maximum_article_age_days"])):
            eligible.append(article)
    return eligible


def rank(articles: list[dict], counts: dict, state: dict, blocked: set[str],
         config: dict) -> list[tuple[dict, dict, int]]:
    """Require meaningful sample + real engagement, no fake 'trending' score."""
    validate_state(state)
    result = []
    for article in articles:
        slug = article["slug"]
        if slug in state["queued"] or slug in blocked:
            continue
        data = counts.get(slug)
        if not isinstance(data, dict) or any(
            type(data.get(key)) is not int or data[key] < 0 for key in METRIC_FIELDS
        ):
            continue
        readers = data["readers"]
        if readers < config["min_readers"]:
            continue
        # One secondary signal or 12 cumulative reader browsers; these
        # thresholds are eligibility heuristics, NOT a site ranking.
        secondary = sum(data[key] for key in MIN_EXTRA_SIGNALS)
        if not (secondary > 0 or readers >= 12):
            continue
        score = (readers + 3 * data["likes"] + 2 * data["share_clicks"]
                 + 2 * data["continued_readers"])
        result.append((article, data, score))
    result.sort(key=lambda row: (row[2], row[1]["readers"],
                                row[0]["published_at"]), reverse=True)
    return result


def cooldown_active(state: dict, now: datetime, hours: int) -> bool:
    """At most one article promotion in any rolling 72-hour window."""
    for entry in state["queued"].values():
        t = datetime.fromisoformat(entry["queued_at_utc"])
        if t.tzinfo and timedelta(0) <= now - t < timedelta(hours=hours):
            return True
    return False


def decide(articles: list[dict], counts: dict, state: dict, existing: list[dict],
           config: dict, now: datetime, creative_ids: set[str]
           ) -> tuple[dict | None, dict | None, int]:
    """Evaluate ONLY after recovering remote posts, keeping original queue alive."""
    known = {a["slug"] for a in articles if isinstance(a.get("slug"), str)}
    recover_from_buffer(state, existing, now, known)
    queued = [p for p in existing if p.get("status") == "scheduled"]
    remote_slugs = set().union(
        *(article_slug_in_text(p.get("text", "")) for p in existing
          if p.get("status") in ("scheduled", "sent"))
    ) if existing else set()
    if (len(queued) >= config["max_queue_size"]
            or cooldown_active(state, now, config["minimum_interval_hours"])):
        return None, None, len(queued)
    # When Buffer contains only unrelated/legacy scheduled posts, let the
    # original short-text lane refill first; do not monopolize its queue.
    if queued and not any(p.get("id") in creative_ids for p in queued):
        return None, None, len(queued)
    candidates = rank(eligible_articles(articles, now, config), counts,
                      state, remote_slugs, config)
    if not candidates:
        return None, None, len(queued)
    item, metrics, _score = candidates[0]
    return item, metrics, len(queued)


def main() -> int:
    config = read_json(HERE / "config.json")
    original = read_json(HERE.parent / "experiments" / "config.json")
    validate_config(config, original)
    if not config["enabled"]:
        print("Article X promotions OFF; short original X lane unaffected.")
        return 0

    state = read_json(HERE / "state.json")
    validate_state(state)
    now = datetime.now(timezone.utc)
    articles = json.loads((ROOT / "content" / "articles.json").read_text(encoding="utf-8"))
    if not isinstance(articles, list):
        raise ValueError("Invalid article archive")

    # Read-only GETs, with strict per-article unit/field checks shared with
    # existing daily snapshots. Not impressions or human unique users.
    candidates = eligible_articles(articles, now, config)
    if not candidates:
        print("No sufficiently old published articles; no Buffer write.")
        return 0
    token = os.environ.get("BUFFER_API_KEY", "").strip()
    if not token:
        raise RuntimeError("Buffer secret unavailable; cannot promote articles")
    _, metrics, _ = read_public_metrics([a["slug"] for a in candidates])
    org_id, channel_id = choose_x_channel(token)
    if channel_id != config["channel_id"]:
        raise RuntimeError("Connected X channel differs from pinned Veripsa account")
    existing = list_existing(token, org_id, channel_id)
    creative_state = read_json(HERE.parent / "experiments" / "state.json")
    creative_ids = {
        v["buffer_id"] for v in creative_state.get("queued", {}).values()
        if isinstance(v, dict) and isinstance(v.get("buffer_id"), str)
    }
    eligible, evidence, scheduled = decide(articles, metrics, state, existing,
                                            config, now, creative_ids)
    # Persist remote recovery before any further POST. The workflow also
    # commits both ledgers even after a partial API/network failure.
    save_state(state)
    if eligible is None:
        print("No eligible reader-interest article today; Buffer scheduled:",
              scheduled, "- original X feed retains priority.")
        return 0
    text = publication_text(eligible)
    buffer_id = queue_one(token, channel_id, {"kind": "text", "text": text})
    state["queued"][eligible["slug"]] = {
        "buffer_id": buffer_id,
        "queued_at_utc": now.isoformat(),
        "source": "verified_anonymous_browser_article_metrics",
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "metrics_at_queue": {key: evidence[key] for key in METRIC_FIELDS},
    }
    save_state(state)
    print("Reader-interest article accepted by Buffer (not X impressions or "
          "verified X publication):", eligible["slug"], buffer_id,
          "scheduled_before=", scheduled)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError) as exc:
        print("Safe article promotion skip:", exc, file=sys.stderr)
        sys.exit(1)
