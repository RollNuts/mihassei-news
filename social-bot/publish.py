#!/usr/bin/env python3
"""Queue original posts in Buffer (Free API). Never publishes without an explicit switch.

Environment:
  ENABLE_X_BOT=true             required to do anything
  BUFFER_API_KEY=...             GitHub Actions secret, not committed
  BUFFER_CHANNEL_ID=...          optional when exactly one X channel is connected
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from ip.audit import check as validate_canon, payload_hash
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
BASE_URL = "https://api.buffer.com"
ASSET_BASE_URL = (
    "https://raw.githubusercontent.com/RollNuts/mihassei-news/main/social-bot/assets"
)
TARGET_QUEUE = 3
MAX_NEW_PER_RUN = 2


def gql(token, query):
    body = json.dumps({"query": query}, ensure_ascii=False).encode("utf-8")
    req = Request(
        BASE_URL, body, method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "User-Agent": "doudemoii-sekai-social/1.0",
        },
    )
    try:
        with urlopen(req, timeout=30) as response:
            result = json.load(response)
    except HTTPError as exc:
        raise RuntimeError("Buffer HTTP error: " + str(exc.code)) from exc
    except URLError as exc:
        raise RuntimeError("Buffer network error: " + str(exc.reason)) from exc
    if result.get("errors"):
        safe = [str(x.get("message", "unknown"))[:150] for x in result["errors"]]
        raise RuntimeError("Buffer GraphQL error: " + "; ".join(safe))
    return result.get("data") or {}


def qstring(value):
    return json.dumps(value, ensure_ascii=False)


def choose_x_channel(token):
    account = gql(token, "query { account { organizations { id name } } }")
    orgs = ((account.get("account") or {}).get("organizations") or [])
    matches = []
    for org in orgs:
        orgid = org.get("id")
        if not orgid:
            continue
        result = gql(
            token,
            "query { channels(input: {organizationId: " + qstring(orgid)
            + "}) { id name service } }",
        )
        for channel in result.get("channels") or []:
            if channel.get("service") in ("twitter", "x"):
                matches.append((orgid, channel))
    selected = os.environ.get("BUFFER_CHANNEL_ID", "").strip()
    if selected:
        matches = [pair for pair in matches if pair[1].get("id") == selected]
    if len(matches) != 1:
        raise RuntimeError(
            "Connect exactly one X account in Buffer, or set BUFFER_CHANNEL_ID "
            "to pick one; found " + str(len(matches)) + " matching channels"
        )
    orgid, channel = matches[0]
    print("Selected X channel:", channel.get("name"), "(" + channel["id"] + ")")
    return orgid, channel["id"]


def list_existing(token, orgid, channelid):
    result = gql(token, (
        "query { posts(first: 100, input: {organizationId: " + qstring(orgid)
        + ", filter: {channelIds: [" + qstring(channelid)
        + "], status: [scheduled, sent]}}) { edges { node { id text status } }"
        " pageInfo { hasNextPage } } }"
    ))
    conn = result.get("posts") or {}
    if (conn.get("pageInfo") or {}).get("hasNextPage"):
        raise RuntimeError("Buffer post list exceeded 100; refusing uncertain dedupe")
    existing = [edge["node"] for edge in conn.get("edges") or []]
    return existing


def remote_asset_available(url):
    req = Request(url, method="HEAD", headers={"User-Agent":"doudemoii-sekai-social/1.0"})
    try:
        with urlopen(req, timeout=15) as r:
            return r.status == 200 and r.headers.get("Content-Type", "").startswith("image/")
    except (HTTPError, URLError):
        return False


def queue_one(token, channelid, post):
    extra = ""
    if post["kind"] == "comic":
        filename = post["id"] + ".png"
        local = HERE / "assets" / filename
        if not local.is_file():
            raise RuntimeError("Comic asset not generated: " + filename)
        url = ASSET_BASE_URL + "/" + filename
        if not remote_asset_available(url):
            raise RuntimeError("Comic asset not publicly available yet: " + filename)
        extra = ", assets: [{image: {url: " + qstring(url) + "}}]"
    mutation = (
        "mutation { createPost(input: {text: " + qstring(post["text"])
        + ", channelId: " + qstring(channelid)
        + ", schedulingType: automatic, mode: addToQueue"
        + extra
        + "}) { ... on PostActionSuccess { post { id } }"
        + " ... on MutationError { message } } }"
    )
    result = gql(token, mutation).get("createPost") or {}
    if not result.get("post", {}).get("id"):
        raise RuntimeError("Buffer did not accept post: " + str(result.get("message","unknown")))
    return result["post"]["id"]


def main():
    if os.environ.get("ENABLE_X_BOT", "").lower() != "true":
        print("SAFE MODE: ENABLE_X_BOT is not true. Nothing submitted.")
        return 0
    token = os.environ.get("BUFFER_API_KEY", "")
    if not token:
        print("SAFE MODE: BUFFER_API_KEY missing. Nothing submitted.")
        return 0

    all_posts = json.loads((HERE/"posts.json").read_text(encoding="utf-8"))["posts"]
    validate_canon(all_posts)
    # Continue the new IP serial in story order; never exhaust or mix in the old idea backlog.
    posts = sorted((p for p in all_posts if p.get("series") == "nejimachi"), key=lambda p: p["episode"])
    state_path = HERE/"state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    submitted = state.setdefault("submitted", {})
    orgid, channelid = choose_x_channel(token)
    existing = list_existing(token, orgid, channelid)
    queued = sum(1 for p in existing if p.get("status") == "scheduled")
    texts = {p.get("text"): p["id"] for p in existing}
    print("Buffer queue:", queued, "of target", TARGET_QUEUE)

    # Reconcile posts already submitted to Buffer even if a previous git push failed.
    for post in posts:
        if post["id"] not in submitted and post["text"] in texts:
            submitted[post["id"]] = {"buffer_id": texts[post["text"]], "recovered": True,
                                      "series": "nejimachi", "episode": post["episode"],
                                      "content_sha256": payload_hash(post)}
    count = 0
    for post in posts:
        if queued >= TARGET_QUEUE or count >= MAX_NEW_PER_RUN:
            break
        if post["id"] in submitted:
            saved = submitted[post["id"]]
            if saved.get("content_sha256") != payload_hash(post):
                raise RuntimeError("Queued serial episode has changed: " + post["id"])
            continue
        if len(post["text"]) > 240:
            raise RuntimeError("Tweet exceeds conservative 240-character cap: " + post["id"])
        buffer_id = queue_one(token, channelid, post)
        submitted[post["id"]] = {
            "buffer_id": buffer_id,
            "queued_at": datetime.now(timezone.utc).isoformat(),
            "series": "nejimachi",
            "episode": post["episode"],
            "content_sha256": payload_hash(post),
        }
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2)+"\n",encoding="utf-8")
        print("Queued:",post["id"])
        queued += 1
        count += 1

    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2)+"\n",encoding="utf-8")
    print("Done: added", count, "post(s); no X API billed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("Publish halted safely:", str(error), file=sys.stderr)
        sys.exit(1)
