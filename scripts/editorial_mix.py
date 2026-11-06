#!/usr/bin/env python3
"""Editorial lane/tone metadata, daily 3/2/2 guidance, and duplicate guards.

This module is intentionally offline and dependency-free. It validates metadata
that the publishing automation writes into content/articles.json. It does not
fetch trends, rank article quality, or publish anything by itself.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")

LANE_TARGETS = {
    "trend": 3,
    "social_life": 2,
    "original_satire": 2,
}
LANE_PRIORITY = tuple(LANE_TARGETS)
EDITORIAL_TONES = (
    "plausible_future",
    "comic_exaggeration",
    "observational",
    "wildcard",
)
EDITORIAL_PAYOFFS = (
    "dialogue",
    "action",
    "visible_result",
    "ironic_success",
    "quiet_aftertaste",
    "wordplay",
)
READER_INTERESTS = (
    "work_money_tech",
    "beauty_wellness",
    "relationships_social",
    "food_travel_shopping",
    "fandom_entertainment",
    "family_home",
    "general_society",
)
LIFE_READER_INTERESTS = (
    "beauty_wellness",
    "relationships_social",
    "food_travel_shopping",
    "fandom_entertainment",
    "family_home",
)
RECURRING_CHARACTERS = ("mina", "nono", "yuu")
PAYOFF_LABELS = {
    "dialogue": "一言",
    "action": "行動",
    "visible_result": "光景",
    "ironic_success": "皮肉な成功",
    "quiet_aftertaste": "余韻",
    "wordplay": "言葉遊び",
}
TONE_LABELS = {
    "plausible_future": "ありそう",
    "comic_exaggeration": "誇張",
    "observational": "観察",
    "wildcard": "変化球",
}
PLAUSIBILITY_KEYS = (
    "present_signal",
    "continuation_force",
    "future_result",
)
TREND_SOURCE_KINDS = {
    "google_trends",
    "x_official",
    "x_public_aggregator",
}
ROLLOUT_AT = datetime.fromisoformat("2026-11-05T20:00:00+09:00")
PAYOFF_ROLLOUT_AT = datetime.fromisoformat("2026-11-06T09:00:00+09:00")
TREND_BRIDGE_ROLLOUT_AT = datetime.fromisoformat("2026-11-06T16:00:00+09:00")
TREND_SPECIFICITY_ROLLOUT_AT = datetime.fromisoformat("2026-11-06T18:00:00+09:00")
READER_INTEREST_ROLLOUT_AT = datetime.fromisoformat("2026-11-07T00:00:00+09:00")
READER_CLARITY_ROLLOUT_AT = datetime.fromisoformat("2026-11-06T21:00:00+09:00")
SATIRE_PRESENT_ROLLOUT_AT = datetime.fromisoformat("2026-11-06T22:00:00+09:00")
VISUAL_STRATEGY_ROLLOUT_AT = datetime.fromisoformat("2026-11-06T22:00:00+09:00")
VISUAL_STRATEGIES = ("cpu-photo", "illustration")
ILLUSTRATION_REASONS = (
    "readable_text_or_ui",
    "precise_count_or_absence",
    "complex_relationship",
    "comparison_layout",
    "brand_character_safety",
)
TREND_BRIDGE_KEYS = ("why_now", "shared_behavior", "satire_gap", "x_discovery_term")
TREND_SPECIFICITY_KEYS = ("real_detail", "unique_hook")
READER_CLARITY_KEYS = ("who", "action", "oddity")
X_TAG_RE = re.compile(r"\w{1,12}\Z")
X_TICKER_RE = re.compile(r"[A-Z][A-Z0-9.]{0,5}\Z")
GENERIC_X_DISCOVERY_TERMS = {
    "AI", "SNS", "アプリ", "イベント", "お金", "ニュース", "メディア",
    "仕事", "働き方", "家族", "技術", "教育", "暮らし", "文化", "旅行",
    "生活", "研究", "社会", "経済",
}
TREND_MAX_AGE_BY_SOURCE = {
    "google_trends": timedelta(hours=12),
    "x_official": timedelta(hours=6),
    "x_public_aggregator": timedelta(hours=6),
}
DUPLICATE_WINDOW = timedelta(days=7)

_ws = re.compile(r"\s+")
_PHOTO_SCENE_RE = re.compile(
    r"\b(?:photorealistic|photo(?:graph)?|documentary[- ]style|press photo|reporting scene|"
    r"candid photograph|editorial photograph)\b",
    re.IGNORECASE,
)
_ILLUSTRATION_SCENE_RE = re.compile(
    r"\b(?:illustration|anime|cartoon|3d render|digital painting|concept art|vector art)\b",
    re.IGNORECASE,
)


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
    """Normalize harmless typography differences before duplicate comparisons."""
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        char for char in normalized
        if not char.isspace()
        and not unicodedata.category(char).startswith("P")
        and unicodedata.category(char) != "Cf"
    ).strip()


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


def tone_counts(articles: list[dict], day_jst: str) -> Counter:
    counts: Counter = Counter()
    for article in articles:
        if article_day(article) != day_jst:
            continue
        tone = article.get("editorial_tone")
        if tone in EDITORIAL_TONES:
            counts[tone] += 1
    return counts


def payoff_counts(articles: list[dict], day_jst: str) -> Counter:
    counts: Counter = Counter()
    for article in articles:
        if article_day(article) != day_jst:
            continue
        payoff = article.get("editorial_payoff")
        if payoff in EDITORIAL_PAYOFFS:
            counts[payoff] += 1
    return counts


def day_story_count(articles: list[dict], day_jst: str) -> int:
    return sum(article_day(article) == day_jst for article in articles)


def reader_interest_counts(articles: list[dict], day_jst: str) -> Counter:
    counts: Counter = Counter()
    for article in articles:
        if article_day(article) != day_jst:
            continue
        interest = article.get("reader_interest")
        if interest in READER_INTERESTS:
            counts[interest] += 1
    return counts


def visual_strategy_counts(articles: list[dict], day_jst: str) -> Counter:
    """Count the requested visual route; legacy omissions use the photo-first default."""
    counts: Counter = Counter()
    for article in articles:
        if article_day(article) != day_jst:
            continue
        strategy = article.get("visual_strategy", "cpu-photo")
        if strategy in VISUAL_STRATEGIES:
            counts[strategy] += 1
    return counts


def visual_strategy_advice(articles: list[dict], day_jst: str) -> str:
    """Keep illustration as an exception without turning photos into a quota."""
    counts = visual_strategy_counts(articles, day_jst)
    total = sum(counts.values())
    illustrations = counts.get("illustration", 0)
    photos = counts.get("cpu-photo", 0)
    if total >= 4 and illustrations > photos:
        return (
            f"cpu-photo preferred (illustration is {illustrations}/{total}; "
            "use illustration only when an explicit visual constraint is essential)"
        )
    return "free (photo-first default; illustration only for a concrete visual constraint)"


def reader_interest_advice(articles: list[dict], day_jst: str) -> str:
    """Nudge candidate generation away from a narrow work/tech-heavy slate."""
    counts = reader_interest_counts(articles, day_jst)
    published = day_story_count(articles, day_jst)
    life_count = sum(counts.get(interest, 0) for interest in LIFE_READER_INTERESTS)

    tracked = sorted(
        (
            article for article in articles
            if _parse_dt(article.get("published_at")) is not None
            and article.get("reader_interest") in READER_INTERESTS
        ),
        key=lambda article: str(article.get("published_at") or ""),
    )
    recent = [article.get("reader_interest") for article in tracked]
    if len(recent) >= 2 and recent[-1] == recent[-2]:
        repeated = recent[-1]
        pool = [
            interest for interest in READER_INTERESTS
            if interest != repeated
        ]
        if repeated in {"work_money_tech", "general_society"}:
            pool = list(LIFE_READER_INTERESTS)
        preferred = min(
            pool,
            key=lambda interest: (counts.get(interest, 0), READER_INTERESTS.index(interest)),
        )
        return (
            f"{preferred} (last 2 tracked stories share reader_interest={repeated}; "
            "compare a different everyday-life angle before repeating it)"
        )

    slots_left = max(0, sum(LANE_TARGETS.values()) - published)
    life_needed = max(0, 3 - life_count)
    if life_needed and slots_left <= life_needed:
        preferred = min(
            LIFE_READER_INTERESTS,
            key=lambda interest: (counts.get(interest, 0), LIFE_READER_INTERESTS.index(interest)),
        )
        return (
            f"{preferred} (life-facing interests are {life_count}/3 toward the soft "
            "7-story balance; compare one without lowering the quality gate)"
        )

    if counts.get("work_money_tech", 0) >= 2 and life_count < 3:
        preferred = min(
            LIFE_READER_INTERESTS,
            key=lambda interest: (counts.get(interest, 0), LIFE_READER_INTERESTS.index(interest)),
        )
        return (
            f"{preferred} (work/money/tech already appears {counts['work_money_tech']} "
            "times today; broaden the next candidate set)"
        )

    return "free (reader-interest mix is varied; quality decides)"


def character_advice(articles: list[dict], day_jst: str) -> str:
    """Keep recurring characters as a light accent, never a quota."""
    day_chars = [
        article.get("recurring_character")
        for article in articles
        if article_day(article) == day_jst
        and article.get("recurring_character") in RECURRING_CHARACTERS
    ]
    if day_chars:
        return "standalone preferred (a recurring-character story is already in today's slate)"

    target_day = datetime.fromisoformat(day_jst + "T00:00:00+09:00")
    window_start = target_day - timedelta(days=6)
    recent_counts: Counter = Counter()
    for article in articles:
        published = _parse_dt(article.get("published_at"))
        character = article.get("recurring_character")
        if (
            published is not None
            and window_start <= published.astimezone(JST) < target_day + timedelta(days=1)
            and character in RECURRING_CHARACTERS
        ):
            recent_counts[character] += 1

    eligible = [
        character for character in RECURRING_CHARACTERS
        if recent_counts.get(character, 0) < 2
    ]
    if not eligible:
        return "standalone preferred (each recurring character already appeared twice in 7 days)"
    preferred = min(
        eligible,
        key=lambda character: (recent_counts.get(character, 0), RECURRING_CHARACTERS.index(character)),
    )
    return f"optional {preferred} (only if the story works for a first-time reader)"


def tone_advice(articles: list[dict], day_jst: str) -> str:
    """Suggest a different reader aftertaste without blocking publication."""
    day_items = sorted(
        (article for article in articles if article_day(article) == day_jst),
        key=lambda article: str(article.get("published_at") or ""),
    )
    day_tones = [
        article.get("editorial_tone")
        for article in day_items
        if article.get("editorial_tone") in EDITORIAL_TONES
    ]
    counts = Counter(day_tones)

    tracked_items = sorted(
        (
            article for article in articles
            if _parse_dt(article.get("published_at")) is not None
            and article.get("editorial_tone") in EDITORIAL_TONES
        ),
        key=lambda article: str(article.get("published_at") or ""),
    )
    recent_tones = [article.get("editorial_tone") for article in tracked_items]

    if counts["plausible_future"] == 0:
        return (
            "plausible_future (today 0; compare one sharp, genuinely plausible "
            "five-year extension before choosing)"
        )

    if len(recent_tones) >= 2 and recent_tones[-1] == recent_tones[-2]:
        repeated = recent_tones[-1]
        alternatives = [tone for tone in EDITORIAL_TONES if tone != repeated]
        preferred = min(
            alternatives,
            key=lambda tone: (counts.get(tone, 0), EDITORIAL_TONES.index(tone)),
        )
        return (
            f"{preferred} (last 2 tracked stories are {repeated}; compare a "
            "different reader aftertaste before a third, even across midnight)"
        )

    if counts["wildcard"] >= 1:
        candidates = [
            "plausible_future", "comic_exaggeration", "observational"
        ]
        preferred = min(
            candidates,
            key=lambda tone: (counts.get(tone, 0), EDITORIAL_TONES.index(tone)),
        )
        return f"{preferred} (wildcard already used today; do not force another)"

    return "free (tone mix is varied; quality decides)"


def payoff_advice(articles: list[dict], day_jst: str) -> str:
    """Suggest a different ending shape when the recent payoff is repeating."""
    counts = payoff_counts(articles, day_jst)
    tracked_items = sorted(
        (
            article for article in articles
            if _parse_dt(article.get("published_at")) is not None
            and article.get("editorial_payoff") in EDITORIAL_PAYOFFS
        ),
        key=lambda article: str(article.get("published_at") or ""),
    )
    recent = [article.get("editorial_payoff") for article in tracked_items]

    if len(recent) >= 2 and recent[-1] == recent[-2]:
        repeated = recent[-1]
        alternatives = [payoff for payoff in EDITORIAL_PAYOFFS if payoff != repeated]
        preferred = min(
            alternatives,
            key=lambda payoff: (counts.get(payoff, 0), EDITORIAL_PAYOFFS.index(payoff)),
        )
        return (
            f"{preferred} (last 2 tracked stories end as {repeated}; compare a "
            "different payoff before a third)"
        )

    if counts:
        dominant, number = counts.most_common(1)[0]
        if number >= 3:
            alternatives = [payoff for payoff in EDITORIAL_PAYOFFS if payoff != dominant]
            preferred = min(
                alternatives,
                key=lambda payoff: (counts.get(payoff, 0), EDITORIAL_PAYOFFS.index(payoff)),
            )
            return (
                f"{preferred} ({dominant} already used {number} times today; "
                "compare a different ending shape)"
            )

    return "free (choose the clearest ending for the story; do not fill a quota)"


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


def _validate_signature(article: dict, slug: str, published: datetime) -> list[str]:
    errors: list[str] = []
    signature = article.get("editorial_signature")
    if not isinstance(signature, dict):
        return [f"{slug}: editorial_signature is required after rollout"]
    for key in ("target", "action", "outcome"):
        value = signature.get(key)
        if not isinstance(value, str) or not 4 <= len(value.strip()) <= 220:
            errors.append(f"{slug}: editorial_signature.{key} must be 4-220 chars")
    if published >= SATIRE_PRESENT_ROLLOUT_AT:
        present = signature.get("present")
        if not isinstance(present, str) or not 8 <= len(present.strip()) <= 220:
            errors.append(
                f"{slug}: editorial_signature.present must be 8-220 chars after "
                "the present-reality rollout"
            )
    return errors


def _validate_visual_strategy(article: dict, slug: str, published: datetime) -> list[str]:
    """Make photo-first routing inspectable and reject contradictory scene directions."""
    if published < VISUAL_STRATEGY_ROLLOUT_AT:
        return []

    strategy = article.get("visual_strategy", "cpu-photo")
    if strategy not in VISUAL_STRATEGIES:
        return [
            f"{slug}: visual_strategy must be one of " + ", ".join(VISUAL_STRATEGIES)
        ]

    errors: list[str] = []
    scene_prompt = article.get("scene_prompt")
    if not isinstance(scene_prompt, str) or len(scene_prompt.strip()) < 24:
        errors.append(f"{slug}: scene_prompt must describe a concrete visual scene")
        scene_prompt = ""

    reason = article.get("illustration_reason")
    if strategy == "illustration":
        if reason not in ILLUSTRATION_REASONS:
            errors.append(
                f"{slug}: illustration_reason required; expected one of "
                + ", ".join(ILLUSTRATION_REASONS)
            )
        if scene_prompt and _PHOTO_SCENE_RE.search(scene_prompt):
            errors.append(
                f"{slug}: visual_strategy=illustration conflicts with a photo-directed "
                "scene_prompt; either use cpu-photo or rewrite the scene as an illustration"
            )
    else:
        if reason is not None:
            errors.append(
                f"{slug}: illustration_reason is only valid with visual_strategy=illustration"
            )
        if scene_prompt and _ILLUSTRATION_SCENE_RE.search(scene_prompt):
            errors.append(
                f"{slug}: visual_strategy=cpu-photo conflicts with illustration-style "
                "scene_prompt wording"
            )
    return errors



def _validate_reader_clarity(article: dict, slug: str, published: datetime) -> list[str]:
    """Require a blind first-read check for newly published stories."""
    if published < READER_CLARITY_ROLLOUT_AT:
        return []

    clarity = article.get("reader_clarity")
    if not isinstance(clarity, dict):
        return [f"{slug}: reader_clarity is required after clarity rollout"]

    errors: list[str] = []
    limits = {
        "who": (2, 60),
        "action": (4, 140),
        "oddity": (8, 180),
    }
    for key in READER_CLARITY_KEYS:
        value = clarity.get(key)
        low, high = limits[key]
        if not isinstance(value, str) or not low <= len(value.strip()) <= high:
            errors.append(
                f"{slug}: reader_clarity.{key} must be {low}-{high} chars"
            )

    title = article.get("title")
    summary = article.get("summary")
    if not isinstance(title, str) or len(title.strip()) < 12:
        errors.append(f"{slug}: clarity rollout requires a self-contained title")
    if not isinstance(summary, str) or len(summary.strip()) < 30:
        errors.append(f"{slug}: clarity rollout requires a self-contained summary")

    who = clarity.get("who")
    if (
        isinstance(who, str)
        and who.strip()
        and isinstance(title, str)
        and isinstance(summary, str)
        and _norm(who) not in _norm(title + " " + summary)
    ):
        errors.append(
            f"{slug}: reader_clarity.who must appear in the title or summary "
            "so the subject is explicit on a blind read"
        )
    return errors

def _validate_plausibility(article: dict, slug: str) -> list[str]:
    """Require an inspectable causal path only for the deliberately plausible tone."""
    if article.get("editorial_tone") != "plausible_future":
        return []

    basis = article.get("plausibility_basis")
    if not isinstance(basis, dict):
        return [f"{slug}: plausible_future requires plausibility_basis"]

    errors: list[str] = []
    for key in PLAUSIBILITY_KEYS:
        value = basis.get(key)
        if not isinstance(value, str) or not 8 <= len(value.strip()) <= 240:
            errors.append(f"{slug}: plausibility_basis.{key} must be 8-240 chars")

    source_url = article.get("source", {}).get("url")
    if not isinstance(source_url, str) or not source_url.startswith("https://"):
        errors.append(
            f"{slug}: plausible_future needs an HTTPS source.url for its present signal"
        )
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
        else:
            max_age = TREND_MAX_AGE_BY_SOURCE.get(
                source_kind, timedelta(hours=6)
            )
            if age > max_age:
                errors.append(
                    f"{slug}: trend observation is {age.total_seconds()/3600:.1f}h old; "
                    f"max is {max_age.total_seconds()/3600:.0f}h for {source_kind}"
                )

    trend_url = trend.get("url")
    if not isinstance(trend_url, str) or not trend_url.startswith("https://"):
        errors.append(f"{slug}: trend_observation.url must be HTTPS")
    else:
        host = (urlsplit(trend_url).hostname or "").casefold()
        if source_kind == "google_trends" and host != "trends.google.com":
            errors.append(
                f"{slug}: google_trends observation URL must use trends.google.com"
            )
        if source_kind == "x_official" and host not in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
            errors.append(
                f"{slug}: x_official observation URL must use x.com or twitter.com"
            )

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


def _validate_x_metadata(article: dict, slug: str, published: datetime) -> list[str]:
    """Reject explicit X terms that would be silently dropped or never published."""
    if published < TREND_BRIDGE_ROLLOUT_AT:
        return []

    errors: list[str] = []
    tags = article.get("x_tags", [])
    tickers = article.get("x_tickers", [])
    if not isinstance(tags, list):
        return [f"{slug}: x_tags must be a list when present"]
    if not isinstance(tickers, list):
        return [f"{slug}: x_tickers must be a list when present"]
    if len(tags) + len(tickers) > 2:
        errors.append(f"{slug}: x_tags + x_tickers may contain at most 2 discovery terms")

    seen: set[str] = set()
    for tag in tags:
        if (
            not isinstance(tag, str)
            or not X_TAG_RE.fullmatch(tag)
            or not any(char.isalpha() for char in tag)
        ):
            errors.append(f"{slug}: invalid x_tags value {tag!r}")
            continue
        if tag in GENERIC_X_DISCOVERY_TERMS:
            errors.append(f"{slug}: generic X tag '{tag}' is too broad")
        identity = _norm(tag)
        if identity in seen:
            errors.append(f"{slug}: duplicate X discovery term '{tag}'")
        seen.add(identity)

    for ticker in tickers:
        if not isinstance(ticker, str) or not X_TICKER_RE.fullmatch(ticker):
            errors.append(f"{slug}: invalid x_tickers value {ticker!r}")
            continue
        identity = _norm(ticker)
        if identity in seen:
            errors.append(f"{slug}: duplicate X discovery term '{ticker}'")
        seen.add(identity)
    return errors


def _validate_trend_bridge(article: dict, slug: str, published: datetime) -> list[str]:
    """Require a deliberate bridge from a live trend to the satire and X discovery term."""
    if published < TREND_BRIDGE_ROLLOUT_AT:
        return []

    bridge = article.get("trend_bridge")
    if not isinstance(bridge, dict):
        return [f"{slug}: trend lane requires trend_bridge after quality rollout"]

    errors: list[str] = []
    for key in TREND_BRIDGE_KEYS[:3]:
        value = bridge.get(key)
        if not isinstance(value, str) or not 8 <= len(value.strip()) <= 240:
            errors.append(f"{slug}: trend_bridge.{key} must be 8-240 chars")

    discovery = bridge.get("x_discovery_term")
    if not isinstance(discovery, str) or not 2 <= len(discovery.strip()) <= 40:
        errors.append(f"{slug}: trend_bridge.x_discovery_term must be 2-40 chars")
        discovery = ""
    else:
        discovery = discovery.strip()
        if discovery.startswith(("#", "$")) or any(char.isspace() for char in discovery):
            errors.append(
                f"{slug}: trend_bridge.x_discovery_term must be a bare hashtag/cashtag term"
            )
        if discovery in GENERIC_X_DISCOVERY_TERMS:
            errors.append(
                f"{slug}: generic X discovery term '{discovery}' is too broad for trend promotion"
            )

    x_tags = article.get("x_tags")
    tag_terms = {
        item.strip()
        for item in x_tags
        if isinstance(item, str) and item.strip()
    } if isinstance(x_tags, list) else set()
    x_tickers = article.get("x_tickers")
    ticker_terms = {
        item.strip().upper()
        for item in x_tickers
        if isinstance(item, str) and item.strip()
    } if isinstance(x_tickers, list) else set()
    if discovery and discovery not in tag_terms and discovery.upper() not in ticker_terms:
        errors.append(
            f"{slug}: x_discovery_term must be explicitly present in x_tags or x_tickers"
        )

    primary_discovery = ""
    if isinstance(x_tickers, list):
        primary_discovery = next(
            (
                item.strip().upper()
                for item in x_tickers
                if isinstance(item, str) and item.strip()
            ),
            "",
        )
    if not primary_discovery and isinstance(x_tags, list):
        primary_discovery = next(
            (
                item.strip()
                for item in x_tags
                if isinstance(item, str) and item.strip()
            ),
            "",
        )
    if discovery and primary_discovery and _norm(discovery) != _norm(primary_discovery):
        errors.append(
            f"{slug}: x_discovery_term must be the first explicit X discovery term"
        )

    title = article.get("title")
    summary = article.get("summary")
    source_topic = article.get("source", {}).get("topic")
    reader_anchor = _norm(
        " ".join(
            value
            for value in (title, summary)
            if isinstance(value, str)
        )
    )
    public_anchor = _norm(
        " ".join(
            value
            for value in (title, summary, source_topic)
            if isinstance(value, str)
        )
    )
    trend = article.get("trend_observation")
    observed_raw = (
        trend.get("term").strip()
        if isinstance(trend, dict) and isinstance(trend.get("term"), str)
        else ""
    )
    observed_term = _norm(observed_raw)
    discovery_norm = _norm(discovery)
    discovery_is_ticker = bool(
        discovery and discovery.upper() in ticker_terms
    )
    observed_is_natural_hashtag = bool(
        observed_raw
        and X_TAG_RE.fullmatch(observed_raw)
        and any(char.isalpha() for char in observed_raw)
        and observed_raw not in GENERIC_X_DISCOVERY_TERMS
    )
    if (
        observed_is_natural_hashtag
        and observed_term
        and observed_term not in reader_anchor
    ):
        errors.append(
            f"{slug}: natural observed trend term '{observed_raw}' must appear "
            "in the title or summary, not only in metadata"
        )
    if (
        observed_is_natural_hashtag
        and discovery_norm
        and discovery_norm != observed_term
        and not discovery_is_ticker
    ):
        errors.append(
            f"{slug}: use the exact observed trend term '{observed_raw}' as the "
            "primary X discovery term when it is already a natural hashtag"
        )
    if (
        discovery_norm
        and discovery_norm != observed_term
        and discovery_norm not in public_anchor
    ):
        errors.append(
            f"{slug}: x_discovery_term must match the observed trend term or be "
            "anchored in title, summary, or source.topic"
        )
    if published >= TREND_SPECIFICITY_ROLLOUT_AT:
        real_detail = bridge.get("real_detail")
        unique_hook = bridge.get("unique_hook")
        if not isinstance(real_detail, str) or not 4 <= len(real_detail.strip()) <= 60:
            errors.append(f"{slug}: trend_bridge.real_detail must be 4-60 chars")
            real_detail = ""
        else:
            real_detail = real_detail.strip()
        if not isinstance(unique_hook, str) or not 12 <= len(unique_hook.strip()) <= 240:
            errors.append(f"{slug}: trend_bridge.unique_hook must be 12-240 chars")
            unique_hook = ""
        else:
            unique_hook = unique_hook.strip()

        detail_norm = _norm(real_detail)
        source_topic_norm = _norm(source_topic)
        paragraphs = article.get("paragraphs", [])
        first_paragraph = (
            paragraphs[0]
            if isinstance(paragraphs, list) and paragraphs and isinstance(paragraphs[0], str)
            else ""
        )
        reader_detail_anchor = _norm(
            " ".join(
                value
                for value in (title, summary, first_paragraph)
                if isinstance(value, str)
            )
        )
        secondary_anchor = _norm(
            " ".join(
                value
                for value in (title, summary, source_topic, first_paragraph)
                if isinstance(value, str)
            )
        )
        explicit_terms: list[str] = []
        if isinstance(x_tickers, list):
            explicit_terms.extend(
                item.strip().upper()
                for item in x_tickers
                if isinstance(item, str) and item.strip()
            )
        if isinstance(x_tags, list):
            explicit_terms.extend(
                item.strip()
                for item in x_tags
                if isinstance(item, str) and item.strip()
            )
        for secondary in explicit_terms[1:]:
            if _norm(secondary) not in secondary_anchor:
                errors.append(
                    f"{slug}: secondary X discovery term '{secondary}' must be "
                    "anchored in title, summary, source.topic, or the first paragraph"
                )
        if detail_norm and detail_norm == observed_term:
            errors.append(
                f"{slug}: real_detail must be more specific than the trend term itself"
            )
        if detail_norm and detail_norm not in source_topic_norm:
            errors.append(
                f"{slug}: real_detail must be quoted or closely preserved in source.topic"
            )
        if detail_norm and detail_norm not in reader_detail_anchor:
            errors.append(
                f"{slug}: real_detail must appear in title, summary, or the first paragraph"
            )
        if detail_norm and unique_hook and detail_norm not in _norm(unique_hook):
            errors.append(
                f"{slug}: unique_hook must explain why real_detail is essential to this satire"
            )

    if not isinstance(title, str) or len(title.strip()) < 16:
        errors.append(f"{slug}: trend title must be self-contained and at least 16 chars")
    if not isinstance(summary, str) or len(summary.strip()) < 36:
        errors.append(f"{slug}: trend summary must explain the contradiction in at least 36 chars")
    return errors


def _followup_ancestors(slug: str, by_slug: dict[str, dict]) -> set[str]:
    """Return existing parent-chain slugs without trusting malformed/cyclic links."""
    ancestors: set[str] = set()
    cursor = by_slug.get(slug)
    while isinstance(cursor, dict):
        parent = cursor.get("followup_of")
        if not isinstance(parent, str) or parent not in by_slug or parent in ancestors:
            break
        ancestors.add(parent)
        cursor = by_slug[parent]
    return ancestors


def validate_articles(articles: list[dict]) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors block malformed metadata/duplicates."""
    errors: list[str] = []
    warnings: list[str] = []
    by_slug = {
        article["slug"]: article
        for article in articles
        if isinstance(article, dict) and isinstance(article.get("slug"), str)
    }

    recent_trends: list[tuple[datetime, str, str]] = []
    recent_signatures: list[tuple[datetime, tuple[str, str, str], str]] = []
    recent_payoffs: list[tuple[datetime, str, str]] = []
    recent_interests: list[tuple[datetime, str, str]] = []
    recent_characters: list[tuple[datetime, str, str]] = []
    visual_by_day: dict[str, list[tuple[str, str]]] = {}

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

        tone = article.get("editorial_tone")
        if tone not in EDITORIAL_TONES:
            errors.append(
                f"{slug}: editorial_tone required after rollout; expected "
                + ", ".join(EDITORIAL_TONES)
            )

        interest = article.get("reader_interest")
        if published >= READER_INTEREST_ROLLOUT_AT and interest not in READER_INTERESTS:
            errors.append(
                f"{slug}: reader_interest required after rollout; expected "
                + ", ".join(READER_INTERESTS)
            )
        elif interest is not None and interest not in READER_INTERESTS:
            errors.append(
                f"{slug}: invalid reader_interest; expected " + ", ".join(READER_INTERESTS)
            )
        elif interest in READER_INTERESTS:
            recent_interests.append((published, interest, slug))

        character = article.get("recurring_character")
        if character is not None:
            if character not in RECURRING_CHARACTERS:
                errors.append(
                    f"{slug}: recurring_character must be one of "
                    + ", ".join(RECURRING_CHARACTERS)
                )
            else:
                recent_characters.append((published, character, slug))

        payoff = article.get("editorial_payoff")
        if published >= PAYOFF_ROLLOUT_AT:
            if payoff not in EDITORIAL_PAYOFFS:
                errors.append(
                    f"{slug}: editorial_payoff required after payoff rollout; expected "
                    + ", ".join(EDITORIAL_PAYOFFS)
                )
            else:
                recent_payoffs.append((published, payoff, slug))

        errors.extend(_validate_signature(article, slug, published))
        errors.extend(_validate_reader_clarity(article, slug, published))
        errors.extend(_validate_plausibility(article, slug))
        errors.extend(_validate_x_metadata(article, slug, published))
        errors.extend(_validate_visual_strategy(article, slug, published))
        strategy = article.get("visual_strategy", "cpu-photo")
        if strategy in VISUAL_STRATEGIES:
            day = published.astimezone(JST).date().isoformat()
            visual_by_day.setdefault(day, []).append((strategy, slug))
        signature = article.get("editorial_signature")
        if isinstance(signature, dict):
            normalized = tuple(
                _norm(signature.get(key))
                for key in ("target", "action", "outcome")
            )
            if all(normalized):
                recent_signatures.append((published, normalized, slug))

        if lane == "trend":
            errors.extend(_validate_trend(article, slug, published))
            errors.extend(_validate_trend_bridge(article, slug, published))
            trend = article.get("trend_observation")
            if isinstance(trend, dict):
                term = _norm(trend.get("term"))
                source_kind = str(trend.get("source_kind") or "")
                if term:
                    recent_trends.append((published, term, slug))
                if source_kind == "x_public_aggregator":
                    warnings.append(
                        f"{slug}: X trend is from a public aggregator; "
                        "do not describe it as an official X ranking"
                    )

    recent_trends.sort(key=lambda item: item[0])
    for i, (published, term, slug) in enumerate(recent_trends):
        ancestors = _followup_ancestors(slug, by_slug)
        for prior_published, prior_term, prior_slug in recent_trends[:i]:
            if published - prior_published > DUPLICATE_WINDOW:
                continue
            if term == prior_term and prior_slug not in ancestors:
                errors.append(
                    f"{slug}: duplicate trend term '{term}' already used by "
                    f"{prior_slug} within {DUPLICATE_WINDOW.days}; "
                    "reuse is allowed only inside the same follow-up chain"
                )
                break

    # Follow-ups may revisit the real-world topic, but the satire mechanism
    # itself must move forward. Exact target/action/outcome reuse stays blocked.
    recent_signatures.sort(key=lambda item: item[0])
    for i, (published, signature, slug) in enumerate(recent_signatures):
        for prior_published, prior_signature, prior_slug in recent_signatures[:i]:
            if published - prior_published > DUPLICATE_WINDOW:
                continue
            if signature == prior_signature:
                errors.append(
                    f"{slug}: editorial_signature duplicates {prior_slug} "
                    f"within {DUPLICATE_WINDOW.days} days"
                )
                break

    recent_interests.sort(key=lambda item: item[0])
    if len(recent_interests) >= 3:
        latest_three = recent_interests[-3:]
        if len({interest for _, interest, _ in latest_three}) == 1:
            interest = latest_three[-1][1]
            slugs = " / ".join(slug for _, _, slug in latest_three)
            warnings.append(
                f"latest 3 tracked stories share reader_interest={interest}: {slugs}; "
                "compare a different reader-interest domain for the next slot"
            )

    interests_by_day: dict[str, list[str]] = {}
    for published, interest, _ in recent_interests:
        day = published.astimezone(JST).date().isoformat()
        interests_by_day.setdefault(day, []).append(interest)
    for day, interests in interests_by_day.items():
        if len(interests) < sum(LANE_TARGETS.values()):
            continue
        life_count = sum(interest in LIFE_READER_INTERESTS for interest in interests)
        if life_count < 3:
            warnings.append(
                f"{day}: only {life_count} of {len(interests)} tracked stories are in "
                "life-facing reader interests; broaden candidate generation next slot"
            )

    recent_characters.sort(key=lambda item: item[0])
    characters_by_day: dict[str, list[str]] = {}
    for published, character, _ in recent_characters:
        day = published.astimezone(JST).date().isoformat()
        characters_by_day.setdefault(day, []).append(character)
    for day, characters in characters_by_day.items():
        if len(characters) > 1:
            warnings.append(
                f"{day}: {len(characters)} recurring-character stories are already present; "
                "keep character episodes as an accent, not a separate lane"
            )
    for i, (published, character, slug) in enumerate(recent_characters):
        window_start = published - timedelta(days=6)
        prior_same = [
            prior_slug
            for prior_published, prior_character, prior_slug in recent_characters[:i]
            if prior_character == character and prior_published >= window_start
        ]
        if len(prior_same) >= 2:
            warnings.append(
                f"{slug}: recurring_character={character} already appeared at least twice "
                "in the prior 7-day window; rotate or use a standalone cast"
            )
            break

    for day, items in sorted(visual_by_day.items()):
        if len(items) < 5:
            continue
        illustration_slugs = [slug for strategy, slug in items if strategy == "illustration"]
        if len(illustration_slugs) > len(items) / 2:
            warnings.append(
                f"{day}: illustration is {len(illustration_slugs)}/{len(items)} tracked stories; "
                "compare a photo-feasible scene next, without lowering article quality"
            )

    recent_payoffs.sort(key=lambda item: item[0])
    if len(recent_payoffs) >= 3:
        latest_three = recent_payoffs[-3:]
        if len({payoff for _, payoff, _ in latest_three}) == 1:
            payoff = latest_three[-1][1]
            slugs = " / ".join(slug for _, _, slug in latest_three)
            warnings.append(
                f"latest 3 tracked stories share editorial_payoff={payoff}: {slugs}; "
                "compare a different ending shape for the next slot"
            )

    return errors, warnings


