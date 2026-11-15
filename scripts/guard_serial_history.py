"""Prevent retroactive rewrites of published serial episodes and market predictions.

GitHub Actions passes the PR base SHA or, on a main push, the pre-push
commit SHA. That compares the complete change set rather than only HEAD^1:
    python3 scripts/guard_serial_history.py '<40-char-base-sha>'

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


def guard_photo_edition_history(before, after):
    """Freeze published contents and their PV evidence; reader typos may change."""
    def editions(data):
        if data is None:
            return {}
        if (not isinstance(data, dict) or data.get("schema_version") != 1
                or not isinstance(data.get("editions"), list)):
            raise ValueError("Invalid photo editions history")
        result, numbers = {}, set()
        for edition in data["editions"]:
            if (not isinstance(edition, dict) or not isinstance(edition.get("slug"), str)
                    or edition["slug"] in result or type(edition.get("number")) is not int
                    or edition["number"] in numbers):
                raise ValueError("Invalid/duplicate photo edition history identity")
            result[edition["slug"]] = edition
            numbers.add(edition["number"])
        return result

    old, new = editions(before), editions(after)
    fixed = ("number", "slug", "state", "legacy", "published_at", "features", "ranking")
    checked = 0
    for slug, original in old.items():
        if original.get("state") != "published":
            continue
        if slug not in new:
            raise ValueError(f"{slug}: published photo edition was removed or renamed")
        # Canonical JSON preserves numeric/boolean types and list order while
        # ignoring harmless object-key formatting changes.
        canonical = lambda edition: json.dumps({key: edition.get(key) for key in fixed},
                                               sort_keys=True, ensure_ascii=False,
                                               separators=(",", ":"), allow_nan=False)
        if canonical(original) != canonical(new[slug]):
            raise ValueError(f"{slug}: published photo contents or ranking evidence changed")
        checked += 1
    return checked


def main():
    if len(sys.argv) != 2 or not re.fullmatch(r"[a-f0-9]{40}", sys.argv[1]):
        raise SystemExit("Usage: guard_serial_history.py <40-char-base-commit-sha>")
    repo = Path(__file__).resolve().parents[1]
    original = subprocess.run(
        ["git", "show", sys.argv[1] + ":content/serials.json"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    before = json.loads(original)
    after = json.loads((repo / "content/serials.json").read_text(encoding="utf-8"))
    checked = guard_serial_history(before, after)
    print(f"Serial history guard: {checked} earlier episodes unchanged or validly reviewed")
    # Market dispatches, daily short-horizon forecasts, and intermediate
    # long-horizon outlooks are append-only. Old records can never be silently
    # rewritten when the conclusion or sentiment changes.
    for name in ("market_bulletins.json", "market_outlooks.json", "market_outlook.json"):
        path = "content/" + name
        old = subprocess.run(
            ["git", "show", sys.argv[1] + ":" + path],
            cwd=repo, capture_output=True, text=True,
        )
        older = json.loads(old.stdout) if old.returncode == 0 else []
        current_path = repo / path
        newer = json.loads(current_path.read_text(encoding="utf-8")) if current_path.exists() else []
        if not isinstance(older, list) or not isinstance(newer, list):
            raise ValueError(path + ": historical archive must be an array")
        if newer[:len(older)] != older:
            raise ValueError(path + ": published history cannot be rewritten or removed")
        print(f"Market history guard: {path}, {len(older)} existing entries preserved")
    # Use this existing CI history entrypoint; no additional job or service.
    path = "content/photo_editions.json"
    old = subprocess.run(["git", "show", sys.argv[1] + ":" + path],
                         cwd=repo, capture_output=True, text=True)
    previous = json.loads(old.stdout) if old.returncode == 0 else None
    current_path = repo / path
    current = json.loads(current_path.read_text(encoding="utf-8")) if current_path.exists() else None
    checked = guard_photo_edition_history(previous, current)
    print(f"Photo edition history guard: {checked} published contents and ranking snapshots preserved")


if __name__ == "__main__":
    main()
