"""Cost-free adapters for the published market-analysis serial, never price advice.

These functions only read committed editorial inputs. They create no market
claims, publication timestamps, images, provider requests, or separate X lane.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

MARKET_PREFIX = "market-lab-"
MARKET_COVER = "social-bot/assets/market-series-cover.png"
SAFE_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
JST = timezone(timedelta(hours=9))


def market_key(episode_slug: str) -> str:
    if not isinstance(episode_slug, str) or not SAFE_SLUG.fullmatch(episode_slug):
        raise ValueError("Unsafe market episode slug")
    return MARKET_PREFIX + episode_slug


def is_market_key(value: object) -> bool:
    return (isinstance(value, str) and value.startswith(MARKET_PREFIX)
            and bool(SAFE_SLUG.fullmatch(value[len(MARKET_PREFIX):])))


def market_episode_slug(key: str) -> str:
    if not is_market_key(key):
        raise ValueError("Unsafe market promotion key")
    return key[len(MARKET_PREFIX):]


def _publication_day(value: object) -> date:
    # The serial records an actual calendar date only. Do not fabricate midnight
    # timestamps or treat the series' forecast-review date as publication time.
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Market publication requires its original date")
    day = date.fromisoformat(value)
    if day.isoformat() != value:
        raise ValueError("Invalid market publication date")
    return day


def load_market_articles(root: Path) -> list[dict]:
    path = Path(root) / "content/serials.json"
    if path.is_symlink():
        raise ValueError("Unsafe market source path")
    if not path.exists():
        return []
    serials = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(serials, list):
        raise ValueError("Invalid serial archive")
    matches = [series for series in serials
               if isinstance(series, dict) and series.get("slug") == "market-lab"]
    if not matches:
        return []
    if len(matches) != 1 or matches[0].get("kind") != "analysis":
        raise ValueError("Market promotion must reference the analysis serial")
    episodes = matches[0].get("episodes")
    if not isinstance(episodes, list):
        raise ValueError("Invalid market episode archive")
    result, seen = [], set()
    for episode in episodes:
        if not isinstance(episode, dict):
            raise ValueError("Invalid market episode")
        key = market_key(episode.get("slug"))
        if key in seen:
            raise ValueError("Duplicate market episode")
        seen.add(key)
        day = _publication_day(episode.get("published_at"))
        title, summary = episode.get("title"), episode.get("summary")
        if not isinstance(title, str) or not title.strip() or not isinstance(summary, str) or not summary.strip():
            raise ValueError("Missing market article copy")
        fingerprint = hashlib.sha256(json.dumps(
            episode, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")).hexdigest()
        result.append({
            "slug": key,
            "title": f"【市場分析｜{day.isoformat()}】" + title,
            "summary": summary,
            "published_at": episode["published_at"],
            "promotion_kind": "market_analysis",
            "episode_sha256": fingerprint,
        })
    return result


def _record_days(entry: object, fields: tuple[str, ...]) -> set[date]:
    if not isinstance(entry, dict):
        raise ValueError("Invalid market promotion record")
    days = set()
    for field in fields:
        value = entry.get(field)
        if value is None:
            continue
        try:
            stamp = datetime.fromisoformat(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid market promotion timestamp") from exc
        if stamp.tzinfo is None:
            raise ValueError("Timezone missing from market promotion timestamp")
        days.add(stamp.astimezone(JST).date())
    if not days:
        raise ValueError("Missing market promotion timestamp")
    return days


def market_candidates(items: list[dict], state: dict, blocked: set[str], now: datetime,
                      max_age_days: int = 7) -> list[dict]:
    """Newest unseen serial episodes, at most one market introduction per JST day.

    Shared dispatcher slot, provider-spacing and total daily caps still apply.
    Date-only editorial input is compared as dates, without an invented hour.
    """
    if now.tzinfo is None or type(max_age_days) is not int or not 0 <= max_age_days <= 30:
        raise ValueError("Invalid market promotion date window")
    today = now.astimezone(JST).date()
    queued, public = state.get("queued", {}), state.get("public_posts", {})
    if not isinstance(queued, dict) or not isinstance(public, dict):
        raise ValueError("Invalid market promotion ledger")
    for key, entry in queued.items():
        if is_market_key(key) and today in _record_days(
                entry, ("queued_at_utc", "slot_jst", "buffer_sent_at_at_accept")):
            return []
    for key, entry in public.items():
        if is_market_key(key) and today in _record_days(entry, ("posted_at_utc",)):
            return []
    unavailable = set(queued) | set(public) | set(blocked)
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if not is_market_key(item.get("slug")) and item.get("promotion_kind") != "market_analysis":
            continue
        if item.get("promotion_kind") != "market_analysis" or not is_market_key(item.get("slug")):
            raise ValueError("Market candidates must be analysis adapters")
        day = _publication_day(item.get("published_at"))
        if (item["slug"] not in unavailable
                and timedelta(0) <= today - day <= timedelta(days=max_age_days)):
            result.append(item)
    return sorted(result, key=lambda item: (item["published_at"], item["slug"]), reverse=True)
