#!/usr/bin/env python3
"""Build original, clearly labelled fictional-news pages. No third-party packages."""
from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import struct
import subprocess
import zlib
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from functools import lru_cache
from pathlib import Path

if __package__:
    from .social_cards import render_social_card, render_issue_card
    from .editorial_covers import render_cover, render_story_visual, story_subject, reviewed_story_scene
    from .media_policy import usable_cached_photo
    from .generated_photos import generated_url
    from .visual_review import reviewed_generated_scene
    from .broadcast_captions import story_cues
    from .motion_clips import usable_clip, clip_url, RELATIVE_DIR as CLIP_DIR
    from .serials import load_serials, render_serials_feature, serial_routes, build_serials
    from .serial_discovery import render_serial_bridge
    from .share_desk import render as render_share_desk
    from .growth_desk import render as render_growth_desk
    from .social_text import article_hashtags, X_TEXT_LIMIT, x_weighted_length, truncate_x_text
    from .editorial_copy import reader_copy, source_reader_copy
    from .reader_paths import suggested_articles
else:
    from social_cards import render_social_card, render_issue_card
    from editorial_covers import render_cover, render_story_visual, story_subject, reviewed_story_scene
    from media_policy import usable_cached_photo
    from generated_photos import generated_url
    from visual_review import reviewed_generated_scene
    from broadcast_captions import story_cues
    from motion_clips import usable_clip, clip_url, RELATIVE_DIR as CLIP_DIR
    from serials import load_serials, render_serials_feature, serial_routes, build_serials
    from serial_discovery import render_serial_bridge
    from share_desk import render as render_share_desk
    from growth_desk import render as render_growth_desk
    from social_text import article_hashtags, X_TEXT_LIMIT, x_weighted_length, truncate_x_text
    from editorial_copy import reader_copy, source_reader_copy
    from reader_paths import suggested_articles
from urllib.parse import quote, urlencode
from xml.etree import ElementTree as ET

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "dist"
SITE = "https://veripsa.com"
# Preserve the precise cascade order of the former eight render-blocking links.
# Keep original source files published (e.g. comments-admin.html), but pages
# request one content-fingerprinted, first-party stylesheet instead.
SITE_STYLESHEETS = (
    "style.css",
    "article-extra.css",
    "newsroom.css",
    "future-poll.css",
    "comments.css",
    "editorial-polish.css",
    "serials.css",
    "reader-first.css",
)
SITE_CSS_PATH = "/assets/newspaper.css"


@lru_cache(maxsize=1)
def bundled_stylesheets():
    """Build the original cascade as one static CSS file, with no paid bundler."""
    parts = ['@charset "UTF-8";\n']
    for name in SITE_STYLESHEETS:
        source = (BASE / "assets" / name).read_text(encoding="utf-8")
        # Only the first declaration in a CSS resource can be @charset.
        source = re.sub(r"^\s*@charset\s+['\"]utf-8['\"];\s*", "", source, count=1, flags=re.I)
        parts.append(f"\n/* SOURCE {name} */\n{source}\n")
    return "".join(parts)


@lru_cache(maxsize=1)
def stylesheet_url():
    """A content digest prevents browsers from reusing an outdated CSS bundle."""
    digest = hashlib.sha256(bundled_stylesheets().encode("utf-8")).hexdigest()[:12]
    return f"{SITE_CSS_PATH}?v={digest}"


@lru_cache(maxsize=None)
def asset_url(name):
    """Fingerprint reader JavaScript so new HTML never points at stale behavior."""
    if not re.fullmatch(r"[a-z0-9-]+\.js", name):
        raise ValueError("Invalid reader asset name")
    source = (BASE / "assets" / name).read_bytes()
    digest = hashlib.sha256(source).hexdigest()[:12]
    return f"/assets/{name}?v={digest}"


def script_tag(name):
    return f'<script defer src="{asset_url(name)}"></script>'
CATEGORIES = ("すべて", "社会", "事件", "経済", "技術", "政治", "暮らし", "スポーツ", "エンタメ")
ARTS = ("notice", "cards", "tower", "robot", "meeting", "generic")
CATEGORY_PATHS = {
    "社会":"society", "事件":"incidents", "経済":"economy", "技術":"technology",
    "政治":"politics", "暮らし":"lifestyle",
    "スポーツ":"sports", "エンタメ":"entertainment"
}
# Editorial category descriptions help readers choose without keyword stuffing.
CATEGORY_INTROS = {
    "社会":"親切な制度ほど、なぜか不便。暮らしの矛盾を切り取る社会風刺。",
    "事件":"架空の事件とまちの珍事。小さな謎を現場の行動とオチで読む。",
    "経済":"値上げ、節約、働き方。数字の理屈が少し暴走した世界のニュース。",
    "技術":"便利になるほど増える手間。AIと技術の奇妙な日常を描く。",
    "政治":"会議も説明も立派なのに、なぜか話が進まない。政治の風刺。",
    "暮らし":"家族、買い物、毎日の小さな面倒。笑えるほど身近な出来事。",
    "スポーツ":"勝つこと、応援すること。熱すぎるルールと期待をニュースに。",
    "エンタメ":"推し活、配信、考察。好きが行きすぎた世界のニュース。",
}


def curated_categories(articles):
    """Create only substantial category archives, not thin or empty SEO pages."""
    return {name: [a for a in articles if a["category"]==name]
            for name in CATEGORY_PATHS
            if sum(a["category"]==name for a in articles)>=3}


def category_url(name):
    return "/category/"+CATEGORY_PATHS[name]+"/"


def category_feed_url(name):
    """Static per-topic RSS: no dependency on paid APIs or dynamic Workers."""
    return "/feeds/" + CATEGORY_PATHS[name] + ".xml"


TOPIC_MIN_INDEXABLE_ARTICLES = 3
PERSON_ATTACKING_SATIRE_TAGS = frozenset({
    "社畜", "情弱", "老害", "ドパガキ", "フレネミー",
})


def article_tags(article):
    """All reader-facing labels: durable topics first, playful satire lenses second."""
    return list(dict.fromkeys([
        *article.get("tags", []),
        *article.get("satire_tags", []),
    ]))


def topic_groups(articles):
    groups = {}
    for article in articles:
        for tag in article_tags(article):
            groups.setdefault(tag, []).append(article)
    return groups


def curated_topics(articles):
    """Only substantial topic pages enter the sitemap; thin pages remain reader-only."""
    return {
        tag: items for tag, items in topic_groups(articles).items()
        if len(items) >= TOPIC_MIN_INDEXABLE_ARTICLES
    }


def topic_is_satire(tag, items):
    return any(tag in item.get("satire_tags", []) for item in items)


def related_topics(tag, items, articles, limit=6):
    """Indexable topic hubs that genuinely co-occur with this topic's stories."""
    if limit <= 0:
        return []
    indexable = curated_topics(articles)
    scores = Counter()
    item_slugs = {item["slug"] for item in items}
    for other_tag, other_items in indexable.items():
        if other_tag == tag:
            continue
        overlap = sum(item["slug"] in item_slugs for item in other_items)
        if overlap:
            scores[other_tag] = overlap
    return [
        other_tag for other_tag, _ in sorted(
            scores.items(),
            key=lambda pair: (
                -pair[1],
                -len(indexable[pair[0]]),
                topic_is_satire(pair[0], indexable[pair[0]]),
                pair[0],
            ),
        )[:limit]
    ]


def issue_url(day):
    """Actual publication date in JST, never the fictional year."""
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day):
        raise ValueError("Invalid real issue date")
    datetime.fromisoformat(day)  # validate month/day before creating a route
    return "/issues/" + day + "/"


def issue_date_label(day):
    d = datetime.fromisoformat(day)
    return f"{d.year}年{d.month}月{d.day}日"


def daily_issues(articles):
    """Only substantial daily editions, never thin index pages of 1-2 articles."""
    groups = {}
    for article in articles:
        day = publication_date(article)[:10]
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day):
            datetime.fromisoformat(day)
            groups.setdefault(day, []).append(article)
    return {
        day: sorted(groups[day], key=lambda a: (
            publication_date(a), a["source"]["published_at"]), reverse=True)
        for day in sorted(groups, reverse=True)
        if len(groups[day]) >= 3
    }


def escape(value):
    return html.escape(str(value), quote=True)


def date_jp(iso):
    d = datetime.fromisoformat(iso)
    return f"{d.year}年{d.month}月{d.day}日 {d.hour:02}:{d.minute:02}"


def five_years_later(iso):
    date = datetime.fromisoformat(iso)
    return date.replace(year=date.year + 5).isoformat()


def publication_date(a):
    """Real publication date, never the fictional dateline five years ahead."""
    return a.get("published_at") or a["source"]["published_at"][:10]


def modification_date(a):
    """Use only genuine recorded content updates for sitemap and search metadata."""
    return a.get("modified_at") or publication_date(a)


MAX_FOLLOWUP_CHAIN_ARTICLES = 3  # initial story + at most two follow-ups


def validate_followup_relationships(articles):
    """A follow-up is a later story in a deliberately short topic chain."""
    by_slug = {a["slug"]: a for a in articles}
    for article in articles:
        parent_slug = article.get("followup_of")
        if parent_slug is None:
            continue
        if (not isinstance(parent_slug, str)
                or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", parent_slug)
                or parent_slug == article["slug"]
                or parent_slug not in by_slug):
            raise ValueError("Invalid followup_of: " + article["slug"])
        parent = by_slug[parent_slug]
        if publication_date(parent) >= publication_date(article):
            raise ValueError("Follow-up must be published after its parent: " + article["slug"])

    for article in articles:
        depth = 1
        cursor = article
        while cursor.get("followup_of"):
            depth += 1
            if depth > MAX_FOLLOWUP_CHAIN_ARTICLES:
                raise ValueError(
                    "Follow-up chain exceeds initial story plus two follow-ups: "
                    + article["slug"]
                )
            cursor = by_slug[cursor["followup_of"]]


def followup_parent(article, articles):
    parent_slug = article.get("followup_of")
    if not parent_slug:
        return None
    return next((item for item in articles if item["slug"] == parent_slug), None)


def followup_children(article, articles):
    return sorted(
        (item for item in articles if item.get("followup_of") == article["slug"]),
        key=lambda item: (publication_date(item), item["source"]["published_at"]),
        reverse=True,
    )


def editorial_lead(articles):
    """Show the newest *actual publication* in the front-page lead.

    Earlier curation could pin an older pick for up to 48 hours, making an
    actively updated newsroom look stale. Measured PV recommendations appear
    separately, after their complete statistics have arrived.
    Use the same keys as load(); never use the fictional date or modified_at.
    """
    if not articles:
        return None
    return max(articles, key=lambda a: (
        publication_date(a), a["source"]["published_at"]
    ))


def load():
    articles = json.loads((BASE / "content/articles.json").read_text(encoding="utf-8"))
    if not isinstance(articles, list):
        raise ValueError("Articles must be a list")
    seen = set()
    for a in articles:
        required = ("slug", "title", "summary", "category", "fiction_at", "paragraphs", "source", "art", "tags")
        if any(k not in a for k in required):
            raise ValueError("Missing article field")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", a["slug"]) or a["slug"] in seen:
            raise ValueError("Invalid or duplicate slug: " + a["slug"])
        seen.add(a["slug"])
        if a["category"] not in CATEGORIES[1:] or a["art"] not in ARTS:
            raise ValueError("Invalid category/art")
        tags = a["tags"]
        satire_tags = a.get("satire_tags", [])
        invalid_label = lambda tag: (
            not isinstance(tag, str) or tag != tag.strip()
            or not 1 <= len(tag) <= 12 or re.search(r"[\s#|/?&]", tag)
        )
        if (not isinstance(tags, list) or not 1 <= len(tags) <= 3
                or len(tags) != len(set(tags))
                or a["category"] in tags
                or any(invalid_label(tag) for tag in tags)):
            raise ValueError("Article tags must be 1-3 distinct reader topics: " + a["slug"])
        if (not isinstance(satire_tags, list) or len(satire_tags) > 2
                or len(satire_tags) != len(set(satire_tags))
                or a["category"] in satire_tags
                or set(tags) & set(satire_tags)
                or any(tag in PERSON_ATTACKING_SATIRE_TAGS for tag in satire_tags)
                or any(invalid_label(tag) for tag in satire_tags)):
            raise ValueError("Satire tags must be 0-2 distinct non-abusive lenses: " + a["slug"])
        source = a["source"]
        if not source["url"].startswith("https://"):
            raise ValueError("Source must use HTTPS")
        if five_years_later(source["published_at"]) != a["fiction_at"]:
            raise ValueError("Fictional datetime must be exactly five years later: " + a["slug"])
        if a.get("photo"):
            p = a["photo"]
            if not p.get("url","").startswith("https://images.unsplash.com/photo-"):
                raise ValueError("Photo CDN must be Unsplash")
            if not p.get("source","").startswith("https://unsplash.com/photos/") or not p.get("credit") or p.get("license") != "Unsplash" or not p.get("alt"):
                raise ValueError("Photo requires verified Unsplash license and attribution")
        if len(a["paragraphs"]) < 2 or len(a["paragraphs"]) > 6:
            raise ValueError("Article must contain 2-6 paragraphs")
    validate_followup_relationships(articles)
    # Source timestamps establish fictional dates; actual site publication controls
    # recency and the home-page lead, so a newly published item never starts buried.
    return sorted(articles, key=lambda a: (
        a.get("published_at") or a["source"]["published_at"],
        a["source"]["published_at"],
    ), reverse=True)


