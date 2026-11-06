#!/usr/bin/env python3
"""Describe editorial lane/tone performance without declaring winners.

The daily analytics snapshots already contain measured PV and coarse browser
signals. This helper reads the newest snapshot, compares only articles in a
bounded post-publication age window, and reports descriptive group statistics.

It deliberately does not rank lanes, infer causation, backfill missing editorial
labels, or convert missing attribution into zero.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")
DEFAULT_MIN_AGE_HOURS = 6.0
DEFAULT_MAX_AGE_HOURS = 48.0
DEFAULT_MIN_GROUP = 3


def _aware_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _count(value: object) -> int | None:
    if type(value) is int and value >= 0:
        return value
    return None


def latest_snapshot_path(root: Path = ROOT) -> Path:
    daily = root / "analytics" / "daily"
    paths = sorted(daily.glob("????-??-??.json"))
    if not paths:
        raise FileNotFoundError("No analytics/daily snapshot is available")
    return paths[-1]


def load_snapshot(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    measured = _aware_datetime(data.get("measured_at_utc"))
    recent = data.get("recent_articles")
    if measured is None or not isinstance(recent, list):
        raise ValueError("Snapshot lacks measured_at_utc or recent_articles")
    return data


def eligible_articles(
    snapshot: dict,
    *,
    min_age_hours: float = DEFAULT_MIN_AGE_HOURS,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> list[dict]:
    if min_age_hours < 0 or max_age_hours <= min_age_hours:
        raise ValueError("Age window must satisfy 0 <= min < max")

    measured = _aware_datetime(snapshot.get("measured_at_utc"))
    if measured is None:
        raise ValueError("Snapshot measured_at_utc is invalid")

    output: list[dict] = []
    for item in snapshot.get("recent_articles", []):
        if not isinstance(item, dict):
            continue
        lane = item.get("editorial_lane")
        tone = item.get("editorial_tone")
        payoff = item.get("editorial_payoff")
        # Do not guess labels for legacy/pre-rollout stories.
        if not isinstance(lane, str) or not lane.strip():
            continue
        if not isinstance(tone, str) or not tone.strip():
            continue

        published = _aware_datetime(item.get("published_at"))
        if published is None:
            continue
        age_hours = (measured - published).total_seconds() / 3600
        if age_hours < min_age_hours or age_hours > max_age_hours:
            continue

        page_views = _count(item.get("page_views_last_7_days"))
        # Without a measured PV value this story cannot support a PV comparison.
        if page_views is None:
            continue

        readers = _count(item.get("readers"))
        continued = _count(item.get("continued_readers"))
        continuation_share = (
            round(continued / readers, 3)
            if readers and continued is not None and continued <= readers else None
        )
        output.append({
            "slug": str(item.get("slug") or ""),
            "editorial_lane": lane,
            "editorial_tone": tone,
            "editorial_payoff": payoff if isinstance(payoff, str) and payoff.strip() else None,
            "age_hours": round(age_hours, 2),
            "page_views_last_7_days": page_views,
            # A rough age normalization for early comparisons. It is not a
            # forecast and does not correct for time-of-day or distribution.
            "page_views_per_hour": round(page_views / age_hours, 3) if age_hours > 0 else None,
            # Missing attribution/history remains null, never zero.
            "readers": readers,
            "x_arrivals": _count(item.get("x_arrivals")),
            "continued_readers": continued,
            "continuation_share": continuation_share,
        })
    return output


def _metric(values: list[int | float | None]) -> dict:
    observed = [value for value in values if value is not None]
    if not observed:
        return {"observed": 0, "median": None, "mean": None, "max": None}
    return {
        "observed": len(observed),
        "median": statistics.median(observed),
        "mean": round(statistics.fmean(observed), 2),
        "max": max(observed),
    }


def group_summary(items: list[dict], field: str, *, min_group: int) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        value = item.get(field)
        if isinstance(value, str) and value:
            groups[value].append(item)

    result: dict[str, dict] = {}
    for name in sorted(groups):
        group = groups[name]
        ages = [float(item["age_hours"]) for item in group]
        result[name] = {
            "articles": len(group),
            "status": "descriptive" if len(group) >= min_group else "insufficient_sample",
            "age_hours": {
                "min": round(min(ages), 2),
                "median": round(float(statistics.median(ages)), 2),
                "max": round(max(ages), 2),
            },
            "page_views_last_7_days": _metric(
                [item.get("page_views_last_7_days") for item in group]
            ),
            "page_views_per_hour": _metric(
                [item.get("page_views_per_hour") for item in group]
            ),
            "x_arrivals": _metric([item.get("x_arrivals") for item in group]),
            "continued_readers": _metric(
                [item.get("continued_readers") for item in group]
            ),
            "continuation_share": _metric(
                [item.get("continuation_share") for item in group]
            ),
            "slugs": [item["slug"] for item in group],
        }
    return result


def build_report(
    snapshot: dict,
    *,
    source_name: str,
    min_age_hours: float = DEFAULT_MIN_AGE_HOURS,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    min_group: int = DEFAULT_MIN_GROUP,
) -> dict:
    if min_group < 1:
        raise ValueError("min_group must be >= 1")
    items = eligible_articles(
        snapshot,
        min_age_hours=min_age_hours,
        max_age_hours=max_age_hours,
    )
    return {
        "schema_version": 1,
        "source_snapshot": source_name,
        "measured_at_utc": snapshot["measured_at_utc"],
        "age_window_hours": {
            "min": min_age_hours,
            "max": max_age_hours,
        },
        "minimum_group_size": min_group,
        "eligible_articles": len(items),
        "note": (
            "Descriptive comparison only. Match publication age before comparing; "
            "page_views_per_hour is only a rough age normalization and does not "
            "correct for time-of-day or distribution. continuation_share is the "
            "share of measured article-reader browser IDs that later continued from "
            "that source article when both counters are available. Missing editorial "
            "labels and attribution are not backfilled. A group below "
            "minimum_group_size is an anecdotal sample, not evidence that a lane, "
            "tone or payoff wins or loses."
        ),
        "by_lane": group_summary(items, "editorial_lane", min_group=min_group),
        "by_tone": group_summary(items, "editorial_tone", min_group=min_group),
        "by_payoff": group_summary(items, "editorial_payoff", min_group=min_group),
        "articles": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", help="analytics snapshot JSON path")
    parser.add_argument("--min-age-hours", type=float, default=DEFAULT_MIN_AGE_HOURS)
    parser.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    parser.add_argument("--min-group", type=int, default=DEFAULT_MIN_GROUP)
    args = parser.parse_args()

    path = Path(args.snapshot) if args.snapshot else latest_snapshot_path()
    snapshot = load_snapshot(path)
    report = build_report(
        snapshot,
        source_name=str(path),
        min_age_hours=args.min_age_hours,
        max_age_hours=args.max_age_hours,
        min_group=args.min_group,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
