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
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHANNEL = "6ae94b74e29c69a171436d72"
DELETABLE_STATUSES = {"draft", "scheduled", "pending", "error", "failed"}
RESULT_PATH = HERE / "purge-retired-x-result.json"


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


def validate_live_identity(target: dict, remote: dict | None) -> str | None:
    """Identity and publication state only; never grants deletion permission."""
    if not isinstance(remote, dict):
        return "not_in_listing_unverified"
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
    return None


def validate_live(target: dict, remote: dict | None) -> str | None:
    reason = validate_live_identity(target, remote)
    if reason:
        return reason
    actions = remote.get("allowedActions") or []
    if "deletePost" not in actions:
        return "delete_action_not_allowed"
    return None


def resolve_live_target(target: dict, listing: dict, read_by_id, *,
                        always_direct: bool = False,
                        error_detail: dict | None = None) -> tuple[dict | None, str | None]:
    """Do not treat omitted list entries as deleted or trust them blindly.

    The provider list can exclude drafts or older posts. A direct ID lookup
    must still pass the exact ID/channel/text/sent/action checks in
    validate_live before any mutation is possible.
    """
    remote = None if always_direct else listing.get(target["buffer_id"])
    if remote is not None:
        return remote, None
    try:
        remote = read_by_id(target["buffer_id"])
    except Exception as exc:
        if error_detail is not None:
            error_detail["error_class"] = type(exc).__name__
            message = str(exc).strip().replace("\n", " ")
            # publish.gql removes authorization headers/tokens from errors.
            # Store only the bounded, known-safe provider error categories.
            if message.startswith((
                    "Buffer GraphQL error:", "Buffer HTTP error:",
                    "Buffer network error:")):
                error_detail["provider_message"] = message[:180]
        return None, "direct_id_lookup_failed"
    if remote is None:
        return None, "not_in_listing_or_direct_lookup_unverified"
    return remote, None


def inspect_live_target(target: dict, draft_listing: dict, read_by_id,
                        error_detail: dict | None = None
                        ) -> tuple[dict | None, str, str | None]:
    """Use an explicitly filtered live Buffer draft list before ID fallback.

    A matching draft snapshot is read-only evidence, never deletion permission.
    Incompatible or missing list entries fall through to an exact-ID lookup;
    an unavailable ID remains unresolved instead of being assumed deleted.
    """
    listed = draft_listing.get(target["buffer_id"])
    if (isinstance(listed, dict) and listed.get("status") == "draft"
            and validate_live_identity(target, listed) is None):
        return listed, "buffer_draft_list", None
    remote, problem = resolve_live_target(
        target, {}, read_by_id, always_direct=True, error_detail=error_detail)
    return remote, "buffer_post_id", problem


def last_verified_historical_draft(target: dict, cleanup: dict) -> dict | None:
    """Only describe exact previous Buffer receipts, never infer live status."""
    matches = []
    for section in ("recovery_results", "continuation_results"):
        rows = cleanup.get(section) or {}
        if not isinstance(rows, dict):
            continue
        row = rows.get(target["post_id"])
        if not isinstance(row, dict) or row.get("state") != "verified_draft":
            continue
        after = row.get("after")
        if not isinstance(after, dict):
            continue
        if (row.get("mutation_receipt_confirmed") is not True
                or after.get("buffer_id") != target["buffer_id"]
                or after.get("channel_id") != CHANNEL
                or after.get("text_sha256") != target["text_sha256"]
                or after.get("provider_status") != "draft"
                or after.get("sent_at")):
            continue
        try:
            verified_at = datetime.fromisoformat(row["verified_at"].replace("Z", "+00:00"))
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if verified_at.tzinfo is None:
            continue
        matches.append(verified_at.astimezone(timezone.utc))
    if not matches:
        return None
    return {
        "status": "draft",
        "verified_at_utc": max(matches).isoformat(),
        "source": "social-bot/queue-cleanup.json",
        "note": "historical_receipt_not_current_buffer_state",
    }


def deletion_recheck_reason(target: dict, before: dict, current: dict | None) -> str | None:
    """Fail closed if an exactly identified post changes before deletion."""
    reason = validate_live(target, current)
    if reason is not None:
        return reason
    if any(current.get(field) != before.get(field)
           for field in ("status", "dueAt", "sentAt")):
        return "changed_before_delete"
    return None


