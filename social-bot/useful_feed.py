#!/usr/bin/env python3
"""One useful original per JST day, using the existing Buffer Free connection.

Owner opt-in: 2026-11-04. This is NOT the paused surreal or manga lane.
Edit useful-posts.json only. Never edit useful-state.json by hand.
Only current-day, explicitly automatic, checked content is eligible. No backlog.
A durable intent is committed BEFORE a Buffer mutation; ambiguous attempts are
held rather than retried. Old reservations, comics and original ledgers stay put.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
JST = timezone(timedelta(hours=9))
CHANNEL = "6ae94b74e29c69a171436d72"
KINDS = {"evergreen_explainer", "conversation_example", "market_close"}
STATE_PATH = HERE / "useful-state.json"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def stamp(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timezone required")
    return dt


def fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def payload_hash(post):
    return fingerprint(json.dumps(post, sort_keys=True, ensure_ascii=False))


def normalized(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text) if c.isalnum())


def close_copy(a, b):
    a, b = normalized(a), normalized(b)
    if a == b:
        return True
    if min(len(a), len(b)) < 24:
        return False
    aa = {a[i:i+3] for i in range(len(a)-2)}
    bb = {b[i:i+3] for i in range(len(b)-2)}
    return len(aa & bb) / len(aa | bb) >= 0.72


def validate(feed, state):
    if feed.get("schema_version") != 1 or type(feed.get("enabled")) is not bool:
        raise ValueError("Invalid useful feed")
    if feed.get("channel_id") != CHANNEL:
        raise ValueError("Wrong pinned account")
    if state.get("schema_version") != 1 or not isinstance(state.get("attempts"), dict):
        raise ValueError("Invalid permanent ledger")
    posts = feed.get("posts")
    if not isinstance(posts, list):
        raise ValueError("Posts must be a list")
    ids, days, texts = set(), set(), []
    for p in posts:
        pid, text = p.get("id"), p.get("text")
        if not isinstance(pid, str) or not re.fullmatch(r"xexp-useful-[a-z0-9-]+", pid):
            raise ValueError("Useful-only post ID required")
        if pid in ids or p.get("kind") not in KINDS:
            raise ValueError("Duplicate ID or forbidden kind")
        if p.get("delivery") not in ("automatic", "manual_only"):
            raise ValueError("Explicit delivery required")
        if p.get("trend_targeted") is not False:
            raise ValueError("Automated trending-topic posts are forbidden")
        if not isinstance(text, str) or not 12 <= len(text) <= 138:
            raise ValueError("Conservative Japanese length limit is 138 code points")
        if any(c in text for c in ("@", "＠", "#", "＃", "\x00")):
            raise ValueError("No mentions, trend tags or NUL in automatic content")
        if p["kind"] == "conversation_example" and not any(x in text for x in ("会話例", "たとえば")):
            raise ValueError("Invented dialogue must be marked as an example")
        due, checked, expiry = (stamp(p[k]) for k in ("publish_at", "checked_at", "valid_until"))
        if not checked <= due < expiry or due - checked > timedelta(hours=48):
            raise ValueError("Missing or stale editorial check")
        if p["kind"] == "market_close":
            if due - checked > timedelta(hours=12) or not p.get("market_session") or not p.get("sources"):
                raise ValueError("Market session, sources and recent check required")
        if not isinstance(p.get("basis"), str) or not p["basis"].strip():
            raise ValueError("Public, non-private editorial basis required")
        for url in p.get("sources", []):
            if not isinstance(url, str) or not url.startswith("https://"):
                raise ValueError("HTTPS sources required")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due.astimezone(JST).date().isoformat()):
            raise ValueError("Invalid day")
        day = due.astimezone(JST).date().isoformat()
        if p["delivery"] == "automatic":
            if day in days:
                raise ValueError("Only one automatic useful post per JST day")
            days.add(day)
        if any(close_copy(text, other) for other in texts):
            raise ValueError("Duplicate or near-duplicate manuscript")
        if pid in state["attempts"] and state["attempts"][pid].get("payload_sha256") not in (None, payload_hash(p)):
            raise ValueError("Cannot change an attempted post or its schedule")
        if pid in state["attempts"] and state["attempts"][pid].get("text_sha256") != fingerprint(text):
            raise ValueError("Cannot modify an attempted manuscript")
        ids.add(pid)
        texts.append(text)
    if not set(state["attempts"]).issubset(ids):
        raise ValueError("Never remove attempted manuscripts")
    return posts


def select(feed, state, now, live, prior_texts=()):
    posts = validate(feed, state)
    if not feed["enabled"]:
        return None
    today = now.astimezone(JST).date().isoformat()
    if any(a.get("day_jst") == today for a in state["attempts"].values()):
        return None
    if sum(p.get("status") == "scheduled" for p in live) >= 9:
        return None  # reserve one of the existing shared Free ten slots
    for p in posts:
        due = stamp(p["publish_at"])
        if p["delivery"] != "automatic" or p["id"] in state["attempts"]:
            continue
        if due.astimezone(JST).date().isoformat() != today:
            continue
        if stamp(p["checked_at"]) > now or not now + timedelta(minutes=15) < due < stamp(p["valid_until"]):
            continue
        if any(close_copy(p["text"], t) for t in list(prior_texts) + [r.get("text", "") for r in live]):
            continue
        if any(r.get("status") == "scheduled" and r.get("dueAt") and
               abs((stamp(r["dueAt"]) - due).total_seconds()) < 1800 for r in live):
            continue  # do not crowd or edit an existing reservation
        return p
    return None


def persist(state):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    subprocess.run(["git", "add", str(STATE_PATH)], check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        return
    subprocess.run(["git", "commit", "-m", "chore(x): persist useful-feed attempt [skip ci]"], check=True)
    for _ in range(4):
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
        if subprocess.run(["git", "push", "origin", "HEAD:main"]).returncode == 0:
            return
    raise RuntimeError("Cannot persist durable posting evidence; do not resubmit")


def main():
    feed = read(HERE / "useful-posts.json")
    state = read(STATE_PATH) if STATE_PATH.exists() else {"schema_version": 1, "attempts": {}}
    validate(feed, state)
    if "--validate" in sys.argv or not feed["enabled"]:
        print("Validated. No Buffer request.")
        return
    if os.environ.get("GITHUB_REF") != "refs/heads/main" or os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
        raise RuntimeError("Live submission only from main Actions")
    token = os.environ.get("BUFFER_API_KEY", "").strip()
    if not token:
        raise RuntimeError("Existing Buffer key unavailable; no fallback or purchase")
    from publish import choose_x_channel, list_existing, queue_one
    org, channel = choose_x_channel(token)
    if channel != CHANNEL:
        raise RuntimeError("Refusing other X account")
    live = list_existing(token, org, channel, include_due_at=True)
    by_id = {p["id"]: p for p in feed["posts"]}
    for pid, row in state["attempts"].items():
        matches = [r for r in live if r.get("text") == by_id[pid]["text"] and
                   (not row.get("buffer_id") or r.get("id") == row["buffer_id"])]
        if matches:
            r = matches[0]
            row.update(buffer_id=r["id"], provider_status=r["status"], due_at=r.get("dueAt"),
                       sent_at=r.get("sentAt"), checked_at=datetime.now(timezone.utc).isoformat())
    persist(state)
    if any(not a.get("buffer_id") for a in state["attempts"].values()):
        raise RuntimeError("Uncertain previous request: hold, inspect Buffer; never blind retry")
    prior = []
    for path, key in ((HERE / "experiments/posts.json", "experiments"), (HERE / "posts.json", "posts")):
        if path.exists():
            prior.extend(p.get("text", "") for p in read(path).get(key, []))
    now = datetime.now(timezone.utc)
    p = select(feed, state, now, live, prior)
    if p is None:
        print("No eligible current-day original (empty/expired/duplicate/crowded/already attempted).")
        return
    row = {"payload_sha256": payload_hash(p), "text_sha256": fingerprint(p["text"]), "day_jst": stamp(p["publish_at"]).astimezone(JST).date().isoformat(),
           "requested_due_at": p["publish_at"], "provider_status": "intent", "attempted_at": now.isoformat()}
    state["attempts"][p["id"]] = row
    persist(state)  # public durable write-ahead marker BEFORE external mutation
    current = read(HERE / "useful-posts.json")
    validate(current, state)
    if not current["enabled"]:
        row["provider_status"] = "cancelled_before_submission"
        persist(state)
        return
    receipt = queue_one(token, channel, {"id": p["id"], "kind": "text", "text": p["text"]},
                        mode="customScheduled", due_at_utc=stamp(p["publish_at"]).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                        return_post=True)
    if not isinstance(receipt, dict) or not receipt.get("id"):
        raise RuntimeError("Unknown provider outcome: hold; never resubmit")
    row.update(buffer_id=receipt["id"], provider_status="accepted_not_verified_published", due_at=receipt.get("dueAt"))
    persist(state)
    if not receipt.get("dueAt") or stamp(receipt["dueAt"]) != stamp(p["publish_at"]):
        raise RuntimeError("Buffer accepted ID but schedule unverified/mismatched; inspect, never resubmit")
    print("Buffer accepted:", receipt["id"], "dueAt:", receipt.get("dueAt"), "Actual X publication is not yet verified.")


if __name__ == "__main__":
    main()