def write(path, text):
    target = OUT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def article_url(a):
    return "/articles/" + a["slug"] + "/"


def tag_url(tag):
    """Stable crawlable path; indexability depends on how much useful content exists."""
    return "/topics/" + quote(tag, safe="") + "/"


def tag_links(a, compact=False):
    """Show subject tags plus a small number of playful, non-abusive satire lenses."""
    tags = article_tags(a)
    if compact:
        tags = tags[:3]
    if not tags:
        return ""
    satire = set(a.get("satire_tags", []))
    suffix = " story-tags--compact" if compact else ""
    return (
        f'<nav class="story-tags{suffix}" aria-label="話題・風刺タグ">'
        + "".join(
            f'<a class="{"satire-tag" if tag in satire else "topic-tag"}" '
            f'href="{escape(tag_url(tag))}">#{escape(tag)}</a>'
            for tag in tags
        )
        + "</nav>"
    )


def category_meta(a, compact=False):
    """Make the real publication timestamp distinct from the fictional dateline.

    Some legacy stories only record a calendar date. Never invent midnight.
    All article lists are sorted by publication_date(), not by fiction_at.
    """
    published = publication_date(a)
    real = datetime.fromisoformat(published)
    future = datetime.fromisoformat(a["fiction_at"])
    real_label = f"公開 {real.year}/{real.month}/{real.day}"
    if "T" in published:
        real_label += f" {real.hour:02}:{real.minute:02}"
    future_label = (
        f"紙面 {future.year}/{future.month}/{future.day}"
        f" {future.hour:02}:{future.minute:02}"
    )
    page_views = (
        f'<span class="article-pv" data-article-pv="{escape(a["slug"])}"'
        ' title="計測開始後のページ表示回数" aria-label="この記事のPVを取得中">'
        '<strong data-article-pv-count>—</strong><span>PV</span></span>'
    )
    followup_badge = '<span class="followup-label">続報</span>' if a.get("followup_of") else ""
    if compact:
        # Dense cards need one truthful date and one clear fiction label.
        # The full five-years-later dateline remains on every article page.
        compact_date = f"公開 {real.year}/{real.month}/{real.day}"
        if "T" in published:
            compact_date += f" {real.hour:02}:{real.minute:02}"
        return (
            '<div class="story-meta story-meta--compact">'
            + '<span class="category">' + escape(a["category"]) + "</span>"
            + followup_badge
            + f'<time class="real-published-at" datetime="{escape(published)}"'
              f' aria-label="実際の掲載日時">{escape(compact_date)}</time>'
            + page_views
            + "</div>"
        )
    return (
        '<div class="story-meta">'
        + '<span class="ticker">' + escape(a.get("flash", "速報")) + "</span>"
        + '<span class="category">' + escape(a["category"]) + "</span>"
        + followup_badge
        + f'<time class="real-published-at" datetime="{escape(published)}"'
          f' aria-label="実際の掲載日時">{escape(real_label)}</time>'
        + f'<time class="fiction-dateline" datetime="{escape(a["fiction_at"])}"'
          f' aria-label="紙面上の未来日付">{escape(future_label)}</time>'
        + page_views
        + "</div>"
    )


def share_url(a):
    url = SITE + article_url(a) + "?utm_source=x&utm_medium=social&utm_campaign=reader_share&utm_content=topic_card_v2"
    # Small standalone hook for the reader's X draft. Does not post automatically.
    prefix = "【未発生新聞】\n"
    ending = "\n\n" + (" ".join(article_hashtags(a)) or "#未発生新聞")
    fixed = prefix + ending + "\n\n" + url
    title = truncate_x_text(a["title"].strip(),
                            X_TEXT_LIMIT - x_weighted_length(fixed))
    heading = prefix + title
    remaining = X_TEXT_LIMIT - x_weighted_length(heading + ending + "\n\n" + url) - 2
    teaser = truncate_x_text(" ".join(reader_copy(a["summary"])[:75].split()), max(0, remaining))
    text = heading + ("\n\n" + teaser if teaser else "") + ending
    return "https://x.com/intent/tweet?" + urlencode({"text": text, "url": url})


def issue_share_url(day, items):
    """Reader-initiated X draft with the true issue date and selected headlines."""
    stamp = datetime.fromisoformat(day)
    lines = [
        f"【未発生新聞 {stamp.month}/{stamp.day}号】",
        f"今号の記事は{len(items)}本。",
    ]
    for item in items[:2]:
        headline = item["title"]
        lines.append("・" + headline[:27] + ("…" if len(headline) > 27 else ""))
    lines.append("#未発生新聞")
    tagged = SITE + issue_url(day) + "?utm_source=x&utm_medium=social&utm_campaign=daily_edition"
    return "https://x.com/intent/tweet?" + urlencode({"text": "\n".join(lines), "url": tagged})


def bookmark_button(a):
    """Opt-in reader bookmark, disabled until the no-cost local script is ready."""
    return (
        f'<button type="button" class="bookmark-button"'
        f' data-bookmark-slug="{escape(a["slug"])}"'
        f' data-bookmark-title="{escape(a["title"])}"'
        f' data-bookmark-category="{escape(a["category"])}"'
        ' disabled aria-pressed="false">☆ あとで読む</button>'
    )


def actions(a, compact=False):
    slug = escape(a["slug"])
    link = article_url(a)
    share = escape(share_url(a))
    return (
        '<div class="engagement-actions' + (" compact" if compact else "") + '">'
        + f'<button class="like-control" type="button" data-like-slug="{slug}" aria-pressed="false"'
        + f' aria-label="この記事にいいね"><span aria-hidden="true">♡</span><span class="like-label">いいね</span><span class="like-count" data-like-count="{slug}" hidden></span></button>'
        + f'<a class="comment-control" href="{escape(link)}#reader-comments" aria-label="この記事のコメント欄へ">'
        + f'<span aria-hidden="true">💬</span><span>コメント</span><span class="comment-count" data-comment-count="{slug}" hidden></span></a>'
        + f'<a class="share-control" data-share-slug="{slug}" href="{share}" target="_blank" rel="noopener noreferrer" aria-label="Xで記事を共有">'
        + '<span aria-hidden="true">𝕏</span><span>Xで共有</span></a>'
        + ('' if compact else f'<button class="copy-control" type="button" data-copy-url="{escape(SITE + link)}">'
           '<span aria-hidden="true">↗</span><span>リンクをコピー</span></button>')
        + '</div>'
    )

def cover(a):
    """Article artwork has no baked-in headline; text remains accessible HTML."""
    return render_story_visual(a)


def illustration_path(a):
    return "/images/" + a["slug"] + ".svg"


def illustration_only(a):
    """Explicit editorial routing wins over previously accepted photographic assets."""
    return a.get("visual_strategy", "cpu-photo") == "illustration"


def uses_generated_scene(a):
    return not illustration_only(a) and reviewed_generated_scene(a, root=BASE)


def uses_stock_photo(a):
    """Illustration routing and reviewed story scenes both outrank stock photos."""
    return (not illustration_only(a)
            and not reviewed_story_scene(a, root=BASE)
            and usable_cached_photo(a, root=BASE))


def editorial_image(a):
    """Prefer verified scenes, then reviewed illustrations, stock and fallback."""
    if uses_generated_scene(a):
        return generated_url(a)
    if uses_stock_photo(a):
        return "/photos/" + a["slug"] + ".jpg"
    return illustration_path(a)


def article_image_alt(a, *, generated=False, photo_ok=False):
    """Honest, article-specific description without claiming to see AI pixels.

    Generated scene prompts are not guaranteed to match each rendered image.
    The headline identifies the depicted story context; the caption carries
    detailed provenance. The alt is useful when images fail or in screen readers.
    """
    if generated:
        return "創作ニュース「" + a["title"] + "」の再現イメージ"
    if photo_ok:
        return a["photo"]["alt"] + "（記事のイメージ写真）"
    scene = reviewed_story_scene(a, root=BASE)
    if scene:
        return ET.fromstring(scene).attrib["aria-label"]
    return "創作ニュース「" + a["title"] + "」の編集部制作イラスト"


def image_tag(a, cls, extra=""):
    generated = uses_generated_scene(a)
    photo_ok = not generated and uses_stock_photo(a)
    url = editorial_image(a)
    alt = article_image_alt(a, generated=generated, photo_ok=photo_ok)
    fallback = (
        f' data-fallback="{escape(illustration_path(a))}"'
        f' data-fallback-alt="{escape(article_image_alt(a))}"'
        if generated or photo_ok else ""
    )
    if generated:
        extra += ' data-generated-scene="true"'
    if reviewed_story_scene(a, root=BASE):
        # Keep the reviewed SVG's full composition in compact cards, including
        # when a failed JPEG switches to this fallback. CSS also checks src so
        # the currently displayed photograph keeps its normal crop.
        extra += f' data-editorial-scene-src="{escape(illustration_path(a))}"'
    # Keep the existing 16:9 reserved image frame to avoid mobile layout shift.
    return (f'<img class="{cls}" src="{escape(url)}" alt="{escape(alt)}"'
            f' width="1200" height="675" {extra}{fallback}>')


def article_clip(article):
    """Publish reviewed footage with current captions; retain legacy compatibility."""
    if not uses_generated_scene(article):
        return None
    clip = usable_clip(article, root=BASE)
    if clip and clip.get("motion_style_version") == 3 and not story_cues(article, root=BASE):
        return None
    return clip


def clip_seconds(clip):
    """Use each verified clip's duration; old four-second clips remain valid."""
    return int(clip.get("duration_ms", 4000)) // 1000


def broadcast_cues(article, clip):
    """Editorial captions belong to the exact story, never stale revised text."""
    if clip.get("motion_style_version") != 3:
        return []
    return story_cues(article, root=BASE)


def broadcast_vtt(article, clip):
    """Expose the same vetted captions to native fullscreen video players."""
    cues = broadcast_cues(article, clip)
    if not cues:
        return ""
    blocks = ["WEBVTT"]
    for index, cue in enumerate(cues):
        end = cues[index + 1]["at"] if index + 1 < len(cues) else clip_seconds(clip)
        # These are plain editorial words, not WebVTT tags or extra cue blocks.
        label = html.escape(" ".join(cue["label"].split()), quote=False)
        text = html.escape(" ".join(cue["text"].split()), quote=False)
        blocks.append(f'00:00:{cue["at"]:02d}.000 --> 00:00:{end:02d}.000\n{label}\n{text}')
    return "\n\n".join(blocks) + "\n"


def broadcast_track_url(article, clip):
    subtitles = broadcast_vtt(article, clip)
    if not subtitles:
        return ""
    version = hashlib.sha256(subtitles.encode("utf-8")).hexdigest()[:12]
    return clip_url(article)[:-4] + ".vtt?v=" + version


def broadcast_track(article, clip):
    url = broadcast_track_url(article, clip)
    if not url:
        return ""
    # The player shows this track in native fullscreen and hides it inline,
    # where the readable newsroom overlay already displays the same words.
    return (f'<track kind="subtitles" srclang="ja" label="日本語"'
            f' src="{escape(url)}" data-broadcast-track>')


def broadcast_overlay(article, clip):
    """Short readable captions share the video clock on home and article pages."""
    cues = broadcast_cues(article, clip)
    label = cues[0]["label"] if cues else article["category"]
    caption = cues[0]["text"] if cues else article["title"]
    cue_data = escape(json.dumps(cues, ensure_ascii=False, separators=(",", ":")))
    counter = "01 / 03" if cues else f'{clip_seconds(clip)}秒'
    return (
        f'<div class="broadcast-super" data-broadcast-overlay data-cues="{cue_data}" aria-hidden="true">'
        '<span class="broadcast-mark">未発生ニュース</span>'
        f'<span class="broadcast-counter" data-broadcast-counter>{counter}</span>'
        '<div class="broadcast-lower-third" data-broadcast-caption>'
        f'<span data-broadcast-label>{escape(label)}</span>'
        f'<strong data-broadcast-text>{escape(caption)}</strong></div>'
        '<div class="broadcast-progress"><span data-broadcast-progress></span></div>'
        '</div>'
    )


def clip_caption(article):
    clip = article_clip(article)
    return (f" · {clip_seconds(clip)}秒・音声なし" if clip else "")


def article_hero_media(a):
    """Only vetted MP4s become video players; preserve a real image on failure."""
    picture = image_tag(a, "article-visual",
                        extra='loading="eager" fetchpriority="high" decoding="async"')
    clip = article_clip(a)
    if clip is None:
        return picture
    version = clip["sha256"][:12]  # prevent stale CDNs/browsers after re-encoding
    fallback = image_tag(a, "article-visual",
                         extra='hidden loading="eager" fetchpriority="high" decoding="async" data-video-fallback')
    return (
        '<div class="article-video-shell" data-video-shell>'
        '<video class="article-visual article-video" controls playsinline muted loop'
        ' preload="metadata"'
        f' poster="{escape(editorial_image(a))}"'
        ' width="1200" height="675"'
        f' aria-label="{escape("創作ニュース「" + a["title"] + "」の" + str(clip_seconds(clip)) + "秒の無音演出映像")}">'
        f'<source src="{escape(clip_url(a))}?v={version}" type="video/mp4">'
        + broadcast_track(a, clip) + '</video>' + fallback
        + (broadcast_overlay(a, clip) if broadcast_cues(a, clip) else '') + '</div>'
    )


