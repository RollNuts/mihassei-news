"""Bound the article bot's shared Buffer request allowance; no network calls."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Below the documented one-channel Free allowance. These count this bot only;
# the provider's response headers remain authoritative for shared account usage.
MAX_REQUESTS_24H = 30
MAX_REQUESTS_30D = 900
MAX_AUDIT_REQUESTS_24H = 7
AUDIT_MONTHLY_RESERVE = 210


def instant(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def read(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("requests"), list):
        raise RuntimeError("Invalid Buffer budget evidence; no request allowed")
    for row in data["requests"]:
        if not isinstance(row, dict) or instant(row.get("at_utc")) is None or row.get("role") not in ("publish", "audit"):
            raise RuntimeError("Invalid Buffer request evidence; no request allowed")
    return data


def write(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def reserve(path, role, now=None):
    """Record before sending, including failed HTTP attempts; never retry here.

    Both production workflows use one concurrency group. The file is saved on
    every exit. Local callers must likewise serialize access to the same file.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or role not in ("publish", "audit"):
        raise ValueError("Invalid Buffer budget role/time")
    data = read(path)
    retry = instant(data.get("provider_retry_at_utc"))
    if retry is not None and now < retry:
        raise RuntimeError("Buffer budget hold until " + retry.isoformat())
    recent = [row for row in data["requests"] if instant(row["at_utc"]) > now - timedelta(days=30)]
    daily = [row for row in recent if instant(row["at_utc"]) > now - timedelta(days=1)]
    if len(recent) >= MAX_REQUESTS_30D or len(daily) >= MAX_REQUESTS_24H:
        raise RuntimeError("Buffer budget hold: local rolling request allowance reached")
    if role == "audit" and (sum(row["role"] == "audit" for row in daily) >= MAX_AUDIT_REQUESTS_24H
                             or len(daily) >= MAX_REQUESTS_24H - 6):
        raise RuntimeError("Buffer budget hold: daily audit allowance reserved for publishing")
    for limit in data.get("provider_limits", []):
        reset = instant(limit.get("reset_at_utc"))
        remaining = limit.get("remaining")
        if reset is None or reset <= now or type(remaining) is not int:
            continue
        if remaining <= 0 or (role == "audit" and limit.get("window_seconds") == 2592000
                              and remaining <= AUDIT_MONTHLY_RESERVE):
            raise RuntimeError("Buffer budget hold: provider allowance reserved or exhausted")
        limit["remaining"] -= 1
    data["requests"] = recent + [{"at_utc": now.isoformat(), "role": role}]
    data.setdefault("tracking_started_at_utc", now.isoformat())
    write(path, data)


def header_values(headers, name):
    if headers is None:
        return []
    return headers.get_all(name, []) if hasattr(headers, "get_all") else [headers.get(name, "")]


def observe(path, headers, now=None, rate_limited=False):
    """Keep numeric quotas and reset times only; no tokens, bucket IDs or body."""
    now = now or datetime.now(timezone.utc)
    data = read(path)
    policies = {}
    for value in header_values(headers, "RateLimit-Policy"):
        for name, quota, window in re.findall(r'"([0-9]+-in-[0-9]+(?:min|day|days))";\s*q=(\d+);\s*w=(\d+)', value):
            policies[name] = (int(quota), int(window))
    limits = []
    for value in header_values(headers, "RateLimit"):
        for name, remaining, seconds in re.findall(r'"([0-9]+-in-[0-9]+(?:min|day|days))";\s*r=(\d+);\s*t=(\d+)', value):
            quota, window = policies.get(name, (None, None))
            limits.append({"remaining": int(remaining), "quota": quota, "window_seconds": window,
                           "reset_at_utc": (now + timedelta(seconds=int(seconds))).isoformat()})
    if limits:
        # A 429 may report only its exhausted policy; preserve other windows.
        merged = {row.get("window_seconds"): row for row in data.get("provider_limits", [])}
        merged.update({row["window_seconds"]: row for row in limits})
        data["provider_limits"] = list(merged.values())
        data["provider_observed_at_utc"] = now.isoformat()
    retry = headers.get("Retry-After", "") if headers is not None else ""
    if rate_limited and re.fullmatch(r"\d{1,9}", retry):
        data["provider_retry_at_utc"] = (now + timedelta(seconds=int(retry))).isoformat()
    write(path, data)


def active_path():
    return os.environ.get("BUFFER_BUDGET_PATH", "").strip() or None
