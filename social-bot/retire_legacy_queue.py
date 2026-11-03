#!/usr/bin/env python3
"""One-time migration: preserve EXACTLY two early test posts as Buffer drafts.

Fails closed on mismatched IDs/channel/content, with no delete and no changes
to any other Buffer posts. Intended for use by the one-shot GitHub workflow.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from publish import choose_x_channel, gql, list_existing, qstring

HERE = Path(__file__).resolve().parent
LOG_PATH = HERE / "legacy_queue_migration.json"
LEGACY_IDS = ("alien-no-entry", "shooting-star-return")


def plan_migration(feed, state, buffer_posts):
    """Offline-safe plan. Only exact known, still-scheduled records can change."""
    src = {item["id"]: item for item in feed["posts"]}
    submitted = state.get("submitted", {})
    by_id = {item["id"]: item for item in buffer_posts}
    planned = []
    results = []
    for item_id in LEGACY_IDS:
        if item_id not in src or item_id not in submitted:
            raise ValueError("Expected original inventory/ledger entry missing: " + item_id)
        original = src[item_id]
        record = submitted[item_id]
        buffer_id = record["buffer_id"]
        if original.get("series") == "nejimachi":
            raise ValueError("Refusing to change an IP story item")
        match = by_id.get(buffer_id)
        if match is None:
            # Only scheduled/sent were requested; a missing record is probably
            # already a draft. Do not blindly retry an unknown post ID.
            results.append({"original_id": item_id, "buffer_id": buffer_id,
                            "outcome": "not_in_scheduled_or_sent"})
            continue
        if match.get("text") != original["text"]:
            raise ValueError("Live Buffer content mismatch; no edit: " + item_id)
        if match.get("status") != "scheduled":
            results.append({"original_id": item_id, "buffer_id": buffer_id,
                            "outcome": "already_" + str(match.get("status"))})
            continue
        planned.append((item_id, buffer_id))
    return planned, results


def move_to_draft(token, post_id, exact_original_text):
    query = (
        "mutation { editPost(input: {id: " + qstring(post_id)
        + ", text: " + qstring(exact_original_text) + ", saveToDraft: true}) { __typename "
        + "... on PostActionSuccess { post { id status } } "
        + "... on MutationError { message } } }"
    )
    obj = gql(token, query).get("editPost") or {}
    if obj.get("__typename") != "PostActionSuccess":
        raise RuntimeError("Buffer editPost rejected: " + str(obj.get("__typename", "unknown")) + " - " + str(obj.get("message","unavailable"))[:200])
    post = obj.get("post") or {}
    if post.get("id") != post_id or post.get("status") != "draft":
        raise RuntimeError("Could not confirm draft state for " + post_id)
    return post


def main():
    if os.environ.get("MIGRATE_LEGACY_QUEUE") != "true":
        print("SAFE MODE: no migration requested.")
        return 0
    token = os.environ.get("BUFFER_API_KEY", "").strip()
    if not token:
        raise ValueError("Missing BUFFER_API_KEY; no posts changed")
    inventory = json.loads((HERE/"posts.json").read_text(encoding="utf-8"))
    state = json.loads((HERE/"state.json").read_text(encoding="utf-8"))

    orgid, channelid = choose_x_channel(token)
    posts = list_existing(token, orgid, channelid)
    planned, other = plan_migration(inventory, state, posts)
    print("Verified target posts:", len(planned), "scheduled; other:", len(other))
    updates = []
    original_text_by_id = {p['id']: p['text'] for p in inventory['posts']}
    for original_id, post_id in planned:
        # Buffer editPost currently validates that text/media is supplied even
        # when the docs say an omitted text value should be preserved.
        # We reuse only the exact original text verified by plan_migration.
        move_to_draft(token, post_id, original_text_by_id[original_id])
        updates.append({
            "original_id": original_id,
            "buffer_id": post_id,
            "outcome": "moved_to_draft",
            "at": datetime.now(timezone.utc).isoformat()
        })
        print("Preserved in drafts:", original_id)

    after = list_existing(token, orgid, channelid)
    remaining_ids = {p["id"] for p in after if p.get("status") == "scheduled"}
    if any(pid in remaining_ids for _, pid in planned):
        raise RuntimeError("Could not verify all requested posts left the schedule")
    log = {"purpose":"Archive two low-quality setup tests, keep reversible drafts",
           "targets": [*other, *updates],
           "verified_no_original_scheduled": True}
    LOG_PATH.write_text(json.dumps(log,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Migration verification passed. New serial posts may be queued.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("Migration halted safely:", str(exc), file=sys.stderr)
        sys.exit(1)
