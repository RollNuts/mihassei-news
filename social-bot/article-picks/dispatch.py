#!/usr/bin/env python3
"""Buffer Free article publishing selected from fresh stories and observed page views.

Reads the same JST seven-date window as on-site recommendations. This is the
only automatic X publishing lane; selected articles use Buffer shareNow.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT / "scripts"))
from publish import (choose_x_channel, list_existing, queue_one, ready_article_comic,
                     comic_publication_text, ComicUnavailable)  # noqa: E402
from article_comics import approved_comic_asset, valid_caption, COMIC_PATH
from article_thread import (prepare_article_thread, ArticleImageUnavailable, validate_thread,
                            thread_digest, digest, post_texts, thread_items, remote_thread_mismatch,
                            parent_text as thread_parent_text, image_sources, image_article_slugs, TRACKING_QUERY)
from social_text import article_hashtags, truncate_x_text, X_TEXT_LIMIT, x_weighted_length, x_share_lead  # noqa: E402
from editorial_copy import reader_copy  # noqa: E402
from generated_photos import share_page_url  # noqa: E402

SAFE_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
# Match canonical articles regardless of query parameters, extra prose or
# UTM links. Do not touch unknown external/shortened/manual-X URLs.
ARTICLE_URL = re.compile(
    r"https?://(?:www\.)?veripsa\.com/articles/([a-z0-9]+(?:-[a-z0-9]+)*)/?"
)
PUBLIC_POST_URL = re.compile(r"https://x\.com/(?i:Veripsa)/status/[1-9][0-9]*\Z")
TEXT_PREFIX = "【未発生新聞】"
BRAND_HASHTAG = "#未発生新聞"
METRIC_FIELDS = ("last7DaysPageViews", "todayPageViews")
METRICS_URL = "https://veripsa.com/api/engagement"
JST = timezone(timedelta(hours=9))
MAX_SAFE_INTEGER = 2**53 - 1
PROMOTION_SLOT_HOURS = (9, 11, 13, 15, 17, 19, 21)
PROMOTION_SLOT_MINUTE = 5
RECOVERABLE_BUFFER_STATUSES = ("scheduled", "sent", "draft", "error", "needs_approval", "sending")
CRISIS_PROMO_PATTERN = re.compile(
    r"地震|津波|台風|豪雨|洪水|土砂災害|山火事|戦争|武力衝突|空爆|侵攻|砲撃|"
    r"ミサイル攻撃|テロ|大量死傷|死者|死亡者|負傷者|被災者|避難者|人質"
)


def trigger_allows_network(event_name: str, now: datetime) -> bool:
    """Allow cron/manual runs anytime; bound article-push fallback to daytime.

    Site publication pushes at 07:30 and 23:30 JST must not touch Buffer.
    The seven logical publication slots are 09:05–21:05 JST. Article pushes
    may act as a fallback when GitHub's scheduled :05 check is delayed or absent.
    A delayed run still belongs to its logical slot instead of consuming the next one.
    """
    if now.tzinfo is None:
        raise ValueError("Current time must have timezone")
    if event_name != "push":
        return True
    hour = now.astimezone(JST).hour
    return 9 <= hour <= 21


def push_has_new_article(before_sha: str, articles: list[dict], now: datetime) -> bool:
    """Fail closed unless this main push introduced a recently published slug.

    A code/config-only update, an edit of an existing article, and an
    unavailable Git parent are not new publication events. Scheduled checks
    still run normally, with their own dedupe and quota protections.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", before_sha):
        return False
    try:
        previous_text = subprocess.run(
            ["git", "show", f"{before_sha}:content/articles.json"],
            cwd=ROOT, capture_output=True, text=True, check=True, timeout=10,
        ).stdout
        previous = json.loads(previous_text)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    if not isinstance(previous, list):
        return False
    previous_slugs = {
        item.get("slug") for item in previous if isinstance(item, dict)
    }
    for item in articles:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not SAFE_SLUG.fullmatch(slug):
            continue
        if slug in previous_slugs:
            continue
        age = article_age(item, now)
        if age is not None and timedelta(0) <= age <= timedelta(hours=24):
            return True
    return False

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
    if config["minimum_interval_hours"] != 2:
        raise ValueError("Article promotion logical slots must remain two hours apart")
    if config["fresh_minimum_age_hours"] >= config["fresh_maximum_age_hours"]:
        raise ValueError("Fresh article window is inverted")
    if config["fresh_maximum_age_hours"] > config["minimum_article_age_hours"] * 2:
        raise ValueError("Fresh article window overlaps too broadly with measured-PV lane")


def promotion_records(state):
    for kind in ("queued", "comic_posts"):
        for slug, entry in state.get(kind, {}).items():
            yield kind, slug, entry