def execution_mode(args: list[str], env: dict) -> str:
    """Read-only inspection may run on main push; deletion is manual-only."""
    if any(arg not in ("--inspect", "--apply") for arg in args):
        raise ValueError("Unknown retired X cleanup argument")
    if "--inspect" in args and "--apply" in args:
        raise ValueError("Inspection and deletion modes are mutually exclusive")
    if not args:
        return "validate"
    if env.get("GITHUB_REF") != "refs/heads/main":
        raise RuntimeError("Buffer inspection/deletion requires main Actions")
    event = env.get("GITHUB_EVENT_NAME")
    if (args[0] == "--apply" and event != "workflow_dispatch") or (
            args[0] == "--inspect" and event not in ("workflow_dispatch", "push")):
        raise RuntimeError("Deletion is manual-only; inspection is manual or trusted main push")
    return "inspect" if args[0] == "--inspect" else "apply"


def main() -> int:
    inventory = read(HERE / "experiments" / "posts.json")
    ledger = read(HERE / "experiments" / "state.json")
    config = read(HERE / "experiments" / "config.json")
    rows = targets(inventory, ledger, config)
    cleanup_path = HERE / "queue-cleanup.json"
    cleanup = read(cleanup_path) if cleanup_path.exists() else {}
    historical_drafts = {
        row["post_id"]: proof
        for row in rows
        if (proof := last_verified_historical_draft(row, cleanup)) is not None
    }
    print(f"Validated {len(rows)} retired experiment Buffer receipts.")

    mode = execution_mode(sys.argv[1:], os.environ)
    if mode == "validate":
        print("Validation only; no Buffer read or mutation.")
        return 0
    if mode == "inspect":
        print("Read-only legacy inspection; Buffer deletion disabled.")

    token = os.environ.get("BUFFER_API_KEY", "").strip()
    if not token:
        raise RuntimeError("Existing Buffer key unavailable; no fallback")

    from publish import choose_x_channel, gql, qstring

    orgid, channel = choose_x_channel(token)
    if channel != CHANNEL:
        raise RuntimeError("Pinned X account mismatch")

    def list_live_posts(*, drafts_only: bool = False):
        cursor = None
        out = {}
        seen = set()
        for _page in range(20):
            after = ", after: " + qstring(cursor) if cursor else ""
            query = (
                "query { posts(first: 100" + after
                + ", input: {organizationId: " + qstring(orgid)
                + ", filter: {channelIds: [" + qstring(CHANNEL)
                + "]" + (", status: [draft]" if drafts_only else "") + "}}) { "
                "edges { node { id channelId text status dueAt sentAt allowedActions } } "
                "pageInfo { hasNextPage endCursor } } }"
            )
            conn = gql(token, query).get("posts") or {}
            for edge in conn.get("edges") or []:
                node = edge.get("node") or {}
                if isinstance(node.get("id"), str):
                    out[node["id"]] = node
            page = conn.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return out
            cursor = page.get("endCursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise RuntimeError("Buffer unpublished pagination cursor invalid")
            seen.add(cursor)
        raise RuntimeError("Buffer live-post pagination exceeded safe page limit")

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
    absent = 0
    published = 0
    verified_unpublished = 0
    held = 0
    outcomes = []

    try:
        # Buffer supports explicit status filtering. Its default listing did
        # not return the six historical drafts even though their move-to-draft
        # receipts were verified. Never equate an omitted list ID with deletion.
        live_posts = list_live_posts(drafts_only=(mode == "inspect"))
        list_error = None
    except Exception as exc:
        live_posts = {}
        list_error = type(exc).__name__ + ": " + str(exc)[:240]
        print("Could not list Buffer posts:", list_error, file=sys.stderr)

    # Read-only inspection still checks exact IDs if the filtered draft list
    # is temporarily unavailable. Manual deletion requires a complete list.
    if list_error and mode == "apply":
        report = {
            "schema_version": 1,
            "mode": mode,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "scope": "retired_x_experiments_only",
            "article_promotion_untouched": True,
            "counts": {"ledger_receipts_checked": len(rows), "deleted_now": 0, "already_absent": 0, "published_untouched": 0, "verified_unpublished": 0, "held": len(rows)},
            "remaining_unpublished_legacy_known": 0,
            "unresolved_legacy_status": len(rows),
            "historical_draft_receipts": len(historical_drafts),
            "provider_list_error": list_error,
            "outcomes": [],
        }
        RESULT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 2

    for target in rows:
        pid = target["post_id"]
        bid = target["buffer_id"]
        lookup_detail = {}
        if mode == "inspect":
            # Draft list is explicit, paginated and read-only. It can resolve
            # drafts hidden from the single-post API without deleting them.
            before, observed_via, lookup_issue = inspect_live_target(
                target, live_posts, fetch_post, error_detail=lookup_detail)
        else:
            # Deletion must never rely on the earlier list snapshot.
            before, lookup_issue = resolve_live_target(
                target, live_posts, fetch_post, always_direct=True,
                error_detail=lookup_detail)
            observed_via = "buffer_post_id"
        if lookup_issue:
            print("HELD", pid, lookup_issue,
                  lookup_detail.get("provider_message", lookup_detail.get("error_class", "")))
            outcomes.append({"post_id": pid, "buffer_id": bid,
                             "state": "held", "reason": lookup_issue,
                             "lookup_error": lookup_detail or None})
            held += 1
            continue

        reason = (validate_live_identity(target, before)
                  if mode == "inspect" else validate_live(target, before))
        if reason == "already_absent":
            print("ABSENT", pid)
            outcomes.append({"post_id": pid, "buffer_id": bid, "state": "absent"})
            absent += 1
            continue
        if reason == "already_published":
            print("PUBLISHED_UNTOUCHED", pid)
            outcomes.append({"post_id": pid, "buffer_id": bid, "state": "published_untouched"})
            published += 1
            continue
        if reason:
            print("HELD", pid, reason)
            outcomes.append({"post_id": pid, "buffer_id": bid, "state": "held", "reason": reason})
            held += 1
            continue

        if mode == "inspect":
            print("VERIFIED_UNPUBLISHED", pid, before.get("status"), before.get("dueAt"))
            outcomes.append({
                "post_id": pid, "buffer_id": bid,
                "state": "verified_unpublished",
                "provider_status": before.get("status"),
                "provider_due_at_utc": before.get("dueAt"),
                "observed_via": observed_via,
            })
            verified_unpublished += 1
            continue

        # A post can change between the first observation and mutation.
        # Re-read its exact ID and refuse deletion if state/timing changed.
        try:
            current = fetch_post(bid)
            reason = deletion_recheck_reason(target, before, current)
        except Exception:
            reason = "direct_id_recheck_failed"
        if reason:
            print("HELD", pid, reason)
            outcomes.append({"post_id": pid, "buffer_id": bid,
                             "state": "held", "reason": reason})
            held += 1
            continue

        try:
            result = delete_post(bid)
        except Exception as exc:
            # A failed mutation plus a failed/empty follow-up read is NOT
            # evidence that our deletion succeeded. Preserve uncertainty.
            reason = "delete_request_failed_unverified"
            print("HELD", pid, reason, type(exc).__name__, str(exc)[:180])
            outcomes.append({"post_id": pid, "buffer_id": bid,
                             "state": "held", "reason": reason})
            held += 1
            continue

        if result.get("__typename") == "DeletePostSuccess" and result.get("id") == bid:
            print("DELETED", pid, bid)
            outcomes.append({"post_id": pid, "buffer_id": bid, "state": "deleted", "verification": "delete_receipt_confirmed"})
            deleted += 1
            continue

        # Without a matching DeletePostSuccess receipt, a null post or an
        # API read error cannot prove that this invocation deleted anything.
        reason = "delete_without_receipt_unverified"
        print("HELD", pid, reason, str(result.get("message", ""))[:180])
        outcomes.append({"post_id": pid, "buffer_id": bid,
                         "state": "held", "reason": reason})
        held += 1

    for result in outcomes:
        if result["state"] == "held" and result["post_id"] in historical_drafts:
            result["last_verified_historical"] = historical_drafts[result["post_id"]]

    report = {
        "schema_version": 1,
        "mode": mode,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "scope": "retired_x_experiments_only",
        "article_promotion_untouched": True,
        "counts": {
            "ledger_receipts_checked": len(rows),
            "deleted_now": deleted,
            "already_absent": absent,
            "published_untouched": published,
            "verified_unpublished": verified_unpublished,
            "held": held,
            "historical_draft_receipts": len(historical_drafts),
        },
        "remaining_unpublished_legacy_known": verified_unpublished,
        "unresolved_legacy_status": held,
        "provider_post_count": len(live_posts) if list_error is None else None,
        "provider_list_filter": "draft" if mode == "inspect" else "all",
        "provider_list_error": list_error,
        "provider_lookup": ("draft_list_with_direct_id_fallback"
                            if mode == "inspect" else "direct_id_each_target"),
        "outcomes": outcomes,
    }
    RESULT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "Retired experiment purge summary:",
        f"deleted={deleted}",
        f"absent={absent}",
        f"published_untouched={published}",
        f"verified_unpublished={verified_unpublished}",
        f"held={held}",
        f"historically_verified_draft={len(historical_drafts)}",
    )
    if held:
        return 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("Retired X purge halted safely:", str(exc), file=sys.stderr)
        raise SystemExit(1)
