#!/usr/bin/env python3
"""Fetch a free Japan search-trend snapshot from Google Trends RSS.

No API key, paid service, external Python package, or repository mutation is
required. X trends are intentionally not scraped here because unauthenticated
official X trend data is not a stable free API; the publishing automation
checks X through browser-accessible sources and labels public aggregators as
unofficial.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime

GOOGLE_TRENDS_RSS = "https://trends.google.com/trending/rss?geo=JP"
USER_AGENT = "veripsa-editorial-trend-brief/1.0 (+https://veripsa.com)"
MAX_RSS_BYTES = 2 * 1024 * 1024


def fetch_bytes(url: str, timeout: float = 12.0) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/xml, text/xml;q=0.9",
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read(MAX_RSS_BYTES + 1)
    if len(payload) > MAX_RSS_BYTES:
        raise ValueError("Google Trends RSS exceeded the 2 MiB safety limit")
    return payload


def _child_text(item: ET.Element, local_name: str) -> str | None:
    for child in item:
        if child.tag.rsplit("}", 1)[-1] == local_name and child.text:
            return child.text.strip()
    return None


def parse_google_trends(xml_bytes: bytes, *, limit: int = 20) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    output: list[dict] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or GOOGLE_TRENDS_RSS).strip()
        pub_raw = (item.findtext("pubDate") or "").strip()
        if not title:
            continue
        published_at = None
        if pub_raw:
            try:
                published_at = (
                    parsedate_to_datetime(pub_raw)
                    .astimezone(timezone.utc)
                    .isoformat()
                )
            except (TypeError, ValueError, OverflowError):
                published_at = None
        output.append(
            {
                "term": title,
                "url": (
                    link
                    if link.startswith("https://")
                    else GOOGLE_TRENDS_RSS
                ),
                "published_at_utc": published_at,
                "approx_traffic": _child_text(item, "approx_traffic"),
            }
        )
        if len(output) >= limit:
            break
    return output


def build_snapshot(xml_bytes: bytes, *, limit: int = 20) -> dict:
    items = parse_google_trends(xml_bytes, limit=limit)
    if not items:
        raise ValueError("Google Trends RSS contained no usable trend items")
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "source_kind": "google_trends",
            "name": "Google Trends - Trending Now (Japan)",
            "url": GOOGLE_TRENDS_RSS,
            "cost": "free",
            "note": (
                "Search trends are discovery signals, not factual verification. "
                "Verify the underlying event separately before publication."
            ),
        },
        "items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--out",
        help="optional JSON output path; stdout when omitted",
    )
    parser.add_argument(
        "--input",
        help="optional local RSS fixture; skips network",
    )
    args = parser.parse_args()

    if not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100")

    try:
        xml_bytes = (
            Path(args.input).read_bytes()
            if args.input
            else fetch_bytes(GOOGLE_TRENDS_RSS)
        )
        snapshot = build_snapshot(xml_bytes, limit=args.limit)
        rendered = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
        if args.out:
            Path(args.out).write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    except (OSError, ValueError, ET.ParseError) as error:
        print(f"Trend snapshot unavailable: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