def validate_state(state: dict) -> None:
    if state.get("schema_version") != 1 or not isinstance(state.get("queued"), dict):
        raise ValueError("Unsafe article post ledger")
    if not isinstance(state.get("comic_posts", {}), dict):
        raise ValueError("Unsafe comic post ledger")
    for kind, slug, entry in promotion_records(state):
        if not isinstance(slug, str) or not SAFE_SLUG.fullmatch(slug) or not isinstance(entry, dict):
            raise ValueError("Unsafe article ID in permanent post ledger")
        thread = entry.get("article_thread")
        if thread is not None:
            validate_thread(thread, slug)
            if (entry.get("thread_sha256") != thread_digest(thread)
                    or entry.get("text_sha256") != digest(thread["parent_text"])):
                raise ValueError("Invalid saved article thread fingerprint")
        pending = (kind == "comic_posts" or thread is not None) and entry.get("submission_pending") is True
        if (not isinstance(entry.get("buffer_id"), str)
                or (not entry["buffer_id"] and not pending)):
            raise ValueError("Incomplete Buffer ID in permanent post ledger")
        if kind == "comic_posts" and (not isinstance(entry.get("comic_attachment"), dict)
                                      or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("text_sha256", "")))):
            raise ValueError("Incomplete comic work evidence")
        if kind == "comic_posts":
            asset = entry["comic_attachment"]
            if (any(not re.fullmatch(r"[0-9a-f]{64}", str(asset.get(key, "")))
                    for key in ("article_sha256", "image_sha256", "caption_sha256"))
                    or not valid_caption(asset.get("caption"), asset.get("caption_sha256"))
                    or not COMIC_PATH.fullmatch(str(asset.get("path", "")))
                    or (thread is None and hashlib.sha256(comic_publication_text({"slug": slug}, asset).encode("utf-8")).hexdigest()
                        != entry["text_sha256"])):
                raise ValueError("Invalid comic work binding")
        stamp = entry.get("queued_at_utc")
        if not isinstance(stamp, str):
            raise ValueError("Missing UTC timestamp in permanent post ledger")
        dt = datetime.fromisoformat(stamp)
        if dt.tzinfo is None:
            raise ValueError("Timezone missing in permanent post ledger")
        slot_stamp = entry.get("slot_jst")
        if slot_stamp is not None:
            if not isinstance(slot_stamp, str):
                raise ValueError("Invalid logical X slot in permanent post ledger")
            slot = datetime.fromisoformat(slot_stamp)
            if (slot.tzinfo is None or slot.astimezone(JST).utcoffset() != JST.utcoffset(None)
                    or slot.astimezone(JST).minute != PROMOTION_SLOT_MINUTE
                    or slot.astimezone(JST).hour not in PROMOTION_SLOT_HOURS):
                raise ValueError("Invalid logical X slot in permanent post ledger")
    public = state.get("public_posts", {})
    if not isinstance(public, dict):
        raise ValueError("Invalid independently observed public X posts")
    for slug, entry in public.items():
        if (not isinstance(slug, str) or not SAFE_SLUG.fullmatch(slug)
                or not isinstance(entry, dict)
                or not isinstance(entry.get("url"), str)
                or not PUBLIC_POST_URL.fullmatch(entry["url"])):
            raise ValueError("Invalid public Veripsa X post")
        posted = provider_instant(entry.get("posted_at_utc"))
        observed = provider_instant(entry.get("observed_at_utc"))
        if (posted is None or observed is None
                or posted.utcoffset() != timedelta(0) or observed.utcoffset() != timedelta(0)
                or observed < posted):
            raise ValueError("Invalid UTC public X publication/observation timestamps")


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


def promotion_slot(now: datetime) -> datetime | None:
    """Return the latest logical JST article slot reached on this date.

    GitHub Actions may begin late. A 09:05 run that starts at 09:36 still uses
    the 09:05 slot, while an 11:05 run that starts at 11:20 uses the 11:05
    slot. This prevents scheduler delay from creating a rolling cooldown.
    """
    if now.tzinfo is None:
        raise ValueError("Current time must have timezone")
    local = now.astimezone(JST)
    for hour in reversed(PROMOTION_SLOT_HOURS):
        slot = local.replace(
            hour=hour, minute=PROMOTION_SLOT_MINUTE, second=0, microsecond=0
        )
        if local >= slot:
            return slot
    return None


def entry_promotion_slot(entry: dict) -> datetime | None:
    """Read a saved logical slot, or infer one for pre-migration receipts."""
    explicit = entry.get("slot_jst")
    if isinstance(explicit, str):
        try:
            slot = datetime.fromisoformat(explicit)
        except ValueError:
            return None
        return slot.astimezone(JST) if slot.tzinfo else None
    if entry.get("source") in ("recovered_from_buffer", "comic_recovery_unverified"):
        # A recovered receipt without provider timing is valid permanent dedupe
        # evidence, but its recovery time is not evidence of a promotion slot.
        return None
    try:
        queued = datetime.fromisoformat(entry["queued_at_utc"])
    except (KeyError, TypeError, ValueError):
        return None
    if queued.tzinfo is None:
        return None
    slot = promotion_slot(queued)
    if slot is not None:
        return slot
    local = queued.astimezone(JST)
    # Before this migration, article-push fallback was allowed from 09:00.
    # Preserve 09:00-09:04 receipts as the current day's 09:05 logical slot.
    if local.hour == PROMOTION_SLOT_HOURS[0]:
        return local.replace(
            hour=PROMOTION_SLOT_HOURS[0], minute=PROMOTION_SLOT_MINUTE,
            second=0, microsecond=0,
        )
    # Earlier after-midnight receipts came from a heavily delayed previous-day
    # run. Attribute them to the previous day's final 21:05 slot so they do not
    # steal today's quota.
    previous = local - timedelta(days=1)
    return previous.replace(
        hour=PROMOTION_SLOT_HOURS[-1], minute=PROMOTION_SLOT_MINUTE,
        second=0, microsecond=0,
    )