def broadcast_playlist(articles, *, exclude_slug=None):
    """A small editorial selection, newest verified video first, with no requests."""
    items = []
    for article in articles:
        if article.get("broadcast_feature") is not True or article["slug"] == exclude_slug:
            continue
        clip = article_clip(article)
        if not clip:
            continue
        published = publication_date(article)
        stamp = datetime.fromisoformat(published)
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone(timezone(timedelta(hours=9)))
        published_label = f"{stamp.year}/{stamp.month}/{stamp.day}"
        if "T" in published:
            published_label += f" {stamp.hour:02}:{stamp.minute:02}"
        version = clip["sha256"][:12]
        items.append({
            "slug": article["slug"], "key": article["slug"] + ":" + version,
            "title": article["title"], "category": article["category"],
            "article_url": article_url(article), "poster_url": editorial_image(article),
            "poster_fallback_url": illustration_path(article),
            "poster_fallback_alt": article_image_alt(article),
            "video_url": clip_url(article) + "?v=" + version,
            "track_url": broadcast_track_url(article, clip),
            "duration_seconds": clip_seconds(clip),
            "published_at": published, "published_label": published_label,
            "created_at": clip["created_at"], "cues": broadcast_cues(article, clip),
        })
    # A newly delivered video gets a turn even when its article is older. Its
    # genuine article publication date is still displayed beside the headline.
    items.sort(key=lambda item: (datetime.fromisoformat(item["created_at"]).timestamp(),
                                 item["published_at"], item["slug"]), reverse=True)
    return items[:3]


def broadcast_feature(articles, *, exclude_slug=None):
    """One player with explicit choices; never swap stories during playback."""
    playlist = broadcast_playlist(articles, exclude_slug=exclude_slug)
    if not playlist:
        return ""
    selected = playlist[0]
    chosen = next(article for article in articles if article["slug"] == selected["slug"])
    clip = article_clip(chosen)
    poster = selected["poster_url"]
    url = escape(selected["article_url"])
    payload = escape(json.dumps(playlist, ensure_ascii=False, separators=(",", ":")))
    choices = "".join(
        f'<button class="broadcast-choice" type="button" data-broadcast-choice="{index}"'
        f' aria-pressed="{str(index == 0).lower()}" aria-controls="broadcast-player">'
        f'<small>公開 <time datetime="{escape(item["published_at"])}">{escape(item["published_label"])}</time>'
        f' · {item["duration_seconds"]}秒</small><strong>{escape(item["title"])}</strong>'
        '</button>' for index, item in enumerate(playlist)
    )
    return (
        '<section class="broadcast-spotlight" aria-labelledby="broadcast-title"'
        f' data-broadcast-playlist="{payload}">'
        '<div class="broadcast-intro"><div>'
        '<span class="small-label red">UNHAPPENED TV / 映像ニュース</span>'
        '<h2 id="broadcast-title">未発生ニュース放送室</h2>'
        '</div>'
        f'<a href="{url}" class="broadcast-article-link" data-broadcast-article-link>記事全文を読む →</a></div>'
        '<div class="broadcast-screen" data-news-broadcast data-video-shell>'
        f'<img class="broadcast-poster" src="{escape(poster)}" alt=""'
        f' data-broadcast-poster-fallback="{escape(selected["poster_fallback_url"])}"'
        f' data-broadcast-poster-fallback-alt="{escape(selected["poster_fallback_alt"])}"'
        ' width="1200" height="675" loading="lazy" decoding="async">'
        '<video class="broadcast-video" controls playsinline muted loop'
        ' preload="none" width="1200" height="675" id="broadcast-player"'
        f' poster="{escape(poster)}"'
        f' aria-label="{escape(chosen["title"])}・{clip_seconds(clip)}秒の無音演出映像">'
        f'<source src="{escape(selected["video_url"])}" type="video/mp4">'
        + broadcast_track(chosen, clip) + '</video>' + broadcast_overlay(chosen, clip) + '</div>'
        f'<p class="broadcast-headline" aria-live="polite"><a href="{url}" data-broadcast-headline>{escape(chosen["title"])}</a></p>'
        f'<div class="broadcast-choices" data-broadcast-choices hidden aria-label="映像ニュースを切り替える">{choices}</div>'
        '<p class="broadcast-note">再現映像・音声なし</p>'
        '</section>'
    )


def photo_credit(a):
    """Always disclose AI staging; never mislabel a synthetic scene as real."""
    if uses_generated_scene(a):
        return "再現イメージ（合成）"
    if not uses_stock_photo(a):
        return "創作イラスト"
    p = a["photo"]
    return (f'イメージ写真：<a href="{escape(p["source"])}" target="_blank"'
            f' rel="noopener noreferrer external">{escape(p["credit"])} / Unsplash</a>')


def card(a, archive=False):
    attrs = ""
    if archive:
        category = a["category"]
        decade = a["fiction_at"][:3] + "0"
        labels = article_tags(a)
        searchable = " ".join((a["title"], reader_copy(a["summary"]), a["fiction_company"], category, *labels))
        attrs = (f' data-archive-card data-category="{escape(category)}" '
                 f'data-decade="{decade}" data-tags="{escape("|".join(labels))}" '
                 f'data-search="{escape(searchable)}"')
    clip = article_clip(a)
    clip_label = (f'<span class="story-clip-label">▶ {clip_seconds(clip)}秒映像</span>'
                  if clip else '')
    return (
        f'<article class="story-card"{attrs}><a class="story-image-link{" story-image-link--generated" if uses_generated_scene(a) else ""}" href="{article_url(a)}">'
        + image_tag(a,"story-thumb",extra='loading="lazy" decoding="async"') + clip_label + '</a>'
        + category_meta(a, compact=True)
        + f'<h3><a href="{article_url(a)}">{escape(a["title"])}</a></h3>'
        + f'<p>{escape(reader_copy(a["summary"]))}</p>'
        + tag_links(a, compact=True)
        + '</article>'
    )

def social_image(article):
    """Use verified local JPEG for OGP when present, else deterministic PNG."""
    if article is not None:
        if uses_generated_scene(article):
            return generated_url(article)
        if uses_stock_photo(article):
            return "/photos/" + article["slug"] + ".jpg"
        return "/og/articles/" + article["slug"] + ".png?v=20261103-topic2"
    # Use a physically new raster URL: X caches both page cards and image URLs.
    # The legacy brand.png remains available for previously published links.
    return "/og/brand-launch-20261102-v3.png"


