#!/usr/bin/env python3
"""One-shot, reversible safety pause for the first two unreviewed manga previews.

Both exact Buffer IDs AND text must match the canonical GitHub ledger.
Never edits user-created/other-channel posts, never deletes a post,
and never touches a previously sent post. Uses existing no-cost Buffer API.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from publish import choose_x_channel, list_existing
from retire_legacy_queue import move_to_draft

HERE=Path(__file__).resolve().parent
LOG_PATH=HERE/"ip"/"quality_held_posts.json"
TARGETS=("nejimachi-001-morning","nejimachi-002-blue-sky")


def plan(posts, submitted, live):
    by_id={p["id"]:p for p in posts}
    live_by_buffer={p["id"]:p for p in live}
    result=[]
    for post_id in TARGETS:
        story=by_id.get(post_id)
        recorded=submitted.get(post_id)
        if not story or not recorded or story.get("series")!="nejimachi":
            raise ValueError("Cannot verify original first two manga entries: "+post_id)
        buffer_id=recorded.get("buffer_id")
        if not buffer_id:
            raise ValueError("No Buffer ID recorded for "+post_id)
        found=live_by_buffer.get(buffer_id)
        if found is None:
            result.append((post_id,buffer_id,story["text"],"not_in_schedule"))
            continue
        if found.get("text") != story["text"]:
            raise ValueError("Buffer content diverged; refusing to edit "+post_id)
        status=found.get("status")
        if status=="scheduled":
            result.append((post_id,buffer_id,story["text"],"move_to_draft"))
        elif status=="sent":
            result.append((post_id,buffer_id,story["text"],"already_sent"))
        else:
            raise ValueError("Unexpected Buffer status for "+post_id+": "+str(status))
    return result


def main():
    if os.environ.get("PAUSE_UNREVIEWED_MANGA")!="true":
        print("SAFE MODE: No manga queue alterations requested.")
        return 0
    gate=json.loads((HERE/"ip"/"publication_quality_gate.json").read_text(encoding="utf-8"))
    if gate.get("new_reservations_enabled") is not False:
        raise ValueError("Quality gate not active; refusing queue migration")
    token=os.environ.get("BUFFER_API_KEY","").strip()
    if not token:
        raise ValueError("Buffer API key not set in GitHub Secrets")
    feed=json.loads((HERE/"posts.json").read_text(encoding="utf-8"))["posts"]
    state=json.loads((HERE/"state.json").read_text(encoding="utf-8"))["submitted"]
    org, channel=choose_x_channel(token)
    before=list_existing(token,org,channel)
    decisions=plan(feed,state,before)
    changed=[]
    for post_id,buffer_id,original,status in decisions:
        if status=="move_to_draft":
            move_to_draft(token,buffer_id,original)
            print("Moved existing queued preview to reversible Buffer draft:",post_id)
            changed.append((post_id,buffer_id))
        else:
            print("No change needed:",post_id,status)
    after=list_existing(token,org,channel)
    still_scheduled={p["id"] for p in after if p.get("status")=="scheduled"}
    if any(buf in still_scheduled for _,buf in changed):
        raise RuntimeError("A held manga preview is still scheduled: verification failed")
    LOG_PATH.write_text(json.dumps({
        "purpose":"Reversible hold on unapproved original manga designs",
        "checked_at_utc":datetime.now(timezone.utc).isoformat(),
        "new_queue_disabled":True,
        "decisions":[{"post_id":p,"buffer_id":b,"result":s} for p,b,_,s in decisions],
        "confirmed_removed_from_schedule":[p for p,b in changed]
    },ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Quality pause complete; no new posts were created.")
    return 0


if __name__=="__main__":
    try:sys.exit(main())
    except Exception as e:
        print("Quality hold refused risky changes:",str(e),file=sys.stderr)
        sys.exit(1)
