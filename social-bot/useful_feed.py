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
KINDS = {"evergreen_explainer", "conversation_example", "market_close", "source_commentary"}
STATE_PATH = HERE / "useful-state.json"
# One shared Free queue; new useful originals may use the last free slot.
# All senders serialize on the same workflow concurrency group.
MAX_TOTAL_SCHEDULED = 10
MAX_DAILY_POSTS = 7
CRISIS_AUTO_PATTERN = re.compile(
    r"地震|津波|台風|豪雨|洪水|土砂災害|山火事|戦争|武力衝突|空爆|侵攻|砲撃|"
    r"ミサイル攻撃|テロ|大量死傷|死者|死亡者|負傷者|被災者|避難者|人質"
)


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


def editorial_issue(post):
    """Check review evidence, not whether a post is funny or insightful.

    An editor must read the manuscript and apply the README's content standard.
    The review should identify a lookup, comparison or decision the text resolves
    for its reader; this validator does not infer that meaning from word counts.
    A nonempty basis or an approval flag alone is not a completed review. Exact
    excerpts and a text hash make the editor's judgment inspectable and prevent
    an old approval from silently following a rewritten manuscript.
    """
    review = post.get("editorial_review")
    if review is None:
        return "editorial_review_missing"
    if not isinstance(review, dict):
        return "editorial_review_invalid"
    if review.get("text_sha256") != fingerprint(post["text"]):
        return "editorial_review_stale"
    if review.get("decision") == "rejected":
        return "editorial_review_rejected"
    if review.get("decision") != "approved":
        return "editorial_review_unapproved"

    excerpts = []
    for key, minimum in (("concrete_anchor", 4), ("added_value", 8)):
        excerpt = review.get(key)
        if (not isinstance(excerpt, str) or excerpt != excerpt.strip()
                or len(normalized(excerpt)) < minimum or excerpt not in post["text"]):
            return "editorial_review_ungrounded"
        excerpts.append(normalized(excerpt))
    if excerpts[0] == excerpts[1]:
        return "editorial_review_ungrounded"

    notes = []
    for key in ("reader_payoff", "why_now_or_save"):
        note = review.get(key)
        # Bare labels such as "有益", "共感できる" or "面白い" do not explain
        # what a reader gains. Length is a schema check, never a quality score.
        if not isinstance(note, str) or not 12 <= len(normalized(note)) <= 400:
            return "editorial_review_incomplete"
        notes.append(normalized(note))
    if notes[0] == notes[1]:
        return "editorial_review_incomplete"
    if post["kind"] != "conversation_example" and not post.get("sources"):
        return "source_evidence_missing"
    return None


