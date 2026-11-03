#!/usr/bin/env python3
"""Validate the existing free social queue plus serialized original manga."""
import json
from pathlib import Path
from ip.audit import check as canon_check
from ip.readability import verify as readability_check

ROOT=Path(__file__).resolve().parent
data=json.loads((ROOT/"posts.json").read_text(encoding="utf-8"))
posts=data["posts"]
assert 1<=len(posts)<=3000,"Post count out of bounds"
ids,texts=set(),set()
for post in posts:
    assert isinstance(post["id"],str) and post["id"].isascii(),post["id"]
    assert all(c.isalnum() or c=="-" for c in post["id"]),post["id"]
    assert post["id"] not in ids,"Duplicate post ID"
    ids.add(post["id"])
    assert post["kind"] in ("text","comic"),post["id"]
    content=post["text"].strip()
    assert 10<=len(content)<=240,post["id"]+" invalid post length"
    assert content not in texts,"Duplicate content"
    texts.add(content)
    if post["kind"]!="comic":
        continue
    if post.get("series")=="nejimachi":
        assert 2<=len(post["panels"])<=6,post["id"]
    else:
        assert len(post.get("panels",[]))==4,post["id"]
        assert all(isinstance(x,str) and 2<=len(x)<=36 for x in post["panels"]),post["id"]
        assert post["motif"] in {"star","alien","vampire","angel","nessie","dinosaur"}
    img=ROOT/"assets"/(post["id"]+".png")
    assert img.is_file(),"Comic PNG missing: "+str(img)
    assert img.stat().st_size<2_000_000,"Comic PNG larger than 2MB: "+str(img)

canon=canon_check(posts)
readability_check(posts)
print(f"Validated {len(posts)} posts; {sum(p['kind']=='comic' for p in posts)} comics; "
      f"{len(canon)} serialized episodes with 2-6 panels.")
