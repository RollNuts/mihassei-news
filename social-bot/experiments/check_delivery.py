#!/usr/bin/env python3
"""Low-cost, lossless Buffer delivery audit for the original X feed.

One listing + <=2 individual post detail queries per day. Provider statuses
and permalinks are NOT independently verified X reach or impressions.
"""
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
from publish import choose_x_channel, list_existing, gql, qstring  # noqa: E402
from probe_delivery import reconcile  # noqa: E402


def trusted_x_permalink(value):
    if not isinstance(value,str):
        return False
    u=urlsplit(value)
    sections=u.path.split("/")
    return (u.scheme=="https" and u.hostname in
            ("x.com","www.x.com","twitter.com","www.twitter.com")
            and u.username is None and u.password is None
            and len(sections)>=4 and sections[-2]=="status"
            and sections[-1].isdigit() and not u.query and not u.fragment)


def report(state, remote, previous=None):
    """Carry forward provider evidence ONLY for exact Buffer ID and sent state."""
    previous=previous if isinstance(previous,dict) else {}
    old={r.get("post_id"):r for r in previous.get("items",[])
         if isinstance(r,dict)}
    by_buffer={p.get("id"):p.get("status") for p in remote
               if p.get("status") in ("sent","scheduled")}
    rows=[]
    for pid,entry in state.get("queued",{}).items():
        bufid=entry.get("buffer_id")
        status=by_buffer.get(bufid,"not_visible_in_recent_buffer_list")
        row={"post_id":pid,"buffer_id":bufid,"buffer_status":status,
             "format":entry.get("format","unknown")}
        prev=old.get(pid) or {}
        if prev.get("buffer_id")==bufid:
            if isinstance(prev.get("last_link_probe_utc"),str):
                row["last_link_probe_utc"]=prev["last_link_probe_utc"]
            if status=="sent" and prev.get("buffer_status")=="sent":
                link=prev.get("buffer_external_link")
                if trusted_x_permalink(link):
                    row["buffer_external_link"]=link
                count=prev.get("buffer_asset_count")
                if type(count) is int and 0<=count<=4:
                    row["buffer_asset_count"]=count
                snapshot=prev.get("buffer_latest_impressions")
                if (isinstance(snapshot,dict) and
                        snapshot.get("provider")=="buffer_experimental_personal_api"):
                    row["buffer_latest_impressions"]=snapshot
        rows.append(row)
    return sorted(rows,key=lambda r:r["post_id"])


def recent_unknown_ids(state, rows, now=None):
    """Missing recent Buffer listing means review, never a presumed failure."""
    now=now or datetime.now(timezone.utc)
    unknown=[]
    for row in rows:
        if row["buffer_status"]!="not_visible_in_recent_buffer_list":
            continue
        stamp=state.get("queued",{}).get(row["post_id"],{}).get("queued_at_utc")
        if not isinstance(stamp,str):
            continue
        try:
            submitted=datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if submitted.tzinfo and timedelta(0)<=now-submitted<=timedelta(hours=72):
            unknown.append(row["post_id"])
    return unknown


def collect_missing_links(rows, state, inventory, channel_id, fetch_one,
                          now=None, max_probes=2):
    """Read at most two new provider permalinks; never create or update posts."""
    now=now or datetime.now(timezone.utc)
    if type(max_probes) is not int or not 0<=max_probes<=2:
        raise ValueError("Free Buffer daily audit cap exceeded")
    by_id={p["id"]:p for p in inventory.get("experiments",[])
           if isinstance(p,dict) and isinstance(p.get("id"),str)}
    candidates=[r for r in rows if r["buffer_status"]=="sent"
                and not r.get("buffer_external_link")
                and r["post_id"] in by_id
                and r["post_id"] in state.get("queued",{})]
    # Newest records take precedence over irrecoverable ancient posts.
    candidates.sort(key=lambda r:state["queued"][r["post_id"]]
                    .get("queued_at_utc",""),reverse=True)
    attempts=0
    for row in candidates:
        if attempts>=max_probes:
            break
        previous_attempt=row.get("last_link_probe_utc")
        if isinstance(previous_attempt,str):
            try:
                stamp=datetime.fromisoformat(previous_attempt)
            except ValueError:
                stamp=None
            if stamp and stamp.tzinfo and timedelta(0)<=now-stamp<timedelta(hours=22):
                continue
        attempts+=1
        row["last_link_probe_utc"]=now.isoformat()
        try:
            rec=state["queued"][row["post_id"]]
            details=reconcile(by_id[row["post_id"]],rec,
                              fetch_one(rec["buffer_id"]),channel_id)
            if details.get("buffer_status")=="sent":
                link=details.get("buffer_external_link")
                if trusted_x_permalink(link):
                    row["buffer_external_link"]=link
                count=details.get("buffer_asset_count")
                if type(count) is int and 0<=count<=4:
                    row["buffer_asset_count"]=count
        except (RuntimeError,ValueError,OSError):
            row["provider_link_probe_failed"]=True
    return attempts


