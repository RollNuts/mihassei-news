#!/usr/bin/env python3
"""Continuity checks for the canonical Nejimachi series.

These are deterministic guardrails, not a substitute for reading the story.
Canon facts are append-only; the script checks provenance and chronology.
"""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def load(path):
    return json.loads((HERE / path).read_text(encoding="utf-8"))


def payload_hash(post):
    """Stable snapshot of text, art instructions, and story metadata."""
    encoded = json.dumps(post, ensure_ascii=False, sort_keys=True, separators=(",",":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def check(loaded_posts=None):
    canon = load("ip/canon.json")
    ledger = load("ip/ledger.json")
    feed = loaded_posts if loaded_posts is not None else load("posts.json")["posts"]
    queue = load("state.json").get("submitted", {})
    series = sorted((p for p in feed if p.get("series") == "nejimachi"), key=lambda p:p["episode"])
    assert len(series) >= 1, "A serial needs at least one installment"
    assert canon["series_id"] == ledger["series_id"] == "nejimachi"
    assert [p["episode"] for p in series] == list(range(1,len(series)+1)), "Missing, repeated, or out-of-order episode"
    entries = ledger["entries"]
    assert len(entries) == len(series), "Story ledger must contain exactly the same episodes"
    assert [e["episode"] for e in entries] == [p["episode"] for p in series]
    seen_facts = set()
    seen_threads = set()
    closed_threads = set()
    previous_day = -1
    prev_order = 0
    previous_chapter = 1
    threads = {a["id"]: a for a in canon["open_mysteries"]}
    periods = set()
    for post, entry in zip(series, entries):
        num = post["episode"]
        assert post["id"] == entry["post_id"], f"Ledger/post ID conflict at #{num}"
        chapter = post["chapter"]
        assert isinstance(chapter, int) and previous_chapter <= chapter <= previous_chapter + (num > 1), f"Chapter gap/backtracking at #{num}"
        assert chapter <= ledger["chapter"], f"Chapter exceeds active chapter at #{num}"
        if num > 3:
            assert entry.get("chapter") == chapter, f"Ledger must record the chapter at #{num}"
        previous_chapter = chapter
        assert post["kind"] == "comic", f"Current serial delivery requires manga art #{num}"
        assert 2 <= len(post["panels"]) <= 6, f"Use 2-6 flexible panels at #{num}"
        assert len(post["text"]) <= 240
        if num >= 4:
            # Record each episode's understandable one-sentence visual premise.
            assert 12 <= len(post.get("episode_core","")) <= 130, f"Missing plain-language premise at #{num}"
        assert post["day"] == entry["day"] and post["order"] == entry["order"]
        assert post["location"] == entry["location"], f"Location conflict at #{num}"
        assert post["characters"] == entry["characters"], f"Cast conflict at #{num}"
        day_number = int(post["day"].removeprefix("D"))
        assert day_number >= previous_day
        if day_number == previous_day:
            assert post["order"] > prev_order, "Time moved backward inside a day"
        else:
            assert post["order"] == 1, "A new day starts at order 1"
        previous_day, prev_order = day_number, post["order"]
        assert post["location"] in canon["locations"]
        assert canon["locations"][post["location"]]["introduced_in"] <= num
        assert len(post["characters"]) == len(set(post["characters"]))
        for who in post["characters"]:
            assert who in canon["characters"], f"Unknown character: {who}"
            assert canon["characters"][who]["introduced_in"] <= num, f"Premature character: {who}"
        for fact in post["canon_refs"]:
            assert fact in canon["facts"], f"Unknown canon fact {fact}"
            assert canon["facts"][fact]["introduced_in"] <= num, f"Future fact referenced in #{num}: {fact}"
        for fact in entry["established"]:
            assert fact in post["canon_refs"], f"Episode #{num} did not cite fact it introduces: {fact}"
            assert fact not in seen_facts, f"Duplicate first introduction: {fact}"
            assert canon["facts"][fact]["introduced_in"] == num, f"Wrong canonical introduction: {fact}"
            seen_facts.add(fact)
        assert len(entry["event"]) >= 12 and len(entry["lasting_effect"]) >= 10
        assert post["location"] in canon["locations"], "Unknown setting location"
        actions=set()
        for p in post["panels"]:
            assert isinstance(p,dict), f"Invalid panel in #{num}"
            assert p.get("shot","medium") in {"wide","medium","close"}, f"Bad camera shot in #{num}"
            assert p.get("prop","none") in {"none","bolt","ticket","lamp","cup"}, f"Bad visual prop in #{num}"
            assert isinstance(p.get("silent",False), bool), f"Silent flag must be boolean at #{num}"
            if p.get("silent",False):
                assert p.get("line","") == "", f"Silent panel has dialogue in #{num}"
                assert p.get("mood") in {"happy","sad","sleepy","confused","wink"} or p.get("prop","none") != "none", f"Silent panel has no visible emotional or object cue in #{num}"
            else:
                assert isinstance(p.get("line"),str) and 2 <= len(p["line"].strip()) <= 65, f"Missing or too long dialogue in #{num}"
            if num >= 4:
                action=p.get("action","").strip()
                assert 4 <= len(action) <= 60, f"Missing concrete action for panel in #{num}"
                assert action not in actions, f"Same visible action duplicated in #{num}"
                actions.add(action)
            assert p["scene"] in canon["locations"], "Scene is not in the canon"
            assert canon["locations"][p["scene"]]["introduced_in"] <= num
            assert p["focus"] in post["characters"], "An unlisted character appears"
            if p.get("secondary"):
                assert p["secondary"] in post["characters"] and p["secondary"] != p["focus"]
            assert p.get("mood","plain") in {"sleepy","plain","confused","sad","happy","wink"}
            assert len(p.get("sign","")) <= 15
        for tid in entry.get("threads_opened",[]):
            assert tid in threads and tid not in seen_threads, f"Unknown or duplicate mystery: {tid}"
            seen_threads.add(tid)
        for tid in entry.get("threads_closed",[]):
            assert tid in seen_threads and tid not in closed_threads
            assert num >= threads[tid]["can_reveal_after"], "Mystery resolved prematurely"
            closed_threads.add(tid)
        lock = queue.get(post["id"], {})
        if lock:
            frozen = lock.get("content_sha256")
            assert frozen, f"Existing serial queued without canon snapshot: {post['id']}"
            assert frozen == payload_hash(post), f"Queued episode modified: {post['id']}"

    assert ledger["chapter"] == previous_chapter, "Current chapter is out of sync"
    assert set(canon["facts"]) == {
        *[fid for fid,f in canon["facts"].items() if f["introduced_in"] == 0],
        *seen_facts,
    }, "Unintroduced facts in canon (add entry in story ledger)"
    assert seen_threads == set(threads), "Mystery declarations must correspond to an opening episode"
    for thread in threads.values():
        if thread["state"] == "unresolved":
            assert thread["id"] not in closed_threads
        else:
            assert thread["id"] in closed_threads
    print(f"Canon OK: {len(series)} episodes, {len(seen_facts)} introduced facts, "
          f"{len(seen_threads)-len(closed_threads)} open mysteries")
    return series


if __name__ == "__main__":
    check()