def report_day(articles: list[dict], day_jst: str) -> str:
    counts = lane_counts(articles, day_jst)
    tracked = sum(counts.values())
    published = day_story_count(articles, day_jst)
    parts = [
        f"{lane}={counts.get(lane, 0)}/{target}"
        for lane, target in LANE_TARGETS.items()
    ]
    tones = tone_counts(articles, day_jst)
    tone_parts = [
        f"{tone}={tones.get(tone, 0)}"
        for tone in EDITORIAL_TONES
    ]
    payoffs = payoff_counts(articles, day_jst)
    payoff_parts = [
        f"{payoff}={payoffs.get(payoff, 0)}"
        for payoff in EDITORIAL_PAYOFFS
    ]
    interests = reader_interest_counts(articles, day_jst)
    interest_parts = [
        f"{interest}={interests.get(interest, 0)}"
        for interest in READER_INTERESTS
    ]
    visuals = visual_strategy_counts(articles, day_jst)
    visual_parts = [
        f"{strategy}={visuals.get(strategy, 0)}"
        for strategy in VISUAL_STRATEGIES
    ]
    if published >= sum(LANE_TARGETS.values()) and tracked < sum(LANE_TARGETS.values()):
        suffix = "quality-only (7-story baseline already reached; rollout tracking partial)"
    else:
        preferred = next_preferred_lane(counts)
        suffix = preferred or "quality-only (3/2/2 target filled)"
    return (
        f"{day_jst}: published={published}; tracked={tracked}; "
        + ", ".join(parts)
        + f"; next={suffix}; tones="
        + ", ".join(tone_parts)
        + f"; tone_next={tone_advice(articles, day_jst)}"
        + "; payoffs="
        + ", ".join(payoff_parts)
        + f"; payoff_next={payoff_advice(articles, day_jst)}"
        + "; interests="
        + ", ".join(interest_parts)
        + f"; interest_next={reader_interest_advice(articles, day_jst)}"
        + f"; character_next={character_advice(articles, day_jst)}"
        + "; visuals="
        + ", ".join(visual_parts)
        + f"; visual_next={visual_strategy_advice(articles, day_jst)}"
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
