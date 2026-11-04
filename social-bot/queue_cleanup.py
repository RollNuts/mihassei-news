#!/usr/bin/env python3
"""Withdraw only the named, unchanged legacy reservations into Buffer drafts.

This is the bounded 2026-11-04 quality repair, not a daily queue maintenance job.
Original manuscripts, assets and submission ledgers remain untouched. The one
request file keeps durable intent and verification; an uncertain edit is never
repeated. No deletePost, createPost or publishing mutation is used.

Buffer validates that editPost contains text or media even for saveToDraft.
Use the already proven legacy helper and pass the exact freshly verified text;
omit assets so existing images remain untouched. A separately named, single
recovery may correct that known input defect while preserving the first result.

Buffer's documented editPost(saveToDraft: true) semantics:
https://developers.buffer.com/reference.html#editpostinput
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUEST_PATH = HERE / "queue-cleanup.json"
CHANNEL = "6ae94b74e29c69a171436d72"
REQUEST_ID = "x-quality-20261104"
RECOVERY_ID = "x-quality-20261104-preserve-text-v1"
RECOVERY_TARGETS = frozenset({
    "xexp-invisible-nails", "xexp-witch-autopilot", "xexp-nejimachi-one-screw",
    "xexp-shadow-home", "xexp-book-review", "xexp-nejimachi-nothing-wrong",
})
MAX_TARGETS = 6
MIN_LEAD = timedelta(minutes=15)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stamp(value):
    if not isinstance(value, str):
        raise ValueError("Timezone-aware timestamp required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timezone-aware timestamp required")
    return parsed


def target_hash(request, target):
    pinned = {key: request[key] for key in ("request_id", "channel_id", "action", "expires_at")}
    pinned["target"] = target
    return digest(json.dumps(pinned, sort_keys=True, ensure_ascii=False))


def recovery_hash(request, target):
    return digest(RECOVERY_ID + ":" + target_hash(request, target))


def provider_error(error):
    """Keep actionable provider errors, never credentials or request headers."""
    message = str(error)
    token = os.environ.get("BUFFER_API_KEY", "")
    if token:
        message = message.replace(token, "[redacted]")
    message = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer [redacted]", message)
    message = re.sub(r"(?i)((?:api[_-]?key|access[_-]?token|authorization)\s*[:=]\s*)[^\s,;]+",
                     r"\1[redacted]", message)
    result = {"error_class": type(error).__name__, "message": message[:400]}
    match = re.search(r"Buffer editPost rejected: ([A-Za-z0-9_]+) - ", message)
    if match:
        result["provider_type"] = match.group(1)
    return result


def validate(request, inventory, ledger):
    if (request.get("schema_version") != 1 or type(request.get("enabled")) is not bool
            or request.get("request_id") != REQUEST_ID
            or request.get("channel_id") != CHANNEL or request.get("action") != "move_to_draft"):
        raise ValueError("Invalid bounded cleanup request")
    stamp(request.get("expires_at"))
    targets, results = request.get("targets"), request.get("results")
    if not isinstance(targets, list) or not 1 <= len(targets) <= MAX_TARGETS or not isinstance(results, dict):
        raise ValueError("Explicit bounded targets and permanent results required")
    recovery = request.get("recovery_results", {})
    if (not isinstance(recovery, dict)
            or request.get("recovery_request_id") not in (None, RECOVERY_ID)
            or (recovery and request.get("recovery_request_id") != RECOVERY_ID)):
        raise ValueError("Invalid named recovery request")
    manuscripts = {post["id"]: post for post in inventory["experiments"]}
    queued = ledger["queued"]
    seen_ids, seen_buffer = set(), set()
    for target in targets:
        pid, bid = target.get("post_id"), target.get("buffer_id")
        if pid in seen_ids or bid in seen_buffer or pid not in manuscripts or pid not in queued:
            raise ValueError("Unknown or duplicated legacy reservation")
        post, receipt = manuscripts[pid], queued[pid]
        text = post["text"]
        if post.get("hashtag"):
            text = text.rstrip() + "\n\n" + post["hashtag"]
        if (not isinstance(bid, str) or not re.fullmatch(r"[0-9a-f]{24}", bid)
                or bid != receipt.get("buffer_id")
                or target.get("text_sha256") != digest(text)
                or target["text_sha256"] != receipt.get("published_text_sha256", receipt.get("text_sha256"))):
            raise ValueError("Cleanup target must match the immutable accepted text and Buffer ID")
        stamp(target.get("expected_due_at"))
        if not isinstance(target.get("reason"), str) or not target["reason"].strip():
            raise ValueError("Editorial reason required for each named reservation")
        if pid in results and results[pid].get("target_sha256") != target_hash(request, target):
            raise ValueError("Cannot change an attempted cleanup target")
        if pid in recovery and recovery[pid].get("target_sha256") != recovery_hash(request, target):
            raise ValueError("Cannot change an attempted recovery target")
        seen_ids.add(pid)
        seen_buffer.add(bid)
    if not set(results).issubset(seen_ids) or not set(recovery).issubset(seen_ids):
        raise ValueError("Never remove cleanup history")
    if request.get("recovery_request_id") and not seen_ids.issubset(RECOVERY_TARGETS):
        raise ValueError("Recovery is restricted to the original six named targets")
    return targets


def initial_input_failure_unchanged(target, initial):
    """Only the observed failed scheduling-only edit qualifies for recovery."""
    if (not isinstance(initial, dict) or initial.get("state") != "held_unknown"
            or initial.get("mutation_receipt_confirmed") is not False):
        return False
    before, after = initial.get("before"), initial.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict) or before != after:
        return False
    if (before.get("provider_status") != "scheduled" or before.get("sent_at")
            or before.get("buffer_id") != target["buffer_id"]
            or before.get("channel_id") != CHANNEL
            or before.get("text_sha256") != target["text_sha256"]
            or not re.fullmatch(r"[a-f0-9]{64}", str(before.get("assets_sha256", "")))):
        return False
    try:
        return stamp(before.get("due_at")) == stamp(target["expected_due_at"])
    except (TypeError, ValueError):
        return False


def snapshot(post):
    """Retain proof without copying other account content into logs."""
    if not isinstance(post, dict):
        return {}
    assets = post.get("assets")
    return {
        "buffer_id": post.get("id"), "channel_id": post.get("channelId"),
        "provider_status": post.get("status"), "due_at": post.get("dueAt"),
        "sent_at": post.get("sentAt"),
        "text_sha256": digest(post["text"]) if isinstance(post.get("text"), str) else None,
        "assets_sha256": digest(json.dumps(assets, sort_keys=True)) if isinstance(assets, list) else None,
    }


def refusal(target, post, now):
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
    if not isinstance(post.get("assets"), list):
        return "assets_unconfirmed"
    if "movePostToDraft" not in (post.get("allowedActions") or []):
        return "draft_action_not_allowed"
    try:
        due = stamp(post.get("dueAt"))
    except (ValueError, TypeError):
        return "due_at_unconfirmed"
    if due != stamp(target["expected_due_at"]):
        return "schedule_changed"
    if due <= now + MIN_LEAD:
        return "past_or_too_close_to_publication"
    return None


def process(request, inventory, ledger, fetch_post, move_to_draft, persist, now_fn, *, recovery=False):
    targets = validate(request, inventory, ledger)
    if recovery and request.get("recovery_request_id") != RECOVERY_ID:
        raise ValueError("Explicit named preserve-text recovery is required")
    if not request["enabled"]:
        return
    results = request.setdefault("recovery_results", {}) if recovery else request["results"]
    for target in targets:
        pid = target["post_id"]
        # Any previous attempt (including an uncertain one) is terminal here.
        if pid in results:
            continue
        row = {"target_sha256": (recovery_hash if recovery else target_hash)(request, target),
               "checked_at": now_fn().isoformat()}
        if recovery:
            row["recovery_request_id"] = RECOVERY_ID
        if recovery and not initial_input_failure_unchanged(target, request["results"].get(pid)):
            row.update(state="skipped", reason="initial_input_failure_not_proven")
        elif now_fn() >= stamp(request["expires_at"]):
            row.update(state="skipped", reason="cleanup_request_expired")
        else:
            try:
                before = fetch_post(target["buffer_id"])
                reason = refusal(target, before, now_fn())
                row["before"] = snapshot(before)
            except Exception as error:
                reason = "provider_read_failed"
                row["read_error"] = provider_error(error)
            if reason:
                row.update(state="skipped", reason=reason)
            else:
                row.update(state="intent", intent_at=now_fn().isoformat())
        results[pid] = row
        persist(request)  # Remote durable intent MUST exist before a Buffer edit.
        if row["state"] != "intent":
            continue
        # Git persistence can take time. Re-read immediately before editing.
        try:
            current = fetch_post(target["buffer_id"])
            reason = refusal(target, current, now_fn())
            if now_fn() >= stamp(request["expires_at"]):
                reason = "cleanup_request_expired"
            if snapshot(current).get("assets_sha256") != row["before"]["assets_sha256"]:
                reason = "assets_changed"
        except Exception as error:
            reason = "provider_recheck_failed"
            row["recheck_error"] = provider_error(error)
        if reason:
            row.update(state="skipped", reason=reason)
            persist(request)
            continue
        receipt_confirmed = False
        try:
            # This is the unchanged live text, including its original tags.
            # The proven helper supplies it to satisfy Buffer input validation.
            receipt = move_to_draft(target["buffer_id"], current["text"])
            receipt_confirmed = (
                isinstance(receipt, dict) and receipt.get("id") == target["buffer_id"]
                and receipt.get("status") == "draft"
            )
        except Exception as error:
            row["provider_error"] = provider_error(error)
            # A timeout may have succeeded; never repeat the mutation.
        row.update(state="held_unknown", reason="draft_outcome_unverified",
                   mutation_receipt_confirmed=receipt_confirmed)
        try:
            after = fetch_post(target["buffer_id"])
            row["after"] = snapshot(after)
            if (refusal(target, after, now_fn()) == "already_draft"
                    and row["after"]["assets_sha256"] == row["before"]["assets_sha256"]):
                row.update(state="verified_draft", verified_at=now_fn().isoformat())
                row.pop("reason", None)
        except Exception as error:
            row["verification_error"] = provider_error(error)
        persist(request)


def persist_request(request):
    content = json.dumps(request, ensure_ascii=False, indent=2) + "\n"
    REQUEST_PATH.write_text(content, encoding="utf-8")
    relative = "social-bot/queue-cleanup.json"
    subprocess.run(["git", "add", relative], cwd=HERE.parent, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet", "--", relative], cwd=HERE.parent).returncode:
        subprocess.run(["git", "commit", "-m", "chore(x): record named queue withdrawal [skip ci]", "--", relative],
                       cwd=HERE.parent, check=True)
    for _ in range(4):
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=HERE.parent, check=True)
        if REQUEST_PATH.read_text(encoding="utf-8") != content:
            raise RuntimeError("Cleanup request changed during persistence; no further Buffer edits")
        if subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=HERE.parent).returncode == 0:
            return
    raise RuntimeError("Cleanup proof could not be persisted; do not repeat an uncertain edit")


def main():
    request = read(REQUEST_PATH)
    inventory, ledger = read(HERE / "experiments/posts.json"), read(HERE / "experiments/state.json")
    targets = validate(request, inventory, ledger)
    recovery = "--recover" in sys.argv
    if "--validate" in sys.argv or not ({"--apply", "--recover"} & set(sys.argv)) or not request["enabled"]:
        print("Validated named queue cleanup. No Buffer request.")
        return 0
    if recovery and "--apply" in sys.argv:
        raise ValueError("Run either the original request or its named recovery, never both")
    if recovery and request.get("recovery_request_id") != RECOVERY_ID:
        print("No named preserve-text recovery requested. No Buffer request.")
        return 0
    if os.environ.get("GITHUB_REF") != "refs/heads/main" or os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
        raise RuntimeError("Queue withdrawal is only permitted from main Actions")
    results = request.get("recovery_results", {}) if recovery else request["results"]
    pending = [t for t in targets if t["post_id"] not in results]
    if not pending:
        print("Named queue cleanup already recorded; no Buffer request or retry.")
        return 0
    token = os.environ.get("BUFFER_API_KEY", "").strip()
    if not token:
        raise RuntimeError("Existing Buffer key unavailable; no fallback")
    from publish import choose_x_channel, gql, qstring
    from retire_legacy_queue import move_to_draft as proven_move_to_draft
    if datetime.now(timezone.utc) < stamp(request["expires_at"]):
        _, channel = choose_x_channel(token)
        if channel != CHANNEL:
            raise RuntimeError("Pinned X account mismatch")

    def fetch_post(bid):
        return gql(token, "query { post(input: {id: " + qstring(bid)
                   + "}) { id channelId text status dueAt sentAt allowedActions assets { id mimeType } } }").get("post")

    def move_to_draft(bid, original_text):
        return proven_move_to_draft(token, bid, original_text)

    process(request, inventory, ledger, fetch_post, move_to_draft, persist_request,
            lambda: datetime.now(timezone.utc), recovery=recovery)
    results = request.get("recovery_results", {}) if recovery else request["results"]
    for pid, row in results.items():
        print("Queue withdrawal:", pid, row["state"], row.get("reason", ""))
        if row.get("provider_error"):
            print("Provider error:", json.dumps(row["provider_error"], ensure_ascii=False))
    # An incomplete withdrawal must be visible even though the independent
    # useful-post step is allowed to continue. A later run never retries it.
    return int(any(row.get("state") != "verified_draft"
                   and row.get("reason") not in ("already_draft", "already_published")
                   for row in results.values()))


if __name__ == "__main__":
    sys.exit(main())