def layout(title, desc, path, main, active="", kind="website", article=None, feed_url=None, collection=None, serial=None, serial_collection=None, indexable=True):
    if path == "/":
        complete = "未発生新聞｜5年後を勝手に想像する風刺ニュース"
    elif path == "/serials/":
        complete = title + " | 未発生新聞（創作と市場分析の連載）"
    elif serial is not None and serial["kind"] == "analysis":
        complete = title + " | 未発生新聞（市場分析）"
    elif serial is not None and serial["kind"] == "fiction":
        complete = title + " | 未発生新聞（創作小説）"
    else:
        complete = title + " | 未発生新聞（創作ニュース）"
    article_time_meta = ""
    # Never label the genuine, source-backed market column as fictional news.
    if path == "/serials/":
        publication_label = "創作と市場分析"
        mast_subtitle = "架空の推理小説と、公開資料に基づく市場予測。"
    elif serial is not None and serial["kind"] == "analysis":
        publication_label = "市場分析"
        mast_subtitle = "公開資料から、相場の先を考える。"
    elif serial is not None and serial["kind"] == "fiction":
        publication_label = "創作小説"
        mast_subtitle = "手掛かりを追って楽しむ、架空の推理小説。"
    else:
        publication_label = "創作報道"
        mast_subtitle = "まだ起きていないニュースを、真顔で。"
    if kind == "story" and article is not None:
        article_time_meta = (
            f'<meta property="article:published_time" content="{escape(publication_date(article))}">'
            + (f'<meta property="article:modified_time" content="{escape(article["modified_at"])}">'
               if article.get("modified_at") else "")
            + f'<meta property="article:section" content="{escape(article["category"])}">'
            + "".join(f'<meta property="article:tag" content="{escape(tag)}">'
                      for tag in article_tags(article))
        )
    # Keep reading routes in the header; utilities remain in the footer.
    nav = [
        ("/", "一面", "home"),
        ("/archive/", "記事一覧", "archive"),
        ("/serials/", "連載", "serials"),
        ("/issues/", "発行号", "issues"),
    ]
    navhtml = "".join(f'<a href="{escape(u)}"{" aria-current=page" if key == active and key else ""}>{escape(text)}</a>'
                      for u,text,key in nav)
    # Use a share-intent link without assuming ownership of an X account.
    site_share = escape("https://x.com/intent/tweet?" + urlencode({
        "text": "5年後は、こうなってるかも。今の話題をちょっと大げさに描く未発生新聞。#未発生新聞",
        "url": SITE + "/?src=x-launch-20261102-v3",
    }))
    issue_path = re.fullmatch(r"/issues/(\d{4}-\d{2}-\d{2})/", path)
    og_image = SITE + ("/og/issues/" + issue_path.group(1) + ".png"
                       if issue_path else social_image(article))
    if serial is not None:
        name = serial["slug"] + ("-" + serial["episode"] if serial.get("episode") else "")
        og_image = SITE + "/og/serials/" + name + ".png"
    photo = article.get("photo") if article else None
    local_photo = og_image.split("?",1)[0].endswith(".jpg")
    og_alt = (article_image_alt(
                  article,
                  generated=uses_generated_scene(article),
                  photo_ok=bool(photo and local_photo and not uses_generated_scene(article))
              ) if article else
              ("未発生新聞 " + issue_path.group(1) + "号の架空の新聞表紙"
               if issue_path else "未発生新聞（創作ニュース）"))
    if article is not None and not local_photo:
        og_alt = "創作ニュース「" + article["title"] + "」のテーマを表した共有イラスト"
    if serial is not None:
        og_alt = ("創作推理小説" if serial["kind"] == "fiction" else "株式市場のシナリオ分析") + "の連載オリジナル表紙"
    og_mime = "image/jpeg" if local_photo else "image/png"
    og_size = "" if local_photo else '<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">'
    metadata = {"@context":"https://schema.org",
                "@type":"WebSite" if path=="/" else ("CollectionPage" if issue_path else "WebPage"),
                "name":title,"url":SITE+path,"description":desc,
                "inLanguage":"ja","genre":"風刺・フィクション"}
    if path == "/serials/":
        metadata["@type"] = "CollectionPage"
        metadata["genre"] = "創作小説・市場分析"
    if serial is not None:
        metadata["@type"] = "CollectionPage" if not serial.get("episode") else metadata["@type"]
        metadata["genre"] = "推理小説・フィクション" if serial["kind"] == "fiction" else "金融・市場分析（編集部の見解）"
    if kind == "serial_episode" and serial is not None:
        metadata.update({
            "@type": "Article",
            "headline": title,
            "datePublished": serial["published_at"],
            "dateModified": serial["published_at"],
            "mainEntityOfPage": {"@type": "WebPage", "@id": SITE + path},
            "author": {"@type": "Organization", "name": "未発生新聞", "url": SITE + "/about/"},
            "publisher": {"@type": "Organization", "name": "未発生新聞", "url": SITE},
            "isAccessibleForFree": True,
            "image": [og_image],
            "articleSection": "推理小説" if serial["kind"] == "fiction" else "株式市場予測"
        })
        if serial.get("title"):
            metadata["isPartOf"] = {
                "@type": "CreativeWorkSeries",
                "name": serial["title"],
                "url": SITE + "/serials/" + serial["slug"] + "/",
            }
    if issue_path:
        metadata["datePublished"] = issue_path.group(1)
    if kind == "story" and article is not None:
        metadata.update({
            "@type":"BlogPosting",
            "headline":title,
            "datePublished":publication_date(article),
            "dateModified":modification_date(article),
            "mainEntityOfPage":{"@type":"WebPage","@id":SITE+path},
            "author":{"@type":"Organization","name":"未発生新聞","url":SITE+"/about/"},
            "publisher":{"@type":"Organization","name":"未発生新聞","url":SITE},
            "isAccessibleForFree":True,
            "image":[og_image],
            "articleSection":article["category"],
            "keywords":article_tags(article)
        })
    schemas = [metadata]
    # Video is an optional, first-party photo-motion extra, not fabricated
    # footage. Only publish structured video data for a verified local MP4.
    clip = article_clip(article) if kind == "story" and article else None
    if clip:
        schemas.append({
            "@context": "https://schema.org",
            "@type": "VideoObject",
            "name": article["title"] + f"｜{clip_seconds(clip)}秒の演出映像",
            "description": "記事の承認済み画像を編集した無音の創作映像。実際の報道映像ではありません。",
            "thumbnailUrl": og_image,
            "contentUrl": SITE + clip_url(article),
            "uploadDate": clip["created_at"],
            "duration": f"PT{clip_seconds(clip)}S",
            "width": 640,
            "height": 360,
        })
    if path == "/serials/":
        schemas.append({
            "@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "トップ", "item": SITE + "/"},
                {"@type": "ListItem", "position": 2, "name": "連載一覧", "item": SITE + "/serials/"},
            ],
        })
        if serial_collection:
            schemas.append({
                "@context": "https://schema.org", "@type": "ItemList",
                "numberOfItems": len(serial_collection),
                "itemListElement": [
                    {"@type": "ListItem", "position": i + 1, "name": item["title"],
                     "url": SITE + "/serials/" + item["slug"] + "/"}
                    for i, item in enumerate(serial_collection)
                ],
            })
    if serial is not None:
        prefix = SITE + "/serials/" + serial["slug"] + "/"
        crumbs = [
            {"@type": "ListItem", "position": 1, "name": "トップ", "item": SITE + "/"},
            {"@type": "ListItem", "position": 2, "name": "連載一覧", "item": SITE + "/serials/"},
            {"@type": "ListItem", "position": 3, "name": serial.get("title") or title, "item": prefix},
        ]
        if serial.get("episode"):
            crumbs.append({"@type": "ListItem", "position": 4,
                           "name": serial.get("episode_title") or title,
                           "item": SITE + path})
        schemas.append({"@context": "https://schema.org",
                        "@type": "BreadcrumbList", "itemListElement": crumbs})
        if not serial.get("episode") and serial.get("episodes"):
            chapters = serial["episodes"]
            schemas.append({
                "@context": "https://schema.org", "@type": "ItemList",
                "numberOfItems": len(chapters),
                "itemListElement": [
                    {"@type": "ListItem", "position": i + 1, "name": ep["title"],
                     "url": prefix + ep["slug"] + "/"}
                    for i, ep in enumerate(chapters)
                ],
            })
    if kind == "story" and article is not None:
        schemas.append({
            "@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "トップ", "item": SITE + "/"},
                {"@type": "ListItem", "position": 2, "name": "記事一覧", "item": SITE + "/archive/"},
                {"@type": "ListItem", "position": 3, "name": article["title"], "item": SITE + path},
            ]
        })
    if issue_path:
        day = issue_path.group(1)
        schemas.append({
            "@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "トップ", "item": SITE + "/"},
                {"@type": "ListItem", "position": 2, "name": "発行号一覧", "item": SITE + "/issues/"},
                {"@type": "ListItem", "position": 3, "name": issue_date_label(day) + "号", "item": SITE + path},
            ],
        })
    if collection:
        entries = list(collection[:30])
        schemas.append({
            "@context": "https://schema.org", "@type": "ItemList",
            "numberOfItems": len(entries),
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1,
                 "name": item["title"], "url": SITE + article_url(item)}
                for i, item in enumerate(entries)
            ],
        })
    ld = json.dumps(schemas if len(schemas) > 1 else metadata, ensure_ascii=False).replace("</", "<\\/")
    # Non-content utility pages should never compete with actual stories in search.
    noindex_page = path in ("/saved/", "/404.html") or not indexable
    robots_tag = '<meta name="robots" content="noindex,follow">' if noindex_page else ""
    image_preview_tag = "" if noindex_page else '<meta name="robots" content="max-image-preview:large">'
    serial_progress_script = (script_tag("serial-progress.js")
                              if path in ('/', '/serials/') or serial is not None else '')
    # Only load reader tools where they can be used. Leave engagement active for
    # real audience counts; these links work without JavaScript.
    archive_script = script_tag("site.js") if path == "/archive/" else ""
    broadcast_script = (script_tag("news-broadcast.js")
                        if path == "/" or (kind == "story" and article is not None
                                           and article_clip(article)) else "")
    comments_script = script_tag("comments.js") if kind == "story" else ""
    comment_activity_script = (script_tag("comment-activity.js")
                               if path == "/" else "")
    bookmarks_script = (script_tag("bookmarks.js")
                        if kind == "story" or path == "/saved/" else "")
    resume_script = (script_tag("returning-shelf.js")
                     if path == "/" else "")
    engagement_script = script_tag("engagement.js")
    topic_rss = (f'<link rel="alternate" type="application/rss+xml" title="{escape(title)} RSS" '
                 f'href="{escape(feed_url)}">' if feed_url else "")
    page_class = "page-home" if path == "/" else "page-inner"
    return f'''<!doctype html><html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{image_preview_tag}
{robots_tag}
<meta name="theme-color" content="#f9f8f3">
<meta name="google-adsense-account" content="ca-pub-6754575901141756">
<meta name="description" content="{escape(desc)}">
<meta property="og:title" content="{escape(complete)}">
<meta property="og:description" content="{escape(desc)}">
<meta property="og:site_name" content="未発生新聞">
<meta property="og:locale" content="ja_JP">
<meta property="og:type" content="{"article" if kind in ("story", "serial_episode") else "website"}">
{article_time_meta}
<meta property="og:url" content="{SITE+path}">
<meta property="og:image" content="{escape(og_image)}">
<meta property="og:image:secure_url" content="{escape(og_image)}">
<meta property="og:image:type" content="{og_mime}">
{og_size}
<meta property="og:image:alt" content="{escape(og_alt)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{escape(complete)}">
<meta name="twitter:description" content="{escape(desc)}">
<meta name="twitter:image" content="{escape(og_image)}">
<meta name="twitter:image:alt" content="{escape(og_alt)}">
<link rel="canonical" href="{SITE+path}">
<link rel="icon" type="image/png" sizes="192x192" href="/assets/favicon-192.png">
<link rel="icon" type="image/x-icon" sizes="16x16 32x32 48x48 64x64" href="/favicon.ico">
<link rel="apple-touch-icon" sizes="180x180" href="/assets/apple-touch-icon.png">
<link rel="alternate" type="application/rss+xml" title="未発生新聞 RSS" href="/feed.xml">
{topic_rss}
<link rel="stylesheet" href="{stylesheet_url()}">
{archive_script}
{broadcast_script}
{engagement_script}
{comments_script}
{comment_activity_script}
{bookmarks_script}
{serial_progress_script}
{resume_script}
<script type="application/ld+json">{ld}</script>
<title>{escape(complete)}</title>
</head><body class="{page_class}">
<a class="skip" href="#main">本文へ</a>
<div class="strip"><div class="container strip-content">
<span class="strip-category"><span class="fiction-mark">{escape(publication_label)}</span></span>
</div></div>
<header class="container"><div class="masthead">
<div class="mast-kicker">THE UNHAPPENED TIMES</div>
<a class="brand" href="/" aria-label="未発生新聞トップへ">未発生<span class="brand-last">新聞</span></a>
<p class="mast-sub">{escape(mast_subtitle)}</p>
</div></header>
<div class="container observer-shell">
<div class="observer-widget pv-widget" data-observer-widget hidden data-pv-widget role="group" aria-label="サイトのページ閲覧数">
<span class="observer-eye" aria-hidden="true">◉</span>
<span class="observer-stat"><span class="observer-label">今日のPV</span>
<strong data-site-pv-today>—</strong><span class="observer-suffix">PV</span></span>
<span class="observer-stat"><span class="observer-label">昨日のPV</span>
<strong data-site-pv-yesterday>—</strong><span class="observer-suffix">PV</span></span>
<span class="observer-stat"><span class="observer-label" data-site-pv-period-label>直近7日</span>
<strong data-site-pv-week>—</strong><span class="observer-suffix">PV</span></span>
<small><span data-pv-tracking-note>PVを読み込み中</span> · <a href="/privacy/#pageviews">集計について</a></small>
</div></div><nav class="nav-bar" aria-label="メインナビゲーション"><div class="container nav-inner">{navhtml}</div></nav>
<main id="main">{main}</main>
<footer class="footer"><div class="container"><div class="footer-grid">
<div><a class="footer-brand" href="/">未発生新聞</a>
<p class="footer-copy">まだ起きていないニュースを、真顔で。</p></div>
<nav class="footer-nav" aria-label="補助リンク"><a href="/saved/">あとで読む</a><a href="/archive/#category-feeds">ジャンル別RSS</a><a href="/about/">本紙について</a>
<a href="/editorial/">編集方針</a><a href="/advertise/">広告・協賛</a><a href="/privacy/">プライバシー</a><a href="/feed.xml">RSS</a><a href="{site_share}" target="_blank" rel="noopener noreferrer external">Xで紹介する</a></nav></div>
<div class="footer-bottom"><span>© 未発生新聞 / veripsa.com</span>
<span>創作記事・推理小説はフィクションです。市場分析は投資助言ではありません。</span></div></div></footer>
</body></html>'''

def pv_recommendations(articles, excluded_slug=""):
    """Only publish eligibility metadata; runtime PVs determine the ranking.

    Include the whole published archive, not just the newest twenty stories.
    The browser fetches metrics in twenty-article batches and keeps this panel
    hidden until all candidates have a measured value for the same JST period.
    """
    candidates = sorted(articles, key=lambda a: (
        publication_date(a), a["source"]["published_at"]
    ), reverse=True)
    options = escape(json.dumps(
        [{"slug": x["slug"], "title": x["title"], "category": x["category"]}
         for x in candidates if x["slug"] != excluded_slug],
        ensure_ascii=False, separators=(",", ":")))
    return f'''<section class="readers-choice" data-popular-section
 data-popular-options="{options}" aria-labelledby="readers-choice-title" hidden>
<div class="section-head"><div>
<h2 id="readers-choice-title">よく読まれている記事</h2></div>
<a href="/archive/">記事一覧へ ↗</a></div>
<p class="readers-choice-note" data-popular-period>過去7日間のPV順</p>
<ol class="readers-choice-list" data-popular-list></ol>
</section>'''


