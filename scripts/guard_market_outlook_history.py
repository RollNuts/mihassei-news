"""Refuse retrospective edits to published interim market observations.

This guard is intentionally separate from the immutable forecast-ledger guard.
New dated observations may be appended but previously published ones cannot
be silently replaced after a scenario change.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import subprocess
import sys

try:
    from .market_outlook import guard_market_outlook_history
except ImportError:
    from market_outlook import guard_market_outlook_history


def main():
    if len(sys.argv) != 2 or not re.fullmatch(r"[a-f0-9]{40}", sys.argv[1]):
        raise SystemExit("Usage: guard_market_outlook_history.py <40-char-base-sha>")
    repo = Path(__file__).resolve().parents[1]
    rev = sys.argv[1]
    subprocess.run(["git", "cat-file", "-e", rev + "^{commit}"], cwd=repo, check=True)
    source = "content/market_outlook.json"
    prev = subprocess.run(["git", "cat-file", "-e", rev + ":" + source],
                          cwd=repo, capture_output=True)
    if prev.returncode == 0:
        payload = subprocess.run(["git", "show", rev + ":" + source],
                                 cwd=repo, check=True, capture_output=True, text=True).stdout
        old = json.loads(payload)
    else:
        old = []
    current = json.loads((repo / source).read_text(encoding="utf-8"))
    checked = guard_market_outlook_history(old, current)
    print(f"Market outlook history guard: {checked} previous observations preserved")


if __name__ == "__main__":
    main()