def validate(feed, state, *, require_editorial=True):
    if feed.get("schema_version") != 1 or type(feed.get("enabled")) is not bool:
        raise ValueError("Invalid useful feed")
    if feed.get("channel_id") != CHANNEL:
        raise ValueError("Wrong pinned account")
    if state.get("schema_version") != 1 or not isinstance(state.get("attempts"), dict):
        raise ValueError("Invalid permanent ledger")
    posts = feed.get("posts")
    if not isinstance(posts, list):
        raise ValueError("Posts must be a list")
    ids, day_counts, texts = set(), {}, []
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
        sources = p.get("sources", [])
        if not isinstance(sources, list):
            raise ValueError("Sources must be a list of HTTPS URLs")
        for url in sources:
            if not isinstance(url, str) or not url.startswith("https://"):
                raise ValueError("HTTPS sources required")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due.astimezone(JST).date().isoformat()):
            raise ValueError("Invalid day")
        day = due.astimezone(JST).date().isoformat()
        if (pid not in state["attempts"] and p["delivery"] == "automatic"
                and CRISIS_AUTO_PATTERN.search(text)):
            raise ValueError(
                "Crisis-topic automatic X posts are forbidden; keep acute disasters, war, terrorism and mass-casualty events out of the automatic lane"
            )
        if pid not in state["attempts"] and p["delivery"] == "automatic":
            if expiry.astimezone(JST).date() != due.astimezone(JST).date():
                raise ValueError("New automatic manuscripts must expire on their publish day in JST")
            if p["kind"] == "source_commentary":
                source_date = p.get("source_date")
                if not isinstance(source_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", source_date):
                    raise ValueError("Source commentary requires its actual source_date (YYYY-MM-DD)")
                source_day = datetime.strptime(source_date, "%Y-%m-%d").date()
                if source_day > checked.astimezone(JST).date() or due - checked > timedelta(hours=12):
                    raise ValueError("Source commentary requires a non-future source date and a check within 12 hours")
            if require_editorial:
                reason = editorial_issue(p)
                # A recorded rejection may remain in the file without becoming
                # a publishing candidate. Missing/broken approvals fail CI.
                if reason not in (None, "editorial_review_rejected"):
                    raise ValueError(f"{pid}: {reason}")
        if p["delivery"] == "automatic":
            day_counts[day] = day_counts.get(day, 0) + 1
            if day_counts[day] > MAX_DAILY_POSTS:
                raise ValueError("No more than seven automatic useful posts per JST day")
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


def live_day_count(live, day_jst):
    """Count all observed X posts for one JST day, regardless of publishing lane."""
    seen = set()
    for row in live:
        if row.get("status") not in ("scheduled", "sent"):
            continue
        value = row.get("sentAt") if row.get("status") == "sent" else row.get("dueAt")
        if not value:
            value = row.get("dueAt") or row.get("sentAt")
        if not value:
            continue
        try:
            observed_day = stamp(value).astimezone(JST).date().isoformat()
        except (TypeError, ValueError):
            continue
        if observed_day == day_jst:
            seen.add(row.get("id") or ("anonymous", row.get("text"), value))
    return len(seen), seen


def select(feed, state, now, live, prior_texts=(), reasons=None):
    # Runtime selection records an exact review failure instead of turning a
    # missing approval into an unexplained empty queue. --validate is stricter.
    posts = validate(feed, state, require_editorial=False)
    reasons = reasons if reasons is not None else []
    if not feed["enabled"]:
        reasons.append({"reason": "disabled"})
        return None
    today = now.astimezone(JST).date().isoformat()
    if sum(p.get("status") == "scheduled" for p in live) >= MAX_TOTAL_SCHEDULED:
        reasons.append({"reason": "shared_free_queue_full"})
        return None  # Never exceed Buffer Free's shared ten scheduled posts.
    remote_today, remote_ids = live_day_count(live, today)
    local_only_today = sum(
        1 for row in state["attempts"].values()
        if row.get("day_jst") == today
        and row.get("provider_status") not in ("cancelled_before_submission",)
        and (not row.get("buffer_id") or row.get("buffer_id") not in remote_ids)
    )
    if remote_today + local_only_today >= MAX_DAILY_POSTS:
        reasons.append({"reason": "daily_x_cap_reached", "observed_or_intent_count": remote_today + local_only_today})
        return None
    for p in posts:
        due = stamp(p["publish_at"])
        reason = None
        if p["delivery"] != "automatic" or p["id"] in state["attempts"]:
            reason = "manual_or_already_attempted"
        elif due.astimezone(JST).date().isoformat() != today:
            reason = "not_current_jst_day"
        elif stamp(p["checked_at"]) > now:
            reason = "check_time_in_future"
        elif not now + timedelta(minutes=15) < due < stamp(p["valid_until"]):
            reason = "past_cutoff_or_expired"
        elif (review_reason := editorial_issue(p)) is not None:
            reason = review_reason
        elif any(close_copy(p["text"], t) for t in list(prior_texts) + [r.get("text", "") for r in live]):
            reason = "duplicate_or_near_duplicate"
        elif any(r.get("status") == "scheduled" and r.get("dueAt") and
                 abs((stamp(r["dueAt"]) - due).total_seconds()) < 1800 for r in live):
            reason = "within_30_minutes_of_existing_reservation"
        if reason:
            reasons.append({"post_id": p["id"], "reason": reason})
            continue
        return p
    if not posts:
        reasons.append({"reason": "no_manuscripts"})
    return None


def durable_state(state):
    """Compare evidence and decisions; observation freshness stays in run logs."""
    result = dict(state)
    result["attempts"] = {
        pid: {key: value for key, value in row.items() if key != "checked_at"}
        for pid, row in state["attempts"].items()
    }
    if "last_selection" in state:
        result["last_selection"] = {
            key: value for key, value in state["last_selection"].items()
            if key != "checked_at_utc"
        }
    return result


def persist(state):
    # Keep every intent/receipt/status change, without committing each recheck.
    if STATE_PATH.exists() and durable_state(read(STATE_PATH)) == durable_state(state):
        return
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
    validate(feed, state, require_editorial="--validate" in sys.argv)
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
            print("Useful feed observation:", json.dumps({"post_id": pid, **{
                key: row[key] for key in ("buffer_id", "provider_status", "due_at", "sent_at", "checked_at")
            }}, ensure_ascii=False))
    persist(state)
    if any(not a.get("buffer_id") for a in state["attempts"].values()):
        raise RuntimeError("Uncertain previous request: hold, inspect Buffer; never blind retry")
    prior = []
    for path, key in ((HERE / "experiments/posts.json", "experiments"), (HERE / "posts.json", "posts")):
        if path.exists():
            prior.extend(p.get("text", "") for p in read(path).get(key, []))
    now = datetime.now(timezone.utc)
    reasons = []
    p = select(feed, state, now, live, prior, reasons=reasons)
    state["last_selection"] = {
        "checked_at_utc": now.isoformat(),
        "scheduled_count": sum(r.get("status") == "scheduled" for r in live),
        "shared_queue_limit": MAX_TOTAL_SCHEDULED,
        "daily_x_cap": MAX_DAILY_POSTS,
        "observed_today_count": live_day_count(live, now.astimezone(JST).date().isoformat())[0],
        "selected_post_id": p["id"] if p else None,
        "reasons": reasons,
    }
    print("Useful feed selection:", json.dumps(state["last_selection"], ensure_ascii=False))
    persist(state)
    if p is None:
        print("No submission; exact reason is recorded in last_selection.")
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
