#!/usr/bin/env python3
"""Fail-closed, zero-cost editorial release preflight for Nejimachi.

This code does not contact X or Buffer, issue credentials, or create approvals.
Only an explicit owner's review record, matching the exact script and PNG,
permits queue submission. Earlier draft/sent status must be verified in Buffer.
"""
import hashlib
import json
from pathlib import Path

from ip.audit import payload_hash

ROOT=Path(__file__).resolve().parent
REVIEW_FILE=ROOT/"ip"/"episode_reviews.json"


class EditorialHold(RuntimeError):
    """Do not queue any new episodes until an editorial blocker is resolved."""


def load_manifest(path=REVIEW_FILE):
    try:
        raw=json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError,ValueError) as exc:
        raise EditorialHold("Editorial approval manifest missing or corrupt") from exc
    if (not isinstance(raw,dict) or raw.get("schema_version")!=1
            or raw.get("owner_approval_required") is not True
            or not isinstance(raw.get("approved"),dict)
            or not isinstance(raw.get("rejected"),dict)):
        raise EditorialHold("Invalid editorial approval format; no releases allowed")
    if set(raw["approved"]).intersection(raw["rejected"]):
        raise EditorialHold("Episode marked both approved and rejected")
    return raw


def verify_one(post, record, images_dir):
    """Pin exact content AND exact artwork; a script change voids permission."""
    post_id=post["id"]
    if not isinstance(record,dict) or record.get("approved_by")!="owner":
        raise EditorialHold(f"{post_id}: missing explicit owner review")
    if not record.get("reviewed_at_jst"):
        raise EditorialHold(f"{post_id}: no reviewed date")
    if record.get("post_sha256")!=payload_hash(post):
        raise EditorialHold(f"{post_id}: story or dialogue differs from approved draft")
    art=Path(images_dir)/(post_id+".png")
    try:
        digest=hashlib.sha256(art.read_bytes()).hexdigest()
    except OSError as exc:
        raise EditorialHold(f"{post_id}: original approved artwork missing") from exc
    if record.get("image_sha256")!=digest:
        raise EditorialHold(f"{post_id}: artwork differs from approved image")
    if not record.get("age_target")=="adult":
        raise EditorialHold(f"{post_id}: adult-audience creative review missing")
    return True


def select_next(ordered, submitted, existing, manifest, images_dir,
                max_new=2, max_queue=3):
    """Return eligible new serial chapters in order; never skip a blocked chapter.

    If any earlier entry was moved to drafts, removed, deleted or lost from the
    bounded Buffer scheduled/sent lookup, the pipeline remains blocked, rather
    than suddenly queue episode 3 while episodes 1 and 2 are not published.
    """
    live_by_id={p["id"]:p for p in existing}
    scheduled=sum(1 for p in existing if p.get("status")=="scheduled")
    if scheduled>=max_queue:
        return []
    selected=[]
    for post in ordered:
        pid=post["id"]
        if post.get("series")!="nejimachi":
            raise EditorialHold("Only canonical Nejimachi posts may be released")
        if pid in manifest["rejected"]:
            raise EditorialHold(f"{pid}: permanently rejected. Never queue it.")
        if pid in submitted:
            record=submitted[pid]
            match=live_by_id.get(record.get("buffer_id"))
            if match is None or match.get("status") not in ("sent","scheduled"):
                raise EditorialHold(f"{pid}: previously queued but not confirmed sent/scheduled; possible draft")
            if match.get("text")!=post["text"]:
                raise EditorialHold(f"{pid}: Buffer text does not match immutable manuscript")
            continue
        approval=manifest["approved"].get(pid)
        if not approval:
            raise EditorialHold(f"{pid}: no human-reviewed art approval; queue unchanged")
        verify_one(post,approval,images_dir)
        selected.append(post)
        if scheduled+len(selected)>=max_queue or len(selected)>=max_new:
            break
    return selected


if __name__=="__main__":
    gate=load_manifest()
    print("Human approvals:",len(gate["approved"]),
          "rejections:",len(gate["rejected"]),
          "(read-only; no posts queued)")
