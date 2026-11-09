#!/usr/bin/env python3
"""Read-only smoke test of the actual newspaper deployment and recent articles.

The script never sends POSTs, creates visits or likes, or invokes a paid API.
It runs after GitHub pushes; Cloudflare may take a little time to deploy.
"""
from __future__ import annotations

import html
import hashlib
import json
import re
import os
import struct
import sys
import time
import subprocess
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree as ET

if __package__:
    from .media_policy import jpeg_dimensions
    from .generated_photos import generated_url, generated_scene_entry
    from .social_cards import reviewed_scene_png
    from .motion_clips import clip_url
    from .share_desk import select_candidates
    from .serial_discovery import choose_serial, primary_episode
    from .editorial_copy import reader_copy, source_reader_copy
    from .build import (
        editorial_lead, broadcast_playlist, CATEGORIES, CATEGORY_PATHS,
        article_clip, uses_stock_photo, reviewed_generated_scene, article_tags, social_image,
    )
else:
    from media_policy import jpeg_dimensions
    from generated_photos import generated_url, generated_scene_entry
    from social_cards import reviewed_scene_png
    from motion_clips import clip_url
    from share_desk import select_candidates
    from serial_discovery import choose_serial, primary_episode
    from editorial_copy import reader_copy, source_reader_copy
    from build import (
        editorial_lead, broadcast_playlist, CATEGORIES, CATEGORY_PATHS,
        article_clip, uses_stock_photo, reviewed_generated_scene, article_tags, social_image,
    )

ROOT=Path(__file__).resolve().parents[1]
SITE="https://veripsa.com"
HEADERS={
    "User-Agent":"MihasseiShimbun-LiveReadOnlyCheck/1.0",
    "Cache-Control":"no-cache",
    "Accept":"text/html,image/jpeg,application/xml,*/*",
}

def get(path):
    req=urllib.request.Request(SITE+path,headers=HEADERS)
    with urllib.request.urlopen(req,timeout=17) as response:
        if response.status!=200:
            raise RuntimeError("HTTP "+str(response.status)+" at "+path)
        return response.read(4*1024*1024),response.headers.get("Content-Type","")

def verify_launch_card(markup: str, image_path: str) -> None:
    """Read-only check for the launch card; never use an undefined crawl variable."""
    expected='name="twitter:image" content="'+SITE+image_path+'"'
    if expected not in markup:
        raise RuntimeError("Cache-busting X share link is not published")


def published_script_path(markup: str, name: str, *, root=ROOT) -> str:
    """Require HTML to select the exact current reader script by content digest."""
    if not re.fullmatch(r"[a-z0-9-]+\.js", name):
        raise ValueError("Invalid reader script name")
    expected = hashlib.sha256((Path(root) / "assets" / name).read_bytes()).hexdigest()[:12]
    match = re.search(
        r'<script\b[^>]*\bsrc="(/assets/' + re.escape(name)
        + r'\?v=([0-9a-f]{12}))"', markup
    )
    if not match or match.group(2) != expected:
        raise RuntimeError("Reader script fingerprint missing or stale: " + name)
    return html.unescape(match.group(1))


def verify_generated_image(article, image: bytes, mime: str, *, root=ROOT) -> None:
    """Detect stale CDN bytes as well as malformed or oversized public JPEGs."""
    record = generated_scene_entry(article, root=root)
    if (not record or "image/jpeg" not in mime
            or len(image) != record["bytes"]
            or jpeg_dimensions(image) != (1200, 675)
            or hashlib.sha256(image).hexdigest() != record["sha256"]):
        raise RuntimeError(article["slug"] + ": production photo differs from the validated generation")


def verify_scene_social_image(article, image: bytes, mime: str, *, root=ROOT) -> None:
    paired = reviewed_scene_png(article, root=root)
    if "image/png" not in mime or len(image) < 1500:
        raise RuntimeError(article["slug"] + ": original social PNG unavailable")
    expected_size = (1200, 675 if paired else 630)
    if _png_icon_size(image) != expected_size:
        raise RuntimeError(article["slug"] + ": social PNG dimensions differ from the selected scene")
    if paired is not None and image != paired:
        raise RuntimeError(article["slug"] + ": production scene PNG differs from the verified static pair")


class _IconLinks(HTMLParser):
    """Collect actual icon declarations from the document head."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_head = False
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "head":
            self.in_head = True
        elif self.in_head and tag == "link":
            self.links.append(dict(attrs))

    def handle_endtag(self, tag):
        if tag == "head":
            self.in_head = False


def _png_icon_size(data: bytes) -> tuple[int, int]:
    if (len(data) < 33 or not data.startswith(b"\x89PNG\r\n\x1a\n")
            or data[8:16] != b"\x00\x00\x00\rIHDR"):
        raise ValueError("invalid PNG header")
    return struct.unpack_from(">II", data, 16)


def _ico_icon_sizes(data: bytes) -> set[tuple[int, int]]:
    if len(data) < 6 or data[:4] != b"\x00\x00\x01\x00":
        raise ValueError("invalid ICO header")
    count = struct.unpack_from("<H", data, 4)[0]
    table_end = 6 + count * 16
    if count != 4 or len(data) < table_end:
        raise ValueError("ICO needs all four browser sizes")
    sizes = set()
    for index in range(count):
        width, height, _, reserved, _, _, length, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + index * 16
        )
        declared = (width or 256, height or 256)
        if reserved or not length or offset < table_end or offset + length > len(data):
            raise ValueError("invalid ICO frame bounds")
        frame = data[offset:offset + length]
        if frame.startswith(b"\x89PNG\r\n\x1a\n"):
            actual = _png_icon_size(frame)
        else:
            if len(frame) < 40:
                raise ValueError("truncated ICO bitmap")
            header, bitmap_width, bitmap_height, planes, bits = struct.unpack_from("<IiiHH", frame)
            if header < 40 or header > len(frame) or planes != 1 or bits not in (1, 4, 8, 16, 24, 32):
                raise ValueError("invalid ICO bitmap header")
            # DIB height includes the colour bitmap and its transparency mask.
            if bitmap_height != declared[1] * 2:
                raise ValueError("ICO bitmap height differs from its directory")
            actual = (bitmap_width, bitmap_height // 2)
        if actual != declared:
            raise ValueError("ICO bitmap size differs from its directory")
        sizes.add(actual)
    return sizes


def verify_site_icons(markup: str, *, fetch=None, root=ROOT) -> None:
    """Verify three icon URLs, including real bytes rather than a 200 HTML fallback."""
    fetch = fetch or get
    parser = _IconLinks()
    parser.feed(markup)
    icons = (
        ("/assets/favicon-192.png", "icon", {"image/png"}, {(192, 192)}),
        ("/favicon.ico", "icon", {"image/x-icon", "image/vnd.microsoft.icon"},
         {(16, 16), (32, 32), (48, 48), (64, 64)}),
        ("/assets/apple-touch-icon.png", "apple-touch-icon", {"image/png"}, {(180, 180)}),
    )
    for path, relation, media_types, sizes in icons:
        declarations = [link for link in parser.links
                        if link.get("href") == path
                        and relation in (link.get("rel") or "").lower().split()]
        declared_sizes = {f"{width}x{height}" for width, height in sizes}
        if (len(declarations) != 1
                or set((declarations[0].get("sizes") or "").split()) != declared_sizes
                or (relation == "icon" and declarations[0].get("type") not in media_types)):
            raise RuntimeError("Site icon declaration missing or invalid: " + path)
        try:
            data, mime = fetch(path)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError("Site icon is not reachable: " + path) from exc
        if mime.split(";", 1)[0].strip().lower() not in media_types:
            raise RuntimeError("Site icon has an invalid MIME: " + path)
        try:
            actual_sizes = (_ico_icon_sizes(data) if path.endswith(".ico")
                            else {_png_icon_size(data)})
            if actual_sizes != sizes:
                raise ValueError("unexpected icon dimensions")
        except (ValueError, struct.error) as exc:
            raise RuntimeError("Site icon format or dimensions invalid: " + path) from exc
        original = Path(root) / "assets" / path.rsplit("/", 1)[-1]
        if hashlib.sha256(data).digest() != hashlib.sha256(original.read_bytes()).digest():
            raise RuntimeError("Site icon differs from the current source: " + path)
    print("Public site icons OK: PNG 192; ICO 16/32/48/64; Apple touch PNG 180")


class _RobotsGroups(RobotFileParser):
    def _add_entry(self, entry):
        # Google merges equally specific groups, including repeated '*'.
        # The standard parser otherwise retains only the first default group.
        self.entries.append(entry)


def verify_icon_crawl_rules(robots: bytes) -> None:
    """Inspect published directives only; this does not observe Google's crawlers."""
    parser = _RobotsGroups()
    # Ignore provider extensions such as Content-Signal and sitemap placement.
    # They must not split otherwise consecutive User-agent declarations.
    parser.parse([line for line in robots.decode("utf-8-sig").splitlines()
                  if line.partition(":")[0].strip().lower()
                  in {"user-agent", "allow", "disallow"}])
    targets = (("Googlebot", "/"),
               ("Googlebot-Image", "/assets/favicon-192.png"),
               ("Googlebot-Image", "/favicon.ico"),
               ("Googlebot-Image", "/assets/apple-touch-icon.png"))
    for bot, path in targets:
        groups = []
        for entry in parser.entries:
            specificity = -1
            for agent in entry.useragents:
                token = re.match(r"[A-Za-z0-9_-]+", agent)
                if agent == "*":
                    specificity = max(specificity, 0)
                elif token and bot.lower().startswith(token.group().lower()):
                    specificity = max(specificity, len(token.group()))
            if specificity >= 0:
                groups.append((specificity, entry))
        best_group = max((score for score, _ in groups), default=-1)
        matches = []
        for score, entry in groups:
            if score != best_group:
                continue
            for rule in entry.rulelines:
                # Undo robotparser's quoting, preserving encoded reserved bytes.
                pattern = unquote(rule.path)
                pattern = re.sub(r"%([0-9A-Fa-f]{2})", lambda match:
                                 chr(int(match[1], 16)) if chr(int(match[1], 16))
                                 in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
                                 else match[0].upper(), pattern)
                end = pattern.endswith("$")
                expression = re.escape(pattern[:-1] if end else pattern).replace(r"\*", ".*")
                if re.match(expression + ("$" if end else ""), path):
                    matches.append((len(pattern.encode("utf-8")), rule.allowance))
        # Unlike RobotFileParser.can_fetch(), Google uses the longest path;
        # an equally specific Allow wins, regardless of line/group order.
        if matches and not max(matches)[1]:
            raise RuntimeError("robots.txt blocks " + bot + " from " + path)
    print("Public robots rules OK: homepage and 3 icon URLs; Google crawling not observed")


