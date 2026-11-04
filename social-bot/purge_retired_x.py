#!/usr/bin/env python3
"""Delete only unsent Buffer posts from the retired X experiment lane.

This is a bounded cleanup for the 2026-11-04 owner decision that automatic X
publishing is article promotion only. It never touches sent posts, article
promotion posts, useful posts, or any Buffer entry whose id/channel/text no
longer matches the immutable legacy experiment ledger.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHANNEL = "6ae94b74e29c69a171436d72"
DELETABLE_STATUSES = {"draft", "scheduled", "pending", "error", "failed"}


def read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def published_text(post: dict) -> str:
    text = post.get("text")
    if not isinstance(text, str):
        raise ValueError("Legacy post text missing")
    if post.get("hashtag"):
        text = text.rstrip() + "\n\n" + str(post["hashtag"])
    return text


def targets(inventory: dict, ledger: dict, config: dict) -> list[dict]:
    if config.get("experiment_publishing_enabled") is not False:
        raise ValueError("Refusing purge while legacy experiment publishing is enabled")
    queued = ledger.get("queued")
    if not isinstance(queued, dict):
        raise ValueError("Invalid legacy experiment ledger")
    rows = []
    for post in inventory.get("experiments", []):
        if not isinstance(post, dict):
            continue
        pid = post.get("id")
        if not isinstance(pid, str) or pid not in queued:
            continue
        receipt = queued[pid]
        if not isinstance(receipt, dict):
            raise ValueError(f"Invalid ledger row for {pid}")
        bid = receipt.get("buffer_id")
        expected = digest(published_text(post))
        recorded = receipt.get("published_text_sha256", receipt.get("text_sha256"))
        if not isinstance(bid, str) or recorded != expected:
            raise ValueError(f"Immutable submission evidence mismatch for {pid}")
        rows.append({
            "post_id": pid,
            "buffer_id": bid,
            "text_sha256": expected,
            "format": post.get("format"),
        })
    return rows


def validate_live(target: dict, remote: dict | None) -> str | None:
    if not isinstance(remote, dict):
        return "already_absent"
    if remote.get("id") != target["buffer_id"]:
        return "identity_mismatch"
    if remote.get("channelId") != CHANNEL:
        return "channel_mismatch"
    if remote.get("status") == "sent" or remote.get("sentAt"):
        return "already_published"
    text = remote.get("text")
    if not isinstance(text, str) or digest(text) != target["text_sha256"]:
        return "text_changed"
    status = remote.get("status")
    if status not in DELETABLE_STATUSES:
        return "status_not_deletable"
    actions = remote.get("allowedActions") or []
    if "deletePost" not in actions:
        return "delete_action_not_allowed"
    return None


def main() -> int:
    inventory = read(HERE / "experiments" / "posts.json")
    ledger = read(HERE / "experiments" / "state.json")
    config = read(HERE / "experiments" / "config.json")
    rows = targets(inventory, ledger, config)
    print(f"Validated {len(rows)} retired experiment Buffer receipts.")

    if "--apply" not in sys.argv:
        print("Validation only; no Buffer mutation.")
        return 0

    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise RuntimeError("Live purge may only run from main Actions")
    if os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
        raise RuntimeError("Live purge is disabled for pull requests")

    token = os.environ.get("BUFFER_API_KEY", "").strip()
    if not token:
        raise RuntimeError("Existing Buffer key unavailable; no fallback")

    from publish import choose_x_channel, gql, qstring

    _, channel = choose_x_channel(token)
    if channel != CHANNEL:
        raise RuntimeError("Pinned X account mismatch")

    def fetch_post(buffer_id: str):
        query = (
            "query { post(input: {id: " + qstring(buffer_id)
            + "}) { id channelId text status dueAt sentAt allowedActions } }"
        )
        return gql(token, query).get("post")

    def delete_post(buffer_id: str):
        query = (
            "mutation { deletePost(input: {id: " + qstring(buffer_id) + "}) { "
            "__typename ... on DeletePostSuccess { id } "
            "... on MutationError { message } } }"
        )
        return gql(token, query).get("deletePost") or {}

    deleted = 0
    skipped = 0
    held = 0

    for target in rows:
        pid = target["post_id"]
        try:
            before = fetch_post(target["buffer_id"])
        except Exception as exc:
            print("HELD", pid, "provider_read_failed", type(exc).__name__, str(exc)[:180])
            held += 1
            continue

        reason = validate_live(target, before)
        if reason:
            print("SKIP", pid, reason)
            skipped += 1
            continue

        result = delete_post(target["buffer_id"])
        if result.get("__typename") == "DeletePostSuccess" and result.get("id") == target["buffer_id"]:
            print("DELETED", pid, target["buffer_id"])
            deleted += 1
            continue

        # A mutation may have completed even if its response was unusual.
        # Re-read once; missing means the requested cleanup succeeded.
        try:
            after = fetch_post(target["buffer_id"])
        except Exception:
            after = None
        if after is None:
            print("DELETED", pid, target["buffer_id"], "verified_absent")
            deleted += 1
        else:
            print("HELD", pid, "delete_unverified", str(result.get("message", ""))[:180])
            held += 1

    print(f"Retired experiment purge summary: deleted={deleted} skipped={skipped} held={held}")
    if held:
        return 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("Retired X purge halted safely:", str(exc), file=sys.stderr)
        raise SystemExit(1)
