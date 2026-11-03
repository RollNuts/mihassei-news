#!/usr/bin/env python3
"""Safely replace media ONLY on the two exact scheduled Nejimachi episodes.

The original 1/2 PNGs stay immutable on GitHub. This is an idempotent
one-time upgrade of their Buffer media to separately versioned hand-ink art.
Do not modify the post's text, scheduled dueAt, or publishing mode.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
sys.path.insert(0,str(ROOT))
from publish import choose_x_channel, gql, qstring, remote_asset_available

ASSET_ROOT="https://raw.githubusercontent.com/RollNuts/mihassei-news/main/social-bot/assets/"
TARGETS={
    "nejimachi-001-morning":"nejimachi-001-morning-ink-v1.png",
    "nejimachi-002-blue-sky":"nejimachi-002-blue-sky-ink-v1.png",
}
LOG=HERE/"ink_remaster_delivery.json"


def read_posts(token,org,channel):
    query=("query { posts(first: 100,input:{organizationId:"+qstring(org)
           +",filter:{channelIds:["+qstring(channel)
           +"],status:[scheduled,sent]}}){edges{node{id text status dueAt}}"
           +"pageInfo{hasNextPage}}}")
    obj=gql(token,query).get("posts") or {}
    if (obj.get("pageInfo") or {}).get("hasNextPage"):
        raise RuntimeError("Buffer queue pagination exceeds 100; refusing ambiguous state")
    return {e["node"]["id"]:e["node"] for e in obj.get("edges") or []}


def plan(feed,state,live):
    by_id={p["id"]:p for p in feed["posts"]}
    actions,skipped=[],[]
    for key,asset in TARGETS.items():
        post=by_id.get(key)
        sent=state.get("submitted",{}).get(key)
        if not post or not sent or post.get("series")!="nejimachi":
            raise RuntimeError("Canonical post/ledger record not found for "+key)
        buf=live.get(sent["buffer_id"])
        if not buf:
            skipped.append((key,"not-scheduled-or-sent"))
            continue
        if buf["text"]!=post["text"]:
            raise RuntimeError("Actual Buffer post text mismatched canonical story: "+key)
        if buf["status"]!="scheduled":
            skipped.append((key,"already-"+str(buf["status"])))
            continue
        if not buf.get("dueAt"):
            raise RuntimeError("No original dueAt; refusing schedule risk: "+key)
        if not asset.endswith("-ink-v1.png"):
            raise RuntimeError("Unexpected remaster asset")
        actions.append({"key":key,"buffer_id":sent["buffer_id"],
                        "text":post["text"],"due_at":buf["dueAt"],
                        "image_url":ASSET_ROOT+asset})
    return actions,skipped


def mutation(item):
    return ("mutation { editPost(input:{id:"+qstring(item["buffer_id"])
            +",text:"+qstring(item["text"])
            +",assets:[{image:{url:"+qstring(item["image_url"])
            +"}}]}) { __typename "
            + "... on PostActionSuccess { post { id text status dueAt } } "
            + "... on MutationError { message } } }")


def main():
    if os.environ.get("ENABLE_MEDIA_REMASTER")!="true":
        print("SAFE MODE: no remaster requested")
        return
    if LOG.exists():
        status=json.loads(LOG.read_text(encoding="utf-8"))
        if status.get("completed") is True:
            print("Already applied; no modifications")
            return
    token=os.environ.get("BUFFER_API_KEY","").strip()
    if not token:raise RuntimeError("Buffer key missing; no posts changed")
    feed=json.loads((ROOT/"posts.json").read_text(encoding="utf-8"))
    state=json.loads((ROOT/"state.json").read_text(encoding="utf-8"))
    org,channel=choose_x_channel(token)
    live=read_posts(token,org,channel)
    actions,skipped=plan(feed,state,live)
    for item in actions:
        if not remote_asset_available(item["image_url"]):
            raise RuntimeError("Remaster image is not publicly accessible: "+item["key"])
    print("Verified",len(actions),"exact eligible posts; skipped",len(skipped))
    completed=[]
    for item in actions:
        response=gql(token,mutation(item)).get("editPost") or {}
        post=response.get("post") or {}
        if response.get("__typename")!="PostActionSuccess":
            raise RuntimeError("Buffer rejected safe media edit for "+item["key"]+
                               ": "+str(response.get("message","unknown"))[:160])
        if post.get("id")!=item["buffer_id"] or post.get("text")!=item["text"] \
           or post.get("status")!="scheduled" or post.get("dueAt")!=item["due_at"]:
            raise RuntimeError("Buffer returned unexpected text/status/schedule: "+item["key"])
        completed.append({"key":item["key"],"buffer_id":item["buffer_id"],
                          "image_url":item["image_url"],"due_at_unchanged":True})
        print("Media upgraded:",item["key"])
    after=read_posts(token,org,channel)
    for item in actions:
        after_post=after.get(item["buffer_id"])
        if not after_post or after_post.get("text")!=item["text"] \
           or after_post.get("status")!="scheduled" \
           or after_post.get("dueAt")!=item["due_at"]:
            raise RuntimeError("Post-update verification rejected "+item["key"])
    result={"completed":True,"checked_at":datetime.now(timezone.utc).isoformat(),
            "changed":completed,"skipped":skipped,"note":"Only media replaced; text and dueAt unchanged"}
    LOG.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Verified media migration complete. No deletions and no re-queue.")


if __name__=="__main__":
    try:main()
    except Exception as ex:
        print("SAFE STOP: "+str(ex),file=sys.stderr)
        sys.exit(1)