def verify_broadcast_delivery(markup, expected_playlist, *, fetch=None, root=ROOT):
    """Check the actual selected media, even when it is not in the newest two articles.

    At most three small MP4s, three caption tracks and one script are fetched.
    A 200 response alone cannot prove that a new player or caption edit deployed.
    """
    fetch = fetch or get
    payloads = re.findall(r'<section\b[^>]*\bdata-broadcast-playlist="([^"]+)"', markup)
    if not expected_playlist:
        if payloads or 'data-news-broadcast' in markup:
            raise RuntimeError("Homepage exposes a broadcast without a verified selection")
        return
    if not 1 <= len(expected_playlist) <= 3 or len(payloads) != 1:
        raise RuntimeError("Published broadcast selection is missing or exceeds its limit")
    try:
        published_playlist = json.loads(html.unescape(payloads[0]))
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Published broadcast selection has invalid data") from exc
    if published_playlist != expected_playlist:
        raise RuntimeError("Published broadcast selection differs from the current editorial selection")

    player = re.search(r'<video\b[^>]*class="broadcast-video"[^>]*>(.*?)</video>', markup, re.S)
    if (not player or
            'src="' + expected_playlist[0]["video_url"] + '"' not in html.unescape(player.group(1))):
        raise RuntimeError("Initial broadcast player and its selected story disagree")
    script_path = published_script_path(markup, "news-broadcast.js", root=root)
    script, script_mime = fetch(script_path)
    if (("javascript" not in script_mime and "text/plain" not in script_mime)
            or script != (root / "assets/news-broadcast.js").read_bytes()):
        raise RuntimeError("Published broadcast script is stale or has an invalid MIME")

    tracks = 0
    for item in expected_playlist:
        slug = item["slug"]
        movie, movie_mime = fetch(item["video_url"])
        expected_movie = (root / "assets/clips" / (slug + ".mp4")).read_bytes()
        if "video/mp4" not in movie_mime or movie != expected_movie:
            raise RuntimeError(slug + ": published broadcast MP4 differs from the verified clip")
        track_url = item.get("track_url")
        if track_url:
            subtitles, subtitle_mime = fetch(track_url)
            version = track_url.rsplit("?v=", 1)[-1]
            if ("text/vtt" not in subtitle_mime or not subtitles.startswith(b"WEBVTT\n")
                    or hashlib.sha256(subtitles).hexdigest()[:12] != version):
                raise RuntimeError(slug + ": published native subtitles are stale or invalid")
            tracks += 1
    print("Public broadcast OK:", len(expected_playlist), "verified videos;", tracks, "native subtitle tracks")


def article_reader_needles(article: dict) -> tuple[str, ...]:
    """Material that must be visible after reader-copy cleanup, not raw legacy JSON."""
    source_context = source_reader_copy(article["source"].get("topic", ""))
    values = [
        article["title"],
        reader_copy(article["summary"]),
        *(reader_copy(paragraph) for paragraph in article["paragraphs"]),
        source_context,
        article["source"]["url"],
        "創作報道",
        f'data-like-count="{article["slug"]}"',
    ]
    return tuple(value for value in values if value)


def latest_articles(articles: list[dict], limit: int | None = 10) -> list[dict]:
    """Use actual site publication recency for live smoke tests.

    Source articles can be older than when we published our fictional story.
    Checking by source time could ignore the newest live edition entirely.
    """
    ordered = sorted(articles, key=lambda a: (
        a.get("published_at") or a["source"]["published_at"],
        a["source"]["published_at"]
    ), reverse=True)
    return ordered if limit is None else ordered[:limit]



def require_matching_public_headline(article: dict, markup: str, surface: str) -> None:
    """Detect stale HTML where the visible headline disagrees with the published source.

    Check the visible heading with its article URL, not a title appearing in
    metadata, a related-story rail, or a JSON payload. No additional GETs.
    """
    slug, title = article["slug"], article["title"]
    url = "/articles/" + slug + "/"
    if surface == "home":
        expected = f'<h1><a href="{url}">{title}</a></h1>'
    elif surface == "archive":
        expected = f'<h3><a href="{url}">{title}</a></h3>'
    elif surface == "article":
        expected = f'<h1>{title}</h1>'
    else:
        raise ValueError("Unknown headline surface: " + surface)
    if expected not in html.unescape(markup):
        raise RuntimeError(slug + ": " + surface + " headline mismatch (stale or mixed revision)")


class _RecommendationMarkup(HTMLParser):
    """Read attributes before unescaping HTML so quoted article titles stay intact."""
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
                 "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        element = {"tag": tag, "attrs": dict(attrs), "children": [], "text": []}
        self.elements.append(element)
        if self.stack:
            self.stack[-1]["children"].append(element)
        if tag not in self.VOID_TAGS:
            self.stack.append(element)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, value):
        for element in self.stack:
            element["text"].append(value)


def _descendants(element):
    for child in element["children"]:
        yield child
        yield from _descendants(child)


