#!/usr/bin/env python3
"""Keep every committed Nejimachi PNG immutable (free Git-only verification).

New image files may be added. Existing pictures must never be silently
redrawn when the renderer, characters, or story preferences change.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


def changed_paths(base, head="HEAD"):
    if not re.fullmatch(r"[0-9a-f]{40}",base or ""):
        raise ValueError("Cannot verify historical manga art without a commit SHA")
    completed=subprocess.run(
        ["git","diff","--name-status",base,head,"--","social-bot/assets/"],
        cwd=ROOT,check=True,capture_output=True,text=True)
    return completed.stdout.splitlines()


def main():
    if os.environ.get("GITHUB_EVENT_NAME") not in ("push","pull_request"):
        return 0
    changes=changed_paths(os.environ.get("CANON_BASE_SHA",""))
    old_changes=[]
    added=[]
    for line in changes:
        fields=line.split("\t")
        status=fields[0]
        path=fields[-1]
        if not path.endswith(".png"):
            continue
        if status=="A":
            added.append(path)
        else:
            old_changes.append(line)
    if old_changes:
        raise RuntimeError("Existing art modified/deleted instead of appended: "+
                           "; ".join(old_changes[:10]))
    print(f"Art history OK: {len(added)} new PNG(s), zero historical image replacements.")


if __name__=="__main__":
    try:main()
    except Exception as e:
        print("ART HISTORY REJECTED:",str(e),file=sys.stderr)
        sys.exit(1)
