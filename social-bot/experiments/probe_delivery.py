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
MAX_STATUS_READS = 20
MAX_STATUS_BATCH = 10
MAX_METRICS_READS = 1


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


def fetch_status_batch(token, post_ids):
    """Fetch up to ten immutable IDs in ONE read-only GraphQL request.

    Buffer documents aliased lookups as an official quota-saving pattern.
    Do not silently fall back to per-ID queries on an API error.
    """
    if (not isinstance(post_ids, list) or
            not 1 <= len(post_ids) <= MAX_STATUS_BATCH or
            len(set(post_ids)) != len(post_ids) or
            any(not isinstance(pid, str) or not pid for pid in post_ids)):
        raise ValueError("Invalid bounded Buffer alias batch")
    fields = "id channelId text status sentAt externalLink assets { id mimeType }"
    aliases = [
        "p" + str(i) + ": post(input: {id: " + qstring(pid) + "}) { " +
        fields + " }"
        for i, pid in enumerate(post_ids)
    ]
    result = gql(token, "query { " + " ".join(aliases) + " }")
    if not isinstance(result, dict) or any(
            "p" + str(i) not in result for i in range(len(post_ids))):
        raise RuntimeError("Buffer alias result incomplete")
    return {pid: result["p" + str(i)] for i, pid in enumerate(post_ids)}


def mature_sent(sent_at, now):
    try:
        timestamp = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        return False
    return (timestamp.tzinfo is not None and
            timedelta(hours=12) <= now-timestamp <= timedelta(hours=96))


JST = timezone(timedelta(hours=9))
DAILY_SLOT_GOAL = 7


