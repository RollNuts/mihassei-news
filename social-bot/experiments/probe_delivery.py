#!/usr/bin/env python3
"""Read-only Buffer delivery check for original X posts. No posts are created."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
from publish import choose_x_channel, gql, qstring
from dispatch import publication_text, validate

STATUSES={"scheduled","sending","sent","error","draft","needs_approval"}


def x_link(value):
    if not isinstance(value,str):
        return None
    u=urlsplit(value)
    if u.scheme!="https" or u.hostname not in ("x.com","www.x.com","twitter.com","www.twitter.com"):
        return None
    if "/status/" not in u.path:
        return None
    return urlunsplit((u.scheme,u.netloc,u.path,"",""))


def reconcile(post, record, remote, channel_id):
    result={"post_id":post["id"],"buffer_id":record["buffer_id"],
            "format":post["format"],"visual":bool(post.get("visual")),
            "buffer_status":"unverified"}
    if not isinstance(remote,dict):
        result["reason"]="post_not_returned"
        return result
    if (remote.get("id")!=record["buffer_id"]
            or remote.get("channelId")!=channel_id
            or remote.get("text")!=publication_text(post)):
        result["reason"]="provider_id_channel_or_text_mismatch"
        return result
    status=remote.get("status")
    if status not in STATUSES:
        result["reason"]="unknown_buffer_status"
        return result
    result["buffer_status"]=status
    assets=remote.get("assets")
    if isinstance(assets,list):
        result["buffer_asset_count"]=len(assets)
        if result["visual"] and not any(
            isinstance(a,dict) and str(a.get("mimeType","")).startswith("image/")
            for a in assets):
            result["media_review_needed"]="buffer_image_asset_missing"
    if status=="sent":
        if remote.get("sentAt"):
            result["buffer_sent_at_utc"]=remote["sentAt"]
        url=x_link(remote.get("externalLink"))
        if url:
            result["buffer_external_link"]=url
    return result


def build(inventory, state, channel_id, fetch):
    posts=validate(inventory,{"schema_version":1,
        "channel_id":channel_id,"max_queue_size":3,
        "max_new_per_run":2},state)
    registered=[p for p in posts if p["id"] in state["queued"]]
    if len(registered)>30:
        raise ValueError("Bounded Buffer delivery check exceeded 30 posts")
    report={"schema_version":1,
        "checked_at_utc":datetime.now(timezone.utc).isoformat(),
        "source":"buffer_post_by_saved_id_readonly",
        "limitations":"Buffer statuses/URLs are provider evidence; they are not independent X verification or X impression counts.",
        "items":[]}
    for post in registered:
        rec=state["queued"][post["id"]]
        try:
            remote=fetch(rec["buffer_id"])
            row=reconcile(post,rec,remote,channel_id)
        except (RuntimeError,ValueError,OSError):
            row={"post_id":post["id"],"buffer_id":rec["buffer_id"],
                 "format":post["format"],"visual":bool(post.get("visual")),
                 "buffer_status":"unverified","reason":"provider_read_failed"}
        report["items"].append(row)
    report["recent_unverified_experiment_ids"]=[
        item["post_id"] for item in report["items"]
        if item["buffer_status"] not in ("scheduled","sent")]
    return report


def main():
    config=json.loads((HERE/"config.json").read_text(encoding="utf-8"))
    key=os.environ.get("BUFFER_API_KEY","").strip()
    if not key:
        print("No Buffer key; keeping last known delivery state")
        return 0
    _,channel=choose_x_channel(key)
    if channel!=config["channel_id"]:
        raise RuntimeError("Pinned Buffer X channel mismatch")
    inventory=json.loads((HERE/"posts.json").read_text(encoding="utf-8"))
    state=json.loads((HERE/"state.json").read_text(encoding="utf-8"))
    def fetch(postid):
        query="query { post(input: {id: "+qstring(postid)+"}) { id channelId text status sentAt externalLink assets { id mimeType } } }"
        return gql(key,query).get("post")
    result=build(inventory,state,channel,fetch)
    (HERE/"delivery.json").write_text(
        json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    for row in result["items"]:
        print("Buffer delivery",row["post_id"],row["buffer_status"],
              "assets",row.get("buffer_asset_count","unknown"),
              "X URL",row.get("buffer_external_link","unverified"))
    print("No media was reposted; no X metrics inferred.")
    return 0


if __name__=="__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("Buffer evidence check skipped safely:",str(error),file=sys.stderr)
        sys.exit(1)
