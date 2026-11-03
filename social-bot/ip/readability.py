#!/usr/bin/env python3
"""Conservative readability checks for short original serial comics.

These checks prevent avoidable walls of text, setup omissions and one-pose
rendering. They cannot certify that a joke is funny or understandable.
"""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MAX_PANEL_TEXT = 55
MAX_SPEECH_LINE = 28
MAX_SOCIAL_TEXT = 180
SUPPORTED_SHOTS = {"wide", "medium", "close", "detail"}
SUPPORTED_EFFECTS = {"", "spark", "glove-wrap", "tiny-touch"}
SUPPORTED_PROPS = {"", "tiny-machine"}


def verify(posts):
    series = sorted(
        (p for p in posts if p.get("series") == "nejimachi"),
        key=lambda p:p["episode"]
    )
    if not series:
        raise ValueError("Missing Nejimachi series")
    for post in series:
        n=post["episode"]
        panels=post["panels"]
        if len(panels)<2 or len(panels)>6:
            raise ValueError(f"Episode {n}: comic must have 2–6 panels")
        # First three were released before the editorial brief was adopted.
        # Never rewrite established episodes merely to meet newer checks.
        if n<4:
            continue
        if len(post["text"])>MAX_SOCIAL_TEXT:
            raise ValueError(f"Episode {n}: summary should be less than {MAX_SOCIAL_TEXT} characters")
        clarity=post.get("clarity")
        if not isinstance(clarity,dict):
            raise ValueError(f"Episode {n}: clarity object is required")
        for key in ("setup","problem","payoff"):
            explanation=clarity.get(key,"")
            if not isinstance(explanation,str) or not 9<=len(explanation)<=110:
                raise ValueError(f"Episode {n}: clarity.{key} must be concrete and concise")
        shots=[]
        first=panels[0]
        last=panels[-1]
        if not first.get("sign") or not first.get("scene") or not first.get("focus"):
            raise ValueError(f"Episode {n}: first panel must establish place and protagonist")
        if first["line"].strip()==last["line"].strip():
            raise ValueError(f"Episode {n}: ending only repeats the first panel")
        for i,p in enumerate(panels,1):
            s=p.get("line","").strip()
            if len(s)>MAX_PANEL_TEXT:
                raise ValueError(f"Episode {n} panel {i}: too much text ({len(s)} chars)")
            for line in s.split("\n"):
                if len(line)>MAX_SPEECH_LINE:
                    raise ValueError(f"Episode {n} panel {i}: long phone-screen dialogue line")
            shot=p.get("shot")
            if shot not in SUPPORTED_SHOTS:
                raise ValueError(f"Episode {n} panel {i}: unknown or unspecified camera shot")
            if p.get("effect","") not in SUPPORTED_EFFECTS:
                raise ValueError(f"Episode {n} panel {i}: unknown visual effect")
            if p.get("prop","") not in SUPPORTED_PROPS:
                raise ValueError(f"Episode {n} panel {i}: important prop not drawable")
            shots.append(shot)
        if len(panels)>=3 and len(set(shots))<2:
            raise ValueError(f"Episode {n}: change at least one shot to show an emotional beat")
        print(f"Readability OK: episode {n:02d}, {len(panels)} panels, {len(set(shots))} shot scales")
    return len(series)


if __name__ == "__main__":
    all_posts=json.loads((ROOT/"posts.json").read_text(encoding="utf-8"))["posts"]
    verify(all_posts)
