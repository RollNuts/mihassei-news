#!/usr/bin/env python3
"""Live Buffer-Free queue feeder for short ORIGINAL X experiments.

This is intentionally independent of the unapproved canonical manga pipeline:
user authorized ongoing posting of original short-form jokes on 2026-11-03.
It uses the existing BUFFER_API_KEY GitHub Secret, not X paid API.

Execute via GitHub Actions on main ONLY. Never create/delete/repost any
unrelated Buffer entry, including old draft-only comic previews.
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from publish import choose_x_channel, list_existing, queue_one  # noqa: E402
from visuals import SCENES  # noqa: E402

FORMATS = {"surreal_text", "character_moment"}
ANGLES = {"everyday_satire"}
MAX_CHARS = 180
MECHANISMS = {
    "inverted_expectation", "self_reference", "literal_wordplay",
    "physical_scale", "meta_commentary", "visual_reveal",
    "dry_dialogue", "small_action", "delayed_response",
    "perspective_flip", "role_reversal", "unexpected_kindness"
}
# Small relevant tags, explicitly assigned to some posts. No trend scraping.
ALLOWED_TAGS = {"surreal_text": "#シュールネタ",
                "character_moment": "#ネジまち"}


def publication_text(post):
    """Render the final Buffer text. No tag means a no-hashtag comparison."""
    text = post["text"]
    tag = post.get("hashtag")
    if tag is None:
        return text
    if tag != ALLOWED_TAGS.get(post.get("format")):
        raise ValueError("Only one relevant hashtag is allowed: " + post["id"])
    if "#" in text or "＃" in text:
        raise ValueError("Additional hashtags in body: " + post["id"])
    return text.rstrip() + "\n\n" + tag


def hashtag_variant(post):
    return "one_relevant_tag" if post.get("hashtag") else "no_tag"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def hash_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate(posts, config, state):
    if config.get("schema_version") != 1:
        raise ValueError("Unknown X experiment config")
    if posts.get("schema_version") != 1 or not isinstance(posts.get("experiments"), list):
        raise ValueError("Invalid experiment inventory")
    if state.get("schema_version") != 1 or not isinstance(state.get("queued"), dict):
        raise ValueError("Invalid experiment submission ledger")
    if not isinstance(config.get("channel_id"), str) or not config["channel_id"].strip():
        raise ValueError("Pinned Buffer X channel is required")
    if type(config.get("launch_first_visual_now", False)) is not bool:
        raise ValueError("Immediate original-visual launch must be a boolean")
    for name in ("max_queue_size", "max_new_per_run"):
        val = config.get(name)
        if type(val) is not int or val < 1 or val > 3:
            raise ValueError(f"{name} must be between 1 and 3 (free, no flooding)")
    ids, texts, orders = set(), set(), set()
    for post in posts["experiments"]:
        if not isinstance(post, dict):
            raise ValueError("Post entry must be object")
        pid, fmt, text, order = (
            post.get("id"), post.get("format"),
            post.get("text"), post.get("sequence")
        )
        if not isinstance(pid, str) or not pid.startswith("xexp-") or not pid.isascii():
            raise ValueError("Invalid experiment ID")
        if fmt not in FORMATS:
            raise ValueError(f"Unsupported experiment format: {fmt}")
        if not isinstance(text, str) or not 12 <= len(text) <= MAX_CHARS:
            raise ValueError(f"Invalid text length: {pid}")
        final_text = publication_text(post)
        if len(final_text) > MAX_CHARS:
            raise ValueError(f"Hashtag exceeds maximum post length: {pid}")
        if type(order) is not int or order < 1:
            raise ValueError("Sequence must be a positive integer")
        mechanism = post.get("mechanism")
        if mechanism is not None and mechanism not in MECHANISMS:
            raise ValueError("Unsupported comedy mechanism: " + str(mechanism))
        angle = post.get("angle")
        if angle is not None and (angle not in ANGLES or fmt != "surreal_text"):
            raise ValueError("Unrecognized X satire experiment angle: " + pid)
        visual = post.get("visual")
        if visual is not None and (fmt != "surreal_text" or SCENES.get(pid) != visual):
            raise ValueError("Unreviewed or character-comic visual is forbidden: " + pid)
        if "featured" in post and type(post["featured"]) is not bool:
            raise ValueError("Featured must be a boolean")
        if pid in ids or text in texts or order in orders:
            raise ValueError("Duplicated ID, content, or sequence")
        ids.add(pid); texts.add(text); orders.add(order)
        if pid in state["queued"]:
            record = state["queued"][pid]
            if record.get("text_sha256") != hash_text(text):
                raise ValueError("Previously queued post was changed: " + pid)
            pinned = record.get("published_text_sha256")
            if pinned is None and post.get("hashtag"):
                raise ValueError("Cannot add a hashtag to an already queued legacy post: " + pid)
            if pinned and pinned != hash_text(final_text):
                raise ValueError("Previously queued hashtag or public text changed: " + pid)
            if visual and not record.get("image_sha256"):
                raise ValueError("Previously queued visual lacks pinned image hash: " + pid)
            if visual:
                asset = HERE.parent / "assets" / (pid + ".png")
                if asset.is_file() and hashlib.sha256(asset.read_bytes()).hexdigest() != record["image_sha256"]:
                    raise ValueError("Previously queued visual image was changed: " + pid)
            if not record.get("buffer_id"):
                raise ValueError("Missing Buffer identifier in post history: " + pid)
    if not posts["experiments"]:
        raise ValueError("No original content to publish")
    return sorted(posts["experiments"], key=lambda x:x["sequence"])


def decide(posts, state, existing, max_queue, max_new):
    """Pure planning function. Recover exact sent/scheduled posts after interrupted git writes."""
    text_to_live = {}
    for post in existing:
        if post.get("status") in ("scheduled", "sent"):
            text_to_live.setdefault(post.get("text"), post)
    scheduled = sum(1 for p in existing if p.get("status") == "scheduled")
    recovered = []
    for post in posts:
        final_text = publication_text(post)
        if post["id"] not in state["queued"] and final_text in text_to_live:
            live = text_to_live[final_text]
            state["queued"][post["id"]] = {
                "buffer_id": live["id"],
                "text_sha256": hash_text(post["text"]),
                "published_text_sha256": hash_text(final_text),
                "hashtag_variant": hashtag_variant(post),
                "hashtag": post.get("hashtag"),
                "format": post["format"],
                "mechanism": post.get("mechanism"),
                **({"image_sha256": hashlib.sha256(
                    (HERE.parent / "assets" / (post["id"] + ".png")).read_bytes()
                ).hexdigest()} if post.get("visual") and
                    (HERE.parent / "assets" / (post["id"] + ".png")).is_file() else {}),
                "recovered_from_buffer": True
            }
            recovered.append(post["id"])
    # The schedule stays at one per day. Interleave distinct creative
    # mechanisms instead of queuing several almost identical work/office jokes.
    # One in five slots can introduce a newly curated 'featured' story from
    # deeper in the backlog. Previously submitted Buffer posts are untouched.
    to_queue = []
    remaining = [p for p in posts if p["id"] not in state["queued"]]
    post_by_id = {p["id"]: p for p in posts}
    visual_already_queued = any(
        post_by_id[pid].get("visual") for pid in state["queued"] if pid in post_by_id
    )
    previous_mechanisms = [
        post_by_id[pid].get("mechanism")
        for pid in state["queued"]
        if pid in post_by_id and post_by_id[pid].get("mechanism")
    ]
    while remaining and scheduled+len(to_queue)<max_queue and len(to_queue)<max_new:
        next_slot = len(state["queued"]) + len(to_queue) + 1
        preferred = "character_moment" if next_slot % 4 == 0 else "surreal_text"
        options = [p for p in remaining if p["format"] == preferred] or remaining
        # Rotate images after the first image too: odd eligible surreal slots
        # are visual candidates; even slots remain text/satire controls.
        # Existing pending slots and every fourth character moment stay intact.
        if not visual_already_queued or (preferred == "surreal_text"
                                          and next_slot % 2 == 1):
            visual_choices = ([p for p in options if p.get("visual")]
                              if visual_already_queued
                              else [p for p in remaining if p.get("visual")])
            if visual_choices:
                options = visual_choices
        # Controlled pilot: sample relatable satire in about one in four
        # free slots. Existing Buffer posts never change.
        if next_slot % 4 == 2 and preferred == "surreal_text":
            relatable = [p for p in options if p.get("angle") == "everyday_satire"]
            if relatable:
                options = relatable
        if next_slot % 5 == 0:
            spotlight = [p for p in options if p.get("featured") is True]
            if spotlight:
                options = spotlight
        if previous_mechanisms:
            recent = previous_mechanisms[-2:]
            alternatives = [
                p for p in options if p.get("mechanism") not in recent
            ]
            if alternatives:
                options = alternatives
        chosen = options[0]
        to_queue.append(chosen)
        remaining.remove(chosen)
        if chosen.get("visual"):
            visual_already_queued = True
        if chosen.get("mechanism"):
            previous_mechanisms.append(chosen["mechanism"])
    return to_queue, recovered, scheduled


def choose_one_time_visual_launch(posts, state, config):
    """Explicit owner-requested one-off image post; never runs twice per ID.

    Does not occupy another Buffer scheduled slot. Sent/scheduled posts are
    recovered by the regular exact-text lookup before choosing a launch.
    """
    if config.get("launch_first_visual_now") is not True:
        return None
    if any(row.get("image_sha256") for row in state["queued"].values()):
        return None
    return next((post for post in posts
                 if post.get("visual") and post.get("hashtag")
                 and post["id"] not in state["queued"]), None)


def save_state(state, path=None):
    path = Path(path or HERE/"state.json")
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def main():
    config = read_json(HERE/"config.json")
    inventory = read_json(HERE/"posts.json")
    state = read_json(HERE/"state.json")
    posts = validate(inventory, config, state)
    if config.get("experiment_publishing_enabled") is not True:
        print("X ORIGINAL TEXT EXPERIMENT OFF; no Buffer call.")
        return 0
    token = os.environ.get("BUFFER_API_KEY", "").strip()
    if not token:
        raise RuntimeError("BUFFER_API_KEY missing. No posts submitted.")
    org_id, channel_id = choose_x_channel(token)
    if channel_id != config["channel_id"]:
        raise RuntimeError("Buffer X account differs from pinned Veripsa. No posts submitted.")
    existing = list_existing(token, org_id, channel_id)
    plan, recovered, queued_count = decide(posts, state, existing,
              config["max_queue_size"], config["max_new_per_run"])
    if recovered:
        save_state(state)
    # The owner requested an immediate picture post, even if regular Buffer
    # slots are currently full. It is one original image only, never manga.
    # A successful accepted ID is persisted; no daily re-send after launch.
    launch = choose_one_time_visual_launch(posts, state, config)
    if launch:
        plan = [launch] + [p for p in plan if p["id"] != launch["id"]]
        plan = plan[:config["max_new_per_run"]]
    print("Current Buffer queued posts:", queued_count,
          "Recovered:",len(recovered),"New experiment posts:",len(plan),
          "Immediate original image:", launch["id"] if launch else "none")
    for post in plan:
        # Use the same proven Buffer Free createPost method as the existing bot.
        final_text = publication_text(post)
        image_sha256 = None
        if post.get("visual"):
            image = HERE.parent / "assets" / (post["id"] + ".png")
            if not image.is_file():
                raise RuntimeError("Visual PNG not generated before Buffer queue: " + post["id"])
            image_sha256 = hashlib.sha256(image.read_bytes()).hexdigest()
        mode = "shareNow" if launch and post["id"] == launch["id"] else "addToQueue"
        buffer_id = queue_one(token, channel_id, {
            "kind": "image" if image_sha256 else "text",
            "id": post["id"], "text": final_text
        }, mode=mode)
        state["queued"][post["id"]] = {
            "buffer_id": buffer_id,
            "text_sha256": hash_text(post["text"]),
            "published_text_sha256": hash_text(final_text),
            "hashtag_variant": hashtag_variant(post),
            "hashtag": post.get("hashtag"),
            "format": post["format"],
            "mechanism": post.get("mechanism"),
            "queued_at_utc": datetime.now(timezone.utc).isoformat(),
            "submission_mode": mode,
            **({"image_sha256": image_sha256} if image_sha256 else {}),
        }
        save_state(state)
        print("Original text queued:", post["id"])
    print("Buffer X experiment feed: accepted", len(plan),
          "new post(s); X API paid endpoint not used.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("EXPERIMENT SUBMISSION FAILED SAFELY:", str(exc), file=sys.stderr)
        sys.exit(1)
