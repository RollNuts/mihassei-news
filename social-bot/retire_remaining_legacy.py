#!/usr/bin/env python3
"""One-time retirement of the four remaining legacy X reservations.

Only exact saved Buffer IDs with unchanged text on the pinned X channel may be
moved from scheduled to draft. Sent/draft/unknown posts are never republished,
deleted or recreated. Intent is persisted before the Buffer mutation so an
ambiguous timeout is never retried.
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

HERE = Path(__file__).resolve().parent
REQUEST_PATH = HERE / "remaining-legacy-retirement.json"
CHANNEL = "6ae94b74e29c69a171436d72"
REQUEST_ID = "x-retire-remaining-legacy-20261104"
TARGET_IDS = (
    "xexp-future-advice",
    "xexp-reaper-late",
    "xexp-selfcheckout-dialogue",
    "xexp-honest-survey",
)
MIN_LEAD = timedelta(minutes=2)


def read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object")
    return value


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stamp(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result if result.tzinfo is not None else None


def published_text(post: dict) -> str:
    text = post["text"]
    if post.get("hashtag"):
        text = text.rstrip() + "\n\n" + post["hashtag"]
    return text


def validate(request: dict, inventory: dict, ledger: dict) -> list[dict]:
    if (request.get("schema_version") != 1
            or request.get("request_id") != REQUEST_ID
            or type(request.get("enabled")) is not bool
            or request.get("channel_id") != CHANNEL
            or request.get("action") != "move_to_draft"):
        raise ValueError("Invalid remaining-legacy retirement request")
    targets = request.get("targets")
    results = request.get("results")
    if not isinstance(targets, list) or not isinstance(results, dict):
        raise ValueError("Targets/results required")
    if tuple(target.get("post_id") for target in targets) != TARGET_IDS:
        raise ValueError("Exactly the four remaining legacy IDs are required")
    posts = {p["id"]: p for p in inventory.get("experiments", []) if isinstance(p, dict)}
    queued = ledger.get("queued")
    if not isinstance(queued, dict):
        raise ValueError("Invalid legacy queue ledger")
    seen = set()
    for target in targets:
        pid, bid = target.get("post_id"), target.get("buffer_id")
        if pid in seen or pid not in posts or pid not in queued:
            raise ValueError("Unknown or duplicate retirement target")
        receipt = queued[pid]
        text = published_text(posts[pid])
        expected_hash = digest(text)
        if (not isinstance(bid, str) or not re.fullmatch(r"[0-9a-f]{24}", bid)
                or bid != receipt.get("buffer_id")
                or target.get("text_sha256") != expected_hash
                or expected_hash != receipt.get("published_text_sha256", receipt.get("text_sha256"))):
            raise ValueError("Target must match immutable Buffer submission evidence")
        seen.add(pid)
    if not set(results).issubset(seen):
        raise ValueError("Unknown result row")
    return targets


def snapshot(post):
    if not isinstance(post, dict):
        return {}
    assets = post.get("assets")
    return {
        "buffer_id": post.get("id"),
        "channel_id": post.get("channelId"),
        "provider_status": post.get("status"),
        "due_at": post.get("dueAt"),
        "sent_at": post.get("sentAt"),
        "text_sha256": digest(post["text"]) if isinstance(post.get("text"), str) else None,
        "assets_sha256": digest(json.dumps(assets, sort_keys=True))
            if isinstance(assets, list) else None,
    }


def refusal(target: dict, post, now: datetime):
    if not isinstance(post, dict):
        return "provider_post_missing"
    if post.get("id") != target["buffer_id"] or post.get("channelId") != CHANNEL:
        return "identity_mismatch"
    if not isinstance(post.get("text"), str) or digest(post["text"]) != target["text_sha256"]:
        return "text_changed"
    if post.get("status") == "sent" or post.get("sentAt"):
        return "already_published"
    if post.get("status") == "draft":
        return "already_draft"
    if post.get("status") != "scheduled":
        return "not_scheduled"
    if "movePostToDraft" not in (post.get("allowedActions") or []):
        return "draft_action_not_allowed"
    due = stamp(post.get("dueAt"))
    if due is None:
        return "due_at_unconfirmed"
    if due <= now + MIN_LEAD:
        return "past_or_too_close_to_publication"
    return None


def process(request, inventory, ledger, fetch_post, move_to_draft, persist, now_fn):
    targets = validate(request, inventory, ledger)
    if not request["enabled"]:
        return
    results = request["results"]
    posts = {p["id"]: p for p in inventory["experiments"]}
    for target in targets:
        pid = target["post_id"]
        if pid in results:
            continue
        row = {"checked_at": now_fn().isoformat()}
        try:
            before = fetch_post(target["buffer_id"])
            reason = refusal(target, before, now_fn())
            row["before"] = snapshot(before)
        except Exception as exc:
            reason = "provider_read_failed"
            row["read_error"] = {"error_class": type(exc).__name__, "message": str(exc)[:240]}
        if reason:
            row.update(state="skipped", reason=reason)
            results[pid] = row
            persist(request)
            continue

        row.update(state="intent", intent_at=now_fn().isoformat())
        results[pid] = row
        persist(request)

        # Re-read after durable intent: publication or text may have changed.
        try:
            current = fetch_post(target["buffer_id"])
            reason = refusal(target, current, now_fn())
            if row["before"].get("assets_sha256") != snapshot(current).get("assets_sha256"):
                reason = "assets_changed"
        except Exception as exc:
            current = None
            reason = "provider_recheck_failed"
            row["recheck_error"] = {"error_class": type(exc).__name__, "message": str(exc)[:240]}
        if reason:
            row.update(state="skipped", reason=reason)
            persist(request)
            continue

        try:
            receipt = move_to_draft(target["buffer_id"], current["text"])
            row["mutation_receipt_confirmed"] = (
                isinstance(receipt, dict)
                and receipt.get("id") == target["buffer_id"]
                and receipt.get("status") == "draft"
            )
        except Exception as exc:
            row.update(
                state="held_unknown",
                reason="draft_outcome_unverified",
                mutation_receipt_confirmed=False,
                provider_error={"error_class": type(exc).__name__, "message": str(exc)[:240]},
            )
            persist(request)
            continue

        try:
            after = fetch_post(target["buffer_id"])
            row["after"] = snapshot(after)
            if (after.get("status") == "draft"
                    and after.get("id") == target["buffer_id"]
                    and after.get("channelId") == CHANNEL
                    and digest(after.get("text", "")) == target["text_sha256"]
                    and row["after"].get("assets_sha256") == row["before"].get("assets_sha256")):
                row.update(state="verified_draft", verified_at=now_fn().isoformat())
            else:
                row.update(state="held_unknown", reason="draft_outcome_unverified")
        except Exception as exc:
            row.update(
                state="held_unknown",
                reason="verification_failed",
                verification_error={"error_class": type(exc).__name__, "message": str(exc)[:240]},
            )
        persist(request)

    # Every target now has a terminal record. Disable the one-time request even
    # for an already-published/too-close item; ambiguous mutation results are
    # deliberately never retried.
    if all(pid in request["results"] for pid in TARGET_IDS):
        request["enabled"] = False
        request["completed_at"] = now_fn().isoformat()
        persist(request)


def persist_request(request):
    content = json.dumps(request, ensure_ascii=False, indent=2) + "\n"
    REQUEST_PATH.write_text(content, encoding="utf-8")
    relative = "social-bot/remaining-legacy-retirement.json"
    subprocess.run(["git", "add", relative], cwd=HERE.parent, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet", "--", relative],
                      cwd=HERE.parent).returncode:
        return
    subprocess.run(
        ["git", "commit", "-m", "chore(x): record remaining legacy retirement [skip ci]", "--", relative],
        cwd=HERE.parent, check=True,
    )
    for _ in range(4):
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=HERE.parent, check=True)
        if REQUEST_PATH.read_text(encoding="utf-8") != content:
            raise RuntimeError("Retirement request changed while running; stop before further Buffer edits")
        if subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=HERE.parent).returncode == 0:
            return
    raise RuntimeError("Could not persist retirement evidence; no further Buffer edits")


def main():
    request = read(REQUEST_PATH)
    inventory = read(HERE / "experiments" / "posts.json")
    ledger = read(HERE / "experiments" / "state.json")
    validate(request, inventory, ledger)
    if "--validate" in sys.argv or "--apply" not in sys.argv or not request["enabled"]:
        print("Validated remaining legacy X retirement. No Buffer mutation.")
        return 0
    if os.environ.get("GITHUB_REF") != "refs/heads/main" or os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
        raise RuntimeError("Retirement may only run from main Actions")
    token = os.environ.get("BUFFER_API_KEY", "").strip()
    if not token:
        raise RuntimeError("Existing Buffer key unavailable; no fallback")

    from publish import choose_x_channel, gql, qstring
    from retire_legacy_queue import move_to_draft

    _, channel = choose_x_channel(token)
    if channel != CHANNEL:
        raise RuntimeError("Pinned X account mismatch")

    def fetch_post(buffer_id):
        return gql(
            token,
            "query { post(input: {id: " + qstring(buffer_id)
            + "}) { id channelId text status dueAt sentAt allowedActions assets { id mimeType } } }",
        ).get("post")

    process(
        request, inventory, ledger, fetch_post,
        lambda bid, text: move_to_draft(token, bid, text),
        persist_request, lambda: datetime.now(timezone.utc),
    )
    for pid, row in request["results"].items():
        print("Legacy retirement:", pid, row.get("state"), row.get("reason", ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("Remaining legacy retirement halted safely:", str(exc), file=sys.stderr)
        sys.exit(1)