def home(articles, serials=None):
    # The newest published story must lead, even if a caller supplies an
    # unsorted list (e.g. a scheduled content edit or a local preview).
    # Use actual site publication time, never the source date alone or 2031.
    articles = sorted(articles, key=lambda a: (
        publication_date(a), a["source"]["published_at"]
    ), reverse=True)
    serials = load_serials(BASE) if serials is None else serials
    a = editorial_lead(articles)
    newest = articles[0]
    remaining = [item for item in articles if item["slug"] != a["slug"]]
    # The same real newest article controls the lead and edition counters.
    last_published = publication_date(newest)
    last_day = last_published[:10]
    last_day_count = sum(publication_date(item)[:10] == last_day for item in articles)
    actual_stamp = datetime.fromisoformat(last_published)
    last_label = f"{actual_stamp.month}月{actual_stamp.day}日"
    if "T" in last_published:
        last_label += f" {actual_stamp.hour:02}:{actual_stamp.minute:02}"
    # The sidebar is the actual newest-article rail after the lead. Keep its
    # three headlines immediately adjacent to the front page so "new" never
    # labels the older card grid below.
    side = "".join('<article class="brief-card">' + category_meta(x, compact=True) +
                   f'<h3><a href="{article_url(x)}">{escape(x["title"])}</a></h3></article>'
                   for x in remaining[:3])
    # A compact fresh grid: the full article archive remains accessible.
    recent_articles = remaining[3:7]
    recent = "".join(card(x) for x in recent_articles)
    # The activity rail doubles as genuine article discovery: do not repeat
    # the lead, sidebar, or visible story grid.
    front_slugs = {a["slug"], *(x["slug"] for x in remaining[:3]),
                   *(x["slug"] for x in recent_articles)}
    # Rotating video choices also appear above the history rail, including
    # older stories whose newly delivered clips move them to the foreground.
    front_slugs.update(item["slug"] for item in broadcast_playlist(articles))
    more_recent = [x for x in articles if x["slug"] not in front_slugs][:3]
    timeline_links = []
    for item in more_recent:
        published = publication_date(item)
        stamp = datetime.fromisoformat(published)
        stamp_label = f"{stamp.month}/{stamp.day}"
        if "T" in published:
            stamp_label += f" {stamp.hour:02}:{stamp.minute:02}"
        timeline_links.append(
            f'<li><a href="{article_url(item)}">'
            f'<time datetime="{escape(published)}">{escape(stamp_label)}</time>'
            f'<span class="press-timeline-category">{escape(item["category"])}</span>'
            f'<span class="press-timeline-headline">{escape(item["title"])}</span>'
            '</a></li>'
        )
    timeline_section = (
        '<section class="press-timeline" aria-labelledby="press-timeline-title">'
        '<div class="press-timeline-heading">'
        '<div><span class="press-timeline-mark" aria-hidden="true"></span>'
        '<h2 id="press-timeline-title">ほかの見出し</h2></div>'
        '<a href="/issues/">発行号一覧 ↗</a></div>'
        '<ol class="press-timeline-list">' + "".join(timeline_links) + '</ol></section>'
    ) if timeline_links else ""
    featured_categories = curated_categories(articles)
    genre_links = []
    for cat in CATEGORIES[1:]:
        count = sum(x["category"] == cat for x in articles)
        if count == 0:
            continue
        href = category_url(cat) if cat in featured_categories else "/archive/?category=" + quote(cat)
        genre_links.append(f'<a href="{escape(href)}">{escape(cat)} <span>{count}本</span></a>')
    genre_links = "".join(genre_links)
    # Promote established topic clusters instead of linking one-off thin tags
    # from the front page. This makes the strongest internal-link hubs stable
    # as the archive grows while keeping playful satire tags secondary.
    home_topics = curated_topics(articles)
    topic_tags = [
        tag for tag, items in sorted(
            home_topics.items(),
            key=lambda pair: (
                topic_is_satire(pair[0], pair[1]), -len(pair[1]), pair[0]
            ),
        )
    ][:8]
    topic_links = "".join(
        f'<a href="{escape(tag_url(tag))}">#{escape(tag)}</a>' for tag in topic_tags
    )
    topic_section = (
        '<section class="topic-explore genre-explore" aria-label="話題タグから記事を探す">'
        '<div><h2>話題から読む</h2></div>'
        '<nav class="story-tags story-tags--home" aria-label="話題タグから探す">'
        + topic_links + '</nav></section>'
    ) if topic_links else ""
    issues_for_home = daily_issues(articles)
    issue_cards = "".join(
        f'<a class="daily-issue-card" href="{issue_url(day)}">'
        f'<time datetime="{day}">{issue_date_label(day)}</time>'
        f'<strong>発行号を読む <span aria-hidden="true">→</span></strong>'
        f'<small>{len(items)}本掲載 · {escape(items[0]["title"])}</small></a>'
        for day, items in list(issues_for_home.items())[:3]
    )
    latest_issue_share = ""
    if issues_for_home:
        day, items = next(iter(issues_for_home.items()))
        latest_issue_share = (
            f'<a class="daily-issue-share" href="{escape(issue_share_url(day, items))}"'
            ' target="_blank" rel="noopener noreferrer external">'
            f'{issue_date_label(day)}号の見出しをXで紹介 ↗</a>'
        )
    issue_section = (
        '<section class="daily-issues-section" aria-labelledby="daily-issues-heading">'
        '<div class="section-head"><h2 id="daily-issues-heading">日付で紙面をめくる</h2>'
        '<a href="/issues/">発行号をすべて見る ↗</a></div>'
        '<div class="daily-issues-grid">' + issue_cards + '</div>'
        + latest_issue_share + '</section>'
    ) if issue_cards else ""
    # A busy newsroom should not ship the entire archive in the homepage HTML.
    # Keep the interactive edition fresh without letting page weight grow forever.
    issue_options = escape(json.dumps(
        [{"url": article_url(x), "headline": x["title"], "category": x["category"]}
         for x in articles[:40]], ensure_ascii=False, separators=(",", ":")))
    popular_section = pv_recommendations(articles)
    discussion_options = escape(json.dumps(
        [{"slug": x["slug"], "title": x["title"], "category": x["category"]}
         for x in articles[:20]], ensure_ascii=False, separators=(",", ":")))
    content = f'''<div class="container index-main">
<div class="news-briefing" data-edition-status data-last-edition="{escape(last_day)}"
 data-edition-count="{last_day_count}" data-last-label="{escape(last_label)}">
<div class="news-briefing-title">
<strong data-edition-title>最新記事</strong>
<span class="edition-updated" data-edition-updated>更新：{escape(last_label)}</span></div>
<a class="return-update" data-return-update data-latest-published="{escape(last_published)}"
 href="{article_url(newest)}" hidden>新着記事を読む <span aria-hidden="true">↗</span></a>
</div>
<aside class="returning-shelf" data-returning-shelf hidden aria-label="この端末に保存した記事">
 <span class="returning-shelf-eyebrow">あとで読む</span>
 <a class="returning-shelf-article" data-returning-shelf-article href="/saved/"></a>
 <span class="returning-shelf-count" data-returning-shelf-count></span>
 <a class="returning-shelf-all" href="/saved/">保存一覧 ↗</a>
</aside>
<section class="hero-grid">
<article class="lead">
<span class="small-label red">一面</span>
<div class="lead-info">{category_meta(a, compact=True)}</div>
<h1><a href="{article_url(a)}">{escape(a["title"])}</a></h1>
<a class="lead-media" href="{article_url(a)}">{image_tag(a, "hero-image", extra='fetchpriority="high" decoding="async"')}</a>
<p class="dek">{escape(reader_copy(a["summary"]))}</p>
{tag_links(a, compact=True)}
<div class="hero-actions"><a class="read-more" href="{article_url(a)}">記事を読む</a>{actions(a,compact=True)}</div>
</article>
<aside class="side-brief"><div class="side-title"><h2>新着記事</h2></div>{side}</aside>
</section>
{broadcast_feature(articles)}
<section class="section-block"><div class="section-head"><h2>さらに読む</h2>
<a href="/archive/">記事一覧を見る ↗</a></div>
<div class="story-grid">{recent}</div></section>
{popular_section}
{timeline_section}
{topic_section}
<section class="genre-explore" aria-label="ジャンル別に記事を探す">
<div><h2>ジャンルから読む</h2></div>
<nav class="genre-links" aria-label="ジャンルから探す">{genre_links}</nav>
</section>
{render_serials_feature(serials)}
<details class="extras-shelf">
<summary><strong>発行号・号外・コメント</strong><span class="extras-open" aria-hidden="true">＋</span></summary>
<div class="extras-shelf-body">
{issue_section}
<section class="special-edition" aria-label="ランダムに号外記事を読む" data-issue-picker data-issue-options="{issue_options}">
  <div class="special-edition-heading">
    <h2>号外</h2>
  </div>
  <div class="special-edition-machine">
    <button type="button" class="issue-picker-button" data-pick-issue>ランダムに1本選ぶ <span aria-hidden="true">↗</span></button>
    <div class="issue-picker-result" data-issue-result aria-live="polite" hidden>
      <span class="small-label"></span>
      <p data-issue-headline></p>
      <a data-issue-link href="/archive/" hidden>記事を読む ↗</a>
    </div>
  </div>
</section>
<section class="reader-discussions" data-comment-discussions data-comment-discussion-options="{discussion_options}" aria-labelledby="reader-discussions-title">
<div class="reader-discussions-copy">
<h2 id="reader-discussions-title">読者のツッコミ</h2>
<a class="reader-discussions-cta" href="{article_url(newest)}#reader-comments">新着記事にコメントする ↗</a></div>
<nav class="reader-discussions-list" data-discussion-list aria-label="コメントの付いた記事" hidden></nav>
</section>
</div></details>
</div>'''
    return layout("未発生新聞", "未発生新聞は、社会の矛盾や日常の理不尽を5年後の架空ニュースにして笑う風刺メディア。読み切りの創作記事と推理小説を公開中。", "/", content,"home")

def archive(articles):
    # Only offer categories with actual published articles; the list grows with
    # future editions without showing dead-end filters in the meantime.
    visible_categories = ["すべて"] + [
        cat for cat in CATEGORIES[1:] if any(a["category"] == cat for a in articles)
    ]
    buttons = "".join(f'<button class="filter-button{" active" if cat=="すべて" else ""}" '
                      f'data-category-filter="{cat}" aria-pressed="{"true" if cat=="すべて" else "false"}" type="button">{cat}</button>'
                      for cat in visible_categories)
    tag_counts = Counter(tag for article in articles for tag in article_tags(article))
    tag_names = sorted(
        (tag for tag, count in tag_counts.items() if count >= 2),
        key=lambda tag: (-tag_counts[tag], tag)
    )
    tag_buttons = (
        '<button class="filter-button tag-filter-button active" data-tag-filter="すべて"'
        ' aria-pressed="true" type="button">すべての話題</button>'
        + "".join(
            f'<button class="filter-button tag-filter-button" data-tag-filter="{escape(tag)}"'
            f' aria-pressed="false" type="button">#{escape(tag)} <span>{tag_counts[tag]}本</span></button>'
            for tag in tag_names
        )
    )
    decades = sorted({a["fiction_at"][:3] + "0" for a in articles})
    options = '<option value="すべて">すべての年代</option>' + "".join(
        f'<option value="{x}">{x}年代</option>' for x in decades)
    # One available decade is not a meaningful filter. Keep the select in DOM
    # so existing filtering works automatically when a second decade exists.
    decade_hidden = ' hidden' if len(decades) < 2 else ''
    content = f'''<div class="container archive-page">
<header class="page-intro"><h1>記事一覧</h1>
<p class="archive-editions-link"><a href="/issues/">発行日ごとの紙面から読む ↗</a></p></header>
<div class="filters" role="group" aria-label="記事のカテゴリー">{buttons}</div>
<details class="tag-filter-panel" data-tag-panel>
<summary>話題・風刺タグから探す</summary>
<div class="tag-filters" role="group" aria-label="記事の話題・風刺タグ">{tag_buttons}</div>
<p class="archive-editions-link"><a href="/topics/">タグ一覧を見る ↗</a></p>
</details>
<div class="search-row"><input class="search-input" data-search-input type="search" aria-label="記事検索" placeholder="見出しやキーワードで検索">
<select class="decade-select" data-decade-filter aria-label="年代フィルター"{decade_hidden}>{options}</select>
<output class="result-counter" data-result-count aria-live="polite">{len(articles)}件の記事</output></div>
<div id="archive-results" class="archive-list">{''.join(card(a,True) for a in articles)}</div>
<div class="no-results" data-no-results hidden role="status">
<p>一致する記事はありません。検索語やジャンルを変えてみてください。</p>
<button class="reset-filters" type="button" data-reset-filters>絞り込みを解除</button>
</div>
<div class="archive-load-more" data-archive-more hidden>
  <button type="button" data-archive-more-button aria-controls="archive-results">さらに記事を見る</button>
</div>
<details class="category-feed-subscriptions" id="category-feeds">
<summary id="category-feeds-title">ジャンル別RSS</summary>
<nav class="category-feed-links" aria-label="ジャンル別RSS">
{''.join(f'<a href="{category_feed_url(name)}" type="application/rss+xml">{escape(name)}のRSS <span aria-hidden="true">↗</span></a>' for name in CATEGORIES[1:] if any(a["category"]==name for a in articles))}
</nav></details>
{''.join('<p><a class="read-more" href="'+category_url(k)+'">'+escape(k)+'の関連記事を読む</a></p>' for k in curated_categories(articles))}
</div>'''
    return layout("記事一覧","現実の話題を5年後に広げた未発生新聞の創作ニュース一覧。未来の的中を狙う予測ではありません。","/archive/",content,"archive")


def category_page(category, items):
    title = category + "の記事"
    intro = CATEGORY_INTROS[category]
    content = (
        '<div class="container archive-page">'
        '<header class="page-intro"><span class="eyebrow">CATEGORY</span>'
        f'<h1>{escape(title)}</h1>'
        f'<p>{escape(intro)}</p>'
        f'<p class="category-page-feed"><a href="{category_feed_url(category)}" type="application/rss+xml">'
        f'{escape(category)}の新着をRSSで購読 ↗</a></p></header>'
        '<div class="archive-list">' + "".join(card(a) for a in items) + '</div>'
        '<p style="margin-top:26px"><a class="read-more" href="/archive/">すべての記事を見る</a></p>'
        '</div>'
    )
    return layout(title,category + "の架空ニュース・社会風刺。"+intro,
                  category_url(category),content,active="archive",
                  feed_url=category_feed_url(category),collection=items)