def queue_health(inventory, state, remote, rows):
    posts=inventory.get("experiments",[])
    submitted=set(state.get("queued",{}))
    pending=[p for p in posts if p["id"] not in submitted]
    image_pending=sum(bool(p.get("visual")) for p in pending)
    counts=Counter(r.get("buffer_status") for r in rows)
    return {
        "original_inventory_total":len(posts),
        "original_ever_submitted":len(submitted),
        "original_unsubmitted":len(pending),
        "unsubmitted_original_images":image_pending,
        "buffer_currently_scheduled_total":sum(
            p.get("status")=="scheduled" for p in remote),
        "buffer_sent_original_records":counts.get("sent",0),
        "buffer_x_permalinks_seen":sum(
            bool(row.get("buffer_external_link")) for row in rows
            if row.get("buffer_status")=="sent"),
        "unsubmitted_inventory_below_14":len(pending)<14,
        "unsubmitted_images_below_2":image_pending<2,
        "note":"Provider status/link evidence is not independent X verification or reward-qualified impressions.",
    }


def main():
    cfg=json.loads((HERE/"config.json").read_text(encoding="utf-8"))
    state=json.loads((HERE/"state.json").read_text(encoding="utf-8"))
    token=os.environ.get("BUFFER_API_KEY","").strip()
    if not token:
        raise RuntimeError("Missing Buffer secret; cannot verify status")
    orgid,channelid=choose_x_channel(token)
    if cfg.get("channel_id")!=channelid:
        raise RuntimeError("Connected X channel differs from pinned Veripsa")
    live=list_existing(token,orgid,channelid)
    old={}
    if (HERE/"delivery.json").is_file():
        old=json.loads((HERE/"delivery.json").read_text(encoding="utf-8"))
    inventory=json.loads((HERE/"posts.json").read_text(encoding="utf-8"))
    rows=report(state,live,old)
    def fetch_one(postid):
        query=("query { post(input: {id: "+qstring(postid)
               +"}) { id channelId text status sentAt externalLink "
               +"assets { id mimeType } } }")
        return gql(token,query).get("post")
    attempted=collect_missing_links(rows,state,inventory,channelid,fetch_one)
    missing=recent_unknown_ids(state,rows)
    health=queue_health(inventory,state,live,rows)
    payload={
        "schema_version":1,
        "checked_at_utc":datetime.now(timezone.utc).isoformat(),
        "source":"buffer_free_posts_api_with_max_two_individual_reads",
        "limitations":"Buffer sent/permalinks are provider evidence only. Independent X publication, impressions and reward eligibility unverified.",
        "items":rows,
        "health":health,
        "buffer_post_detail_queries":attempted,
        "recent_unverified_experiment_ids":missing,
    }
    (HERE/"delivery.json").write_text(
        json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Buffer delivery state (not X impressions):",
          dict(Counter(r["buffer_status"] for r in rows)))
    print("Queue health:",health)
    if health["unsubmitted_inventory_below_14"]:
        print("::warning::Fewer than 14 unsubmitted X originals; replenish quality ideas.")
    if health["unsubmitted_images_below_2"]:
        print("::warning::Fewer than two unsubmitted original images; replenish visuals.")
    if missing:
        print("::warning::Recent Buffer IDs not visible; verify manually: "+", ".join(missing))
    return 0


if __name__=="__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("Buffer status audit failed safely:",str(error),file=sys.stderr)
        sys.exit(1)