def slot_already_used(state: dict, now: datetime) -> bool:
    """Allow one automatic article per logical slot, regardless of run delay."""
    current = promotion_slot(now)
    if current is None:
        return False
    for _kind, _slug, entry in promotion_records(state):
        if entry_promotion_slot(entry) == current:
            return True
    return False


def buffer_observed_time(item: dict) -> datetime | None:
    """Best provider timestamp for attributing a recovered article to a past slot."""
    candidates = (
        item.get("sentAt") if item.get("status") == "sent" else item.get("dueAt"),
        item.get("dueAt"),
        item.get("sentAt"),
        item.get("createdAt"),
    )
    for value in candidates:
        if not isinstance(value, str) or not value:
            continue
        try:
            observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if observed.tzinfo is not None:
            return observed
    return None


def provider_instant(value: object) -> datetime | None:
    """Parse a provider timestamp without treating absent or invalid data as zero."""
    if not isinstance(value, str) or not value:
        return None
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return instant if instant.tzinfo is not None else None


def _public_post_spacing_hold(state: dict, now: datetime, minimum: timedelta) -> str | None:
    """Independent X observations protect spacing without implying a Buffer receipt."""
    for entry in state.get("public_posts", {}).values():
        sent = provider_instant(entry.get("posted_at_utc"))
        if sent is None:
            return "unverified_public_x_sent_time"
        if now - sent < minimum:
            return "recent_verified_public_x_sent"
    return None


def buffer_delivery_spacing_hold(existing: list[dict], state: dict,
                                 now: datetime, hours: int) -> str | None:
    """Guard the real X-channel send window, not just two-hour logical slots.

    Buffer's sentAt is stronger evidence than workflow schedule, queue time or
    logical slot. Include other posts on this *same* X channel for spacing only,
    not for the article lane's seven-slot quota. Unresolved send/schedule timing
    fails closed rather than assuming there were zero recent sends. Reuse the
    already-fetched Buffer rows and immutable receipts; make no extra API call.
    """
    if now.tzinfo is None or type(hours) is not int or hours < 2:
        raise ValueError("Invalid real-send spacing window")
    minimum = timedelta(hours=hours)
    public_hold = _public_post_spacing_hold(state, now, minimum)
    if public_hold is not None:
        return public_hold
    accepted_sent_by_id = {}
    for kind, _slug, entry in promotion_records(state):
        if entry.get("submission_pending") is True:
            return "unverified_comic_submission" if kind == "comic_posts" else "unverified_article_submission"
        sent = provider_instant(entry.get("buffer_sent_at_at_accept"))
        if sent is not None:
            accepted_sent_by_id[entry["buffer_id"]] = sent
            if now - sent < minimum:
                return "recent_verified_article_sent"
        # An accepted shareNow request may still be sending after the receipt
        # was saved. Never treat that recent attempt as proof of no activity.
        if entry.get("submission_mode") == "shareNow":
            accepted = provider_instant(entry.get("queued_at_utc"))
            if accepted is None or now - accepted < minimum:
                return "recent_or_unverified_article_submission"

    for row in existing:
        if not isinstance(row, dict):
            return "unverified_buffer_post"
        status = row.get("status")
        if status == "sent":
            sent = (provider_instant(row.get("sentAt"))
                    or accepted_sent_by_id.get(row.get("id")))
            if sent is None:
                return "unverified_buffer_sent_time"
            if now - sent < minimum:
                return "recent_buffer_sent"
        elif status == "sending":
            return "buffer_send_in_progress"
        elif status == "scheduled":
            due = provider_instant(row.get("dueAt"))
            if due is None:
                return "unverified_buffer_schedule"
            # Include overdue scheduled posts, which may be released late,
            # and posts due less than two hours from now.
            if due - now < minimum:
                return "buffer_schedule_near_or_overdue"
    return None


def initial_provider_error_evidence(post: dict) -> dict:
    """Keep only bounded, user-facing Buffer publishing error details."""
    error = post.get("error")
    if not isinstance(error, dict):
        return {}
    result = {}
    message = error.get("message")
    if isinstance(message, str) and message.strip():
        result["buffer_error_message_at_accept"] = message.strip()[:500]
    support_url = error.get("supportUrl")
    if isinstance(support_url, str) and support_url.startswith("https://"):
        result["buffer_error_support_url_at_accept"] = support_url[:500]
    return result


