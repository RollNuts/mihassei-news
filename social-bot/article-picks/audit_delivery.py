#!/usr/bin/env python3
"""Read-only Buffer delivery audit for Veripsa article promotions."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from publish import gql, qstring  # noqa: E402
from article_thread import (THREAD_FIELDS, post_texts, thread_items, remote_thread_mismatch,
                            thread_digest, digest, validate_thread)

JST = timezone(timedelta(hours=9))
MAX_AUDIT_DAYS = 7
MAX_ITEMS = 60
UNDELIVERED_STATUSES = {"draft", "error", "needs_approval", "sending"}
IMMEDIATE_PROBLEM_STATUSES = {"draft", "error", "needs_approval"}
SENDING_GRACE = timedelta(minutes=15)
MAX_DIRECT_READS = 3


def reusable_observations(state: dict, previous: dict, now: datetime) -> dict:
    """Preserve dated evidence; never relabel a cached observation as a fresh read."""
    previous_items = {row.get("promotion_key", row.get("slug")): row for row in previous.get("items", [])
                      if isinstance(row, dict)}
    cached = {}
    for slug, entry, _queued in selected_entries(state, now):
        if not entry["buffer_id"]:
            continue  # An ambiguous submission intent is never delivery evidence.
        row = previous_items.get(slug, {})
        if slug.startswith("comic:") and (
                row.get("kind") != "comic"
                or not isinstance(entry.get("text_sha256"), str)
                or row.get("text_sha256") != entry["text_sha256"]):
            continue
        if entry.get("article_thread") is not None and row.get("thread_sha256") != entry.get("thread_sha256"):
            continue
        if str(row.get("reason", "")).startswith("deferred_"):
            continue
        observed = aware(row.get("provider_checked_at_utc") or previous.get("checked_at_utc"))
        if (row.get("buffer_id") != entry["buffer_id"] or observed is None
                or observed > now):
            continue
        status = row.get("buffer_status")
        # Sent is historical delivery evidence, not proof the X page still exists.
        confirmed = status == "sent" and aware(row.get("buffer_sent_at_utc")) is not None
        if entry.get("article_thread") is not None or row.get("thread_publication") == "unverified":
            confirmed = confirmed and row.get("thread_configuration") == "matched"
        interval = timedelta(minutes=15) if status in ("sending", "scheduled") else timedelta(hours=6)
        if confirmed or now - observed < interval:
            cached[slug] = dict(row, provider_checked_at_utc=observed.isoformat(),
                                observation_reused=True)
    return cached


def trigger_allows_provider_read(event_name: str, now: datetime) -> bool:
    """Late article pushes back up the read-only 23:45 JST provider audit."""
    if now.tzinfo is None:
        raise ValueError("Audit current time must have timezone")
    return event_name != "push" or now.astimezone(JST).hour >= 21


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object: " + str(path))
    return value


def aware(value: object):
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


def canonical_url(slug: str) -> str:
    return f"https://veripsa.com/articles/{slug}/"


def selected_entries(state: dict, now: datetime, days: int = MAX_AUDIT_DAYS):
    if state.get("schema_version") != 1 or not isinstance(state.get("queued"), dict):
        raise ValueError("Invalid article promotion ledger")
    if now.tzinfo is None or type(days) is not int or not 1 <= days <= 30:
        raise ValueError("Invalid audit window")
    cutoff = now - timedelta(days=days)
    rows = []
    for prefix, records in (("", state["queued"]), ("comic:", state.get("comic_posts", {}))):
        if not isinstance(records, dict):
            raise ValueError("Invalid article promotion records")
        for slug, entry in records.items():
            if not isinstance(slug, str) or ":" in slug or not isinstance(entry, dict):
                raise ValueError("Invalid article promotion record")
            queued_at = aware(entry.get("queued_at_utc"))
            buffer_id = entry.get("buffer_id")
            pending = ((prefix == "comic:" or entry.get("article_thread") is not None)
                       and entry.get("submission_pending") is True and buffer_id == "")
            if (queued_at is None or not isinstance(buffer_id, str)
                    or (not buffer_id and not pending)):
                raise ValueError("Incomplete article promotion record")
            if queued_at >= cutoff or pending:
                rows.append((prefix + slug, entry, queued_at))
    # Ambiguous submissions never disappear merely through age or a busy ledger.
    rows.sort(key=lambda row: (not row[1]["buffer_id"], row[2]), reverse=True)
    return rows[:MAX_ITEMS]


def read_post_by_id(token: str, buffer_id: str):
    """Read one saved Buffer receipt without mutating or retrying it."""
    if not isinstance(buffer_id, str) or not buffer_id:
        raise ValueError("Invalid Buffer post id")
    query = (
        "query { post(input: {id: " + qstring(buffer_id)
        + "}) { id text status createdAt dueAt sentAt externalLink sharedNow shareMode error { message supportUrl } " + THREAD_FIELDS + " } }"
    )
    return gql(token, query).get("post")


def reconcile(slug: str, entry: dict, remote: object, lookup_error: str | None = None) -> dict:
    promotion_key = slug
    is_comic = slug.startswith("comic:")
    if is_comic:
        slug = slug.removeprefix("comic:")
    result = {
        "slug": slug,
        "buffer_id": entry["buffer_id"],
        "queued_at_utc": aware(entry["queued_at_utc"]).astimezone(timezone.utc).isoformat(),
        "buffer_status": "unverified",
    }
    thread = entry.get("article_thread")
    if thread is not None:
        result.update(thread_sha256=entry.get("thread_sha256"), text_sha256=entry.get("text_sha256"),
                      thread_publication="unverified")
    if not entry["buffer_id"] and entry.get("submission_pending"):
        if is_comic:
            result.update(promotion_key=promotion_key, kind="comic", text_sha256=entry.get("text_sha256"))
        result.update(submission_pending=True, reason="submission_pending_without_buffer_id")
        return result
    if is_comic:
        result.update(promotion_key=promotion_key, kind="comic",
                      text_sha256=entry.get("text_sha256"))
        if not entry["buffer_id"]:
            result.update(submission_pending=True, reason="submission_pending_without_buffer_id")
            return result
    initial_fields = (
        "buffer_status_at_accept",
        "buffer_created_at_at_accept",
        "buffer_due_at_at_accept",
        "buffer_sent_at_at_accept",
        "buffer_external_link_at_accept",
        "buffer_shared_now_at_accept",
        "buffer_share_mode_at_accept",
        "buffer_error_message_at_accept",
        "buffer_error_support_url_at_accept",
    )
    for field in initial_fields:
        if field in entry:
            result[field] = entry[field]
    if not isinstance(remote, dict):
        result["reason"] = lookup_error or "not_returned_in_recent_scheduled_or_sent_window"
        return result
    if remote.get("id") != entry["buffer_id"]:
        result["reason"] = "buffer_id_mismatch"
        return result
    text = remote.get("text")
    if thread is not None:
        try:
            validate_thread(thread, slug)
        except ValueError:
            mismatch = "invalid_saved_thread_evidence"
        else:
            mismatch = remote_thread_mismatch(remote, thread)
            if (entry.get("thread_sha256") != thread_digest(thread)
                    or entry.get("text_sha256") != digest(thread["parent_text"])):
                mismatch = "saved_thread_fingerprint_mismatch"
        if mismatch:
            result["reason"] = mismatch
            result["provider_status_observed"] = remote.get("status")
            # Preserve this ID's safe provider errors and timing even when a
            # reply or image is missing; parent status is not thread delivery.
        else:
            result["thread_configuration"] = "matched"
            result["reason"] = "reply_publication_not_independently_verified"
    elif not isinstance(text, str) or canonical_url(slug) not in "\n".join(post_texts(remote)):
        result["reason"] = "canonical_article_url_missing_or_text_mismatch"
        return result
    elif thread_items(remote):
        result.update(thread_publication="unverified", reason="recovered_thread_without_original_image_proof")
    if is_comic and thread is None and hashlib.sha256(text.encode("utf-8")).hexdigest() != entry.get("text_sha256"):
        result["reason"] = "comic_text_sha256_mismatch"
        return result
    status = remote.get("status")
    if status not in ("scheduled", "sent") and status not in UNDELIVERED_STATUSES:
        result["reason"] = "unexpected_buffer_status"
        return result
    result["buffer_status"] = status
    created = aware(remote.get("createdAt"))
    if created is not None:
        result["buffer_created_at_utc"] = created.astimezone(timezone.utc).isoformat()
    external_link = remote.get("externalLink")
    if isinstance(external_link, str) and external_link:
        result["buffer_external_link"] = external_link
    if type(remote.get("sharedNow")) is bool:
        result["buffer_shared_now"] = remote["sharedNow"]
    share_mode = remote.get("shareMode")
    if isinstance(share_mode, str) and share_mode:
        result["buffer_share_mode"] = share_mode
    provider_error = remote.get("error")
    if isinstance(provider_error, dict):
        message = provider_error.get("message")
        if isinstance(message, str) and message.strip():
            result["buffer_error_message"] = message.strip()[:500]
        support_url = provider_error.get("supportUrl")
        if isinstance(support_url, str) and support_url.startswith("https://"):
            result["buffer_error_support_url"] = support_url[:500]
    if status in IMMEDIATE_PROBLEM_STATUSES:
        result["review_needed"] = "provider_not_sent_" + status
    if status == "scheduled":
        due = aware(remote.get("dueAt"))
        if due is not None:
            result["buffer_due_at_utc"] = due.astimezone(timezone.utc).isoformat()
        else:
            result["due_at_unavailable"] = True
    if status == "sent":
        sent = aware(remote.get("sentAt"))
        if sent is not None:
            result["buffer_sent_at_utc"] = sent.astimezone(timezone.utc).isoformat()
        else:
            result["sent_at_unavailable"] = True
    return result


def build(state: dict, existing: list[dict], now: datetime,
          lookup_errors: dict[str, str] | None = None,
          cached_items: dict[str, dict] | None = None) -> dict:
    chosen = selected_entries(state, now)
    remote = {
        row.get("id"): row
        for row in existing
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    lookup_errors = lookup_errors or {}
    cached_items = cached_items or {}
    items = [dict(cached_items[slug]) if slug in cached_items else reconcile(
        slug, entry, remote.get(entry["buffer_id"]),
        lookup_errors.get(entry["buffer_id"]),
    ) for slug, entry, _queued_at in chosen]
    for row in items:
        if (row.get("promotion_key", row["slug"]) not in cached_items
                and row["buffer_id"] in remote and row["buffer_id"]):
            row["provider_checked_at_utc"] = now.astimezone(timezone.utc).isoformat()
        # Recompute age-dependent flags even when an observation is reused.
        for key in ("delivery_in_progress", "review_needed", "sending_age_minutes"):
            row.pop(key, None)
    today = now.astimezone(JST).date()
    sent_today = 0
    scheduled_today = 0
    overdue_scheduled = 0
    missing_provider_timestamps = 0
    delivery_problems = 0
    delivery_in_progress = 0
    overdue_sending = 0
    for row in items:
        sent = aware(row.get("buffer_sent_at_utc"))
        due = aware(row.get("buffer_due_at_utc"))
        previous_problems = delivery_problems
        thread_problem = row.get("thread_publication") == "unverified" and row.get("thread_configuration") != "matched"
        if thread_problem:
            row["review_needed"] = row.get("reason", "thread_delivery_unknown")
        if row["buffer_status"] == "sent" and sent is None:
            missing_provider_timestamps += 1
        if row["buffer_status"] in IMMEDIATE_PROBLEM_STATUSES:
            delivery_problems += 1
            row["review_needed"] = "provider_not_sent_" + row["buffer_status"]
        if row["buffer_status"] == "sending":
            created = (
                aware(row.get("buffer_created_at_utc"))
                or aware(row.get("buffer_created_at_at_accept"))
                or aware(row.get("queued_at_utc"))
            )
            if created is None:
                missing_provider_timestamps += 1
                delivery_problems += 1
                row["review_needed"] = "provider_sending_without_created_at"
            elif now - created > SENDING_GRACE:
                delivery_problems += 1
                overdue_sending += 1
                row["review_needed"] = "provider_sending_more_than_15_minutes"
                row["sending_age_minutes"] = int((now - created).total_seconds() // 60)
            else:
                delivery_in_progress += 1
                row["delivery_in_progress"] = True
                row["sending_age_minutes"] = max(
                    0, int((now - created).total_seconds() // 60)
                )
        if row["buffer_status"] == "scheduled":
            if due is None:
                missing_provider_timestamps += 1
            elif now - due > timedelta(hours=2):
                overdue_scheduled += 1
                row["review_needed"] = "scheduled_more_than_two_hours_past_due"
        if thread_problem and delivery_problems == previous_problems:
            delivery_problems += 1
        if sent is not None and sent.astimezone(JST).date() == today:
            sent_today += 1
        if due is not None and due.astimezone(JST).date() == today:
            scheduled_today += 1
    return {
        "schema_version": 1,
        "checked_at_utc": now.astimezone(timezone.utc).isoformat(),
        "source": "buffer_saved_id_readonly_with_dated_observations",
        "scope": {
            "ledger_days": MAX_AUDIT_DAYS,
            "max_items": MAX_ITEMS,
            "automatic_lane": "article-picks-only",
            "max_direct_reads_per_run": MAX_DIRECT_READS,
        },
        "today_jst": today.isoformat(),
        "observed": {
            "article_records_checked": len(items),
            "buffer_sent_today": sent_today,
            "buffer_scheduled_today": scheduled_today,
            "unverified": sum(row["buffer_status"] == "unverified" for row in items),
            "overdue_scheduled": overdue_scheduled,
            "missing_provider_timestamps": missing_provider_timestamps,
            "delivery_problems": delivery_problems,
            "delivery_in_progress": delivery_in_progress,
            "overdue_sending": overdue_sending,
            "thread_publication_unverified": sum(row.get("thread_publication") == "unverified" for row in items),
        },
        "limitations": (
            "Buffer status is provider evidence, not independent X page verification. "
            "Only unresolved saved Buffer IDs are read. Reused observations keep their original timestamp; "
            "sent evidence is historical, not current X-page existence. Draft/error/needs_approval "
            "require investigation. Sending is normal for up to 15 minutes, then requires review. "
            "Overdue scheduled posts also require investigation. Nothing is automatically retried. "
            "Missing provider timestamps are not assumed sent. Thread configuration and parent-level status "
            "do not independently verify publication of the first reply; no partial thread is retried."
        ),
        "items": items,
    }


def save(report: dict, path: Path = HERE / "delivery.json") -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    config = read_json(HERE / "config.json")
    state = read_json(HERE / "state.json")
    if config.get("schema_version") != 1 or not isinstance(config.get("channel_id"), str):
        raise ValueError("Invalid article promotion config")
    now = datetime.now(timezone.utc)
    event_name = os.environ.get("X_AUDIT_TRIGGER_EVENT", "").strip()
    if not trigger_allows_provider_read(event_name, now):
        print("Article audit daytime push: offline validation only; no Buffer read/write.")
        return 0
    token = os.environ.get("BUFFER_API_KEY", "").strip()
    if not token:
        print("No Buffer key; article delivery audit skipped without changing evidence.")
        return 0
    # IDs were accepted on the pinned channel by dispatch; reconcile also checks
    # the exact ID and canonical article URL. Auditing them needs no account,
    # channel discovery, or repeated full-history listing.
    previous_path = HERE / "delivery.json"
    previous = read_json(previous_path) if previous_path.is_file() else {}
    retry_at = aware(previous.get("provider_retry_at_utc"))
    if retry_at is not None and now < retry_at:
        # An already-known provider cooldown needs no new read or repeated red CI.
        # The original request that discovered 429 still exits with failure.
        print("Buffer cooldown until", retry_at.isoformat(), "; no provider read.")
        return 0
    cached = reusable_observations(state, previous, now)
    existing = []
    chosen = selected_entries(state, now)
    lookup_errors = {}
    reads = 0
    rate_limited = False
    retry_at = None
    for slug, entry, _queued_at in chosen:
        buffer_id = entry["buffer_id"]
        if not buffer_id:
            continue  # No saved ID: report pending, never search or guess a receipt.
        if slug in cached:
            continue
        if rate_limited or reads >= MAX_DIRECT_READS:
            lookup_errors[buffer_id] = "deferred_rate_limit" if rate_limited else "deferred_read_budget"
            continue
        reads += 1
        try:
            direct = read_post_by_id(token, buffer_id)
        except (RuntimeError, ValueError) as exc:
            message = str(exc).strip().replace("\n", " ")
            if message.startswith("Buffer budget hold:") or message.startswith("Buffer budget hold until"):
                print("Article audit deferred to preserve publishing allowance:", message)
                # Nothing was requested. Keep the last report's real observation
                # times rather than replacing known delivery with a quota error.
                if not existing:
                    return 0
                lookup_errors[buffer_id] = "deferred_request_budget"
                break
            lookup_errors[buffer_id] = "direct_id_lookup_failed:" + message[:160]
            if "Buffer HTTP error: 429" in message:
                print("Article audit rate limited; remaining reads deferred:", message)
                rate_limited = True
                for part in message.split("; "):
                    if part.startswith("retry_at_utc="):
                        retry_at = aware(part.split("=", 1)[1])
            continue
        if isinstance(direct, dict):
            existing.append(direct)
    if not reads:
        if not any(not entry["buffer_id"] for _key, entry, _stamp in chosen):
            print("No unresolved article delivery checks due; no Buffer read/write.")
            return 0
        print("Article/comic submission pending without a saved Buffer ID; no provider read.")
    report = build(state, existing, now, lookup_errors, cached)
    report["provider_rate_limited"] = rate_limited
    if retry_at is not None:
        report["provider_retry_at_utc"] = retry_at.isoformat()
    save(report, HERE / "delivery.json")
    print(
        "Article delivery audit:",
        report["observed"]["article_records_checked"],
        "checked;",
        report["observed"]["unverified"],
        "unverified;", report["observed"]["delivery_in_progress"],
        "sending;", report["observed"]["delivery_problems"],
        "delivery problems.",
    )
    return 1 if rate_limited else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print("Article delivery audit failed safely:", exc, file=sys.stderr)
        sys.exit(1)