def topic_index(articles):
    groups = topic_groups(articles)
    regular = []
    satire = []
    one_off = []
    for tag, items in sorted(groups.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        entry = (
            f'<a class="topic-index-link" href="{escape(tag_url(tag))}">'
            f'<strong>#{escape(tag)}</strong><span>{len(items)}本</span></a>'
        )
        if len(items) == 1:
            one_off.append(entry)
        else:
            (satire if topic_is_satire(tag, items) else regular).append(entry)
    sections = []
    if regular:
        sections.append(
            '<section class="topic-index-section"><h2>題材から読む</h2>'
            '<div class="tag-filters">' + "".join(regular) + '</div></section>'
        )
    if satire:
        sections.append(
            '<section class="topic-index-section"><h2>何を風刺しているか</h2>'
            '<p>記事の中にある「あるある」や、行きすぎた空気感から探せます。</p>'
            '<div class="tag-filters">' + "".join(satire) + '</div></section>'
        )
    if one_off:
        sections.append(
            '<details class="tag-filter-panel topic-one-offs">'
            '<summary>細かいタグも見る</summary>'
            '<div class="tag-filters">' + "".join(one_off) + '</div></details>'
        )
    content = (
        '<div class="container archive-page">'
        '<header class="page-intro"><span class="eyebrow">TOPICS</span>'
        '<h1>話題・風刺タグ</h1>'
        '<p>題材と、記事がどんな「あるある」を風刺しているかから探せます。</p></header>'
        + "".join(sections)
        + '<p style="margin-top:26px"><a class="read-more" href="/archive/">記事一覧へ戻る</a></p>'
        '</div>'
    )
    return layout(
        "話題・風刺タグ",
        "AI、タイパ、推し活などの題材と、仕事してる感・考察沼などの軽い風刺タグから未発生新聞を探せます。",
        "/topics/", content, active="archive"
    )


def topic_page(tag, items, articles=None):
    satire = topic_is_satire(tag, items)
    indexable = len(items) >= TOPIC_MIN_INDEXABLE_ARTICLES
    all_articles = items if articles is None else articles
    neighbors = related_topics(tag, items, all_articles) if indexable else []
    related = (
        '<section class="topic-index-section topic-neighbors" aria-labelledby="topic-neighbors-title">'
        '<h2 id="topic-neighbors-title">関連する話題</h2>'
        '<div class="tag-filters">'
        + "".join(
            f'<a class="topic-index-link" href="{escape(tag_url(other))}">'
            f'<strong>#{escape(other)}</strong></a>'
            for other in neighbors
        )
        + '</div></section>'
        if neighbors else ""
    )
    if satire:
        intro = f'「{tag}」と呼びたくなる行動や空気感を、5年後の架空ニュースで描いた記事。'
        eyebrow = "SATIRE TAG"
    else:
        intro = f'{tag}を題材に、今の生活や社会のズレを5年後の架空ニュースへ広げた記事。'
        eyebrow = "TOPIC"
    title = f"#{tag}の記事"
    content = (
        '<div class="container archive-page">'
        f'<header class="page-intro"><span class="eyebrow">{eyebrow}</span>'
        f'<h1>{escape(title)}</h1><p>{escape(intro)}</p></header>'
        + related
        + '<div class="archive-list">' + "".join(card(a) for a in items) + '</div>'
        '<p style="margin-top:26px"><a class="read-more" href="/topics/">タグ一覧へ戻る</a></p>'
        '</div>'
    )
    return layout(
        title, intro, tag_url(tag), content, active="archive",
        collection=items, indexable=indexable
    )


def issue_index(issues):
    """Browse substantial editions, grouped by genuine issue publication dates."""
    links = []
    for day, items in issues.items():
        headline = escape(items[0]["title"])
        kinds = " / ".join(dict.fromkeys(a["category"] for a in items))
        links.append(
            '<article class="issue-index-entry">'
            '<div><span class="eyebrow">DAILY EDITION / 創作新聞</span>'
            f'<h2><a href="{issue_url(day)}"><time datetime="{day}">{issue_date_label(day)}</time>号</a></h2>'
            f'<p>{len(items)}本 · {escape(kinds)}</p>'
            f'<p class="issue-index-headline">最新見出し：{headline}</p></div>'
            f'<a class="issue-index-open" href="{issue_url(day)}">この号を読む ↗</a>'
            '</article>'
        )
    content = (
        '<div class="container archive-page issue-page">'
        '<header class="page-intro"><span class="eyebrow">THE EDITION ARCHIVE</span>'
        '<h1>発行号</h1>'
        '<p>日付ごとにまとめて読む。</p></header>'
        '<section class="issue-index-list" aria-label="日付ごとの発行号">'
        + ("".join(links) if links else '<p>まとまった発行号はまだありません。新着記事をご覧ください。</p>')
        + '</section><p><a class="read-more" href="/archive/">新着記事をすべて読む</a></p></div>'
    )
    return layout("発行号一覧",
                  "実際の発行日ごとに読む未発生新聞。3本以上の記事がある号をまとめました。すべて創作記事です。",
                  "/issues/", content, active="issues")


def issue_page(day, items, issues):
    """One crawlable page per substantial day; no need for JavaScript."""
    days = list(issues)
    index = days.index(day)
    neighboring = []
    if index > 0:
        later = days[index - 1]
        neighboring.append(
            f'<a href="{issue_url(later)}">← 新しい号：{issue_date_label(later)}</a>')
    if index + 1 < len(days):
        earlier = days[index + 1]
        neighboring.append(
            f'<a href="{issue_url(earlier)}">前の号：{issue_date_label(earlier)} →</a>')
    nav = "".join(neighboring)
    kinds = " / ".join(dict.fromkeys(a["category"] for a in items))
    title = issue_date_label(day) + "発行号"
    content = (
        '<div class="container archive-page issue-page">'
        '<nav class="breadcrumbs" aria-label="パンくずリスト"><a href="/">トップ</a>'
        '<span aria-hidden="true"> / </span><a href="/issues/">発行号</a>'
        f'<span aria-hidden="true"> / </span><span aria-current="page">{title}</span></nav>'
        '<header class="page-intro">'
        '<span class="eyebrow">DAILY EDITION / 5年後の架空ニュース</span>'
        f'<h1><time datetime="{day}">{title}</time></h1>'
        f'<p>{len(items)}本掲載</p>'
        f'<p class="issue-categories">{escape(kinds)}</p></header>'
        '<aside class="issue-share-panel" aria-label="この号を紹介する">'
        f'<a class="issue-share-cta" href="{escape(issue_share_url(day, items))}"'
        ' target="_blank" rel="noopener noreferrer external">𝕏 この号をXで紹介する ↗</a>'
        '</aside>'
        '<section aria-label="この号の記事一覧"><div class="archive-list">'
        + "".join(card(a) for a in items) +
        '</div></section><nav class="issue-navigation" aria-label="発行号の前後">'
        + nav + '</nav><p><a class="read-more" href="/issues/">発行号一覧へ戻る</a></p></div>'
    )
    return layout(title, f"{title}。{len(items)}本の5年後の架空ニュースを掲載。すべてフィクションです。",
                  issue_url(day), content, active="issues", collection=items)


def story(a, articles, issues=None, serials=None):
    paras = "".join(f'<p>{escape(reader_copy(p))}</p>' for p in a["paragraphs"])
    src = a["source"]
    # Continue through the publication stream instead of always jumping back
    # to the newest other story. The previous behavior could create a two-story
    # loop (newest <-> second newest) and strand readers there.
    reading_order = sorted(articles, key=lambda x: (
        publication_date(x), x["source"]["published_at"]
    ), reverse=True)
    current_index = next(
        (index for index, item in enumerate(reading_order) if item["slug"] == a["slug"]),
        None,
    )
    next_story = (
        reading_order[current_index + 1]
        if current_index is not None and current_index + 1 < len(reading_order)
        else None
    )
    next_url = article_url(next_story) if next_story else "/archive/"
    next_title = next_story["title"] if next_story else "記事一覧から次のニュースを探す"
    next_category = next_story["category"] if next_story else "記事一覧"
    text_len = sum(len(p) for p in a["paragraphs"])
    read_minutes = max(1, (text_len + 449) // 450)
    available_issues = daily_issues(articles) if issues is None else issues
    issue_day = publication_date(a)[:10]
    # Even a thin category should have a useful reader route, without
    # creating a duplicate low-value category page for search engines.
    category_href = (category_url(a["category"])
                     if a["category"] in curated_categories(articles)
                     else "/archive/?category=" + quote(a["category"]))
    category_link = (
        f'<a href="{escape(category_href)}">'
        f'{escape(a["category"])}の記事を読む ↗</a>'
    )
    issue_link = (
        f'<a class="article-issue-link" href="{issue_url(issue_day)}">'
        f'{issue_date_label(issue_day)}の発行号を読む ↗</a>'
        if issue_day in available_issues else ""
    )
    bridge = render_serial_bridge(a, load_serials(BASE) if serials is None else serials)
    popular_section = pv_recommendations(articles, a["slug"])
    # Static related-story links strengthen topic clusters even without JS or PV data.
    # Only stories sharing an actual published tag qualify; the existing topic
    # ranker decides order, so this adds no new opaque recommendation system.
    ranked_related = suggested_articles(a, articles, limit=max(0, len(articles) - 1))
    related_items = []
    for item in ranked_related:
        item_topics = set(article_tags(item))
        shared_topics = [tag for tag in article_tags(a) if tag in item_topics][:2]
        if not shared_topics:
            continue
        shared_links = "".join(
            f'<a href="{escape(tag_url(tag))}">#{escape(tag)}</a>'
            for tag in shared_topics
        )
        related_items.append(
            '<article class="topic-related-item">'
            f'<div><span class="topic-related-category">{escape(item["category"])}</span>'
            f'<h3><a href="{article_url(item)}">{escape(item["title"])}</a></h3></div>'
            f'<nav class="topic-related-tags" aria-label="共通する話題">{shared_links}</nav>'
            '</article>'
        )
        if len(related_items) >= 3:
            break
    related_section = (
        '<section class="topic-related" aria-labelledby="topic-related-title">'
        '<div class="topic-related-heading"><div><span class="small-label">RELATED</span>'
        '<h2 id="topic-related-title">同じ話題の記事</h2></div>'
        '<a href="/topics/">話題一覧 ↗</a></div>'
        '<div class="topic-related-list">' + "".join(related_items) + '</div></section>'
        if related_items else ""
    )
    parent = followup_parent(a, articles)
    followup_context = (
        '<aside class="story-followup-context" aria-label="前の記事">'
        '<strong>続報</strong>'
        f'<a href="{article_url(parent)}">前の記事：{escape(parent["title"])} <span aria-hidden="true">↗</span></a>'
        '</aside>'
        if parent else ""
    )
    children = followup_children(a, articles)
    followup_section = (
        '<section class="story-followups" aria-labelledby="story-followups-title">'
        '<h2 id="story-followups-title">この話題の続報</h2><ul>'
        + "".join(
            f'<li><a href="{article_url(item)}">{escape(item["title"])}</a>'
            f'<time datetime="{escape(publication_date(item))}">{escape(publication_date(item)[:10])}</time></li>'
            for item in children
        )
        + '</ul></section>'
        if children else ""
    )
    content = f'''<div class="container article-page">
<div class="article-shell"><article>
<header class="article-header">
<nav class="breadcrumbs" aria-label="パンくずリスト"><a href="/">トップ</a><span aria-hidden="true"> / </span><a href="/archive/">記事一覧</a><span aria-hidden="true"> / </span><span aria-current="page">この記事</span></nav>
<div class="article-dateline">{category_meta(a)}</div>
<h1>{escape(a["title"])}</h1>
<p class="dek">{escape(reader_copy(a["summary"]))}</p>
{followup_context}
<div class="story-meta"><span>未発生新聞 編集部</span><span>読了目安 約{read_minutes}分</span></div>
{tag_links(a)}
</header>
<figure class="article-hero-figure">
{article_hero_media(a)}
<figcaption>{photo_credit(a)}{clip_caption(a)}</figcaption></figure>
<div class="article-body">{paras}</div>
<div class="article-endmark">◆</div>
<nav class="reader-next" data-popular-fallback aria-label="次の記事へ進む">
<span class="reader-next-kicker">次の記事</span>
<span class="reader-next-meta">続けて読む · {escape(next_category)}</span>
<a href="{escape(next_url)}" class="reader-next-title">{escape(next_title)} <span aria-hidden="true">→</span></a>
</nav>
{popular_section}
<section class="future-poll" data-future-poll data-poll-article="{escape(a["slug"])}" aria-labelledby="future-poll-title">
<div class="future-poll-heading">
<h2 id="future-poll-title">この「もしも」、ありそう？</h2>

</div>
<div class="future-poll-choices" role="group" aria-label="この「もしも」のありそう度を投票">
<button type="button" data-poll-choice="plausible" aria-pressed="false"><span aria-hidden="true">◉</span> ありそう</button>
<button type="button" data-poll-choice="unlikely" aria-pressed="false"><span aria-hidden="true">✕</span> それはない！</button>
</div>
<p class="future-poll-status" data-poll-status role="status">投票すると結果が見られます。</p>
<div class="future-poll-results" data-poll-results hidden aria-label="もしもの投票結果">
<div class="future-poll-result-line"><span>ありそう</span><strong data-poll-plausible-percent>—</strong></div>
<div class="future-poll-meter"><span data-poll-plausible-bar></span></div>
<div class="future-poll-result-line"><span>それはない！</span><strong data-poll-unlikely-percent>—</strong></div>
<div class="future-poll-meter"><span data-poll-unlikely-bar></span></div>
</div>
</section>
{actions(a)}
<div class="article-save-inline">{bookmark_button(a)}<a href="/saved/">保存した記事を見る ↗</a></div>
<p class="bookmark-status" data-bookmark-status role="status" aria-live="polite"></p>
<details class="source-context" id="story-source" aria-label="着想した実際の話題">
<summary>着想元を見る</summary>
<div class="source-context-body">
<p>{escape(source_reader_copy(src["topic"]))}</p>
<p class="source-context-disclaimer">着想元は、この記事の出来事を報じたものではありません。</p>
<a href="{escape(src["url"])}" target="_blank" rel="noopener noreferrer external">
{escape(src["publisher"])} で着想元を見る ↗</a>
</div>
</details>
{related_section}
{followup_section}
{bridge}
<div class="article-end"><span class="reporter">未発生新聞</span>{issue_link}{category_link}<a href="/archive/">記事一覧へ ↗</a></div>
</article>
<section class="reader-comments" id="reader-comments" data-comments data-article="{escape(a["slug"])}" aria-labelledby="comments-heading">
<div class="reader-comments-heading">
<h2 id="comments-heading">読者のツッコミ <small data-comments-count></small></h2>
<p>投稿内容は公開されます。</p>
</div>
<div class="reader-comments-list" data-comments-list></div>
<p class="reader-comments-status" data-comments-status role="status">コメントを読み込み中…</p>
<form class="reader-comments-form" data-comments-form>
<label for="reader-nickname">ニックネーム <span>（任意・16文字以内）</span></label>
<input id="reader-nickname" name="nickname" type="text" maxlength="16" autocomplete="nickname" placeholder="匿名の未来人">
<label for="reader-body">コメント</label>
<textarea id="reader-body" name="body" minlength="2" maxlength="180" rows="3" required placeholder="未来のニュースにひとこと…"></textarea>
<div class="comment-form-footer"><span>2〜180文字。URL・個人情報・誹謗中傷は投稿できません。</span>
<button type="submit">コメントを投稿</button></div>
</form>
</section></div>
</div>'''
    return layout(a["title"], reader_copy(a["summary"]) + " ※創作ニュースです。", article_url(a), content,kind="story",article=a)

def about():
    content = '''<div class="container archive-page">
<header class="page-intro"><span class="eyebrow">ABOUT US</span>
<h1>未発生新聞について</h1>
<p>まだ起きていないニュースを、真顔で。</p></header>
<div class="about-copy">
<p>未発生新聞は、今のニュースや身近な出来事を5年後にふくらませて楽しむ、風刺とユーモアの新聞です。</p>
<h2>掲載内容</h2>
<p>ニュース記事と推理小説はフィクションです。連載「未来相場観測室」では、現実の公開資料に基づく市場分析を掲載しています。</p>
<p><a href="/archive/" style="text-decoration:underline">記事一覧 ↗</a>　<a href="/serials/" style="text-decoration:underline">連載 ↗</a></p>
<p><a href="/editorial/" style="text-decoration:underline">編集方針・お問い合わせ ↗</a></p>
<p><a href="/feed.xml" style="text-decoration:underline">RSSで購読する ↗</a></p>
</div></div>'''
    return layout("本紙について","今のニュースや身近な出来事を5年後にふくらませて楽しむ、風刺とユーモアの新聞。","/about/",content,"about")

def editorial_policy():
    """Public disclosure of the creative process and commercial independence."""
    content = '''<div class="container archive-page">
<header class="page-intro"><span class="eyebrow">EDITORIAL POLICY</span>
<h1>編集方針</h1></header>
<div class="about-copy">
<h2>創作と市場分析</h2>
<p>ニュース記事と「未発生事件簿」はフィクションです。「未来相場観測室」は現実の公開資料に基づく市場分析で、出典・予測日・検証条件を示します。投資助言や売買の勧誘ではありません。</p>
<h2>出典・画像</h2>
<p>着想元の公開情報や投稿は、本文末尾の「着想元を見る」から確認できます。参照先が記事内の創作内容を裏付けるものではありません。</p>
<p>文章づくりの補助に生成AIを使用しています。実写風の合成画像は「再現イメージ（合成）」と表示し、写真素材には出典・クレジットを添えます。</p>
<h2>広告</h2>
<p>広告・アフィリエイトを掲載する場合は「広告」「PR」などを表示し、記事と区別します。協賛者の意向で記事を書き換えません。<a href="/advertise/">広告・協賛のご相談 ↗</a></p>
<h2>訂正・お問い合わせ</h2>
<p>出典の誤り、権利上の問題、訂正依頼は<a href="https://github.com/RollNuts/mihassei-news/issues" target="_blank" rel="noopener noreferrer external">公開GitHub Issues</a>へ。投稿は公開されるため、個人情報は記入しないでください。</p>
<p><a href="/about/" style="text-decoration:underline">未発生新聞について ↗</a>　<a href="/privacy/" style="text-decoration:underline">プライバシーについて ↗</a></p>
</div></div>'''
    return layout("編集方針","未発生新聞の創作・市場分析・出典・画像・広告の方針と、訂正の連絡先。","/editorial/",content)


def advertise():
    """Public, unambiguous commercial inquiry policy; no active advertising."""
    content = '''<div class="container archive-page">
<header class="page-intro"><span class="eyebrow">ADVERTISING &amp; PARTNERSHIPS</span>
<h1>広告・協賛</h1></header>
<div class="about-copy">
<h2>ご相談いただける内容</h2>
<p>サイト内の協賛枠や関連企画についてご相談を受け付けています。内容・掲載期間・料金は個別に相談します。</p>
<p>広告・PRは一般記事と区別して表示し、協賛者の意向で記事を書き換えません。</p>
<h2>お問い合わせ</h2>
<p><a href="https://github.com/RollNuts/mihassei-news/issues/new?template=sponsorship.yml" target="_blank" rel="noopener noreferrer external">広告・協賛のお問い合わせ ↗</a>（GitHubアカウントが必要です）</p>
<p>投稿・返信は公開されます。個人情報や非公開の契約条件を記入せず、ご相談の概要だけをお知らせください。</p>
<p><a href="/editorial/" style="text-decoration:underline">編集方針を読む ↗</a>　<a href="/privacy/" style="text-decoration:underline">プライバシーについて ↗</a></p>
</div></div>'''
    return layout("広告・協賛のご案内",
                  "未発生新聞の広告・協賛掲載相談、広告表記、編集の独立性、公開問い合わせに関するご案内。",
                  "/advertise/",content)


def privacy():
    content='''<div class="container archive-page"><header class="page-intro">
<span class="eyebrow">PRIVACY</span><h1>プライバシーについて</h1></header>
<div class="about-copy">
<h2>アクセスの集計</h2>
<p>閲覧・いいね・投票などの集計に、ブラウザ内のランダムな識別子を使います。識別子はハッシュ化してCloudflareに保存します。氏名やメールアドレスの登録は不要です。</p>
<h2 id="pageviews">PV（ページ閲覧数）</h2>
<p>PVは、ページを表示し、閲覧通知を受信した回数です。再読み込みも数え、人数・読了数とは区別します。トップはサイト全体、記事の見出し横はその記事の累計PVです。</p>
<p>日別の区切りは日本時間の0時。「直近7日」は今日を含む7日間です。計測期間が7日に満たない間は「計測開始から」、未計測の期間は「—」と表示します。</p>
<p>「よく読まれている記事」は、公開済みの全記事を過去7日間のPV順に並べています。計測期間が短い場合は、表示した開始日以降のPVを使います。</p>
<p>通信の再送による二重計上を防ぎ、タブへの復帰や集計の自動更新だけではPVを増やしません。JavaScriptが無効な場合や通信に失敗した場合は計測できません。自動アクセスを完全に除外した数字ではありません。</p>
<div class="analytics-preference">
<label><input type="checkbox" data-analytics-opt-out> このブラウザの閲覧を集計しない</label>
<p data-analytics-status role="status">設定を読み込み中です。</p>
<noscript><p>JavaScriptが無効なため、閲覧通知は送信されていません。</p></noscript>
<p>設定はこのブラウザに保存されます。過去の集計は変わらず、いいね・投票は引き続き利用できます。</p>
</div>
<h2 id="readership">閲覧ブラウザ数・流入元</h2>
<p>PVとは別に、閲覧したブラウザ数も集計します。同じ記事の一日内の重複閲覧を除いた件数も記録します。実人数ではなく、ブラウザを変えたり保存データを消したりすると、重複する場合があります。</p>
<p>流入元は「X」「検索」「その他の外部サイト」「判別できない流入」に分類します。「X」「検索」は記事別にも集計します。これらは運営改善の補助指標で、PVやおすすめの順位には使いません。X共有ボタンのクリックや、記事から別の記事へ進んだブラウザ数も集計します。</p>
<p>参照元URL全体・検索語・SNSアカウント・UTMの詳細・IPアドレスは集計データベースに保存しません。記事間の移動は元の記事の識別子だけを送り、移動先との組み合わせや個人の閲覧経路は保存しません。</p>
<p>日別集計と再送判別の記録は直近14日分を保持し、次の訪問時に古い記録を削除します。累計や日次の集計値は別途保存する場合があります。</p>
<h2>投票</h2>
<p>「ありそう／それはない！」は1ブラウザにつき1記事1票です。公開するのは合計と割合だけで、個人の回答は公開しません。投票後の変更・取り消しはできません。</p>
<p>別のブラウザやデータ削除後には重複投票できるため、実人数・世論調査・出来事が起こる確率を示すものではありません。IPアドレスやメールアドレスは投票データベースに保存しません。</p>
<h2>コメント</h2>
<p>ニックネーム・本文・投稿日時を公開します。ニックネームは任意、本文は180文字以内です。URL・個人情報・誹謗中傷は投稿できません。</p>
<p>ハッシュ化したブラウザ識別子を、連続投稿の制限・投稿の削除・通報の重複防止に使います。IPアドレスやメールアドレスはコメントデータベースに保存しません。異なる3ブラウザからの通報で一時非表示になり、不適切な投稿は運営者が非表示・削除する場合があります。</p>
<p>自分の投稿は同じブラウザから削除できます。ブラウザの保存データを消すと削除できなくなる場合があります。</p>
<h2>保存記事・読了記録</h2>
<p>「あとで読む」で選んだ記事の識別子・タイトル・ジャンル・保存日時を最大100件、読了ボタンで選んだ作品・回の識別子を、このブラウザ内だけに保存します。サーバーに送信せず、別の端末とは同期しません。保存・読了はいつでも解除できます。</p>
<p>ブラウザのサイトデータを削除すると記録も消えます。提供を終了した推理メモの文章が残っている場合も、同じ方法で削除できます。</p>
<p>再生した映像の識別子も、このブラウザに最大12件保存し、次に表示する映像の選択に使います。サーバーには送信しません。</p>
<h2>外部サービス・広告</h2>
<p>公開・集計にCloudflareを使用します。一部の写真にはUnsplashを使用し、表示時に通信する場合があります。Xで共有するとXのページへ移動します。</p>
<p>広告を掲載する際は、使用する事業者とプライバシー情報をこのページに記載します。</p>
<h2>お問い合わせ</h2>
<p><a href="https://github.com/RollNuts/mihassei-news/issues" rel="noopener noreferrer external">公開GitHub Issues</a>へお知らせください。投稿は公開されるため、個人情報は記入しないでください。</p>
</div></div>'''
    return layout("プライバシーについて","閲覧計測、コメント、保存記事と外部サービスの取り扱い。","/privacy/",content)


def saved_page():
    """Browser-only reading list; indexable article content is elsewhere."""
    content = '''<div class="container saved-page">
<nav class="breadcrumbs" aria-label="パンくずリスト"><a href="/">トップ</a>
<span aria-hidden="true"> / </span><span aria-current="page">あとで読む</span></nav>
<header class="page-intro">
<h1>あとで読む</h1>
<p>このブラウザに保存した記事</p></header>
<section class="saved-intro" aria-label="保存について">
<div><p>別の端末とは同期されません。ブラウザのデータを消すと保存も消えます。</p></div>
<a href="/archive/">新しい記事を探す ↗</a></section>
<p class="saved-status" data-bookmark-status role="status" aria-live="polite"></p>
<p class="saved-empty" data-saved-empty hidden>まだ保存された記事はありません。
記事ページの「☆ あとで読む」を押すと、ここに並びます。</p>
<ol class="saved-list" data-saved-list aria-label="保存した記事一覧"></ol>
<div class="saved-controls"><button type="button" data-clear-bookmarks hidden>
保存をすべて解除</button><a href="/archive/">記事一覧を見る ↗</a></div>
<noscript><p>保存した記事を見るにはJavaScriptを有効にしてください。</p></noscript>
</div>'''
    return layout("あとで読む",
                  "未発生新聞で気になった創作ニュースを、この端末のブラウザだけに保存する読書リスト。",
                  "/saved/", content, active="saved")


def image_review_page(articles):
    """Public-but-noindex visual QA for published article and social card pairs.

    This is a human review aid, not an automated guarantee of artistic quality.
    It includes only already-public articles and keeps site navigation uncluttered.
    """
    cards = []
    for a in articles:
        generated = uses_generated_scene(a)
        photo_ok = not generated and uses_stock_photo(a)
        main_src = editorial_image(a)
        social_src = social_image(a)
        category = escape(a["category"])
        slug = escape(a["slug"])
        title = escape(a["title"])
        status = "AI生成の架空再現画像" if generated else "承認済み写真" if photo_ok else "創作イラスト"
        photo_note = (
            'AI生成・架空のコント風再現画像（実際の出来事を撮影していません）'
            if generated else f'撮影：{escape(a["photo"]["credit"])} / Unsplash'
            if photo_ok else '編集部制作の創作イラスト'
        )
        motif = escape(story_subject(a))
        cards.append(
            '<article class="review-card">'
            f'<header><span class="status">{status}</span><span>{category}</span></header>'
            f'<h2>{title}</h2>'
            f'<p class="sub">題材：{motif} / {photo_note}</p>'
            '<div class="review-images">'
            f'<figure><img src="{escape(main_src)}" alt="{title} の掲載画像" loading="lazy" decoding="async">'
            '<figcaption>記事の掲載画像（16:9）</figcaption></figure>'
            f'<figure><img src="{escape(social_src)}" alt="{title} のSNS用画像" loading="lazy" decoding="async">'
            '<figcaption>SNS用（PNG・承認済み写真・AI生成の架空再現画像）</figcaption></figure>'
            '</div>'
            f'<p><a href="/articles/{slug}/">公開記事を確認する ↗</a></p>'
            '</article>'
        )
    return f'''<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<meta name="description" content="未発生新聞の公開記事画像を確認する編集用一覧です。">
<title>画像検品一覧 | 未発生新聞</title>
<style>
:root{{font-family:system-ui,-apple-system,"Hiragino Kaku Gothic ProN",Meiryo,sans-serif;color:#263631;background:#f8f6f0}}
*{{box-sizing:border-box}}body{{margin:0}}main{{max-width:1160px;margin:0 auto;padding:32px 18px 65px}}
h1{{font-size:clamp(25px,4vw,38px);margin:12px 0}}p{{line-height:1.85}}.note{{color:#606d66;font-size:13px}}
.review-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,450px),1fr));gap:20px;margin-top:25px}}
.review-card{{background:#fffefa;border:1px solid #d4d5c9;padding:18px;border-top:4px solid #bd513f}}
.review-card header{{display:flex;justify-content:space-between;gap:10px;font-size:12px;color:#6e7670}}
.status{{font-weight:800;color:#a23e30}}h2{{font-size:17px;line-height:1.65;margin:10px 0}}
.sub{{font-size:11px;color:#626f67;margin:3px 0 12px}}
.review-images{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
figure{{min-width:0;margin:0}}img{{width:100%;display:block;aspect-ratio:16/9;object-fit:cover;border:1px solid #ddd8ca;background:#e9e7dc}}
figcaption{{font-size:10px;color:#72776e;line-height:1.6;margin-top:6px}}
a{{color:#9c392b;font-weight:750}}a:hover{{color:#d85a48}}
@media(max-width:470px){{.review-images{{grid-template-columns:1fr}}}}
</style></head><body><main>
<p><a href="/">未発生新聞トップへ戻る</a> · <a href="/share-desk/">X投稿デスク</a></p>
<h1>公開画像の検品一覧</h1>
<p class="note">公開済みの記事 {len(articles)} 本を掲載画像とSNS用画像で並べて確認できます。
写真が承認済みでも、構図・文字の見やすさ・テーマとの相性は最終的に人が確認してください。
このページは検索登録対象外で、サイトマップにも含めません。</p>
<div class="review-grid">{''.join(cards)}</div></main></body></html>'''


def generate_rss(items, title, description, link, self_url, limit=30):
    """One canonical RSS implementation for all posts and genre subscriptions.

    Items must be sorted newest-first by REAL publication time. The fictional
    2031 dateline is never written into pubDate. All images are first-party.
    """
    media_ns = "http://search.yahoo.com/mrss/"
    atom_ns = "http://www.w3.org/2005/Atom"
    ET.register_namespace("media", media_ns)
    ET.register_namespace("atom", atom_ns)
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    for name, value in (("title",title),("description",description),
                        ("link",link),("language","ja")):
        ET.SubElement(channel, name).text=value
    ET.SubElement(channel,f"{{{atom_ns}}}link",{
        "href": SITE+self_url,"rel":"self","type":"application/rss+xml"
    })
    for a in items[:limit]:
        entry=ET.SubElement(channel,"item")
        fields=[("title",a["title"]+" | 未発生新聞（創作）"),
                ("link",SITE+article_url(a)),("guid",SITE+article_url(a)),
                ("description",reader_copy(a["summary"])+" ※創作記事です。")]
        posted=publication_date(a)
        if "T" in posted:
            fields.append(("pubDate",format_datetime(datetime.fromisoformat(posted))))
        for tag, value in fields:
            ET.SubElement(entry,tag).text=value
        ET.SubElement(entry,"category").text=a["category"]
        for tag in article_tags(a):
            ET.SubElement(entry,"category").text=tag
        image=SITE+social_image(a)
        generated=reviewed_generated_scene(a,root=BASE)
        approved=not generated and uses_stock_photo(a)
        raster=generated or approved
        thumb={"url":image}
        if generated:
            thumb.update({"width":"1200","height":"675"})
        elif not approved:
            thumb.update({"width":"1200","height":"630"})
        ET.SubElement(entry,f"{{{media_ns}}}thumbnail",thumb)
        ET.SubElement(entry,f"{{{media_ns}}}content",{
            "url":image,"medium":"image",
            "type":"image/jpeg" if raster else "image/png"
        })
        if approved:
            ET.SubElement(entry,f"{{{media_ns}}}credit",{"role":"photographer"}).text=a["photo"]["credit"]
    return ET.tostring(rss,encoding="unicode")


def other_files(articles, issues=None, serials=None):
    serials = load_serials(BASE) if serials is None else serials
    latest = max(
        list(modification_date(a) for a in articles) +
        list(e["published_at"] for s in serials for e in s["episodes"]),
        default=None
    )
    issues = daily_issues(articles) if issues is None else issues
    routes = [("/", latest), ("/archive/", latest), ("/topics/", latest), ("/issues/", latest), ("/about/", None), ("/editorial/", None), ("/advertise/", None), ("/privacy/", None)]
    routes.extend(
        (issue_url(day), max(modification_date(a) for a in items))
        for day, items in issues.items()
    )
    routes.extend((category_url(category),
                   max(modification_date(a) for a in group))
                  for category, group in curated_categories(articles).items())
    routes.extend((article_url(a), modification_date(a)) for a in articles)
    routes.extend(
        (tag_url(tag), max(modification_date(a) for a in items))
        for tag, items in curated_topics(articles).items()
    )
    routes.extend(serial_routes(serials))
    xmlns = "http://www.sitemaps.org/schemas/sitemap/0.9"
    image_xmlns = "http://www.google.com/schemas/sitemap-image/1.1"
    ET.register_namespace("image", image_xmlns)
    # These first-party raster cards already exist on the published pages.
    # Expose them to image crawlers even when the visible illustration is SVG.
    sitemap_images = {article_url(a): SITE + social_image(a) for a in articles}
    sitemap_images.update({
        issue_url(day): SITE + "/og/issues/" + day + ".png"
        for day in issues
    })
    for series in serials:
        slug = series["slug"]
        sitemap_images["/serials/" + slug + "/"] = SITE + "/og/serials/" + slug + ".png"
        for episode in series["episodes"]:
            sitemap_images["/serials/" + slug + "/" + episode["slug"] + "/"] = (
                SITE + "/og/serials/" + slug + "-" + episode["slug"] + ".png"
            )
    root = ET.Element("urlset", xmlns=xmlns)
    for url, modified in routes:
        node = ET.SubElement(root, "url")
        ET.SubElement(node, "loc").text = SITE + url
        if modified:
            ET.SubElement(node, "lastmod").text = modified
        if url in sitemap_images:
            image = ET.SubElement(node, f"{{{image_xmlns}}}image")
            ET.SubElement(image, f"{{{image_xmlns}}}loc").text = sitemap_images[url]
    write("sitemap.xml", ET.tostring(root, encoding="unicode"))
    write("feed.xml", generate_rss(
        articles, "未発生新聞 | 創作ニュース",
        "今の話題をちょっと大げさに。5年後の架空ニュースを楽しむ創作新聞。", SITE, "/feed.xml", limit=90
    ))
    substantial=curated_categories(articles)
    for category in CATEGORIES[1:]:
        category_items=[a for a in articles if a["category"]==category]
        if not category_items:
            continue
        feed=category_feed_url(category)
        category_link=(category_url(category) if category in substantial else
                       "/archive/?category="+quote(category))
        write(feed.lstrip("/"),generate_rss(
            category_items, f"未発生新聞 | {category}の創作ニュース",
            f"{category}にまつわる5年後の架空ニュース。フィクションです。",
            SITE+category_link,feed
        ))
    write("robots.txt","User-agent: *\nAllow: /\nDisallow: /archive/?\nSitemap: "+SITE+"/sitemap.xml\n")
    write("404.html",layout("記事がありません","お探しの架空ニュースはありません。","/404.html",
                         '<div class="container not-found"><h1>404</h1><p>まだ起きていないどころか、記事が存在しません。</p><a href="/">トップに戻る ↗</a></div>'))
    write("_headers","/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n  X-Frame-Options: DENY\n/build-revision.txt\n  Cache-Control: no-store\n/image-review/*\n  X-Robots-Tag: noindex, nofollow\n/feeds/*\n  X-Robots-Tag: noindex\n/share-desk/*\n  X-Robots-Tag: noindex, nofollow\n/growth-desk/*\n  X-Robots-Tag: noindex, nofollow\n/saved/*\n  X-Robots-Tag: noindex\n")



def make_brand_image():
    """Self-contained PNG for social previews. Pure stdlib; no fonts or paid APIs."""
    width, height = 1200, 630
    bg = (247,244,236)
    raw = bytearray(bytes(bg) * (width * height))

    def rect(x0,y0,x1,y1,rgb):
        color=bytes(rgb)
        for y in range(max(0,y0), min(height,y1)):
            i=(y*width+max(0,x0))*3
            raw[i:i+max(0,min(width,x1)-max(0,x0))*3] = color * max(0,min(width,x1)-max(0,x0))

    rect(0,0,1200,44,(28,42,39))
    rect(65,70,80,560,(214,74,53))
    rect(108,113,1092,122,(34,43,39))
    rect(108,479,1092,487,(34,43,39))
    rect(108,521,1092,525,(171,172,163))
    rect(845,182,1000,337,(238,96,68))
    rect(868,206,977,315,(247,244,236))
    rect(900,238,945,280,(238,96,68))
    font={
        "M":["10001","11011","10101","10101","10001","10001","10001"],
        "I":["11111","00100","00100","00100","00100","00100","11111"],
        "H":["10001","10001","10001","11111","10001","10001","10001"],
        "A":["01110","10001","10001","11111","10001","10001","10001"],
        "S":["01111","10000","10000","01110","00001","00001","11110"],
        "E":["11111","10000","10000","11110","10000","10000","11111"],
        "N":["10001","11001","10101","10101","10011","10001","10001"],
        "W":["10001","10001","10001","10101","10101","11011","10001"],
        "F":["11111","10000","10000","11110","10000","10000","10000"],
        "V":["10001","10001","10001","10001","10001","01010","00100"],
        "Y":["10001","10001","01010","00100","00100","00100","00100"],
        "R":["11110","10001","10001","11110","10100","10010","10001"],
        "L":["10000","10000","10000","10000","10000","10000","11111"],
        "T":["11111","00100","00100","00100","00100","00100","00100"],
        "O":["01110","10001","10001","10001","10001","10001","01110"],
        "D":["11110","10001","10001","10001","10001","10001","11110"],
        "0":["01110","10011","10101","10101","11001","10001","01110"],
        "1":["00100","01100","00100","00100","00100","00100","01110"],
        "2":["01110","10001","00001","00010","00100","01000","11111"],
        "3":["11110","00001","00001","01110","00001","00001","11110"],
        "5":["11111","10000","10000","11110","00001","00001","11110"],
        " ":["00000"]*7,
    }
    def draw_text(text,x,y,scale,color):
        for ch in text.upper():
            grid=font.get(ch,font[" "])
            for dy,line in enumerate(grid):
                for dx,pixel in enumerate(line):
                    if pixel=="1":
                        rect(x+dx*scale,y+dy*scale,x+(dx+1)*scale,y+(dy+1)*scale,color)
            x+=6*scale

    draw_text("MIHASSEI",120,215,15,(29,41,38))
    draw_text("NEWS",120,350,12,(214,74,53))
    draw_text("5 YEARS LATER",115,550,5,(76,86,79))
    draw_text("2026 2031",730,550,5,(76,86,79))

    payload=b"".join(b"\x00"+raw[y*width*3:(y+1)*width*3] for y in range(height))
    def pngchunk(typ,data):
        import binascii
        return struct.pack(">I",len(data))+typ+data+struct.pack(">I",binascii.crc32(typ+data)&0xffffffff)
    png=(b"\x89PNG\r\n\x1a\n"+pngchunk(b"IHDR",struct.pack(">2I5B",width,height,8,2,0,0,0))
         +pngchunk(b"IDAT",zlib.compress(payload,9))+pngchunk(b"IEND",b""))
    filename=OUT/"og/brand.png"
    filename.parent.mkdir(parents=True,exist_ok=True)
    filename.write_bytes(png)
    # New path, not just a query parameter, to avoid the older image cache.
    (OUT/"og/brand-launch-20261102-v3.png").write_bytes(png)


def build_revision():
    """Expose this exact Git commit so public verification never trusts stale assets."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=BASE, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5,
            check=False,
        )
        sha = result.stdout.strip().lower()
        if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha):
            return sha
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def make_issue_social_images(issues):
    """First-party PNG covers make daily issue URLs shareable without paid APIs."""
    for day in issues:
        render_issue_card(day, OUT / "og" / "issues" / (day + ".png"))


def make_article_social_images(articles):
    """Create article-specific, clearly fictional PNGs without external services."""
    for article in articles:
        # Cached stock images are not auto-approved; always generate a safe
        # topic-specific PNG for anything not explicitly editorially reviewed.
        if uses_generated_scene(article) or uses_stock_photo(article):
            continue
        render_social_card(article, OUT / "og" / "articles" / (article["slug"] + ".png"))


def build():
    articles = load()
    serials = load_serials(BASE)
    issues = daily_issues(articles)
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT/"assets").mkdir(parents=True)
    (OUT/"images").mkdir(parents=True)
    for f in (BASE/"assets").iterdir():
        if f.is_file():
            shutil.copy2(f, OUT/"assets"/f.name)
    # Serve the conventional root URL as well as the explicit HTML icon links.
    shutil.copy2(BASE / "assets" / "favicon.ico", OUT / "favicon.ico")
    write(SITE_CSS_PATH.lstrip("/"), bundled_stylesheets())
    shutil.copy2(BASE / "ads.txt", OUT / "ads.txt")
    # Publish only stock photos selected by the current image policy. Cached
    # candidates that lost review, were replaced by a generated scene, or now have
    # a reviewed story illustration stay in the source tree but never ship to readers.
    photos = BASE / "assets" / "photos"
    for item in articles:
        if uses_stock_photo(item):
            source = photos / (item["slug"] + ".jpg")
            destination = OUT / "photos" / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    # Publish ONLY byte-verified and visually reviewed JPEGs; the provenance manifest
    # itself never needs to be public or requested by a browser.
    for item in articles:
        if uses_generated_scene(item):
            destination = OUT / generated_url(item).lstrip("/")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(BASE / "assets" / "generated" / (item["slug"] + ".jpg"), destination)
    # Only validated, fingerprint-matched optional MP4s are ever published.
    # Stale/over-budget files in the source tree do not reach the live site.
    for item in articles:
        clip = article_clip(item)
        if clip:
            target = OUT / clip_url(item).lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(BASE / CLIP_DIR / (item["slug"] + ".mp4"), target)
            subtitles = broadcast_vtt(item, clip)
            if subtitles:
                write(clip_url(item).lstrip("/")[:-4] + ".vtt", subtitles)
    write("build-revision.txt", build_revision() + "\n")
    write("index.html",home(articles,serials))
    write("about/index.html",about())
    write("editorial/index.html",editorial_policy())
    write("advertise/index.html",advertise())
    write("privacy/index.html",privacy())
    write("archive/index.html",archive(articles))
    write("topics/index.html",topic_index(articles))
    for tag, items in topic_groups(articles).items():
        write("topics/"+tag+"/index.html", topic_page(tag, items, articles))
    write("saved/index.html",saved_page())
    # Owner-only review helpers remain available as local Python renderers.
    # They are NOT shipped as public website pages: no auth provider or fee.
    write("issues/index.html",issue_index(issues))
    for day, items in issues.items():
        write("issues/"+day+"/index.html",issue_page(day, items, issues))
    for category, items in curated_categories(articles).items():
        write("category/"+CATEGORY_PATHS[category]+"/index.html",
              category_page(category, items))
    for a in articles:
        write("articles/"+a["slug"]+"/index.html",story(a,articles,issues,serials))
        write("images/"+a["slug"]+".svg",cover(a))
    build_serials(serials, layout, write, OUT)
    other_files(articles, issues, serials)
    make_brand_image()
    make_issue_social_images(issues)
    make_article_social_images(articles)
    print(f"Built {len(articles)} fictional articles to {OUT}")


if __name__ == "__main__":
    build()