def recover_from_buffer(state: dict, existing: list[dict], now: datetime,
                        known_slugs: set[str], comic_texts_to_ignore=(), parent_slugs=None) -> list[str]:
    """Reserve remote article URLs without stealing the current logical slot.

    Recovery can happen long after Buffer accepted or sent the post. Attribute
    the recovered entry to provider timing when available. If the provider does
    not expose a usable timestamp, preserve dedupe evidence but leave slot_jst
    absent so a recovery alone cannot consume the current article slot.
    """
    validate_state(state)
    recovered = []
    comic_ids = {entry.get("buffer_id") for entry in state.get("comic_posts", {}).values()}
    comic_texts = {entry.get("text_sha256") for entry in state.get("comic_posts", {}).values()}
    comic_texts.update(comic_texts_to_ignore)
    for item in existing:
        if item.get("status") not in RECOVERABLE_BUFFER_STATUSES:
            continue
        post_id = item.get("id")
        if not isinstance(post_id, str) or not post_id:
            continue
        if (post_id in comic_ids or hashlib.sha256((item.get("text", "") or "").encode("utf-8")).hexdigest()
                in comic_texts):
            continue
        url_slugs = article_slug_in_text("\n".join(post_texts(item)))
        observed_slugs = url_slugs | image_article_slugs(item)
        if parent_slugs:
            for text in post_texts(item):
                observed_slugs.update(parent_slugs.get(digest(text), set()))
        for slug in observed_slugs & known_slugs:
            if slug not in state["queued"]:
                recovered_entry = {
                    "buffer_id": post_id,
                    "queued_at_utc": now.astimezone(timezone.utc).isoformat(),
                    "source": "recovered_from_buffer",
                    "buffer_status_when_recovered": item.get("status"),
                    "text_sha256": hashlib.sha256(
                        (item.get("text", "") or "").encode("utf-8")
                    ).hexdigest(),
                }
                observed = buffer_observed_time(item)
                if observed is not None:
                    slot = promotion_slot(observed)
                    if slot is not None:
                        recovered_entry["slot_jst"] = slot.isoformat()
                    else:
                        local = observed.astimezone(JST)
                        if local.hour == PROMOTION_SLOT_HOURS[0]:
                            recovered_entry["slot_jst"] = local.replace(
                                hour=PROMOTION_SLOT_HOURS[0],
                                minute=PROMOTION_SLOT_MINUTE,
                                second=0,
                                microsecond=0,
                            ).isoformat()
                recovered_entry["recovered_provider_time_utc"] = (
                    observed.astimezone(timezone.utc).isoformat()
                    if observed is not None else None
                )
                if slug not in url_slugs:
                    recovered_entry["recovery_binding"] = "observed_parent_or_fixed_image_source"
                    recovered_entry["thread_delivery"] = "unverified"
                if thread_items(item):
                    # No saved intent means no claim about original image bytes.
                    # Preserve provider text/asset observations on this same ID.
                    recovered_entry["buffer_thread_at_recovery"] = thread_items(item)
                    recovered_entry["thread_delivery"] = "unverified"
                state["queued"][slug] = recovered_entry
                recovered.append(slug)
    return recovered


def publication_text(article: dict) -> str:
    """No misleading popularity ranking, analytics numbers or AI promo labels."""
    slug, title = article["slug"], article["title"]
    if not SAFE_SLUG.fullmatch(slug):
        raise ValueError("Unsafe article link")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Empty article title")
    tidy = re.sub(r"\s+", " ", reader_copy(title.strip()))
    if len(tidy) > 68:
        tidy = tidy[:67].rstrip() + "…"
    url = "https://veripsa.com/articles/" + slug + "/" + TRACKING_QUERY
    url = share_page_url(article, url, root=ROOT)
    hashtags = " ".join([BRAND_HASHTAG, *article_hashtags(article)])
    prefix = "【続報｜未発生新聞】" if article.get("followup_of") else TEXT_PREFIX
    suffix = ("\n\n" + hashtags if hashtags else "") + "\n\n" + url

    lead = x_share_lead(article, suffix)
    if lead is not None:
        rendered = lead
    else:
        rendered = prefix + "\n" + tidy
        summary = article.get("summary")
        if isinstance(summary, str):
            summary = re.sub(r"\s+", " ", reader_copy(summary.strip()))
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


# Block context-only teasers before either fresh or measured-PV promotion.
# This is a conservative structural backstop, not a machine judgement of humor.
# Editors can explicitly skip an article without rewriting it or its public title.
CONTEXT_DEPENDENT_OPENING = re.compile(
    r"^(?:【続報】\s*)?(?:この件|あの件|例の(?:件|発表|ニュース)|"
    r"これを受け|それを受け|その結果|前回の話|この話の続き)"
)


def passes_x_first_read_gate(article: dict) -> bool:
    """Require a self-contained headline and short summary for X discovery.

    Do not use tags, earlier posts, a longer X explanation or PV to rescue
    an article that cannot stand on its own. Semantic/funny judgement remains
    with the editorial first-read review in docs/EDITORIAL_LOOP.md.
    """
    if article.get("x_promotion_skip") is True:
        return False
    title, summary = article.get("title"), article.get("summary")
    if not isinstance(title, str) or not isinstance(summary, str):
        return False
    title, summary = title.strip(), summary.strip()
    if len(title) < 16 or len(summary) < 36:
        return False
    if CONTEXT_DEPENDENT_OPENING.match(title) or CONTEXT_DEPENDENT_OPENING.match(summary):
        return False
    return True


def eligible_articles(articles: list[dict], now: datetime, config: dict) -> list[dict]:
    """Measured-PV lane uses real publication time, never fictional dates."""
    if now.tzinfo is None:
        raise ValueError("Current time must have timezone")
    eligible = []
    for article in articles:
        slug = article.get("slug")
        if (not isinstance(slug, str) or not SAFE_SLUG.fullmatch(slug)
                or is_crisis_article(article) or not passes_x_first_read_gate(article)):
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
        if (not isinstance(slug, str) or not SAFE_SLUG.fullmatch(slug)
                or is_crisis_article(article) or not passes_x_first_read_gate(article)):
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
        if (slug in state["queued"] or slug in state.get("comic_posts", {})
                or slug in state.get("public_posts", {}) or slug in blocked):
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
        if (slug in state["queued"] or slug in state.get("comic_posts", {})
                or slug in state.get("public_posts", {}) or slug in blocked):
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
    for _kind, slug, entry in promotion_records(state):
        slot = entry_promotion_slot(entry)
        if slot is None or slot.astimezone(JST).date() != day:
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
    """Count logical promotion slots on the JST date, not delayed receipt dates."""
    day = now.astimezone(JST).date()
    count = 0
    for _kind, _slug, entry in promotion_records(state):
        slot = entry_promotion_slot(entry)
        if slot is not None and slot.astimezone(JST).date() == day:
            count += 1
    return count


