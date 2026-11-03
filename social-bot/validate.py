#!/usr/bin/env python3
"""Fail-closed local validation for the no-cost X bot's editorial queue."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
data = json.loads((ROOT/"posts.json").read_text(encoding="utf-8"))
posts = data["posts"]
assert 1 <= len(posts) <= 3000, "Posts count out of bounds"
ids, texts = set(), set()
for post in posts:
    assert post["id"].isascii(), post["id"]
    assert all(ch.isalnum() or ch == "-" for ch in post["id"]), post["id"]
    assert post["id"] not in ids, "Duplicate ID"
    ids.add(post["id"])
    assert post["kind"] in ("text", "comic")
    text = post["text"].strip()
    assert 10 <= len(text) <= 240, post["id"] + " length invalid"
    assert text not in texts, "Duplicate text"
    texts.add(text)
    if post["kind"] == "comic":
        assert len(post.get("panels",[])) == 4, post["id"]
        assert all(isinstance(s,str) and 2 <= len(s) <= 36 for s in post["panels"])
        assert post["motif"] in {"star","alien","vampire","angel","nessie","dinosaur"}
        img = ROOT/"assets"/(post["id"]+".png")
        assert img.is_file(), "Missing rendered PNG: " + str(img)
        assert img.stat().st_size < 5_000_000, "Oversized PNG: " + str(img)
print("Validated",len(posts),"original posts;",
      sum(p["kind"]=="comic" for p in posts),"4-panel comics")
