#!/usr/bin/env python3
"""Optional 12:30 JST SECOND original post. Buffer Free customScheduled.

Unlike the 09:35 queue refill, this adds at most one post for the current
JST date. It never edits, moves or deletes existing Buffer reservations.
The scheduled 19:30 queue remains controlled by the original workflow.
"""
import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dispatch import (
    HERE, decide, hashtag_variant, hash_text, publication_text,
    read_json, save_state, validate,
)
from publish import choose_x_channel, list_existing, queue_one

JST = timezone(timedelta(hours=9))
MIDDAY_HOUR, MIDDAY_MINUTE = 12, 30
MAX_SCHEDULED_TOTAL = 4  # Buffer Free supports 10, we use <=4.
MAX_POSTS_PER_JST_DAY = 2
MIN_SPACING = timedelta(hours=4)
MIN_LEAD = timedelta(minutes=45)
MAX_LEAD = timedelta(hours=4)


def parse_time(value):
    """Parse provider UTC timestamps; never assume a naive time is JST."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def midday_target(now):
    """The job must start early enough to schedule TODAY, not tomorrow."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(JST)
    target = local.replace(hour=MIDDAY_HOUR, minute=MIDDAY_MINUTE,
                           second=0, microsecond=0)
    lead = target - local
    if not MIN_LEAD <= lead <= MAX_LEAD:
        return None
    return target


def date_guard(existing, state, now, target):
    """Avoid third posts, nearby time slots and unsafe provider uncertainty."""
    today = now.astimezone(JST).date()
    scheduled = sum(item.get("status") == "scheduled" for item in existing)
    if scheduled >= MAX_SCHEDULED_TOTAL:
        return "free_queue_safety_cap"
    already_seen = set()
    daily_times = []
    for item in existing:
        if item.get("status") not in ("scheduled", "sent"):
            continue
        # Not all providers supply sentAt for historic posts; dueAt is primary.
        raw = item.get("dueAt") or (item.get("sentAt")
                                   if item.get("status") == "sent" else None)
        t = parse_time(raw)
        if t is None:
            if item.get("status") == "scheduled":
                return "unknown_scheduled_time"
            continue
        if t.astimezone(JST).date() == today:
            daily_times.append(t)
            already_seen.add(item.get("id"))
    # Local ledger is an independent guard if Buffer omitted a recent item.
    for item in state.get("queued", {}).values():
        if item.get("submission_mode") != "customScheduled":
            continue
        t = parse_time(item.get("due_at_utc"))
        if t and t.astimezone(JST).date() == today:
            if item.get("buffer_id") not in already_seen:
                daily_times.append(t)
            # Only one extra midday reservation can exist for a JST date.
            return "midday_already_submitted"
    if len(daily_times) >= MAX_POSTS_PER_JST_DAY:
        return "daily_cap"
    if any(abs(t - target.astimezone(timezone.utc)) < MIN_SPACING
           for t in daily_times):
        return "four_hour_spacing"
    return None


def plan_midday(posts, state, existing, now):
    target = midday_target(now)
    if target is None:
        return None, [], None, "outside_safe_schedule_window"
    guard = date_guard(existing, state, now, target)
    if guard:
        return None, [], target, guard
    # Reuse the live text/ID reconciliation and editorial mix; never pick the
    # same original twice or silently change a previously reserved caption.
    plan, recovered, _ = decide(
        posts, state, existing, MAX_SCHEDULED_TOTAL, 1
    )
    if not plan:
        return None, recovered, target, "no_capacity_or_fresh_original"
    return plan[0], recovered, target, None


def main(now=None):
    cfg = read_json(HERE / "config.json")
    inventory = read_json(HERE / "posts.json")
    state = read_json(HERE / "state.json")
    posts = validate(inventory, cfg, state)
    if (cfg.get("experiment_publishing_enabled") is not True or
            cfg.get("two_daily_midday_enabled") is not True):
        print("MIDDAY X EXPERIMENT OFF; no Buffer calls.")
        return 0
    now = now or datetime.now(timezone.utc)
    if midday_target(now) is None:
        print("Outside today's safe 12:30 JST scheduling window; nothing sent.")
        return 0
    token = os.environ.get("BUFFER_API_KEY", "").strip()
    if not token:
        raise RuntimeError("BUFFER_API_KEY missing; no second post submitted")
    org_id, channel_id = choose_x_channel(token)
    if channel_id != cfg["channel_id"]:
        raise RuntimeError("Pinned Veripsa channel mismatch; nothing sent")
    existing = list_existing(token, org_id, channel_id, include_due_at=True)
    post, recovered, target, reason = plan_midday(posts, state, existing, now)
    if recovered:
        save_state(state)
    if reason:
        print("Midday second post skipped safely:", reason)
        return 0
    final_text = publication_text(post)
    image_sha256 = None
    if post.get("visual"):
        img = HERE.parent / "assets" / (post["id"] + ".png")
        if not img.is_file():
            raise RuntimeError("Missing original X image: " + post["id"])
        image_sha256 = hashlib.sha256(img.read_bytes()).hexdigest()
    due_at_utc = target.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    buffer_id = queue_one(token, channel_id, {
        "id": post["id"],
        "kind": "image" if image_sha256 else "text",
        "text": final_text,
    }, mode="customScheduled", due_at_utc=due_at_utc)
    state["queued"][post["id"]] = {
        "buffer_id": buffer_id,
        "text_sha256": hash_text(post["text"]),
        "published_text_sha256": hash_text(final_text),
        "hashtag_variant": hashtag_variant(post),
        "hashtag": post.get("hashtag"),
        "format": post["format"],
        "mechanism": post.get("mechanism"),
        "queued_at_utc": now.astimezone(timezone.utc).isoformat(),
        "submission_mode": "customScheduled",
        "due_at_utc": due_at_utc,
        **({"image_sha256": image_sha256} if image_sha256 else {}),
    }
    save_state(state)
    print("Buffer accepted ONE additional 12:30 JST post:", post["id"],
          "dueAt:", due_at_utc,
          "(X publication and impressions still unverified)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("MIDDAY X POST FAILED SAFELY:", str(error), file=sys.stderr)
        sys.exit(1)