def decide(articles: list[dict], counts: dict, state: dict, existing: list[dict],
           config: dict, now: datetime
           ) -> tuple[dict | None, dict | None, int]:
    """Evaluate only after recovering remote article URLs and saved Buffer IDs."""
    known = {a["slug"] for a in articles if isinstance(a.get("slug"), str)}
    configured = config.get("comic_attachments", {})
    configured = configured if isinstance(configured, dict) else {}
    comic_texts = {
        hashlib.sha256(comic_publication_text({"slug": slug}, review).encode("utf-8")).hexdigest()
        for slug, review in configured.items()
        if isinstance(slug, str) and SAFE_SLUG.fullmatch(slug)
        and isinstance(review, dict) and valid_caption(review.get("caption"), review.get("caption_sha256"))
    }
    parent_slugs = {}
    for article in articles:
        try:
            review = configured.get(article["slug"])
            if isinstance(review, dict) and valid_caption(review.get("caption"), review.get("caption_sha256")):
                comic_texts.add(digest(thread_parent_text(article, review["caption"])))
            elif article["slug"] not in configured:
                parent_slugs.setdefault(digest(thread_parent_text(article)), set()).add(article["slug"])
        except (ValueError, TypeError, KeyError):
            continue
    recover_from_buffer(state, existing, now, known, comic_texts, parent_slugs)
    queued = [p for p in existing if p.get("status") == "scheduled"]
    remote_slugs = set().union(
        *(article_slug_in_text("\n".join(post_texts(p))) for p in existing
          if p.get("status") in RECOVERABLE_BUFFER_STATUSES)
    ) if existing else set()
    remote_slugs.update(configured)
    today_count = promotions_today(state, now)
    current_slot = promotion_slot(now)
    if (current_slot is None
            or today_count >= config["max_per_jst_day"]
            or slot_already_used(state, now)
            or buffer_delivery_spacing_hold(
                existing, state, now, config["minimum_interval_hours"]
            ) is not None):
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


def comic_candidates(articles, state, config, now):
    """One owner-reviewed work per source; only this work may bypass URL age/dedupe."""
    reviews = config.get("comic_attachments", {})
    if not isinstance(reviews, dict):
        return []
    used_images = {entry["comic_attachment"].get("image_sha256")
                   for _kind, _slug, entry in promotion_records(state)
                   if isinstance(entry.get("comic_attachment"), dict)}
    result = []
    for article in articles:
        slug = article.get("slug")
        if (slug not in reviews or slug in state.get("comic_posts", {})
                or isinstance(state["queued"].get(slug, {}).get("comic_attachment"), dict)
                or is_crisis_article(article) or article.get("x_promotion_skip") is True):
            continue
        age = article_age(article, now)
        if age is None or age < timedelta(0):
            continue
        asset = approved_comic_asset(article, reviews[slug], ROOT, now)
        if asset is not None and asset["image_sha256"] not in used_images:
            result.append((article, reviews[slug], asset))
    return result


def add_provider_receipt(entry, post):
    fields = {"status": "buffer_status_at_accept", "createdAt": "buffer_created_at_at_accept",
              "dueAt": "buffer_due_at_at_accept", "sentAt": "buffer_sent_at_at_accept",
              "externalLink": "buffer_external_link_at_accept", "sharedNow": "buffer_shared_now_at_accept",
              "shareMode": "buffer_share_mode_at_accept"}
    for key, target in fields.items():
        if post.get(key) is not None:
            entry[target] = post[key]
    entry.update(initial_provider_error_evidence(post))
    if entry.get("article_thread") is not None:
        entry["buffer_thread_match_at_accept"] = remote_thread_mismatch(post, entry["article_thread"]) or "matched"
        entry["thread_delivery"] = "unverified"  # No individual X reply delivery IDs in Buffer's schema.


def recover_article_threads(state, existing, now):
    """A durable exact intent may recover one receipt; never resend a partial thread."""
    known_ids = {entry.get("buffer_id") for _kind, _slug, entry in promotion_records(state) if entry.get("buffer_id")}
    for _kind, _slug, entry in promotion_records(state):
        thread = entry.get("article_thread")
        if thread is None or not entry.get("submission_pending"):
            continue
        attempted = provider_instant(entry.get("queued_at_utc"))
        matches = [post for post in existing
                   if isinstance(post, dict) and post.get("status") in RECOVERABLE_BUFFER_STATUSES
                   and isinstance(post.get("id"), str) and post["id"] and post["id"] not in known_ids
                   and remote_thread_mismatch(post, thread) is None
                   and attempted is not None and provider_instant(post.get("createdAt")) is not None
                   and attempted <= provider_instant(post["createdAt"]) <= now]
        if len(matches) == 1:
            entry.update(buffer_id=matches[0]["id"], submission_pending=False)
            add_provider_receipt(entry, matches[0])
            known_ids.add(matches[0]["id"])


