"""Prevent retroactive rewrites of published serial episodes and market predictions.

GitHub Actions calls this with the first parent of the evaluated commit:
    python3 scripts/guard_serial_history.py 'HEAD^1'

The original forecast is immutable; only later, evidence-backed verification
may be added. No external API, database, or new runtime dependency.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

if __package__:
    from .forecast_ledger import _verified_outcome
else:
    from forecast_ledger import _verified_outcome


def _unique_map(items):
    if not isinstance(items, list):
        raise ValueError("Serial archive must be a list")
    mapped = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("slug"), str):
            raise ValueError("Invalid series identity")
        if item["slug"] in mapped:
            raise ValueError("Duplicate series identity")
        mapped[item["slug"]] = item
    return mapped


def _compare_market_episode(before, after, label, now):
    frozen_before = {k: v for k, v in before.items() if k != "forecast"}
    frozen_after = {k: v for k, v in after.items() if k != "forecast"}
    if frozen_before != frozen_after:
        raise ValueError(f"{label}: previously published market article changed")

    old_fc = before.get("forecast")
    new_fc = after.get("forecast")
    if not isinstance(old_fc, dict) or not isinstance(new_fc, dict):
        raise ValueError(f"{label}: missing forecast")
    locked = lambda fc: {k: v for k, v in fc.items() if k not in ("status", "result")}
    if locked(old_fc) != locked(new_fc):
        raise ValueError(f"{label}: original forecast or method changed")
    if old_fc == new_fc:
        return

    old_status = old_fc.get("status", "未検証")
    new_status = new_fc.get("status", "未検証")
    if old_status == "検証済み":
        raise ValueError(f"{label}: verified result must stay immutable")
    if old_status not in ("未検証", "検証中"):
        raise ValueError(f"{label}: invalid old status")
    if old_fc.get("result") is not None:
        raise ValueError(f"{label}: previous unverified forecast had a result")
    if new_status == "検証中":
        if new_fc.get("result") is not None:
            raise ValueError(f"{label}: pending prediction cannot claim a result")
        return
    if new_status != "検証済み":
        raise ValueError(f"{label}: forecast review cannot be reset")
    result = _verified_outcome(new_fc)
    verified = date.fromisoformat(result["verified_on"])
    reviewed = date.fromisoformat(new_fc["reviewed_on"])
    if verified < reviewed or verified > now:
        raise ValueError(f"{label}: verification date outside allowed period")


def guard_serial_history(before, after, *, today=None):
    """Existing chapters must remain a prefix; no deletion or rewriting."""
    today = today or datetime.now(ZoneInfo("Asia/Tokyo")).date()
    old = _unique_map(before)
    new = _unique_map(after)
    if not old.keys() <= new.keys():
        raise ValueError("Previously published series was removed")
    checked = 0
    for slug, original in old.items():
        updated = new[slug]
        if original.get("kind") != updated.get("kind"):
            raise ValueError(f"{slug}: kind changed")
        old_eps = original.get("episodes")
        new_eps = updated.get("episodes")
        if not isinstance(old_eps, list) or not isinstance(new_eps, list):
            raise ValueError(f"{slug}: missing episodes")
        if len(new_eps) < len(old_eps):
            raise ValueError(f"{slug}: published episodes removed")
        for i, (old_ep, new_ep) in enumerate(zip(old_eps, new_eps), 1):
            label = f"{slug} episode {i}"
            if old_ep.get("slug") != new_ep.get("slug"):
                raise ValueError(f"{label}: published chapter order or identity changed")
            if original["kind"] == "fiction":
                if old_ep != new_ep:
                    raise ValueError(f"{label}: published mystery rewritten")
            else:
                _compare_market_episode(old_ep, new_ep, label, today)
            checked += 1
    return checked


def main():
    if len(sys.argv) != 2 or not re.fullmatch(r"(?:[a-f0-9]{7,40}|HEAD\^1)", sys.argv[1]):
        raise SystemExit("Usage: guard_serial_history.py <base-commit-sha|HEAD^1>")
    repo = Path(__file__).resolve().parents[1]
    original = subprocess.run(
        ["git", "show", sys.argv[1] + ":content/serials.json"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    before = json.loads(original)
    after = json.loads((repo / "content/serials.json").read_text(encoding="utf-8"))
    checked = guard_serial_history(before, after)
    print(f"Serial history guard: {checked} earlier episodes unchanged or validly reviewed")


if __name__ == "__main__":
    main()
