#!/usr/bin/env python3
"""Read-only Buffer delivery check for original X posts. No posts are created."""
import json
import math
import os
import sys
from datetime import datetime, timezone, timedelta
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
    # Buffer can return an initial impressions=0 seconds after posting,
    # before real network performance has been ingested. Suppress any
    # near-immediate provider sample (even non-zero) as immature.
    if (stamp-sent).total_seconds() < 12*3600:
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


# Query budgets for the shared Buffer Free monthly allowance. This is invoked
# once daily, not by every refill or every main push.
MAX_STATUS_READS = 10
MAX_METRICS_READS = 2


def selected_posts(registered, state, previous, limit=MAX_STATUS_READS):
    previous_by_id = {
        r.get("post_id"): r for r in previous.get("items", [])
        if isinstance(r, dict) and isinstance(r.get("post_id"), str)
    }
    newest = sorted(registered, key=lambda p: (
        state["queued"][p["id"]].get("queued_at_utc") or
        state["queued"][p["id"]].get("queued_at") or "",
        p.get("sequence", 0)), reverse=True)
    unseen = [p for p in newest if p["id"] not in previous_by_id]
    unresolved = [p for p in reversed(newest)
                  if previous_by_id.get(p["id"], {}).get("buffer_status")
                  in ("scheduled", "sending", "error", "unverified", "needs_approval")]
    output, seen = [], set()
    for post in unseen[:7] + unresolved[:3] + unresolved[3:] + unseen[7:] + newest:
        if post["id"] not in seen:
            seen.add(post["id"])
            output.append(post)
    return output[:limit]


def mature_sent(sent_at, now):
    try:
        timestamp = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        return False
    return (timestamp.tzinfo is not None and
            timedelta(hours=12) <= now-timestamp <= timedelta(hours=96))


def build(inventory, state, channel_id, fetch, fetch_metrics=None,
          previous=None, now=None):
    now = now or datetime.now(timezone.utc)
    previous = previous if isinstance(previous, dict) else {}
    old = {r.get("post_id"): r for r in previous.get("items", [])
           if isinstance(r, dict) and isinstance(r.get("post_id"), str)}
    posts = validate(inventory, {"schema_version": 1, "channel_id": channel_id,
                     "max_queue_size": 9, "max_new_per_run": 7}, state)
    registered = [p for p in posts if p["id"] in state["queued"]]
    chosen = selected_posts(registered, state, previous)
    output = {}
    metric_reads = 0
    for post in chosen:
        record = state["queued"][post["id"]]
        try:
            remote = fetch(record["buffer_id"])
            row = reconcile(post, record, remote, channel_id)
            if (row["buffer_status"] == "sent" and fetch_metrics is not None and
                    metric_reads < MAX_METRICS_READS and
                    mature_sent(row.get("buffer_sent_at_utc"), now)):
                metric_reads += 1
                try:
                    sample = normalize_buffer_impressions(
                        fetch_metrics(record["buffer_id"]),
                        row["buffer_sent_at_utc"])
                    if sample is not None:
                        row["buffer_latest_impressions"] = sample
                except (RuntimeError, ValueError, OSError):
                    row["buffer_metrics_available"] = False
        except (RuntimeError, ValueError, OSError):
            row = {"post_id": post["id"], "buffer_id": record["buffer_id"],
                   "format": post["format"], "visual": bool(post.get("visual")),
                   "buffer_status": "unverified", "reason": "provider_read_failed"}
        row["last_buffer_check_utc"] = now.astimezone(timezone.utc).isoformat()
        old_metric = old.get(post["id"], {}).get("buffer_latest_impressions")
        if old_metric is not None and "buffer_latest_impressions" not in row:
            row["buffer_latest_impressions"] = old_metric
        output[post["id"]] = row
    for post in registered:
        pid = post["id"]
        if pid in output:
            continue
        record = state["queued"][pid]
        cached = old.get(pid)
        if isinstance(cached, dict) and cached.get("buffer_id") == record.get("buffer_id"):
            output[pid] = dict(cached)
        else:
            output[pid] = {"post_id": pid, "buffer_id": record["buffer_id"],
                           "format": post["format"], "visual": bool(post.get("visual")),
                           "buffer_status": "unverified",
                           "reason": "outside_bounded_daily_audit"}
    items = [output[p["id"]] for p in registered]
    return {
        "schema_version": 1,
        "checked_at_utc": now.astimezone(timezone.utc).isoformat(),
        "source": "buffer_post_by_saved_id_daily_bounded_readonly",
        "limits": {"post_reads_this_run": len(chosen),
                   "max_post_reads": MAX_STATUS_READS,
                   "metric_reads_this_run": metric_reads,
                   "max_metric_reads": MAX_METRICS_READS},
        "limitations": ("Buffer status and permalink are provider evidence, not "
                        "independent X page verification. Older cached rows are "
                        "not reread. Metrics are not exact 24h/72h impressions."),
        "items": items,
        "recent_unverified_experiment_ids": [
            item["post_id"] for item in items
            if item.get("buffer_status") not in ("sent", "scheduled")]
    }

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
    try:
        previous = json.loads((HERE/"delivery.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    result=build(inventory,state,channel,fetch,fetch_metrics,
                 previous=previous)
    (HERE/"delivery.json").write_text(
        json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    for row in result["items"]:
        print("Buffer delivery",row["post_id"],row["buffer_status"],
              "assets",row.get("buffer_asset_count","unknown"),
              "X URL",row.get("buffer_external_link","unverified"))
    print("Read budget:", result["limits"]["post_reads_this_run"],
          "post checks and", result["limits"]["metric_reads_this_run"],
          "insights checks")
    print("No media was reposted. Missing insights remain unmeasured; provider "
          "metric snapshots are NOT 24h or 72h X impression counts.")
    return 0


if __name__=="__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("Buffer evidence check skipped safely:",str(error),file=sys.stderr)
        sys.exit(1)