def recover_comic_posts(state, existing, now, candidates=()):
    """Never turn an old matching URL into evidence for a new comic work.

    A saved pre-send intent plus one exact, later-created provider text can
    recover an acceptance. Without that intent, matching text is ambiguous and
    remains pending for review; it never authorizes a retry or claims delivery.
    """
    normal_ids = {entry.get("buffer_id") for entry in state["queued"].values()}
    for _slug, entry in state.get("comic_posts", {}).items():
        if (entry.get("article_thread") is not None or not entry.get("submission_pending")
                or entry.get("source") == "comic_recovery_unverified"):
            continue
        attempted = provider_instant(entry.get("queued_at_utc"))
        matches = [post for post in existing
                   if isinstance(post, dict) and post.get("status") in RECOVERABLE_BUFFER_STATUSES
                   and isinstance(post.get("id"), str) and post["id"] and post["id"] not in normal_ids
                   and isinstance(post.get("text"), str)
                   and hashlib.sha256(post["text"].encode("utf-8")).hexdigest() == entry["text_sha256"]
                   and provider_instant(post.get("createdAt")) is not None
                   and attempted is not None and attempted <= provider_instant(post["createdAt"]) <= now]
        if len(matches) == 1:
            entry.update(buffer_id=matches[0]["id"], submission_pending=False)
            add_provider_receipt(entry, matches[0])
    for article, _review, asset in candidates:
        slug = article["slug"]
        if slug in state.get("comic_posts", {}):
            continue
        text = comic_publication_text(article, asset)
        parent = thread_parent_text(article, asset["caption"])
        # A provider thread can have been recovered into ordinary queued after
        # Git lost the comic intent. Its fixed image source / deterministic
        # parent still forbids resending the same comic, including partials.
        observations = [post for post in existing
                        if isinstance(post, dict) and post.get("status") in RECOVERABLE_BUFFER_STATUSES
                        and isinstance(post.get("id"), str) and post["id"]
                        and ((post["id"] not in normal_ids and post.get("text") == text)
                             or parent in post_texts(post)
                             or any(re.fullmatch(r"https://raw\.githubusercontent\.com/RollNuts/mihassei-news/[0-9a-f]{40}/" + re.escape(asset["path"]), source)
                                    for source in image_sources(post)))]
        if observations:
            state.setdefault("comic_posts", {})[slug] = {
                "buffer_id": "", "submission_pending": True,
                "queued_at_utc": now.isoformat(), "source": "comic_recovery_unverified",
                "observed_buffer_ids": sorted({post["id"] for post in observations}),
                "expected_parent_sha256": digest(parent),
                "observed_parent_sha256_by_buffer_id": {post["id"]: digest(post["text"]) for post in observations
                                                        if isinstance(post.get("text"), str)},
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "comic_attachment": {key: value for key, value in asset.items() if key != "text"},
            }


def delivery_retry_time(path: Path):
    if not path.is_file():
        return None
    delivery = json.loads(path.read_text(encoding="utf-8"))
    return provider_instant(delivery.get("provider_retry_at_utc"))


def connected_channel(token: str, config: dict, state: dict, now: datetime):
    """Cache verified connection IDs for one day, never the secret or post list."""
    configured = os.environ.get("BUFFER_CHANNEL_ID", "").strip()
    if configured and configured != config["channel_id"]:
        raise RuntimeError("Configured X channel differs from pinned Veripsa account")
    cached = state.get("provider_connection", {})
    checked = provider_instant(cached.get("verified_at_utc"))
    if (cached.get("channel_id") == config["channel_id"]
            and isinstance(cached.get("organization_id"), str) and cached["organization_id"]
            and checked is not None and timedelta(0) <= now - checked < timedelta(days=1)):
        return cached["organization_id"], cached["channel_id"]
    org_id, channel_id = choose_x_channel(token)
    if channel_id != config["channel_id"]:
        raise RuntimeError("Connected X channel differs from pinned Veripsa account")
    state["provider_connection"] = {
        "organization_id": org_id, "channel_id": channel_id,
        "verified_at_utc": now.astimezone(timezone.utc).isoformat(),
    }
    save_state(state)
    return org_id, channel_id