def observed_original_sent_by_jst_day(rows, now=None, days=7):
    """Historical Buffer 'sent' receipts by JST day; incomplete lower bounds.

    Some statuses may be cached. Missing posts and unrelated X posts are
    not represented here, so zero never means zero actual X publications.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or type(days) is not int or not 1 <= days <= 14:
        raise ValueError("Invalid daily evidence window")
    today = now.astimezone(JST).date()
    counted = {}
    for row in rows:
        if row.get("buffer_status") != "sent":
            continue
        raw = row.get("buffer_sent_at_utc")
        if not isinstance(raw,str):
            continue
        try:
            stamp = datetime.fromisoformat(raw.replace("Z","+00:00"))
        except ValueError:
            continue
        if stamp.tzinfo is None or stamp > now:
            continue
        date_jst = stamp.astimezone(JST).date()
        if 0 <= (today-date_jst).days < days:
            counted[date_jst] = counted.get(date_jst,0) + 1
    return [
        {"date_jst":(today-timedelta(days=offset)).isoformat(),
         "observed_original_buffer_sent": counted.get(
             today-timedelta(days=offset),0),
         "configured_daily_slots":DAILY_SLOT_GOAL,
         "evidence":"incomplete_lower_bound_not_total_X_publications"}
        for offset in range(days-1,-1,-1)
    ]


def queue_health(inventory, state, rows, now=None):
    """Capacity signals from the original ledger, never fabricated X views.

    Some status rows are cached from prior audits. Do not call their counts
    "currently sent" or assert that provider URLs are independently verified.
    """
    posts=inventory.get("experiments",[])
    queued=set(state.get("queued",{}))
    pending=[p for p in posts if p["id"] not in queued]
    pending_images=sum(bool(p.get("visual")) for p in pending)
    return {
        "original_inventory_total":len(posts),
        "original_ever_accepted_by_buffer":len(queued),
        "original_unsubmitted":len(pending),
        "unsubmitted_original_images":pending_images,
        "provider_sent_last_known":sum(
            r.get("buffer_status")=="sent" for r in rows),
        "provider_permalinks_last_known":sum(
            r.get("buffer_status")=="sent" and bool(r.get("buffer_external_link"))
            for r in rows),
        "unsubmitted_inventory_below_14":len(pending)<14,
        "unsubmitted_images_below_2":pending_images<2,
        "observed_original_sent_by_jst_day":
            observed_original_sent_by_jst_day(rows,now=now),
        "note":"Daily counts are incomplete lower bounds of Buffer-sent originals; cached statuses may be old. Neither provider sent nor permalink independently verifies X reach.",
    }


def build(inventory, state, channel_id, fetch, fetch_metrics=None,
          previous=None, now=None, fetch_batch=None):
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
    bulk = {}
    batch_failures = set()
    status_requests = 0
    if fetch_batch is not None:
        for offset in range(0, len(chosen), MAX_STATUS_BATCH):
            group = chosen[offset:offset + MAX_STATUS_BATCH]
            ids = [state["queued"][item["id"]]["buffer_id"] for item in group]
            status_requests += 1
            try:
                fetched = fetch_batch(ids)
                if not isinstance(fetched, dict) or any(
                        pid not in fetched for pid in ids):
                    raise RuntimeError("Buffer alias batch missing a post result")
                bulk.update(fetched)
            except (RuntimeError, ValueError, OSError):
                batch_failures.update(ids)
    for post in chosen:
        record = state["queued"][post["id"]]
        try:
            if fetch_batch is None:
                status_requests += 1
                remote = fetch(record["buffer_id"])
            else:
                if record["buffer_id"] in batch_failures:
                    raise RuntimeError("Buffer alias batch failed")
                remote = bulk.get(record["buffer_id"])
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
        previous_row=old.get(post["id"], {})
        old_metric=previous_row.get("buffer_latest_impressions")
        if (previous_row.get("buffer_id")==record["buffer_id"]
                and old_metric is not None
                and "buffer_latest_impressions" not in row):
            row["buffer_latest_impressions"]=old_metric
        # A temporary provider read error must not erase the fact that
        # Buffer previously returned a URL, but don't claim current sent.
        if (row["buffer_status"]=="unverified"
                and previous_row.get("buffer_id")==record["buffer_id"]):
            previous_link=x_link(previous_row.get("buffer_external_link"))
            if previous_link:
                row["previous_buffer_external_link"]=previous_link
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
                   "status_api_requests_this_run": status_requests,
                   "max_batched_status_api_requests": 2,
                   "metric_reads_this_run": metric_reads,
                   "max_metric_reads": MAX_METRICS_READS},
        "limitations": ("Buffer status and permalink are provider evidence, not "
                        "independent X page verification. Older cached rows are "
                        "not reread. Metrics are not exact 24h/72h impressions."),
        "items": items,
        "health": queue_health(inventory,state,items,now=now),
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
                 previous=previous,
                 fetch_batch=lambda ids:fetch_status_batch(key, ids))
    (HERE/"delivery.json").write_text(
        json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    for row in result["items"]:
        print("Buffer delivery",row["post_id"],row["buffer_status"],
              "assets",row.get("buffer_asset_count","unknown"),
              "X URL",row.get("buffer_external_link","unverified"))
    print("Read budget:", result["limits"]["post_reads_this_run"],
          "post statuses via",result["limits"]["status_api_requests_this_run"],
          "aliased API calls; and", result["limits"]["metric_reads_this_run"],
          "insights calls")
    health=result["health"]
    print("X original inventory capacity:",health)
    print("Observed Buffer-sent originals by JST day (incomplete):",
          health["observed_original_sent_by_jst_day"][-3:])
    if health["unsubmitted_inventory_below_14"]:
        print("::warning::Fewer than 14 unsubmitted X originals; replenish.")
    if health["unsubmitted_images_below_2"]:
        print("::warning::Fewer than two unused original X image scenes.")
    print("No media was reposted. Missing insights remain unmeasured; provider "
          "metric snapshots are NOT 24h or 72h X impression counts.")
    return 0


if __name__=="__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("Buffer evidence check skipped safely:",str(error),file=sys.stderr)
        sys.exit(1)
