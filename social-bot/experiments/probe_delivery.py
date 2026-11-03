#!/usr/bin/env python3
"""Read-only Buffer delivery check for original X posts. No posts are created."""
import json
import math
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


def normalize_buffer_impressions(post_data, sent_at):
    """One experimental provider snapshot, NEVER a normalized 24/72h X sample."""
    if not isinstance(post_data,dict):
        return None
    updated=post_data.get("metricsUpdatedAt")
    if not isinstance(updated,str) or not updated:
        return None
    try:
        stamp=datetime.fromisoformat(updated.replace("Z","+00:00"))
        sent=datetime.fromisoformat(sent_at.replace("Z","+00:00"))
    except (ValueError,AttributeError):
        return None
    if stamp.tzinfo is None or sent.tzinfo is None or stamp<sent:
        return None
    for metric in post_data.get("metrics") or []:
        if not isinstance(metric,dict) or metric.get("type")!="impressions":
            continue
        val=metric.get("value")
        if (metric.get("unit")!="count" or isinstance(val,bool)
                or not isinstance(val,(int,float))
                or not math.isfinite(val) or val<0 or int(val)!=val):
            continue
        return {"impressions":int(val),
                "observed_at_utc":stamp.astimezone(timezone.utc).isoformat(),
                "provider":"buffer_experimental_personal_api",
                "window_hours":None}
    return None


def build(inventory, state, channel_id, fetch, fetch_metrics=None):
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
            if (row["buffer_status"]=="sent" and fetch_metrics
                    and row.get("buffer_sent_at_utc")):
                try:
                    snapshot=normalize_buffer_impressions(
                        fetch_metrics(rec["buffer_id"]), row["buffer_sent_at_utc"])
                    if snapshot is not None:
                        row["buffer_latest_impressions"]=snapshot
                except (RuntimeError,ValueError,OSError):
                    # Insights scope is optional; delivery status remains valid.
                    row["buffer_metrics_available"]=False
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
    def fetch_metrics(postid):
        query="query { post(input: {id: "+qstring(postid)+"}) { metrics { type value unit } metricsUpdatedAt } }"
        return gql(key,query).get("post")
    result=build(inventory,state,channel,fetch,fetch_metrics)
    (HERE/"delivery.json").write_text(
        json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    for row in result["items"]:
        print("Buffer delivery",row["post_id"],row["buffer_status"],
              "assets",row.get("buffer_asset_count","unknown"),
              "X URL",row.get("buffer_external_link","unverified"))
    print("No media was reposted. Missing insights remain unmeasured; provider "
          "metric snapshots are NOT 24h or 72h X impression counts.")
    return 0


if __name__=="__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("Buffer evidence check skipped safely:",str(error),file=sys.stderr)
        sys.exit(1)