def report_outcome(outcome: str, *, retry_at: datetime | None = None,
                   slug: str | None = None, provider_status: object = None) -> int:
    """Summarize this check without exposing credentials or implying X delivery."""
    messages = {
        "off": "OFF — 未送信・provider通信なし。",
        "outside": "時間外 — 未送信・provider通信なし。",
        "slot": "枠上限・重複 — 未送信・provider通信なし。",
        "spacing_local": "間隔待ち — 未送信・provider通信なし。",
        "spacing": "間隔・送達状態の確認待ち — 未送信。provider照会後に送信を見送りました。",
        "cooldown": "プロバイダ待機 — 未送信・provider通信なし。",
        "no_new": "対象なし — このpushに新しい公開記事はなく、未送信・provider通信なし。",
        "no_candidates": "対象なし — 公開期間等の条件に合う記事がなく、未送信・provider通信なし。",
        "no_eligible": "対象なし — 重複防止・選定条件を満たす記事がなく、未送信。provider照会は実施しました。",
        "accepted": "Buffer受付 — 即時送信を依頼しました。X実公開は未確認です。",
        "error": "処理失敗 — 送達状態は未確認です。成功扱いせず、実行ログと保存済み台帳を確認してください。",
    }
    lines = ["### X記事紹介の実行結果", "", messages[outcome]]
    if outcome == "cooldown":
        if retry_at is None or retry_at.tzinfo is None:
            raise ValueError("Provider retry time must have timezone")
        lines += ["", "保存済みの待機期限："
                  + retry_at.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S JST")
                  + "（" + retry_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") + "）。",
                  "既存記事のX共有から手動投稿用の原稿を準備できます。この実行では手動投稿も行っていません。"]
    if outcome == "accepted":
        if isinstance(slug, str) and SAFE_SLUG.fullmatch(slug):
            lines += ["", "対象記事：`" + slug + "`。"]
        status = provider_status if isinstance(provider_status, str) and provider_status in RECOVERABLE_BUFFER_STATUSES else "未確認"
        lines += ["", "Buffer応答の状態：`" + status + "`。provider上の値であり、X実公開の独立確認ではありません。"]
    summary = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if summary:
        with Path(summary).open("a", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n\n")
    return 0


def main() -> int:
    config = read_json(HERE / "config.json")
    validate_config(config)
    if not config["enabled"]:
        print("Article X promotions OFF; no automatic X post is sent.")
        return report_outcome("off")

    state = read_json(HERE / "state.json")
    validate_state(state)
    now = datetime.now(timezone.utc)
    trigger_event = os.environ.get("X_TRIGGER_EVENT", "").strip()
    if not trigger_allows_network(trigger_event, now):
        print("Article X push trigger outside 09:00-21:59 JST; no Buffer read/write.")
        return report_outcome("outside")
    # A known local hold cannot be made publishable by reading Buffer. Keep
    # provider reconciliation for the next open slot, before any possible POST.
    if promotion_slot(now) is None or now.astimezone(JST).hour > 21:
        print("Article X slot outside 09:05-21:59 JST; no Buffer read/write.")
        return report_outcome("outside")
    if (slot_already_used(state, now)
            or promotions_today(state, now) >= config["max_per_jst_day"]):
        print("Article X slot closed, already used, or daily limit reached; no Buffer read/write.")
        return report_outcome("slot")
    for _kind, _slug, receipt in promotion_records(state):
        sent = provider_instant(receipt.get("buffer_sent_at_at_accept"))
        if sent is not None and now - sent < timedelta(hours=config["minimum_interval_hours"]):
            print("Article X recorded send is too recent; no Buffer read/write.")
            return report_outcome("spacing_local")
    if _public_post_spacing_hold(state, now, timedelta(hours=config["minimum_interval_hours"])) is not None:
        print("Independently observed X send is too recent; no Buffer read/write.")
        return report_outcome("spacing_local")
    retry_at = delivery_retry_time(HERE / "delivery.json")
    if retry_at is not None and now < retry_at:
        # Already-recorded provider backoff is a safe no-op, not a new failed send.
        # Keep the original 429 as an error on first observation, but do not turn
        # every scheduled/push check during cooldown into an Actions failure.
        print("Buffer cooldown until", retry_at.isoformat(), "; no provider read/write.")
        return report_outcome("cooldown", retry_at=retry_at)
    articles = json.loads((ROOT / "content" / "articles.json").read_text(encoding="utf-8"))
    if not isinstance(articles, list):
        raise ValueError("Invalid article archive")
    if trigger_event == "push" and not push_has_new_article(
        os.environ.get("X_PUSH_BEFORE", ""), articles, now
    ):
        print("No new publication in this push; no Buffer read/write.")
        return report_outcome("no_new")

    # Fresh articles can be promoted before PV has had time to accumulate.
    # Mature candidates still use the site's actual PV period, never legacy
    # browser counts, cumulative reactions or estimated snapshot changes.
    configured_comics = config.get("comic_attachments", {})
    configured_comics = configured_comics if isinstance(configured_comics, dict) else {}
    candidates = [a for a in eligible_articles(articles, now, config) if a["slug"] not in configured_comics]
    fresh_candidates = [a for a in eligible_fresh_articles(articles, now, config) if a["slug"] not in configured_comics]
    comics = comic_candidates(articles, state, config, now)
    if (not candidates and not fresh_candidates and not comics
            and not any(entry.get("submission_pending") for _kind, _slug, entry in promotion_records(state))):
        print("No fresh or measured-interest articles; no Buffer write.")
        return report_outcome("no_candidates")
    token = os.environ.get("BUFFER_API_KEY", "").strip()
    if not token:
        raise RuntimeError("Buffer secret unavailable; cannot promote articles")
    org_id, channel_id = connected_channel(token, config, state, now)
    existing = list_existing(
        token, org_id, channel_id,
        include_due_at=True, include_unsettled=True,
    )
    recover_article_threads(state, existing, now)
    recover_comic_posts(state, existing, now)
    # First reconcile Buffer and evaluate fresh posts without depending on
    # mature-article PV. A stale/unavailable PV API must not block a valid
    # 1–18-hour article. Dedupe, caps and one-per-logical-slot gates still apply.
    eligible, evidence, scheduled = decide(articles, {}, state, existing,
                                            config, now)
    spacing_hold = buffer_delivery_spacing_hold(existing, state, now, config["minimum_interval_hours"])
    if spacing_hold is not None:
        save_state(state)
        print("Article X real-send spacing hold; no Buffer POST:", spacing_hold)
        return report_outcome("spacing")
    if slot_already_used(state, now) or promotions_today(state, now) >= config["max_per_jst_day"]:
        save_state(state)
        return report_outcome("slot")
    selected_comic = None
    for article, review, _asset in comics:
        if article["slug"] in state.get("comic_posts", {}):
            continue
        ready = ready_article_comic(article, review, ROOT)
        if ready is not None:
            recover_comic_posts(state, existing, now, [(article, review, ready)])
            if article["slug"] in state.get("comic_posts", {}):
                save_state(state)
                return report_outcome("spacing")
            selected_comic, eligible, evidence = (article, review, ready), article, None
            break
    metrics, window = ({}, None)
    if eligible is None and candidates:
        # Save recovered remote receipts even if measured-PV lookup fails.
        save_state(state)
        metrics, window = read_page_view_metrics([a["slug"] for a in candidates], now)
        eligible, evidence, scheduled = decide(articles, metrics, state, existing,
                                                config, now)
    # Persist remote recovery before any further POST. The workflow also
    # commits both ledgers even after a partial API/network failure.
    save_state(state)
    if eligible is None:
        print("No eligible article for this check; Buffer scheduled:", scheduled)
        return report_outcome("no_eligible")
    post = {"kind": "article", "id": "article-" + eligible["slug"], "article": eligible}
    if selected_comic:
        post.update(comic_approval=selected_comic[1], comic_required=True)
    try:
        thread = prepare_article_thread(eligible, selected_comic[1] if selected_comic else None, root=ROOT)
    except ArticleImageUnavailable:
        print("Required parent image unavailable; no Buffer POST.")
        return report_outcome("no_eligible")
    post.update(article_thread=thread, text=thread["parent_text"])
    record_kind = "comic_posts" if selected_comic else "queued"
    intent = {
        "buffer_id": "", "submission_pending": True, "queued_at_utc": now.isoformat(),
        "slot_jst": promotion_slot(now).isoformat(), "submission_mode": "shareNow",
        "source": "approved_comic" if selected_comic else (
            "fresh_article" if evidence is None else "verified_article_page_views"),
        "text_sha256": digest(thread["parent_text"]), "article_thread": thread,
        "thread_sha256": thread_digest(thread), "thread_delivery": "unverified",
    }
    if selected_comic:
        intent["comic_attachment"] = {key: value for key, value in selected_comic[2].items() if key != "text"}
    state.setdefault(record_kind, {})[eligible["slug"]] = intent
    save_state(state)  # A failed durable intent save forbids any provider mutation.
    try:
        buffer_receipt = queue_one(token, channel_id, post, mode="shareNow", return_post=True)
    except (ArticleImageUnavailable, ComicUnavailable, RuntimeError) as exc:
        if isinstance(exc, (ArticleImageUnavailable, ComicUnavailable)) or str(exc).startswith("Buffer budget hold"):
            del state[record_kind][eligible["slug"]]
            save_state(state)
            if isinstance(exc, (ArticleImageUnavailable, ComicUnavailable)):
                return report_outcome("no_eligible")
        raise  # Unknown acceptance remains pending; never automatically retry.
    if not isinstance(buffer_receipt, dict) or not isinstance(buffer_receipt.get("id"), str) or not buffer_receipt["id"]:
        raise RuntimeError("Buffer accepted no auditable article receipt")
    if buffer_receipt.get("_article_thread", thread) != thread:
        raise RuntimeError("Article acceptance differs from its durable intent; pending review")
    comic = buffer_receipt.get("_article_comic")
    if selected_comic and not isinstance(comic, dict):
        raise RuntimeError("Comic acceptance lacks exact work evidence; pending review")
    buffer_id = buffer_receipt["id"]
    slot = promotion_slot(now)
    if slot is None:
        raise RuntimeError("No logical article promotion slot is active")
    entry = {
        **intent, "submission_pending": False,
        "buffer_id": buffer_id,
        "queued_at_utc": now.isoformat(),
        "slot_jst": slot.isoformat(),
        "submission_mode": "shareNow",
        "source": "approved_comic" if selected_comic else (
            "fresh_article" if evidence is None else "verified_article_page_views"),
        "copy_mode": "title_hook_image_thread",
        "text_sha256": digest(thread["parent_text"]),
        "published_at": eligible["published_at"],
        "primary_topic": primary_topic(eligible) or None,
    }
    if isinstance(comic, dict):
        entry["comic_attachment"] = {key: value for key, value in comic.items() if key != "text"}
    add_provider_receipt(entry, buffer_receipt)
    if evidence is not None:
        entry["metrics_at_queue"] = {**window,
                                     "last_7_days_page_views": evidence["last7DaysPageViews"],
                                     "today_page_views": evidence["todayPageViews"]}
    state.setdefault(record_kind, {})[eligible["slug"]] = entry
    save_state(state)
    print("Article accepted by Buffer for immediate send (X publication still unverified):",
          eligible["slug"], buffer_id, "slot_jst=", entry["slot_jst"],
          "provider_status=", entry.get("buffer_status_at_accept"),
          "external_link=", entry.get("buffer_external_link_at_accept"),
          "scheduled_before=", scheduled, "selection=", entry["source"],
          "page_views=", evidence,
          "measurement_window=", window)
    return report_outcome("accepted", slug=eligible["slug"],
                          provider_status=entry.get("buffer_status_at_accept"))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError) as exc:
        print("Safe article promotion skip:", exc, file=sys.stderr)
        report_outcome("error")
        sys.exit(1)