def verify_pv_recommendations(markup: str, articles: list[dict], current_article=None) -> None:
    """Verify the entire eligible archive and a usable, honestly labelled fallback.

    This only inspects an existing HTML GET. Static metadata must not claim that
    a story is popular before the browser receives the real recent-PV snapshot.
    """
    parser = _RecommendationMarkup()
    parser.feed(markup)
    parser.close()
    for element in parser.elements:
        if {"starter-editorial", "article-related"} & set(element["attrs"].get("class", "").split()):
            raise RuntimeError("Old editorial recommendations remain beside the PV panel")
    panels = [element for element in parser.elements
              if "data-popular-section" in element["attrs"]]
    if len(panels) != 1:
        raise RuntimeError("Expected exactly one PV recommendation panel")
    panel = panels[0]
    attrs = panel["attrs"]
    if (panel["tag"] != "section" or "hidden" not in attrs
            or "readers-choice" not in attrs.get("class", "").split()
            or attrs.get("aria-labelledby") != "readers-choice-title"):
        raise RuntimeError("PV recommendation panel must start hidden with an accessible heading")
    try:
        options = json.loads(attrs.get("data-popular-options", ""))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("PV recommendation candidate JSON is invalid") from exc
    if not isinstance(options, list) or any(not isinstance(item, dict) for item in options):
        raise RuntimeError("PV recommendation candidates must be a list of article metadata")
    excluded = current_article["slug"] if current_article else None
    expected = {article["slug"]: article for article in articles if article["slug"] != excluded}
    slugs = [item.get("slug") for item in options]
    if (any(not isinstance(slug, str) for slug in slugs)
            or len(slugs) != len(set(slugs)) or set(slugs) != set(expected)):
        raise RuntimeError("PV candidates must match every eligible published article exactly once")
    for option in options:
        article = expected[option["slug"]]
        if option.get("title") != article["title"] or option.get("category") != article["category"]:
            raise RuntimeError(option["slug"] + ": PV candidate metadata is stale")
    descendants = list(_descendants(panel))
    headings = [element for element in descendants
                if element["tag"] == "h2" and element["attrs"].get("id") == "readers-choice-title"]
    periods = [element for element in descendants if "data-popular-period" in element["attrs"]]
    lists = [element for element in descendants if "data-popular-list" in element["attrs"]]
    if len(headings) != 1 or "".join(headings[0]["text"]).strip() != "よく読まれている記事":
        raise RuntimeError("PV recommendation heading is missing or misleading")
    if len(periods) != 1 or "".join(periods[0]["text"]).strip() != "過去7日間のPV順":
        raise RuntimeError("PV recommendation period is missing or still uses browser counts")
    if (len(lists) != 1 or lists[0]["tag"] != "ol" or lists[0]["children"]
            or "".join(lists[0]["text"]).strip()):
        raise RuntimeError("PV recommendation results must wait for measured API values")

    fallbacks = [element for element in parser.elements if "data-popular-fallback" in element["attrs"]]
    if current_article is None:
        if fallbacks:
            raise RuntimeError("Homepage has an unexpected article-continuation fallback")
        return
    if (len(fallbacks) != 1 or fallbacks[0]["tag"] != "nav"
            or "reader-next" not in fallbacks[0]["attrs"].get("class", "").split()
            or "hidden" in fallbacks[0]["attrs"]):
        raise RuntimeError("Article must retain a visible sequential-story fallback without JavaScript")
    fallback = fallbacks[0]
    fallback_text = "".join(fallback["text"])
    if "次の記事" not in fallback_text:
        raise RuntimeError("Article fallback must clearly label the next-story route")
    mode = fallback["attrs"].get("data-reader-next-mode")
    if mode not in {"related-topic", "sequential", "archive"}:
        raise RuntimeError("Article fallback must declare a supported next-story mode")

    reading_order = latest_articles(articles, limit=None)
    current_index = next(
        (index for index, article in enumerate(reading_order)
         if article["slug"] == excluded),
        None,
    )
    next_article = (
        reading_order[current_index + 1]
        if current_index is not None and current_index + 1 < len(reading_order)
        else None
    )
    links = [element for element in _descendants(fallback)
             if element["tag"] == "a" and "reader-next-title" in element["attrs"].get("class", "").split()]
    if len(links) != 1:
        raise RuntimeError("Article fallback must expose exactly one next-story link")
    href = links[0]["attrs"].get("href")

    if mode == "archive":
        if next_article is not None or href != "/archive/" or "続けて読む" not in fallback_text:
            raise RuntimeError("Archive fallback is only valid at the end of the publication stream")
        return

    if not isinstance(href, str):
        raise RuntimeError("Article fallback next-story URL is missing")
    match = re.fullmatch(r"/articles/([a-z0-9]+(?:-[a-z0-9]+)*)/", href)
    if not match:
        raise RuntimeError("Article fallback must link to a canonical published article")
    target_slug = match.group(1)
    target_index = next(
        (index for index, article in enumerate(reading_order)
         if article["slug"] == target_slug),
        None,
    )
    if current_index is None or target_index is None or target_index <= current_index:
        raise RuntimeError("Article fallback must only advance to an older published story")

    if mode == "sequential":
        expected_url = "/articles/" + next_article["slug"] + "/" if next_article else "/archive/"
        if href != expected_url or "続けて読む" not in fallback_text:
            raise RuntimeError("Sequential article fallback must advance one publication step")
        return

    if "同じ話題" not in fallback_text:
        raise RuntimeError("Related article fallback must label the shared-topic route")
    current_tags = set(article_tags(current_article))
    target = reading_order[target_index]
    if not current_tags.intersection(article_tags(target)):
        raise RuntimeError("Related article fallback must share a published topic tag")


def verify_pageview_contract(payload: dict, article_slug: str) -> None:
    """Validate measured PVs and the common seven-date ranking window from GET."""
    tracking = payload.get("pageViewTracking")
    if (not isinstance(tracking, dict) or tracking.get("timezone") != "Asia/Tokyo"
            or tracking.get("unit") != "page-view" or "startedAtUtc" not in tracking
            or "pageViews" not in payload):
        raise RuntimeError("Separate PV measurement metadata is not deployed")
    started = tracking["startedAtUtc"]
    if started is not None:
        if not isinstance(started, str):
            raise RuntimeError("PV measurement start must be an ISO timestamp")
        parsed_start = datetime.fromisoformat(started.replace("Z", "+00:00"))
        if parsed_start.tzinfo is None:
            raise RuntimeError("PV measurement start must include a timezone")

    def valid_pv(value):
        return type(value) is int and value >= 0

    def measured_count(container, key):
        if (key not in container or (started is None and container[key] is not None)
                or (started is not None and not valid_pv(container[key]))):
            raise RuntimeError("PV value is missing or disagrees with measurement availability: " + key)

    measured_count(payload, "pageViews")
    daily, yesterday = payload.get("today"), payload.get("yesterday")
    for label, period in (("today", daily), ("yesterday", yesterday)):
        if (not isinstance(period, dict) or not isinstance(period.get("dateJst"), str)
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", period["dateJst"])
                or "pageViews" not in period or period.get("pageViewsCoverage") not in
                {"unmeasured", "partial", "complete", "in-progress"}):
            raise RuntimeError("PV daily measurement coverage missing: " + label)
        date.fromisoformat(period["dateJst"])
        if period["pageViewsCoverage"] == "unmeasured":
            if period["pageViews"] is not None:
                raise RuntimeError("Unmeasured PV history must not be fabricated as zero")
        elif started is None or not valid_pv(period["pageViews"]):
            raise RuntimeError("Invalid measured daily PV: " + label)
    if daily["pageViewsCoverage"] != ("in-progress" if started is not None else "unmeasured"):
        raise RuntimeError("Today must expose the current PV measurement state")
    today_date = date.fromisoformat(daily["dateJst"])
    if date.fromisoformat(yesterday["dateJst"]) != today_date - timedelta(days=1):
        raise RuntimeError("PV yesterday is not the preceding JST date")
    week = payload.get("last7Days")
    if (not isinstance(week, dict) or type(week.get("complete")) is not bool
            or type(week.get("measuredDays")) is not int or not 0 <= week["measuredDays"] <= 7
            or week.get("startDateJst") != (today_date - timedelta(days=6)).isoformat()
            or week.get("endDateJst") != today_date.isoformat()):
        raise RuntimeError("PV seven-date aggregate has a missing or inconsistent JST period")
    measured_count(week, "pageViews")
    if started is None and (week["measuredDays"] != 0 or week["complete"]):
        raise RuntimeError("Unmeasured PV history cannot claim a covered ranking period")
    if started is not None and not daily["pageViews"] <= week["pageViews"] <= payload["pageViews"]:
        raise RuntimeError("Site PV totals disagree across daily, seven-day and cumulative periods")
    article_metrics = payload.get("articles")
    details = article_metrics.get(article_slug) if isinstance(article_metrics, dict) else None
    if not isinstance(details, dict):
        raise RuntimeError("Per-article PV is missing: " + article_slug)
    for key in ("pageViews", "todayPageViews", "last7DaysPageViews"):
        measured_count(details, key)
    if started is not None and (
            not details["todayPageViews"] <= details["last7DaysPageViews"] <= details["pageViews"]
            or details["last7DaysPageViews"] > week["pageViews"]):
        raise RuntimeError("Per-article PV ranking values disagree with measured totals")


