#!/usr/bin/env python3
"""Read-only Buffer delivery audit for Veripsa article promotions."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from publish import choose_x_channel, list_existing  # noqa: E402

JST = timezone(timedelta(hours=9))
MAX_AUDIT_DAYS = 7
MAX_ITEMS = 60


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
    for slug, entry in state["queued"].items():
        if not isinstance(slug, str) or not isinstance(entry, dict):
            raise ValueError("Invalid article promotion record")
        queued_at = aware(entry.get("queued_at_utc"))
        buffer_id = entry.get("buffer_id")
        if queued_at is None or not isinstance(buffer_id, str) or not buffer_id:
            raise ValueError("Incomplete article promotion record")
        if queued_at >= cutoff:
            rows.append((slug, entry, queued_at))
    rows.sort(key=lambda row: row[2], reverse=True)
    return rows[:MAX_ITEMS]


def reconcile(slug: str, entry: dict, remote: object) -> dict:
    result = {
        "slug": slug,
        "buffer_id": entry["buffer_id"],
        "queued_at_utc": aware(entry["queued_at_utc"]).astimezone(timezone.utc).isoformat(),
        "buffer_status": "unverified",
    }
    if not isinstance(remote, dict):
        result["reason"] = "not_returned_in_recent_scheduled_or_sent_window"
        return result
    if remote.get("id") != entry["buffer_id"]:
        result["reason"] = "buffer_id_mismatch"
        return result
    text = remote.get("text")
    if not isinstance(text, str) or canonical_url(slug) not in text:
        result["reason"] = "canonical_article_url_missing_or_text_mismatch"
        return result
    status = remote.get("status")
    if status not in ("scheduled", "sent"):
        result["reason"] = "unexpected_buffer_status"
        return result
    result["buffer_status"] = status
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


def build(state: dict, existing: list[dict], now: datetime) -> dict:
    chosen = selected_entries(state, now)
    remote = {
        row.get("id"): row
        for row in existing
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    items = [reconcile(slug, entry, remote.get(entry["buffer_id"]))
             for slug, entry, _queued_at in chosen]
    today = now.astimezone(JST).date()
    sent_today = 0
    scheduled_today = 0
    overdue_scheduled = 0
    missing_provider_timestamps = 0
    for row in items:
        sent = aware(row.get("buffer_sent_at_utc"))
        due = aware(row.get("buffer_due_at_utc"))
        if row["buffer_status"] == "sent" and sent is None:
            missing_provider_timestamps += 1
        if row["buffer_status"] == "scheduled":
            if due is None:
                missing_provider_timestamps += 1
            elif now - due > timedelta(hours=2):
                overdue_scheduled += 1
                row["review_needed"] = "scheduled_more_than_two_hours_past_due"
        if sent is not None and sent.astimezone(JST).date() == today:
            sent_today += 1
        if due is not None and due.astimezone(JST).date() == today:
            scheduled_today += 1
    return {
        "schema_version": 1,
        "checked_at_utc": now.astimezone(timezone.utc).isoformat(),
        "source": "buffer_recent_scheduled_sent_readonly",
        "scope": {
            "ledger_days": MAX_AUDIT_DAYS,
            "max_items": MAX_ITEMS,
            "automatic_lane": "article-picks-only",
        },
        "today_jst": today.isoformat(),
        "observed": {
            "article_records_checked": len(items),
            "buffer_sent_today": sent_today,
            "buffer_scheduled_today": scheduled_today,
            "unverified": sum(row["buffer_status"] == "unverified" for row in items),
            "overdue_scheduled": overdue_scheduled,
            "missing_provider_timestamps": missing_provider_timestamps,
        },
        "limitations": (
            "Buffer scheduled/sent is provider evidence, not independent X page verification. "
            "Overdue scheduled posts require investigation, not automatic retry. "
            "Posts outside the recent provider window and missing timestamps are not assumed sent."
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
    org_id, channel_id = choose_x_channel(token)
    if channel_id != config["channel_id"]:
        raise RuntimeError("Connected X channel differs from pinned Veripsa account")
    existing = list_existing(token, org_id, channel_id, include_due_at=True)
    report = build(state, existing, now)
    save(report)
    print(
        "Article delivery audit:",
        report["observed"]["article_records_checked"],
        "checked;",
        report["observed"]["unverified"],
        "unverified.",
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print("Article delivery audit failed safely:", exc, file=sys.stderr)
        sys.exit(1)
