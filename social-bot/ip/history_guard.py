#!/usr/bin/env python3
"""Protect published canon and prior episodes against retroactive rewriting.

Checks the incoming branch against the GitHub event's base commit. Existing
story material is APPEND ONLY: corrections must be shown in later chapters.
No paid API, dependency, or privileged credential is required.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


class CanonHistoryError(ValueError):
    pass


def refuse(condition, message):
    if not condition:
        raise CanonHistoryError(message)


def old_is_subset(old, new, label):
    """Existing object keys/values must remain stable; new keys may be added."""
    refuse(isinstance(old, dict) and isinstance(new, dict), f"{label}: expected objects")
    for key, value in old.items():
        refuse(key in new, f"{label}: deleted key {key}")
        refuse(value == new[key], f"{label}: changed established {key}")


def unchanged_prefix(old, new, key, label):
    refuse(isinstance(old, list) and isinstance(new, list), f"{label}: expected arrays")
    refuse(len(new) >= len(old), f"{label}: deleted older material")
    for i, item in enumerate(old):
        refuse(new[i] == item, f"{label}: rewritten or reordered established item #{i+1}")


def check_history(before, after):
    """Pure function for no-network unit testing."""
    old_feed, old_canon, old_ledger, old_state = (before[k] for k in
                                                  ("posts","canon","ledger","state"))
    new_feed, new_canon, new_ledger, new_state = (after[k] for k in
                                                  ("posts","canon","ledger","state"))
    refuse(old_feed["brand"] == new_feed["brand"], "Original feed brand changed")
    unchanged_prefix(old_feed["posts"], new_feed["posts"], "id", "posts.json")
    unchanged_prefix(old_ledger["entries"], new_ledger["entries"], "episode", "ledger entries")
    refuse(old_ledger["series_id"] == new_ledger["series_id"], "Ledger switched series")

    old_is_subset(old_state.get("submitted",{}), new_state.get("submitted",{}),
                  "state.submitted")
    for key in ("series_id", "brand", "subtitle", "created_jst"):
        if key in old_canon:
            refuse(old_canon[key] == new_canon.get(key), f"Canon identity changed: {key}")
    for key in ("identity", "setting", "characters", "locations", "facts", "forbidden"):
        if key in old_canon:
            old_is_subset(old_canon[key], new_canon.get(key, {}), f"canon.{key}")

    # Mysteries may move unresolved -> resolved, but never be silently replaced
    # or revealed before the declared earliest episode.
    was = old_canon.get("open_mysteries", [])
    now = new_canon.get("open_mysteries", [])
    refuse(len(now) >= len(was), "Deleted a declared long-term mystery")
    for idx, mystery in enumerate(was):
        current = now[idx]
        for k, val in mystery.items():
            if k == "state":
                continue
            refuse(current.get(k) == val, f"Rewrote mystery {mystery['id']} field {k}")
        prev, new = mystery["state"], current.get("state")
        if prev != new:
            refuse(prev == "unresolved" and new == "resolved",
                   f"Invalid backwards change to mystery {mystery['id']}")
            completed = [
                ent["episode"] for ent in new_ledger["entries"]
                if mystery["id"] in ent.get("threads_closed",[])
            ]
            refuse(completed, f"Mystery {mystery['id']} resolved without episode")
            refuse(min(completed) >= mystery["can_reveal_after"],
                   f"Mystery {mystery['id']} resolved prematurely")

    new_chapter = new_ledger.get("chapter", 1)
    old_chapter = old_ledger.get("chapter", 1)
    refuse(isinstance(new_chapter, int) and old_chapter <= new_chapter <= old_chapter+1,
           "Chapter transition must advance by at most one and never backwards")
    ids = [item["id"] for item in new_feed["posts"]]
    refuse(len(set(ids)) == len(ids), "Duplicate post ID")
    print(f"History OK: {len(old_feed['posts'])} earlier posts and "
          f"{len(old_ledger['entries'])} earlier story events preserved")


def git_json(ref, path):
    try:
        result = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=REPO,
                                check=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as err:
        raise CanonHistoryError(f"Baseline unavailable: {ref}:{path}") from err
    return json.loads(result.stdout)


def main():
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event not in ("pull_request", "push"):
        print("No code change event; canonical history diff not required.")
        return 0
    base = os.environ.get("CANON_BASE_SHA", "")
    refuse(bool(re.fullmatch(r"[a-f0-9]{40}", base)),
           "Missing original GitHub event base SHA (refusing unchecked change)")
    refuse(base != "0"*40, "Null base SHA is not a verifiable history")
    paths = {
        "posts":"social-bot/posts.json",
        "canon":"social-bot/ip/canon.json",
        "ledger":"social-bot/ip/ledger.json",
        "state":"social-bot/state.json",
    }
    before = {key:git_json(base,filename) for key, filename in paths.items()}
    after = {key:json.loads((REPO/filename).read_text(encoding="utf-8"))
             for key,filename in paths.items()}
    check_history(before, after)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (CanonHistoryError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"CANON HISTORY REJECTED: {error}",file=sys.stderr)
        sys.exit(1)