def check():
    # Verify the exact public deployment, not merely an older set of 200 pages.
    expected=subprocess.run(
        ["git","rev-parse","HEAD"],cwd=ROOT,text=True,stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,timeout=5,check=False
    ).stdout.strip().lower()
    if re.fullmatch(r"[0-9a-f]{40}", expected):
        marker,_=get("/build-revision.txt?version="+expected[:12])
        actual=marker.decode("utf-8").strip().lower()
        if actual!=expected:
            raise RuntimeError("Cloudflare serving different revision: live="+actual+" expected="+expected)
    articles=json.loads((ROOT/"content/articles.json").read_text(encoding="utf-8"))
    sorted_articles=latest_articles(articles, limit=None)
    recent=sorted_articles[:10]
    home,mime=get("/")
    verify_site_icons(home.decode("utf-8"))
    body=html.unescape(home.decode("utf-8"))
    for token in ("未発生新聞","data-edition-status","data-issue-picker","data-pick-issue","fiction-mark"):
        if token not in body:
            raise RuntimeError("Newest homepage has not reached production: "+token)
    # PV is measured separately from the legacy distinct-browser counters.
    # Missing pre-activation history must remain unmeasured, never backfilled.
    for token in ("data-observer-widget","data-pv-widget",
                  "data-site-pv-today","data-site-pv-yesterday",
                  "data-site-pv-week","data-pv-tracking-note",
                  "今日のPV","昨日のPV","data-article-pv="):
        if token not in body:
            raise RuntimeError("PV display missing: "+token)
    if "未来を見にきた人" in body:
        raise RuntimeError("Homepage must not label browser counters as people")

    # Keep the lead current and all recommendations tied to measured recent PV.
    chosen_lead = editorial_lead(sorted_articles)
    if not chosen_lead or chosen_lead["slug"] != sorted_articles[0]["slug"]:
        raise RuntimeError("Latest publication is not the front-page lead")
    require_matching_public_headline(chosen_lead, body, "home")
    verify_broadcast_delivery(home.decode("utf-8"), broadcast_playlist(sorted_articles))
    verify_pv_recommendations(home.decode("utf-8"), sorted_articles)
    # Verify the already-shipped topic navigation rather than adding more UI.
    # This also catches a successful main merge whose public deploy is stale.
    if 'class="topic-explore genre-explore"' not in body or "話題から読む" not in body:
        raise RuntimeError("Front-page topic navigation has not reached production")
    if chosen_lead.get("tags") and 'class="story-tags story-tags--compact"' not in body:
        raise RuntimeError("Front-page lead topic tags have not reached production")
    if "未発生ニュース <small>FICTION</small>" in body:
        raise RuntimeError("Old broadcast overlay is still deployed")
    if 'class="reading-ways"' in body:
        raise RuntimeError("Duplicate starter portals were not removed")
    if not re.search(r'<section class="section-block">.*?</section>', body, re.S):
        raise RuntimeError("Fresh-news grid is missing")
    if '<aside class="side-brief"><div class="side-title"><h2>新着記事</h2>' not in body:
        raise RuntimeError("Newest headline rail is not labelled as the actual fresh-news list")
    if '<section class="section-block"><div class="section-head"><h2>さらに読む</h2>' not in body:
        raise RuntimeError("Older homepage cards still claim to be the newest articles")
    
    # A saved reading list is opt-in, browser-local and not a thin SEO archive.
    # Only GETs: no visits, bookmark writes, analytics POSTs or paid services.
    saved_html, saved_mime = get("/saved/")
    saved_markup = saved_html.decode("utf-8")
    if ("text/html" not in saved_mime or
        'name="robots" content="noindex,follow"' not in saved_markup or
        'data-saved-list' not in saved_markup or
        'data-clear-bookmarks' not in saved_markup):
        raise RuntimeError("Private browser-only reading list is missing from production")
    if 'href="/saved/"' not in body:
        raise RuntimeError("Saved reading list is not discoverable from homepage")
    bookmark_path = published_script_path(saved_markup, "bookmarks.js")
    bookmark_js, bookmark_mime = get(bookmark_path)
    if ("javascript" not in bookmark_mime and "text/plain" not in bookmark_mime):
        raise RuntimeError("Bookmark script is missing or served with wrong MIME")
    if (bookmark_js != (ROOT / "assets/bookmarks.js").read_bytes() or
        b"mihassei-saved-stories-v1" not in bookmark_js or
        b"localStorage.setItem" not in bookmark_js):
        raise RuntimeError("Bookmark script missing expected current device-local functionality")

    # Serial long-form reading, links and share cards must ship alongside the news.
    # Static GETs only; do not generate visits, votes or any paid API traffic.
    serials=json.loads((ROOT/"content/serials.json").read_text(encoding="utf-8"))
    if 'class="serials-feature"' not in body or 'href="/serials/"' not in body:
        raise RuntimeError("New serials not discoverable on published homepage")
    serial_hub, serial_type=get("/serials/")
    serial_index=serial_hub.decode("utf-8")
    if "text/html" not in serial_type:
        raise RuntimeError("Live serial index was not deployed")
    if ('<title>連載一覧 | 未発生新聞（創作と市場分析の連載）</title>' not in serial_index or
        '"@type": "ItemList"' not in serial_index or
        '"@type": "BreadcrumbList"' not in serial_index):
        raise RuntimeError("Serial index lacks clear non-fiction/fiction SEO labels")
    live_sitemap=get("/sitemap.xml")[0].decode("utf-8")
    if SITE+"/saved/" in live_sitemap:
        raise RuntimeError("Private saved list should not be indexed")
    if "https://veripsa.com/serials/" not in live_sitemap:
        raise RuntimeError("New serial index missing from sitemap")
    for serial in serials:
        prefix="/serials/"+serial["slug"]+"/"
        if 'href="'+prefix+'"' not in serial_index:
            raise RuntimeError("Serial missing from landing page: "+serial["slug"])
        series_markup,series_type=get(prefix)
        if "text/html" not in series_type or serial["title"] not in series_markup.decode("utf-8"):
            raise RuntimeError("Series detail page missing: "+prefix)
        if ('"@type": "ItemList"' not in series_markup.decode("utf-8") or
            '"@type": "BreadcrumbList"' not in series_markup.decode("utf-8")):
            raise RuntimeError("Series overview missing chapter list or breadcrumb schema: "+prefix)
        svg,svg_mime=get(prefix+"cover.svg")
        if not svg.startswith(b"<svg") or "svg" not in svg_mime:
            raise RuntimeError("Generated original serial cover unavailable: "+prefix)
        expected_kind="推理小説" if serial["kind"]=="fiction" else "市場分析"
        feed="/feeds/series-"+serial["slug"]+".xml"
        if ('href="'+feed+'"' not in serial_index or
            'href="'+feed+'"' not in series_markup.decode("utf-8")):
            raise RuntimeError("Serial RSS is not discoverable on the public site: "+feed)
        rss_bytes,rss_type=get(feed)
        if "xml" not in rss_type:
            raise RuntimeError("Serial RSS is not published as XML: "+feed)
        channel=ET.fromstring(rss_bytes).find("channel")
        if channel is None or serial["title"] not in (channel.findtext("title") or ""):
            raise RuntimeError("Serial RSS title missing or incorrect: "+feed)
        ns_serial={"atom":"http://www.w3.org/2005/Atom",
                   "media":"http://search.yahoo.com/mrss/"}
        self_link=channel.find("atom:link",ns_serial)
        if self_link is None or self_link.get("href")!=SITE+feed:
            raise RuntimeError("Original serial RSS self URL missing: "+feed)
        feed_entries=channel.findall("item")
        expected_episodes=list(reversed(serial["episodes"][-30:]))
        if len(feed_entries)!=len(expected_episodes):
            raise RuntimeError("Serial RSS lists incorrect number of released chapters")
        for feed_item,chapter in zip(feed_entries,expected_episodes):
            url=SITE+prefix+chapter["slug"]+"/"
            if feed_item.findtext("link")!=url or feed_item.findtext("guid")!=url:
                raise RuntimeError("Serial RSS chapter link mismatch: "+url)
            if feed_item.find("pubDate") is not None:
                raise RuntimeError("Serial RSS cannot invent unrecorded publication times")
            thumb=feed_item.find("media:thumbnail",ns_serial)
            expected_picture=SITE+"/og/serials/"+serial["slug"]+"-"+chapter["slug"]+".png"
            if thumb is None or thumb.get("url")!=expected_picture:
                raise RuntimeError("Serial RSS first-party chapter preview missing: "+url)
        for ep in serial["episodes"]:
            path=prefix+ep["slug"]+"/"
            url=SITE+path
            article,article_type=get(path)
            page=article.decode("utf-8")
            if "text/html" not in article_type or expected_kind not in page:
                raise RuntimeError("Serial chapter missing or type mislabeled: "+path)
            for tag in ('<link rel="canonical" href="'+url+'"',
                        'name="twitter:card" content="summary_large_image"',
                        'name="twitter:image" content="'+SITE+'/og/serials/'+serial["slug"]+'-'+ep["slug"]+'.png"',
                        ep["title"], 'href="'+feed+'"'):
                if tag not in page:
                    raise RuntimeError("Serial live metadata incomplete: "+path+" "+tag)
            if url not in live_sitemap:
                raise RuntimeError("Serial episode missing from sitemap: "+path)
            if ('"isPartOf": {"@type": "CreativeWorkSeries"' not in page or
                '"@type": "BreadcrumbList"' not in page or
                '"name": "'+serial["title"]+'"' not in page):
                raise RuntimeError("Serial chapter lost structured parent/breadcrumb: "+path)
            png,png_mime=get("/og/serials/"+serial["slug"]+"-"+ep["slug"]+".png")
            if not png.startswith(b"\x89PNG\r\n\x1a\n") or "image/png" not in png_mime or len(png)<1500:
                raise RuntimeError("Serial X card not served as a proper PNG: "+path)
            if serial["kind"]=="fiction" and "すべて創作" not in page:
                raise RuntimeError("Fiction episode missing disclosure: "+path)
            if serial["kind"]=="analysis" and ("投資助言" not in page or "参照した公開資料" not in page):
                raise RuntimeError("Market analysis missing sources/disclosure: "+path)

    # Internal publishing tools are intentionally not part of the public site.
    # Do not mistake a robots noindex hint for real privacy.
    for internal_path in ("/growth-desk/", "/share-desk/", "/image-review/"):
        try:
            get(internal_path)
        except urllib.error.HTTPError as exc:
            if exc.code not in (404, 410):
                raise RuntimeError("Unexpected internal-tool status: "+internal_path)
        else:
            raise RuntimeError("Internal-only page is exposed: "+internal_path)


    # The browser now requests ONE fingerprinted CSS bundle. Verify both the
    # content digest (cache busting) and the preserved styles in production.
    style_tags = re.findall(r'<link rel="stylesheet" href="([^"]+)"', body)
    match = (re.fullmatch(r'/assets/newspaper\.css\?v=([0-9a-f]{12})', style_tags[0])
             if len(style_tags) == 1 else None)
    if not match:
        raise RuntimeError("Expected one versioned first-party stylesheet: " + str(style_tags))
    style_data, style_mime = get(style_tags[0])
    if "text/css" not in style_mime:
        raise RuntimeError("Bundled CSS served with an invalid MIME")
    style_text = style_data.decode("utf-8")
    if hashlib.sha256(style_data).hexdigest()[:12] != match.group(1):
        raise RuntimeError("Bundled CSS body and version hash disagree (stale cache)")
    for name in ("style.css", "article-extra.css", "newsroom.css",
                 "future-poll.css", "comments.css", "editorial-polish.css",
                 "serials.css", "reader-first.css"):
        if ("/* SOURCE " + name + " */") not in style_text:
            raise RuntimeError("Bundled CSS has lost styles from " + name)
    for selector in (".article-body", ".story-card", ".nav-inner",
                     ".masthead", ".hero-grid", ".reader-comments",
                     ".future-poll", ".serial-card", "@media(max-width:660px)"):
        if selector not in style_text:
            raise RuntimeError("Bundled CSS missing: " + selector)

    # A failed daily schedule must not leave the homepage falsely saying
    # "today's" stories. The page embeds a genuine, JST publication date.
    latest=max(articles,key=lambda a:(
        a.get("published_at") or a["source"]["published_at"],
        a["source"]["published_at"]
    ))
    last_day=(latest.get("published_at") or latest["source"]["published_at"])[:10]
    dated_articles=sum((a.get("published_at") or a["source"]["published_at"])[:10]==last_day for a in articles)
    for token in ('data-edition-status data-last-edition="'+last_day+'"',
                  'data-edition-count="'+str(dated_articles)+'"',
                  'data-edition-updated>更新：'):
        if token not in body:
            raise RuntimeError("Publication freshness information not deployed: "+token)
    if "● LIVE" in body or "今日の号外をめくる" in body:
        raise RuntimeError("Old archive must not masquerade as today's live news")

    # A valid X link card needs HTML metadata AND a publicly served raster
    # image. The first published social preview may be cached by X.
    for tag in (
        'name="twitter:card" content="summary_large_image"',
        'name="twitter:image" content="https://veripsa.com/og/brand-launch-20261102-v3.png"',
        'property="og:image" content="https://veripsa.com/og/brand-launch-20261102-v3.png"',
    ):
        if tag not in body:
            raise RuntimeError("X card metadata missing or not yet deployed: " + tag)
    image, image_mime=get("/og/brand-launch-20261102-v3.png")
    # A clean launch URL forces a new card fetch instead of reusing old cached metadata.
    share,mime=get("/?src=x-launch-20261102-v3")
    if "text/html" not in mime:
        raise RuntimeError("Launch X share URL has unexpected Content-Type")
    verify_launch_card(share.decode("utf-8"), "/og/brand-launch-20261102-v3.png")
    if not image.startswith(bytes((137, 80, 78, 71, 13, 10, 26, 10))) or "image/png" not in image_mime or len(image) < 1024:
        raise RuntimeError("X card brand image missing or invalid PNG")

    if 'class="genre-explore"' not in body or 'href="/editorial/"' not in body:
        raise RuntimeError("Reader discovery or editorial link missing from live homepage")
    if ('href="/feed.xml">' not in body or
        '<link rel="alternate" type="application/rss+xml" title="未発生新聞 RSS" href="/feed.xml">' not in body):
        raise RuntimeError("Footer and metadata RSS links are missing from production")
    editorial, editorial_mime = get("/editorial/")
    editorial_html = html.unescape(editorial.decode("utf-8"))
    if "text/html" not in editorial_mime or any(x not in editorial_html for x in ("生成AI", "広告・アフィリエイト", "創作")):
        raise RuntimeError("Editorial transparency page is not yet fully deployed")
    commercial, commercial_mime = get("/advertise/")
    commercial_html = html.unescape(commercial.decode("utf-8"))
    if "text/html" not in commercial_mime or any(x not in commercial_html for x in (
        "広告・協賛のご案内", "公開", "PR", "GitHub",
    )):
        raise RuntimeError("Public sponsorship information page is missing or incomplete")
    if 'href="/advertise/"' not in body:
        raise RuntimeError("Footer sponsor inquiry link not deployed")
    if '<nav class="footer-nav" aria-label="補助リンク">' not in body:
        raise RuntimeError("Compact footer navigation not deployed")
    footer_nav = body.split('<nav class="footer-nav"', 1)[1].split('</nav>', 1)[0]
    if (footer_nav.count('<a href=') != 8 or
        any(f'href="{path}"' not in footer_nav for path in (
            "/saved/", "/archive/#category-feeds", "/about/",
            "/editorial/", "/advertise/", "/privacy/", "/feed.xml"
        ))):
        raise RuntimeError("Footer has lost a utility link or gained duplicated navigation")
    if "adsbygoogle.js" in commercial_html:
        raise RuntimeError("Unapproved advertising script unexpectedly enabled")

    # Check the growth path against the live deployment, not just the local
    # generator. All archived article links must remain accessible without JS.
    archive, archive_mime = get("/archive/")
    archive_html = archive.decode("utf-8")
    for required in ('id="archive-results"', 'data-archive-more hidden',
                     'data-archive-more-button aria-controls="archive-results"'):
        if required not in archive_html:
            raise RuntimeError("Live archive is missing progressive browsing: "+required)
    for article in articles:
        if 'href="/articles/'+article["slug"]+'/"' not in archive_html:
            raise RuntimeError("Live archive missing article link: "+article["slug"])
        require_matching_public_headline(article, archive_html, "archive")
    site_path = published_script_path(archive_html, "site.js")
    site_js, site_mime = get(site_path)
    if (("javascript" not in site_mime and "text/plain" not in site_mime)
            or site_js != (ROOT / "assets/site.js").read_bytes()
            or b"PAGE_SIZE = 18" not in site_js or b"visibleLimit" not in site_js):
        raise RuntimeError("Live archive pagination script has not deployed")
    # The random edition is deliberately bounded to avoid an ever-growing
    # homepage payload as automated publishing accelerates.
    issue_match = re.search(r'data-issue-options="([^"]+)"', home.decode("utf-8"))
    if not issue_match:
        raise RuntimeError("Live edition picker data missing")
    issue_items = json.loads(html.unescape(issue_match.group(1)))
    if len(issue_items) != min(len(articles),40):
        raise RuntimeError("Live edition picker should include latest 40 at most")

    sitemap,mime=get("/sitemap.xml")
    root=ET.fromstring(sitemap)
    ns={"s":"http://www.sitemaps.org/schemas/sitemap/0.9"}
    siteurls={url.findtext("s:loc",namespaces=ns) for url in root.findall("s:url",ns)}
    if not siteurls:
        raise RuntimeError("Sitemap has no URLs")
    for article in articles:
        url=SITE+"/articles/"+article["slug"]+"/"
        if url not in siteurls:
            raise RuntimeError("Sitemap missing "+article["slug"])

    # Daily editions must be published from the real article publication day,
    # not the fictional 2031 dateline. Sample only the newest three issue pages
    # to keep the free read-only production verification lightweight.
    issue_days=Counter(
        (article.get("published_at") or article["source"]["published_at"])[:10]
        for article in articles
    )
    substantial={day: [a for a in articles
                       if (a.get("published_at") or a["source"]["published_at"])[:10]==day]
                 for day,count in issue_days.items() if count>=3}
    index_data, index_mime = get("/issues/")
    index_markup=index_data.decode("utf-8")
    if ("text/html" not in index_mime or 'class="issue-index-list"' not in index_markup or
        '<link rel="canonical" href="'+SITE+'/issues/"' not in index_markup):
        raise RuntimeError("Issue archive listing is not published")
    if SITE+"/issues/" not in siteurls:
        raise RuntimeError("Daily issue index missing from sitemap")
    for day, items in substantial.items():
        url=SITE+"/issues/"+day+"/"
        if url not in siteurls:
            raise RuntimeError("Daily issue missing from sitemap: "+day)
        if 'href="/issues/'+day+'/"' not in index_markup:
            raise RuntimeError("Daily issue not linked from archive: "+day)
    for day in sorted(substantial,reverse=True)[:3]:
        page, page_mime=get("/issues/"+day+"/")
        markup=page.decode("utf-8")
        if "text/html" not in page_mime or 'datetime="'+day+'"' not in markup:
            raise RuntimeError("Real publication date missing from issue page: "+day)
        for article in substantial[day]:
            if 'href="/articles/'+article["slug"]+'/"' not in markup:
                raise RuntimeError("Issue missing published article "+article["slug"])
        image_path="/og/issues/"+day+".png"
        if ('<meta property="og:image" content="'+SITE+image_path+'">' not in markup or
            'name="twitter:image" content="'+SITE+image_path+'"' not in markup):
            raise RuntimeError("Edition-specific social image not deployed for "+day)
        cover,cover_mime=get(image_path)
        if (not cover.startswith(bytes((137,80,78,71,13,10,26,10))) or
            "image/png" not in cover_mime or len(cover)<1500):
            raise RuntimeError("Edition image missing or invalid for "+day)
        linked=re.search(r'<script type="application/ld\+json">(.+?)</script>',markup,re.S)
        if not linked:
            raise RuntimeError("Edition lacks JSON-LD "+day)
        schema=json.loads(linked.group(1))
        if not isinstance(schema,list):
            raise RuntimeError("Edition JSON-LD should provide CollectionPage and ItemList")
        itemlist=next((x for x in schema if x.get("@type")=="ItemList"),None)
        if not itemlist or itemlist.get("numberOfItems")!=min(len(substantial[day]),30):
            raise RuntimeError("Edition structured article count mismatch for "+day)
        if not any(x.get("@type")=="BreadcrumbList" for x in schema):
            raise RuntimeError("Edition structured breadcrumb missing for "+day)
    print("Public daily editions and their first-party social images OK:",len(substantial))

    # Verify both feed-reader images and the noindex visual editorial review.
    feed_bytes,feed_type=get("/feed.xml")
    if "xml" not in feed_type:
        raise RuntimeError("RSS feed has an unexpected Content-Type")
    feed_root=ET.fromstring(feed_bytes)
    feed_entries=feed_root.findall("./channel/item")
    if len(feed_entries)!=min(len(articles),90):
        raise RuntimeError("New RSS retention window is not deployed")
    # The RSS verifier must follow the newspaper's actual editorial sections,
    # including newly added 事件, not a stale hard-coded set.
    if any(item.findtext("category") not in set(CATEGORIES[1:])
           for item in feed_entries):
        raise RuntimeError("RSS feed category metadata missing")
    namespace={"media":"http://search.yahoo.com/mrss/"}
    for entry in feed_root.findall("./channel/item")[:min(5,len(articles))]:
        thumb=entry.find("media:thumbnail",namespace)
        media=entry.find("media:content",namespace)
        if thumb is None or media is None or thumb.get("url")!=media.get("url"):
            raise RuntimeError("RSS thumbnail metadata is missing or inconsistent")
        if not thumb.get("url","").startswith(SITE+"/"):
            raise RuntimeError("RSS image is not first-party: "+str(thumb.get("url")))

    checked_photos=0
    for item in recent:
        slug=item["slug"]
        data,mime=get("/articles/"+slug+"/")
        page=html.unescape(data.decode("utf-8"))
        require_matching_public_headline(item, page, "article")
        for needle in article_reader_needles(item):
            if needle not in page:
                raise RuntimeError(slug+": missing reader-visible material "+needle[:75])
        if 'class="story-fiction-context"' in page:
            raise RuntimeError(slug+": redundant per-story fiction badge reached production")
        published=item.get("published_at") or item["source"]["published_at"][:10]
        if f'"datePublished": "{published}"' not in page:
            raise RuntimeError(slug+": real publication timestamp missing")
        # The dateline belongs to 5-years-later fiction, never to publication.
        # Verify the same clear distinction on the live HTML without extra GETs.
        for date_tag in (
            f'class="real-published-at" datetime="{published}"',
            f'class="fiction-dateline" datetime="{item["fiction_at"]}"',
            f'property="article:published_time" content="{published}"',
        ):
            if date_tag not in page:
                raise RuntimeError(slug+": publication/fiction label missing: "+date_tag)
        if f'"datePublished": "{item["fiction_at"]}"' in page:
            raise RuntimeError(slug+": fictional year leaked into SEO date")
        schema_match=re.search(r'<script type="application/ld\+json">(.+?)</script>',page,re.S)
        if not schema_match:
            raise RuntimeError(slug+": missing article JSON-LD")
        structures=json.loads(schema_match.group(1))
        metadata=next((x for x in structures if x.get("@type")=="BlogPosting"),None)
        if not metadata:
            raise RuntimeError(slug+": missing BlogPosting structured data")
        actual_url=SITE+"/articles/"+slug+"/"
        if metadata.get("mainEntityOfPage",{}).get("@id")!=actual_url:
            raise RuntimeError(slug+": canonical page not linked from structured article")
        if metadata.get("dateModified")!=(item.get("modified_at") or published):
            raise RuntimeError(slug+": real modification timestamp missing or fictional")
        # Reading flow is headline -> image -> uninterrupted prose -> tools.
        # Source material is always present in HTML, behind a native details
        # disclosure instead of another large pre-reading explanation.
        for tag in ('<div class="article-body">',
                    '<div class="article-save-inline">',
                    '<details class="source-context" id="story-source" aria-label="着想した実際の話題">',
                    '<summary>着想元を見る</summary>'):
            if tag not in page:
                raise RuntimeError(slug+": reading-first layout missing: "+tag)
        positions = [
            page.index('<figure class="article-hero-figure">'),
            page.index('<div class="article-body">'),
            page.index('<div class="article-save-inline">'),
            page.index('<details class="source-context" id="story-source" aria-label="着想した実際の話題">'),
        ]
        if positions != sorted(positions) or len(set(positions)) != len(positions):
            raise RuntimeError(slug+": image, prose, bookmark and source order regressed")
        if 'href="#story-source"' in page:
            raise RuntimeError(slug+": duplicate source CTA before the article text")
        if 'class="article-visual"' not in page or 'fetchpriority="high"' not in page:
            raise RuntimeError(slug+": article hero lacks high-priority image loading")
        # The optional video never replaces a readable still, and it must
        # match the source article + local manifest rather than a guessed URL.
        recorded_clip = article_clip(item)
        if recorded_clip:
            for token in (
                'class="article-visual article-video"',
                'controls playsinline muted loop preload="metadata"',
                'poster="'+generated_url(item)+'"',
                '<source src="'+clip_url(item)+'?v='+recorded_clip["sha256"][:12]+'" type="video/mp4">',
                '"@type": "VideoObject"',
            ):
                if token not in page:
                    raise RuntimeError(slug+": verified clip metadata or player missing: "+token)
            if 'autoplay' in page:
                raise RuntimeError(slug+": video must not autoplay")
            # The display image is available even if the codec or browser rejects video.
            if 'data-video-fallback' not in page:
                raise RuntimeError(slug+": missing explicit playable-video image fallback")
            # The existing photo is always an image fallback, even if video fails.
            if 'src="'+generated_url(item)+'"' not in page:
                raise RuntimeError(slug+": video poster image fallback lost")
            # A few HEAD-free GET probes per build; do not download every MP4.
            if slug in {a["slug"] for a in recent[:2]}:
                movie, movie_mime = get(clip_url(item))
                if ("video/mp4" not in movie_mime or
                    hashlib.sha256(movie).hexdigest() != recorded_clip["sha256"]):
                    raise RuntimeError(slug+": live optional clip has wrong MIME or SHA-256")
        elif 'class="article-visual article-video"' in page:
            raise RuntimeError(slug+": unverified motion clip leaked into public site")
        # Only link to a real serial episode, with a clear editorial boundary.
        chosen_serial=choose_serial(item,serials)
        if chosen_serial:
            # Share the source of truth with site rendering to prevent drift.
            linked_chapter = primary_episode(chosen_serial)
            recommended_path="/serials/"+chosen_serial["slug"]+"/"+linked_chapter["slug"]+"/"
            for required in ('class="article-serial-bridge"',
                             'href="'+recommended_path+'"',
                             '/serials/'+chosen_serial["slug"]+'/cover.svg',
                             '<span class="article-serial-bridge-label">'+
                             ("現実の市場分析" if chosen_serial["kind"]=="analysis" else "創作推理小説")+
                             '</span>'):
                if required not in page:
                    raise RuntimeError(slug+": editorial serial discovery is not published: "+required)
            if chosen_serial["kind"]=="analysis" and "投資助言・売買推奨ではありません。" not in page:
                raise RuntimeError(slug+": market analysis bridge missing its disclosure")
        elif 'class="article-serial-bridge"' in page:
            raise RuntimeError(slug+": unrelated serial was forced into fictional news")
        verify_pv_recommendations(data.decode("utf-8"), sorted_articles, item)
        for widget in ('data-future-poll data-poll-article="'+slug+'"',
                       'data-poll-choice="plausible"', 'data-poll-choice="unlikely"'):
            if widget not in page:
                raise RuntimeError(slug+": future prediction UI not deployed: "+widget)
        for token in ('class="reader-next"', 'class="share-control"', '"@type": "BreadcrumbList"'):
            if token not in page:
                raise RuntimeError(slug + ": reader path / breadcrumb missing: " + token)
        # Publicly inspect the actual image picked by the review gate.
        # Merely having a cached stock file must NEVER imply it is suitable.
        if reviewed_generated_scene(item,root=ROOT):
            image_path=generated_url(item)
            if 'src="'+image_path+'"' not in page:
                raise RuntimeError(slug+": locally generated staged photograph not displayed")
            if "再現イメージ（合成）" not in page or '<figcaption>再現イメージ（合成）' not in page:
                raise RuntimeError(slug+": staged photo needs a concise synthetic-image disclosure")
            if ('<meta property="og:image" content="'+SITE+image_path+'">' not in page):
                raise RuntimeError(slug+": local staged photograph missing from OGP")
            image,kind=get(image_path)
            verify_generated_image(item, image, kind, root=ROOT)
        elif uses_stock_photo(item):
            photo=item["photo"]
            if photo["source"] not in page or photo["credit"] not in page:
                raise RuntimeError(slug+": reviewed photo attribution missing")
            if 'src="/photos/'+slug+'.jpg"' not in page:
                raise RuntimeError(slug+": expected reviewed photo not displayed")
            if ('<meta property="og:image" content="'+SITE+'/photos/'+slug+'.jpg">' not in page):
                raise RuntimeError(slug+": reviewed social photo metadata missing")
            image,kind=get("/photos/"+slug+".jpg")
            if image[:3]!=bytes((255,216,255)) or len(image)<16000 or "image/jpeg" not in kind:
                raise RuntimeError(slug+": reviewed JPEG not accessible")
            checked_photos+=1
        else:
            if 'src="/images/'+slug+'.svg"' not in page:
                raise RuntimeError(slug+": safe original illustration not displayed")
            if "創作イラスト" not in page:
                raise RuntimeError(slug+": image not clearly marked as fictional artwork")
            image_path=social_image(item)
            og_url=SITE+image_path
            if '<meta property="og:image" content="'+og_url+'">' not in page:
                raise RuntimeError(slug+": original story PNG metadata missing")
            picture,kind=get(image_path)
            verify_scene_social_image(item,picture,kind,root=ROOT)
            cover,kind=get("/images/"+slug+".svg")
            if "image/svg+xml" not in kind:
                raise RuntimeError(slug+": original SVG has incorrect Content-Type")
            if b"data-visual-topic=" not in cover or b"<text" in cover or b"<image" in cover:
                raise RuntimeError(slug+": text-free editorial illustration not deployed")
            try:
                parsed=ET.fromstring(cover)
                if parsed.attrib.get("viewBox")!="0 0 1200 675" or parsed.attrib.get("role")!="img":
                    raise RuntimeError(slug+": illustration aspect ratio or image role is wrong")
            except ET.ParseError:
                raise RuntimeError(slug+": original illustration XML is invalid")
        print("Public article OK:",slug)

    # Keep the live checker synchronized with the site's category routes.
    category_paths = CATEGORY_PATHS
    counts=Counter(a["category"] for a in articles)
    for cat,number in counts.items():
        if number>=3:
            if SITE+"/category/"+category_paths[cat]+"/" not in siteurls:
                raise RuntimeError("Missing indexed category "+cat)
    # RSS discovery is a free returning-reader path, not a new thin index
    # page. Check first-party feeds and that every item is truly in its genre.
    feed_namespace={"media":"http://search.yahoo.com/mrss/",
                    "atom":"http://www.w3.org/2005/Atom"}
    for category, total in counts.items():
        feed="/feeds/"+category_paths[category]+".xml"
        body_bytes, content_type=get(feed)
        if "xml" not in content_type:
            raise RuntimeError("Category RSS not served as XML: "+category)
        channel=ET.fromstring(body_bytes).find("channel")
        if channel is None:
            raise RuntimeError("Missing category RSS channel: "+category)
        if category not in (channel.findtext("title") or ""):
            raise RuntimeError("Wrong category RSS title: "+category)
        self_link=channel.find("atom:link",feed_namespace)
        if self_link is None or self_link.get("href")!=SITE+feed:
            raise RuntimeError("Category RSS canonical link missing: "+category)
        expected_items=sorted(
            [a for a in articles if a["category"]==category],
            key=lambda a:(a.get("published_at") or a["source"]["published_at"],
                          a["source"]["published_at"]),reverse=True
        )[:30]
        entries=channel.findall("item")
        if len(entries)!=min(total,30):
            raise RuntimeError("Wrong category RSS article count: "+category)
        for entry, article in zip(entries,expected_items):
            if entry.findtext("link")!=SITE+"/articles/"+article["slug"]+"/":
                raise RuntimeError("Wrong category RSS article: "+article["slug"])
            thumb=entry.find("media:thumbnail",feed_namespace)
            if thumb is None or not thumb.get("url","").startswith(SITE+"/"):
                raise RuntimeError("First-party category RSS thumbnail missing")
        if 'href="'+feed+'" type="application/rss+xml"' not in archive_html:
            raise RuntimeError("RSS feed is not discoverable: "+feed)
    print("Public category RSS OK:",len(counts))
    robots,_=get("/robots.txt")
    if b"Sitemap: https://veripsa.com/sitemap.xml" not in robots:
        raise RuntimeError("Missing robots sitemap discovery")
    verify_icon_crawl_rules(robots)
    ads,_=get("/ads.txt")
    if ads.strip()!=b"google.com, pub-6754575901141756, DIRECT, f08c47fec0942fa0":
        raise RuntimeError("AdSense ownership file mismatch")
    # GET-only validation: ensure the deployed Worker exposes coarse aggregates
    # without ever generating a new visit, reaction or comment.
    traffic,_=get("/api/engagement?articles="+articles[0]["slug"])
    payload=json.loads(traffic)
    channel_counts=payload.get("channels")
    names={"x","search","external","unattributed"}
    if not isinstance(channel_counts,dict) or set(channel_counts)!=names:
        raise RuntimeError("Coarse traffic attribution API is not deployed")
    if any(type(channel_counts[k]) is not int or channel_counts[k]<0 for k in names):
        raise RuntimeError("Coarse traffic attribution contains invalid counters")
    # This is an existing GET; the extra field must be present after an API
    # deployment before daily snapshots start requiring it.
    first_article=payload.get("articles",{}).get(articles[0]["slug"],{})
    if (not isinstance(first_article,dict) or
        type(first_article.get("continuedReaders")) is not int or
        first_article["continuedReaders"]<0):
        raise RuntimeError("Anonymous article-continuation metric not deployed")
    arrivals=first_article.get("arrivals")
    if (not isinstance(arrivals,dict) or set(arrivals)!={"x","search"} or
        any(type(arrivals[k]) is not int or arrivals[k]<0 for k in ("x","search"))):
        raise RuntimeError("Per-article X/search arrival counters missing or invalid")
    daily=payload.get("today")
    if (not isinstance(daily,dict)
        or not isinstance(daily.get("dateJst"),str)
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", daily["dateJst"])
        or any(type(daily.get(key)) is not int or daily[key]<0
               for key in ("visitors","articleReads"))):
        raise RuntimeError("Daily JST audience counters missing or invalid")
    yesterday=payload.get("yesterday")
    if (not isinstance(yesterday,dict)
        or not isinstance(yesterday.get("dateJst"),str)
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", yesterday["dateJst"])
        or any(type(yesterday.get(key)) is not int or yesterday[key]<0
               for key in ("visitors","articleReads"))):
        raise RuntimeError("Previous JST audience counters missing or invalid")
    if (type(first_article.get("todayReaders")) is not int
        or first_article["todayReaders"]<0):
        raise RuntimeError("Daily per-story browser count missing or invalid")
    verify_pageview_contract(payload, articles[0]["slug"])
    # Re-read the latest measured hour explicitly. This catches deployments
    # where the live summary exists but past-hour source/article diagnosis is stale.
    hourly_today=payload.get("hourly",{}).get("today")
    measured_hours=[
        row for row in hourly_today or []
        if isinstance(row,dict)
        and row.get("pageViewsCoverage") in {"partial","complete","in-progress"}
        and type(row.get("pageViews")) is int and row["pageViews"]>=0
        and isinstance(row.get("hourJst"),str)
    ]
    if not measured_hours:
        raise RuntimeError("Hourly PV diagnostics are not deployed")
    latest_hour=max(measured_hours,key=lambda row:row["hourJst"])["hourJst"][:13]
    selected_bytes,_=get(
        "/api/engagement?articles="+articles[0]["slug"]+"&hour="+latest_hour
    )
    selected_payload=json.loads(selected_bytes)
    selected=selected_payload.get("selectedHour")
    selected_top=selected_payload.get("topArticles",{}).get("selectedHour")
    if (not isinstance(selected,dict)
            or selected.get("hourJst")!=latest_hour+":00"
            or selected.get("pageViewsCoverage") not in
               {"partial","complete","in-progress"}
            or type(selected.get("pageViews")) is not int
            or selected["pageViews"]<0
            or not isinstance(selected_top,list)):
        raise RuntimeError("Historical hour PV/article diagnosis is not deployed")
    source_coverage=selected.get("pageViewSourcesCoverage")
    selected_sources=selected.get("pageViewSources")
    if source_coverage=="unmeasured":
        if selected_sources is not None:
            raise RuntimeError("Unmeasured historical source mix must remain null")
    elif (source_coverage not in {"partial","complete","in-progress"}
          or not isinstance(selected_sources,dict)
          or set(selected_sources)!=names
          or any(type(selected_sources[k]) is not int or selected_sources[k]<0
                 for k in names)):
        raise RuntimeError("Historical hour source attribution is missing or invalid")
    votes=payload.get("articles",{}).get(articles[0]["slug"],{}).get("votes")
    if not isinstance(votes,dict) or any(type(votes.get(k)) is not int or votes[k]<0
                                           for k in ("plausible","unlikely")):
        raise RuntimeError("Future verdict counters missing or invalid")
    print(f"OK: {len(articles)} indexed article URLs; {len(recent)} recent pages; {checked_photos} cached images; real SEO dates; whole-archive recent-PV recommendations; separate site/article PV with coverage; free channel attribution; free hosting.")

def main():
    last=None
    for i in range(15):
        try:
            check()
            return
        except (ValueError,AssertionError,RuntimeError,ET.ParseError,urllib.error.URLError,TimeoutError) as exc:
            last=exc
            print(f"Read-only live check {i+1}/15: {exc}",flush=True)
            if i<14:
                time.sleep(6)
    sys.exit("Production did not match GitHub's latest commit: "+str(last))

if __name__=="__main__":
    main()

