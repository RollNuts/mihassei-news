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
from datetime import datetime
from email.utils import format_datetime
from functools import lru_cache
from pathlib import Path

if __package__:
    from .social_cards import render_social_card, render_issue_card
    from .editorial_covers import render_cover, render_story_visual, story_subject
    from .media_policy import usable_cached_photo
    from .generated_photos import usable_generated_scene, generated_url
    from .serials import load_serials, render_serials_feature, serial_routes, build_serials
    from .reader_paths import suggested_articles
    from .editorial_starter import starter_picks, homepage_starter_picks, render_starter_picks
    from .serial_discovery import render_serial_bridge
    from .share_desk import render as render_share_desk
    from .growth_desk import render as render_growth_desk
else:
    from social_cards import render_social_card, render_issue_card
    from editorial_covers import render_cover, render_story_visual, story_subject
    from media_policy import usable_cached_photo
    from generated_photos import usable_generated_scene, generated_url
    from serials import load_serials, render_serials_feature, serial_routes, build_serials
    from reader_paths import suggested_articles
    from editorial_starter import starter_picks, homepage_starter_picks, render_starter_picks
    from serial_discovery import render_serial_bridge
    from share_desk import render as render_share_desk
    from growth_desk import render as render_growth_desk
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
CATEGORIES = ("すべて", "社会", "経済", "技術", "政治", "暮らし", "スポーツ", "エンタメ")
ARTS = ("notice", "cards", "tower", "robot", "meeting", "generic")
CATEGORY_PATHS = {
    "社会":"society", "経済":"economy", "技術":"technology",
    "政治":"politics", "暮らし":"lifestyle",
    "スポーツ":"sports", "エンタメ":"entertainment"
}
# Editorial category descriptions help readers choose without keyword stuffing.
CATEGORY_INTROS = {
    "社会":"親切な制度ほど、なぜか不便。暮らしの矛盾を切り取る社会風刺。",
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


def editorial_lead(articles):
    """Use a recent, human-curated pick for the front page, never for RSS."""
    if not articles:
        return None
    latest = datetime.fromisoformat(publication_date(articles[0]))
    for item in starter_picks(articles, base=BASE, limit=8):
        article = item["article"]
        published = datetime.fromisoformat(publication_date(article))
        hours_old = (latest - published).total_seconds() / 3600
        if 0 <= hours_old <= 48:
            return article
    return articles[0]


def load():
    articles = json.loads((BASE / "content/articles.json").read_text(encoding="utf-8"))
    if not isinstance(articles, list):
        raise ValueError("Articles must be a list")
    seen = set()
    for a in articles:
        required = ("slug", "title", "summary", "category", "fiction_at", "paragraphs", "source", "art")
        if any(k not in a for k in required):
            raise ValueError("Missing article field")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", a["slug"]) or a["slug"] in seen:
            raise ValueError("Invalid or duplicate slug: " + a["slug"])
        seen.add(a["slug"])
        if a["category"] not in CATEGORIES[1:] or a["art"] not in ARTS:
            raise ValueError("Invalid category/art")
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
        f"架空 {future.year}/{future.month}/{future.day}"
        f" {future.hour:02}:{future.minute:02}"
    )
    if compact:
        # Dense cards need one truthful date and one clear fiction label.
        # The full five-years-later dateline remains on every article page.
        compact_date = f"公開 {real.year}/{real.month}/{real.day}"
        if "T" in published:
            compact_date += f" {real.hour:02}:{real.minute:02}"
        return (
            '<div class="story-meta story-meta--compact">'
            + '<span class="category">' + escape(a["category"]) + "</span>"
            + f'<time class="real-published-at" datetime="{escape(published)}"'
              f' aria-label="実際の掲載日時">{escape(compact_date)}</time>'
            + "</div>"
        )
    return (
        '<div class="story-meta">'
        + '<span class="ticker">' + escape(a.get("flash", "速報")) + "</span>"
        + '<span class="category">' + escape(a["category"]) + "</span>"
        + f'<time class="real-published-at" datetime="{escape(published)}"'
          f' aria-label="実際の掲載日時">{escape(real_label)}</time>'
        + f'<time class="fiction-dateline" datetime="{escape(a["fiction_at"])}"'
          f' aria-label="5年後の架空のニュース日時">{escape(future_label)}</time>'
        + "</div>"
    )


def share_url(a):
    url = SITE + article_url(a) + "?utm_source=x&utm_medium=social&utm_campaign=reader_share&utm_content=topic_card_v2"
    # Small standalone hook for the reader's X draft. Does not post automatically.
    teaser = a["summary"][:75]
    text = "【未発生新聞｜5年後のもしも（創作）】\n" + a["title"] + "\n\n" + teaser + "\n\n#未発生新聞"
    return "https://x.com/intent/tweet?" + urlencode({"text": text, "url": url})


def issue_share_url(day, items):
    """Reader-initiated X draft, with the true issue date and fiction disclosure."""
    stamp = datetime.fromisoformat(day)
    lines = [
        f"【未発生新聞 {stamp.month}/{stamp.day}号】",
        f"5年後の『もしも』を真顔で報じる創作ニュース、{len(items)}本。",
    ]
    for item in items[:2]:
        headline = item["title"]
        lines.append("・" + headline[:27] + ("…" if len(headline) > 27 else ""))
    lines.append("※フィクション #未発生新聞")
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


def editorial_image(a):
    """Priority: locally generated staged scene > approved stock > offline SVG."""
    if usable_generated_scene(a, root=BASE):
        return generated_url(a)
    if usable_cached_photo(a, root=BASE):
        return "/photos/" + a["slug"] + ".jpg"
    return illustration_path(a)


def article_image_alt(a, *, generated=False, photo_ok=False):
    """Honest, article-specific description without claiming to see AI pixels.

    Generated scene prompts are not guaranteed to match each rendered image.
    The headline identifies the depicted story context; the caption carries
    detailed provenance. The alt is useful when images fail or in screen readers.
    """
    if generated:
        return "創作ニュース「" + a["title"] + "」を題材にしたAI生成のイメージ画像"
    if photo_ok:
        return a["photo"]["alt"] + "（記事のイメージ写真）"
    return "創作ニュース「" + a["title"] + "」の編集部制作イラスト"


def image_tag(a, cls, extra=""):
    generated = usable_generated_scene(a, root=BASE)
    photo_ok = not generated and usable_cached_photo(a, root=BASE)
    url = editorial_image(a)
    alt = article_image_alt(a, generated=generated, photo_ok=photo_ok)
    fallback = f' data-fallback="{escape(illustration_path(a))}"' if (generated or photo_ok) else ""
    if generated:
        extra += ' data-generated-scene="true"'
    # Keep the existing 16:9 reserved image frame to avoid mobile layout shift.
    return (f'<img class="{cls}" src="{escape(url)}" alt="{escape(alt)}"'
            f' width="1200" height="675" {extra}{fallback}>')


def photo_credit(a):
    """Always disclose AI staging; never mislabel a synthetic scene as real."""
    if usable_generated_scene(a, root=BASE):
        return "AI生成の架空ニュース再現画像（コント風演出）。実際の事件・人物を撮影した報道写真ではありません。"
    if not usable_cached_photo(a, root=BASE):
        return "記事の題材を表現した編集部制作の創作イラスト。実際の出来事の写真ではありません。"
    p = a["photo"]
    return (f'イメージ写真：<a href="{escape(p["source"])}" target="_blank"'
            f' rel="noopener noreferrer external">{escape(p["credit"])} / Unsplash</a>。'
            '記事内の出来事や架空の企業とは関係ありません。')


def card(a, archive=False):
    attrs = ""
    if archive:
        category = a["category"]
        decade = a["fiction_at"][:3] + "0"
        searchable = " ".join((a["title"], a["summary"], a["fiction_company"], category))
        attrs = (f' data-archive-card data-category="{escape(category)}" '
                 f'data-decade="{decade}" data-search="{escape(searchable)}"')
    return (
        f'<article class="story-card"{attrs}><a class="story-image-link{" story-image-link--generated" if usable_generated_scene(a, root=BASE) else ""}" href="{article_url(a)}">'
        + image_tag(a,"story-thumb",extra='loading="lazy" decoding="async"') + '</a>'
        + category_meta(a, compact=True)
        + f'<h3><a href="{article_url(a)}">{escape(a["title"])}</a></h3>'
        + f'<p>{escape(a["summary"])}</p>'
        + '</article>'
    )

def social_image(article):
    """Use verified local JPEG for OGP when present, else deterministic PNG."""
    if article is not None:
        if usable_generated_scene(article, root=BASE):
            return generated_url(article)
        if usable_cached_photo(article, root=BASE):
            return "/photos/" + article["slug"] + ".jpg"
        return "/og/articles/" + article["slug"] + ".png?v=20261103-topic2"
    # Use a physically new raster URL: X caches both page cards and image URLs.
    # The legacy brand.png remains available for previously published links.
    return "/og/brand-launch-20261102-v3.png"


def layout(title, desc, path, main, active="", kind="website", article=None, feed_url=None, collection=None, serial=None, serial_collection=None):
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
        mast_subtitle = "公開資料から考える未来。予測は保証されません。"
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
    # Native mobile navigation: important reading routes are always visible.
    # Secondary pages live in a keyboard-accessible details menu, not an
    # undiscoverable horizontal scroller. Desktop keeps the full navigation.
    mobile_main_keys = {"home", "archive", "serials"}
    mobile_primary = "".join(
        f'<a href="{escape(u)}"{" aria-current=page" if key == active and key else ""}>{escape(text)}</a>'
        for u, text, key in nav if key in mobile_main_keys
    )
    mobile_secondary = "".join(
        f'<a href="{escape(u)}"{" aria-current=page" if key == active and key else ""}>{escape(text)}</a>'
        for u, text, key in nav if key not in mobile_main_keys
    )
    mobile_navhtml = (
        '<div class="container mobile-nav">'
        + mobile_primary
        + '<details class="mobile-nav-more"><summary>メニュー</summary>'
        + '<div class="mobile-nav-menu">' + mobile_secondary + '</div></details></div>'
    )
    # Use a share-intent link without assuming ownership of an X account.
    site_share = escape("https://x.com/intent/tweet?" + urlencode({
        "text": "5年後は、こうなってるかも。今の話題をちょっと大げさに描く未発生新聞。#未発生新聞（創作）",
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
                  generated=usable_generated_scene(article, root=BASE),
                  photo_ok=bool(photo and local_photo and not usable_generated_scene(article, root=BASE))
              ) if article else
              ("未発生新聞 " + issue_path.group(1) + "号の架空の新聞表紙"
               if issue_path else "未発生新聞（創作ニュース）"))
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
            "articleSection":article["category"]
        })
    schemas = [metadata]
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
    robots_tag = '<meta name="robots" content="noindex,follow">' if path in ("/saved/", "/404.html") else ""
    serial_progress_script = ('<script defer src="/assets/serial-progress.js"></script>'
                              if path in ('/', '/serials/') or serial is not None else '')
    # Only load reader tools where they can be used. Leave engagement active for
    # the newspaper clock and real audience counts; these links work without JS.
    archive_script = '<script defer src="/assets/site.js"></script>' if path == "/archive/" else ""
    comments_script = '<script defer src="/assets/comments.js"></script>' if kind == "story" else ""
    comment_activity_script = ('<script defer src="/assets/comment-activity.js"></script>'
                               if path == "/" else "")
    bookmarks_script = ('<script defer src="/assets/bookmarks.js"></script>'
                        if kind == "story" or path == "/saved/" else "")
    resume_script = ('<script defer src="/assets/returning-shelf.js"></script>'
                     if path == "/" else "")
    topic_rss = (f'<link rel="alternate" type="application/rss+xml" title="{escape(title)} RSS" '
                 f'href="{escape(feed_url)}">' if feed_url else "")
    return f'''<!doctype html><html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
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
<link rel="icon" type="image/svg+xml" href="/assets/favicon.svg">
<link rel="alternate" type="application/rss+xml" title="未発生新聞 RSS" href="/feed.xml">
{topic_rss}
<link rel="stylesheet" href="{stylesheet_url()}">
{archive_script}
<script defer src="/assets/engagement.js"></script>
{comments_script}
{comment_activity_script}
{bookmarks_script}
{serial_progress_script}
{resume_script}
<script type="application/ld+json">{ld}</script>
<title>{escape(complete)}</title>
</head><body>
<a class="skip" href="#main">本文へ</a>
<div class="strip"><div class="container strip-content">
<span class="strip-category"><span class="fiction-mark">{escape(publication_label)}</span></span>
<div class="clock-line"><span class="live-pulse" aria-hidden="true"></span><span>紙面の架空時計</span>
<time data-future-clock aria-label="物語内の架空の時刻">--:--:--</time></div>
</div></div>
<header class="container"><div class="masthead">
<div class="mast-kicker">THE UNHAPPENED TIMES</div>
<a class="brand" href="/" aria-label="未発生新聞トップへ">未発生<span class="brand-last">新聞</span></a>
<p class="mast-sub">{escape(mast_subtitle)}</p>
</div></header>
<div class="container observer-shell">
<div class="observer-widget" data-observer-widget hidden role="group" aria-label="本日の読者の動き">
<span class="observer-eye" aria-hidden="true">◉</span>
<span class="observer-stat"><span class="observer-label">今日の来訪</span>
<strong data-observer-count>—</strong><span class="observer-suffix">ブラウザ</span></span>
<span class="observer-stat"><span class="observer-label">今日の記事閲覧</span>
<strong data-observer-reads>—</strong><span class="observer-suffix">件</span></span>
<small>※1ブラウザ・1記事につき1日1回の集計</small>
</div></div><nav class="nav-bar" aria-label="メインナビゲーション"><div class="container nav-inner">{navhtml}</div>{mobile_navhtml}</nav>
<main id="main">{main}</main>
<footer class="footer"><div class="container"><div class="footer-grid">
<div><a class="footer-brand" href="/">未発生新聞</a>
<p class="footer-copy">まだ起きていないニュースを、真顔で。</p></div>
<nav class="footer-nav" aria-label="補助リンク"><a href="/saved/">あとで読む</a><a href="/archive/#category-feeds">ジャンル別RSS</a><a href="/about/">本紙について</a>
<a href="/editorial/">編集方針</a><a href="/advertise/">広告・協賛</a><a href="/privacy/">プライバシー</a><a href="/feed.xml">RSS</a><a href="{site_share}" target="_blank" rel="noopener noreferrer external">Xで紹介する</a></nav></div>
<div class="footer-bottom"><span>© 未発生新聞 / veripsa.com</span>
<span>創作記事・推理小説はフィクションです。市場分析は投資助言ではありません。</span></div></div></footer>
</body></html>'''

def home(articles, serials=None):
    serials = load_serials(BASE) if serials is None else serials
    a = editorial_lead(articles)
    newest = articles[0]
    remaining = [item for item in articles if item["slug"] != a["slug"]]
    # A curated story may lead, but issue dates and feeds always use the newest.
    last_published = publication_date(newest)
    last_day = last_published[:10]
    last_day_count = sum(publication_date(item)[:10] == last_day for item in articles)
    actual_stamp = datetime.fromisoformat(last_published)
    last_label = f"{actual_stamp.month}月{actual_stamp.day}日"
    if "T" in last_published:
        last_label += f" {actual_stamp.hour:02}:{actual_stamp.minute:02}"
    # A sidebar should read like a newspaper's short headline rail, not three
    # miniature article pages with duplicate summaries and action buttons.
    side = "".join('<article class="brief-card">' + category_meta(x, compact=True) +
                   f'<h3><a href="{article_url(x)}">{escape(x["title"])}</a></h3></article>'
                   for x in remaining[:3])
    # Curated starters must not repeat the editorial lead or the sidebar.
    # Share this selection with the public read-only checker.
    starters = homepage_starter_picks(articles, a, base=BASE)
    starter_section = render_starter_picks(starters, image_tag, article_url)
    picked_slugs = {item["article"]["slug"] for item in starters}
    # A compact fresh grid: the full article archive remains accessible.
    recent_articles = [x for x in remaining[3:] if x["slug"] not in picked_slugs][:4]
    recent = "".join(card(x) for x in recent_articles)
    # The activity rail doubles as genuine article discovery: do not repeat
    # the lead, sidebar, visible story grid, or carefully selected starters.
    front_slugs = {a["slug"], *(x["slug"] for x in remaining[:3]),
                   *(x["slug"] for x in recent_articles), *picked_slugs}
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
        '<h2 id="press-timeline-title">発行ログ</h2>'
        '<span>新着の次に読む、まだ見えていない記事</span></div>'
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
    # This is only a pool of real published articles, not a claim that any
    # of them is popular. The browser shows a ranking ONLY after the existing
    # readership API returns genuine, sufficiently populated counts.
    popular_candidates = articles[:20]
    popular_options = escape(json.dumps(
        [{"slug": x["slug"], "title": x["title"], "category": x["category"]}
         for x in popular_candidates], ensure_ascii=False, separators=(",", ":")))
    popular_section = f'''<section class="readers-choice" data-popular-section
 data-popular-options="{popular_options}" aria-labelledby="readers-choice-title" hidden>
<div class="section-head"><div>
<span class="small-label red">READER DISCOVERY / 読者の注目</span>
<h2 id="readers-choice-title">新着の中で読まれている記事</h2></div>
<a href="/archive/">ほかの記事も探す ↗</a></div>
<p class="readers-choice-note">実測ブラウザ単位の参考集計。実人数・PVではありません。</p>
<ol class="readers-choice-list" data-popular-list></ol>
</section>'''
    discussion_options = escape(json.dumps(
        [{"slug": x["slug"], "title": x["title"], "category": x["category"]}
         for x in articles[:20]], ensure_ascii=False, separators=(",", ":")))
    content = f'''<div class="container index-main">
<div class="news-briefing" data-edition-status data-last-edition="{escape(last_day)}"
 data-edition-count="{last_day_count}" data-last-label="{escape(last_label)}">
<div class="news-briefing-title"><span class="small-label red" data-edition-eyebrow>LATEST HEADLINES</span>
<strong data-edition-title>最新の架空ニュース</strong>
<span class="edition-updated" data-edition-updated>最終発行：{escape(last_label)}（現実時間）</span></div>
<a class="return-update" data-return-update data-latest-published="{escape(last_published)}"
 href="{article_url(newest)}" hidden>前回から新しい記事が届きました <span aria-hidden="true">↗</span></a>
</div>
<aside class="returning-shelf" data-returning-shelf hidden aria-label="この端末に保存した記事">
 <span class="returning-shelf-eyebrow">SAVED / あとで読む</span>
 <span class="returning-shelf-intro">保存した記事から再開</span>
 <a class="returning-shelf-article" data-returning-shelf-article href="/saved/"></a>
 <span class="returning-shelf-count" data-returning-shelf-count></span>
 <a class="returning-shelf-all" href="/saved/">保存一覧 ↗</a>
</aside>
<section class="hero-grid">
<article class="lead">
<span class="small-label red">TOP STORY / 一面</span>
<div class="lead-info">{category_meta(a, compact=True)}</div>
<h1><a href="{article_url(a)}">{escape(a["title"])}</a></h1>
<a class="lead-media" href="{article_url(a)}">{image_tag(a, "hero-image", extra='fetchpriority="high" decoding="async"')}</a>
<p class="dek">{escape(a["summary"])}</p>
<div class="hero-actions"><a class="read-more" href="{article_url(a)}">記事を読む</a>{actions(a,compact=True)}</div>
</article>
<aside class="side-brief"><div class="side-title"><h2>新着の見出し</h2><span>LATEST</span></div>{side}</aside>
</section>
<section class="section-block"><div class="section-head"><h2>新着記事</h2>
<a href="/archive/">記事一覧を見る ↗</a></div>
<div class="story-grid">{recent}</div></section>
{timeline_section}
<section class="special-edition" aria-label="ランダムに号外記事を読む" data-issue-picker data-issue-options="{issue_options}">
  <div class="special-edition-heading">
    <span class="special-edition-kicker">EXTRA EDITION / 号外</span>
    <h2>何が起きるか、まだ分からない。</h2>
    <p>気になる号外を一枚めくってみよう。</p>
  </div>
  <div class="special-edition-machine">
    <button type="button" class="issue-picker-button" data-pick-issue>号外をめくる <span aria-hidden="true">↗</span></button>
    <div class="issue-picker-result" data-issue-result aria-live="polite">
      <span class="small-label">まだ開封されていません</span>
      <p data-issue-headline>ニュースの続きは、めくってからのお楽しみ。</p>
      <a data-issue-link href="/archive/" hidden>記事を読む ↗</a>
    </div>
  </div>
</section>
<section class="genre-explore" aria-label="ジャンル別に記事を探す">
<div><span class="small-label red">SECTIONS</span><h2>ジャンルから読む</h2></div>
<nav class="genre-links" aria-label="ジャンルから探す">{genre_links}</nav>
</section>
{render_serials_feature(serials)}
{starter_section}
{popular_section}
<details class="extras-shelf">
<summary><strong>発行号・コメント</strong><span class="extras-open" aria-hidden="true">＋</span></summary>
<div class="extras-shelf-body">
{issue_section}
<section class="reader-discussions" data-comment-discussions data-comment-discussion-options="{discussion_options}" aria-labelledby="reader-discussions-title">
<div class="reader-discussions-copy"><span class="small-label red">READER PARTICIPATION / みんなのツッコミ</span>
<h2 id="reader-discussions-title">このニュース、あなたならどうツッコむ？</h2>
<p data-discussion-intro>架空ニュースへのツッコミを、記事の最後からひとこと。ニックネームで参加できます。</p>
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
    decades = sorted({a["fiction_at"][:3] + "0" for a in articles})
    options = '<option value="すべて">すべての年代</option>' + "".join(
        f'<option value="{x}">{x}年代</option>' for x in decades)
    # One available decade is not a meaningful filter. Keep the select in DOM
    # so existing filtering works automatically when a second decade exists.
    decade_hidden = ' hidden' if len(decades) < 2 else ''
    content = f'''<div class="container archive-page">
<header class="page-intro"><span class="eyebrow">FICTION ARCHIVE</span><h1>架空ニュースの記事一覧</h1>
<p>気になる見出しから、どうぞ。</p>
<p class="archive-editions-link"><a href="/issues/">発行日ごとの紙面から読む ↗</a></p></header>
<div class="filters" aria-label="カテゴリー">{buttons}</div>
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
    title = category + "の架空ニュース"
    intro = CATEGORY_INTROS[category]
    content = (
        '<div class="container archive-page">'
        '<header class="page-intro"><span class="eyebrow">CATEGORY / FICTIONAL NEWS</span>'
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
        '<h1>未発生新聞の発行号</h1>'
        '<p>現実に記事を公開した日付ごとに、5年後の架空ニュースをまとめています。'
        '3本以上の創作ニュースを掲載した日だけをひとつの発行号にしています。</p></header>'
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
        f'<p>現実の{issue_date_label(day)}に掲載した、{len(items)}本の創作ニュースです。'
        f'物語の舞台は{int(day[:4]) + 5}年の架空の未来。'
        '記事内の出来事・発言・登場人物はフィクションです。</p>'
        f'<p class="issue-categories">{escape(kinds)}</p></header>'
        '<aside class="issue-share-panel" aria-label="この号を紹介する">'
        '<div><span class="eyebrow">SHARE THIS EDITION / 創作新聞</span>'
        '<p>気になる見出しを、フィクションと明記してXで紹介できます。</p></div>'
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
    paras = "".join(f'<p>{escape(p)}</p>' for p in a["paragraphs"])
    src = a["source"]
    # Strong topic continuity; no duplicate next story in the related cards.
    recommended = suggested_articles(a, articles, limit=4)
    next_story = recommended[0] if recommended else None
    related = "".join(card(x) for x in recommended[1:4])
    next_url = article_url(next_story) if next_story else "/archive/"
    next_title = next_story["title"] if next_story else "記事一覧から次のニュースを探す"
    next_category = next_story["category"] if next_story else "新着記事"
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
    related_section = (
        '<section class="article-related"><div class="section-head"><h2>さらに読む</h2>'
        '<a href="/archive/">ほかの記事 ↗</a></div>'
        '<div class="story-grid">' + related + '</div></section>'
    ) if related else ""
    content = f'''<div class="container article-page">
<div class="article-shell"><article>
<header class="article-header">
<nav class="breadcrumbs" aria-label="パンくずリスト"><a href="/">トップ</a><span aria-hidden="true"> / </span><a href="/archive/">記事一覧</a><span aria-hidden="true"> / </span><span aria-current="page">この記事</span></nav>
<div class="article-dateline">{category_meta(a)}</div>
<h1>{escape(a["title"])}</h1>
<p class="dek">{escape(a["summary"])}</p>
<p class="story-fiction-context"><strong>この記事は創作です。</strong></p>
<div class="story-meta"><span>未発生新聞 編集部</span><span>読了目安 約{read_minutes}分</span></div>
</header>
<figure class="article-hero-figure">
{image_tag(a, "article-visual", extra='loading="eager" fetchpriority="high" decoding="async"')}
<figcaption>{photo_credit(a)}</figcaption></figure>
<div class="article-body">{paras}</div>
<div class="article-endmark">◆</div>
<nav class="reader-next" aria-label="続けて読む記事">
<span class="reader-next-kicker">NEXT STORY / もう1本、読む · {escape(next_category)}</span>
<a href="{escape(next_url)}" class="reader-next-title">{escape(next_title)} <span aria-hidden="true">→</span></a>
</nav>
<section class="future-poll" data-future-poll data-poll-article="{escape(a["slug"])}" aria-labelledby="future-poll-title">
<div class="future-poll-heading">
<span class="future-poll-kicker">WHAT IF? / もしもの投票</span>
<h2 id="future-poll-title">この「もしも」、ありそう？</h2>

</div>
<div class="future-poll-choices" role="group" aria-label="架空ニュースのありそう度を投票">
<button type="button" data-poll-choice="plausible" aria-pressed="false"><span aria-hidden="true">◉</span> ありそう</button>
<button type="button" data-poll-choice="unlikely" aria-pressed="false"><span aria-hidden="true">✕</span> それはない！</button>
</div>
<p class="future-poll-status" data-poll-status role="status">ブラウザ単位で1記事1票。投票後に結果を表示します。</p>
<div class="future-poll-results" data-poll-results hidden aria-label="もしもの投票結果">
<div class="future-poll-result-line"><span>ありそう</span><strong data-poll-plausible-percent>—</strong></div>
<div class="future-poll-meter"><span data-poll-plausible-bar></span></div>
<div class="future-poll-result-line"><span>それはない！</span><strong data-poll-unlikely-percent>—</strong></div>
<div class="future-poll-meter"><span data-poll-unlikely-bar></span></div>
</div>
</section>
<div class="article-reader-metric" data-article-readers hidden><span>今日この記事を読んだブラウザ <strong data-article-reader-count>—</strong>件</span><small>※日本時間・1日1回集計、実人数ではありません</small></div>
{actions(a)}
<div class="article-save-inline">{bookmark_button(a)}<a href="/saved/">保存した記事を見る ↗</a></div>
<p class="bookmark-status" data-bookmark-status role="status" aria-live="polite"></p>
<details class="source-context" id="story-source" aria-label="着想した実際の話題">
<summary>着想元を見る</summary>
<div class="source-context-body">
<p>{escape(src["topic"])}</p>
<p class="source-context-disclaimer">記事内の出来事は創作であり、元記事が報じた事実ではありません。</p>
<a href="{escape(src["url"])}" target="_blank" rel="noopener noreferrer external">
{escape(src["publisher"])} の元記事を見る ↗</a>
</div>
</details>
{bridge}
<div class="article-end"><span class="reporter">未発生新聞</span>{issue_link}{category_link}<a href="/archive/">記事一覧へ ↗</a></div>
</article>
<section class="reader-comments" id="reader-comments" data-comments data-article="{escape(a["slug"])}" aria-labelledby="comments-heading">
<div class="reader-comments-heading">
<span class="eyebrow">READERS' VOICES / 読者の声</span>
<h2 id="comments-heading">読者のツッコミ <small data-comments-count></small></h2>
<p>感想やツッコミをどうぞ。投稿内容は公開されます。</p>
</div>
<div class="reader-comments-list" data-comments-list></div>
<p class="reader-comments-status" data-comments-status role="status">コメントを準備しています…</p>
<form class="reader-comments-form" data-comments-form>
<label for="reader-nickname">ニックネーム <span>（任意・16文字以内）</span></label>
<input id="reader-nickname" name="nickname" type="text" maxlength="16" autocomplete="nickname" placeholder="匿名の未来人">
<label for="reader-body">コメント</label>
<textarea id="reader-body" name="body" minlength="2" maxlength="180" rows="3" required placeholder="未来のニュースにひとこと…"></textarea>
<div class="comment-form-footer"><span>2〜180文字／URL・個人情報・誹謗中傷の投稿は禁止。スパム対策の投稿制限があります。</span>
<button type="submit">コメントを投稿</button></div>
</form>
</section></div>
{related_section}
</div>'''
    return layout(a["title"], a["summary"] + " ※創作ニュースです。", article_url(a), content,kind="story",article=a)

def about():
    content = '''<div class="container archive-page">
<header class="page-intro"><span class="eyebrow">ABOUT US</span>
<h1>未発生新聞について</h1>
<p>まだ起きていないニュースを、真顔で。</p></header>
<div class="about-copy">
<h2>5年後を、ちょっと大げさに想像する新聞です。</h2>
<p>今のニュースや身近な話題を、「もし5年後もこの調子だったら？」と勝手に膨らませる架空の新聞です。現実の出来事を出発点に、少し大げさで、ちょっと皮肉な世界を創作します。</p>
<p>未来を当てようとしているわけではありません。「そんなわけないだろ」と笑えて、「でも、ちょっとありそう」と思えるくらいが理想。人を見下すためではなく、世の中の変な仕組みや行き過ぎた常識を笑うための新聞です。</p>
<h2>架空ニュースと推理小説は創作です。</h2>
<p>本紙の架空ニュース・推理小説に登場する企業、人物、事件、発言、数値はフィクションです。実際のニュースや報道ではありません。</p>
<p>連載「未来相場観測室」は別枠の市場考察です。出典を示した現実の情報と、編集上の仮説・予測を区別し、後日検証します。投資成果を保証したり、売買を勧めたりするものではありません。<a href="/serials/">連載を読む ↗</a></p>
<p>元の話題から着想して記事を書きますが、元記事やSNSの文章、画像、スクリーンショットは転載しません。参考にした実際の話題へは記事末尾から移動できます。</p>
<h2>今日も、未発生。</h2>
<p>小さなニュースから大きな事件まで。何も起きていない毎日に、ちょっとした号外を。</p>
<p>「観測されたブラウザ」「いいね」「もしもの二択投票」は匿名のブラウザ単位の参考集計です。実際の人数とは一致しない場合があります。</p>
<p><a href="/editorial/" style="text-decoration:underline">編集方針と記事の作り方を読む ↗</a></p>
<p><a href="/feed.xml" style="text-decoration:underline">RSSで購読する ↗</a></p>
</div></div>'''
    return layout("本紙について","未発生新聞は今の話題を5年後の架空ニュースにして笑う創作メディアです。未来を的中させる予測ではありません。","/about/",content,"about")

def editorial_policy():
    """Public disclosure of the creative process and commercial independence."""
    content = '''<div class="container archive-page">
<header class="page-intro"><span class="eyebrow">EDITORIAL POLICY</span>
<h1>編集方針・記事の作り方</h1><p>5年後を当てるためではなく、今を少し笑うために。事実と創作は分けて伝えます。</p></header>
<div class="about-copy">
<h2>これは予知ではなく、未来を使った冗談です。</h2>
<p>今の話題の何がちょっとおかしいのかを見つけ、「もし5年後に行き過ぎたら？」と想像して創作ニュースにします。予測の的中率を競うものでも、誰かを攻撃するためのものでもありません。見出しだけでも状況が伝わり、最後にもう一段笑える記事を目指します。</p>
<h2>創作ニュースと現実の市場考察を区別します。</h2>
<p>ニュース面と連載「未発生事件簿」は創作です。架空ニュースに登場する企業、人物、発言、制度、事件、数値はフィクションであり、未来予測や実際の報道ではありません。</p>
<p>連載「未来相場観測室」は現実の公開資料に基づく編集上の分析です。執筆時点と参照先を明記し、仮説・反対シナリオ・後日検証の条件を公開します。実在企業に関する事実は出典で確認し、予想を事実のように扱わず、特定銘柄の売買を勧誘しません。<a href="/serials/">連載の入り口 ↗</a></p>
<h2>生成AIと自動化の利用</h2>
<p>公開されたニュースを着想元として探し、生成AIを文章づくりの補助に活用しています。更新や公開の工程にも自動化を取り入れています。AIによる文章や着想には誤りや表現の偏りが起こりうるため、元の話題へのリンク、記事の創作表示、形式・重複などの確認を設けています。掲載後も必要に応じて修正します。</p>
<p>実在する人物や企業が架空の不祥事を起こしたかのように見せることは避け、創作部分の固有名詞は原則として架空のものを用います。</p>
<h2>出典と創作の境界</h2>
<p>着想元の現実のニュースは、各記事末尾の「この記事の着想元」から確認できます。着想元の報道が、記事内の創作内容を裏付けているわけではありません。元記事の本文や写真を無断転載せず、写真素材は使用条件とクレジットを確認し、架空の出来事の記録写真と誤認されないように表示します。</p>
<h2>広告・アフィリエイトについて</h2>
<p>現在は広告の自動配信を行っていません。将来、広告やアフィリエイトリンクを掲載する場合は、記事内容との区別と広告・PRの表示を行い、読みにくいほど広告を増やす運用は避けます。収益や検索順位を保証するものではありません。</p>
<p><a href="/advertise/" style="text-decoration:underline">広告・協賛に関するご案内 ↗</a></p>
<h2>運営者・訂正の連絡</h2>
<p>出典の誤り、権利上の問題、記事の訂正依頼は<a href="https://github.com/RollNuts/mihassei-news/issues" target="_blank" rel="noopener noreferrer external">公開GitHub Issues</a>からお知らせください。公開フォームのため個人情報は書かないでください。</p>
<p><a href="/about/" style="text-decoration:underline">未発生新聞について ↗</a>　<a href="/privacy/" style="text-decoration:underline">プライバシーについて ↗</a></p>
</div></div>'''
    return layout("編集方針・記事の作り方","未発生新聞の創作ニュース制作、生成AIの利用、出典、広告と訂正に関する編集方針。","/editorial/",content)


def advertise():
    """Public, unambiguous commercial inquiry policy; no active advertising."""
    content = '''<div class="container archive-page">
<header class="page-intro"><span class="eyebrow">ADVERTISING &amp; PARTNERSHIPS</span>
<h1>広告・協賛のご案内</h1><p>広告より先に、読者が楽しめる新聞を。</p></header>
<div class="about-copy">
<h2>未発生新聞について</h2>
<p>未発生新聞は、今のニュースから「5年後はこうなるかも」を少し大げさに描く、風刺とユーモアの創作新聞です。未来の的中を目指す予測ではなく、記事内の出来事や団体はフィクションです。</p>
<h2>ご相談いただける内容</h2>
<p>サイト内の小さな協賛枠や、読者に役立つ関連企画について相談を受け付けています。内容・掲載期間・料金・実施可否は個別に決めます。現在、広告枠は販売・表示しておらず、スポンサー契約済みの企業も掲載していません。</p>
<p>記事の文章やニュース判断は協賛者の意向で書き換えません。広告・PRの掲載が決まった場合は、一般記事との境界に「広告」「PR」等を明確に表示します。記事や共有投稿の中に広告を無表示で紛れ込ませません。</p>
<h2>読者体験と実績</h2>
<p>広告を大量に並べず、スマートフォンでも読みやすい配置を優先します。アクセスは匿名のブラウザ単位で参考集計していますが、実人数・ページビュー・広告の表示数・クリック数と同義ではありません。出稿効果、PV、売上、検索順位は保証しません。架空の実績や架空の協賛者を掲載しません。</p>
<h2>ご相談の入口</h2>
<p>まずは<a href="https://github.com/RollNuts/mihassei-news/issues/new?template=sponsorship.yml" target="_blank" rel="noopener noreferrer external">公開GitHubのお問い合わせフォーム</a>から、公開しても問題のない概要のみお知らせください。GitHubアカウントが必要です。</p>
<p><strong>重要：</strong>このフォームの投稿・返信は一般に公開されます。氏名、個人のメールアドレス、電話番号、見積書、非公開の契約条件、その他の機密情報を絶対に記入しないでください。非公開情報のやり取りが必要な場合は、公開フォームでの詳細交換を行わず、双方が合意した安全な連絡方法を別途検討します。</p>
<p><a href="/editorial/" style="text-decoration:underline">編集方針を読む ↗</a>　<a href="/privacy/" style="text-decoration:underline">プライバシーについて ↗</a></p>
</div></div>'''
    return layout("広告・協賛のご案内",
                  "未発生新聞の広告・協賛掲載相談、広告表記、編集の独立性、公開問い合わせに関するご案内。",
                  "/advertise/",content)


def privacy():
    content='''<div class="container archive-page"><header class="page-intro">
<span class="eyebrow">PRIVACY</span><h1>プライバシーについて</h1>
<p>ニュースは架空。数字は実測。</p></header>
<div class="about-copy">
<h2>アクセスといいね</h2>
<p>未発生新聞では、来訪端末のおおよその数や、記事の閲覧・いいねを把握するため、ブラウザ内にランダムな識別子を保存します。識別子をハッシュ化した値をCloudflareのデータ保存機能に保管し、サイト全体の訪問、記事閲覧、いいね、X共有ボタンのクリックを集計しています。アクセス計測のために氏名やメールアドレスの登録は求めません。</p>
<p>閲覧数は人数ではなくブラウザ単位の参考値です。ブラウザの保存データを削除すると、新しいブラウザとして集計される場合があります。</p>
<h2>「今日の来訪」「今日の記事閲覧」について</h2>
<p>毎日の数字は日本時間の0時で区切ります。「今日の来訪」は、その日にページを開いた匿名ブラウザ識別子の数です。「今日の記事閲覧」は、同じブラウザが同じ記事を何度読み直してもその日は1件とし、異なる記事を読んだ場合は記事ごとに数える延べ件数です。どちらも実際の人数やPV、現在閲覧中の人数ではありません。過去のアクセスを後から推定して加算することはなく、導入時点から集計します。</p>
<p>重複を防ぐための日別記録と日別の集計値は直近14日分に限定し、訪問が発生した際に古い記録を削除します。集計には既存の匿名ブラウザ識別子のハッシュ値のみを使い、新しい個人情報や課金型の外部サービスは追加しません。</p>
<p>どこから訪れたかは「X」「検索」「その他の外部サイト」「判別できない流入」の4種類に大まかに分類します。ブラウザ内で分類した種類名だけを送信し、参照元URL全体・検索した言葉・IPアドレスを集計データベースには保存しません。サイト内のページ移動は流入経路に含めず、同じブラウザでも複数経路を利用すれば複数の区分に数えられます。数値は実際の来訪人数やPVではなく、計測開始以降の参考値です。</p>
<h2>記事別のX・検索流入について</h2>
<p>「X」「検索」の2区分について、匿名ブラウザの識別子をハッシュ化し、記事ごと・区分ごとに重複を除いて記録することがあります。投稿内容、検索語、SNSアカウント、参照元URL全体、UTMの詳細、IPアドレスはこの集計データベースに保存しません。区分の推定はブラウザ側の参照情報などに依存し、実際の投稿数・クリック数・ページビュー・実人数ではありません。同じブラウザが複数の記事や区分に重複して数えられることがあり、この計測機能を導入する前の過去の流入は復元できません。記事ごとの匿名ブラウザ単位の参考値は各記事ページや、条件を満たした場合のトップページの読者の注目欄に表示することがあります。運営者向けの分析画面は公開していません。</p>
<h2>記事から次の記事への移動</h2>
<p>サイト内の記事から別の記事へ進んだ場合、元の記事を表す短い識別子だけを、通常の閲覧計測と一緒に送信することがあります。サイト内の参照元であることを確認し、参照元URL全体・検索語・クエリ文字列は送信しません。識別子をハッシュ化したブラウザ単位で、元の記事から別の記事へ進んだ経験のあるブラウザを重複排除して集計します。移動先の組み合わせ、クリック履歴、個人の閲覧経路は保存しません。ブラウザが参照元を送らない場合は計測できず、実人数やクリック総数、回遊率とは異なります。</p>
<h2>架空ニュースの二択投票について</h2>
<p>記事にある「ありそう／それはない！」は、匿名ブラウザ単位の任意投票です。投票する際はブラウザのランダム識別子をSHA-256でハッシュ化して保存し、各記事で1ブラウザ1票として重複を防ぎます。公開するのは選択肢ごとの合計と割合だけで、個人の回答は公開しません。IPアドレス・メールアドレスを投票データベースに保存しません。別のブラウザやデータ削除後は重複投票が可能なため、実人数や世論調査ではなく、未来の出来事が的中する確率を示すものでもありません。投票後の変更・取り消しはできません。</p>
<h2>読者コメントについて</h2>
<p>記事へのコメントはニックネーム（省略可能）と本文、投稿日時を公開します。本文は180文字以内で、URLや個人情報の記載は禁止しています。匿名のブラウザ識別子をハッシュ化して保存し、連続投稿防止・自分の投稿の削除・通報の重複防止に使用します。IPアドレスやメールアドレスをコメントデータベースに保存しません。</p>
<p>3件の異なるブラウザから通報があると、一時的に非表示になります。運営者は不適切な投稿を非表示・削除することがあります。自分の投稿は同じブラウザから削除できます。履歴を削除すると操作できなくなることがあります。</p>
<h2>以前の推理メモについて</h2>
<p>以前の推理メモ欄に明示的に保存した文章は、そのブラウザのlocalStorageに残っている場合があります。現在は記事本文の読みやすさを優先してメモ欄の提供を終了し、古い文章をサイト側から自動で消去・送信しません。削除するにはブラウザのveripsa.comのサイトデータを消去してください。ただし読了記録や「あとで読む」の保存も消えます。</p>
<h2>連載の読了記録</h2>
<p>連載の各話で「この回を読了にする」を押した場合に限り、作品・エピソードを表す短い識別子をこのブラウザのlocalStorageへ記録します。読了状態はいつでも取り消せます。連載ページではその記録を使って次の未読回へのリンクを表示します。閲覧だけで読了を記録せず、サーバーへ送信しません。別の端末やブラウザには同期せず、サイトデータを消すと記録も消えます。</p>
<h2>「あとで読む」の保存</h2>
<p>任意で「あとで読む」を押した記事の識別子・タイトル・ジャンル・保存日時を、その端末のブラウザ内（localStorage）だけに最大100件保存します。保存情報は運営者のサーバーへ送信せず、他の端末とは同期しません。保存記事の一覧から個別に解除・すべて解除できます。ブラウザのサイトデータを消去すると保存情報も消えます。</p>
<h2>外部サービス</h2>
<p>サイトの公開・集計にはCloudflareを使用します。記事の一部にはUnsplashで公開された商用利用可能な写真を使用しており、表示時にUnsplashへ通信する場合があります。Xへの共有を選択した場合、Xのページへ移動します。</p>
<h2>広告について</h2>
<p>現在は広告の審査準備中で、広告の自動表示はしていません。将来広告を掲載する際は、使用する事業者とプライバシー情報を更新します。</p>
<h2>お問い合わせ</h2>
<p>運営上の問題や権利に関するご連絡は、<a href="https://github.com/RollNuts/mihassei-news/issues" rel="noopener noreferrer external">公開GitHubのIssues</a>をご利用ください。公開される場所のため個人情報は投稿しないでください。</p>
</div></div>'''
    return layout("プライバシーについて","未発生新聞の閲覧計測と外部サービスの取り扱い。","/privacy/",content)


def saved_page():
    """Browser-only reading list; indexable article content is elsewhere."""
    content = '''<div class="container saved-page">
<nav class="breadcrumbs" aria-label="パンくずリスト"><a href="/">トップ</a>
<span aria-hidden="true"> / </span><span aria-current="page">あとで読む</span></nav>
<header class="page-intro"><span class="eyebrow">YOUR READING LIST / 保存記事</span>
<h1>あとで読む</h1>
<p>気になる未発生ニュースを、このブラウザだけに保存できます。</p></header>
<section class="saved-intro" aria-label="保存について">
<div><strong>アカウント・メール登録不要</strong>
<p>記事の保存先は、この端末のブラウザ内だけ。運営者のサーバーには送信しません。
ブラウザのデータを消去すると、保存記事も消えます。</p></div>
<a href="/archive/">新しい記事を探す ↗</a></section>
<p class="saved-status" data-bookmark-status role="status" aria-live="polite">
JavaScriptが有効なら、保存した記事をここから開けます。</p>
<p class="saved-empty" data-saved-empty hidden>まだ保存された記事はありません。
記事ページの「☆ あとで読む」を押すと、ここに並びます。</p>
<ol class="saved-list" data-saved-list aria-label="保存した記事一覧"></ol>
<div class="saved-controls"><button type="button" data-clear-bookmarks hidden>
保存をすべて解除</button><a href="/archive/">記事一覧を見る ↗</a></div>
<noscript><p>保存記事の一覧にはJavaScriptが必要です。記事自体は通常どおり読めます。</p></noscript>
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
        generated = usable_generated_scene(a, root=BASE)
        photo_ok = not generated and usable_cached_photo(a, root=BASE)
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
                ("description",a["summary"]+" ※創作記事です。")]
        posted=publication_date(a)
        if "T" in posted:
            fields.append(("pubDate",format_datetime(datetime.fromisoformat(posted))))
        for tag, value in fields:
            ET.SubElement(entry,tag).text=value
        ET.SubElement(entry,"category").text=a["category"]
        image=SITE+social_image(a)
        generated=usable_generated_scene(a,root=BASE)
        approved=not generated and usable_cached_photo(a,root=BASE)
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
    routes = [("/", latest), ("/archive/", latest), ("/issues/", latest), ("/about/", None), ("/editorial/", None), ("/advertise/", None), ("/privacy/", None)]
    routes.extend(
        (issue_url(day), max(modification_date(a) for a in items))
        for day, items in issues.items()
    )
    routes.extend((category_url(category),
                   max(modification_date(a) for a in group))
                  for category, group in curated_categories(articles).items())
    routes.extend((article_url(a), modification_date(a)) for a in articles)
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
    write("robots.txt","User-agent: *\nAllow: /\nSitemap: "+SITE+"/sitemap.xml\n")
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
        if usable_generated_scene(article, root=BASE) or usable_cached_photo(article, root=BASE):
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
    write(SITE_CSS_PATH.lstrip("/"), bundled_stylesheets())
    shutil.copy2(BASE / "ads.txt", OUT / "ads.txt")
    photos = BASE / "assets" / "photos"
    if photos.exists():
        shutil.copytree(photos, OUT / "photos", dirs_exist_ok=True)
    # Publish ONLY verified generated JPEGs; the local provenance manifest
    # itself never needs to be public or requested by a browser.
    for item in articles:
        if usable_generated_scene(item, root=BASE):
            destination = OUT / generated_url(item).lstrip("/")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(BASE / "assets" / "generated" / (item["slug"] + ".jpg"), destination)
    write("build-revision.txt", build_revision() + "\n")
    write("index.html",home(articles,serials))
    write("about/index.html",about())
    write("editorial/index.html",editorial_policy())
    write("advertise/index.html",advertise())
    write("privacy/index.html",privacy())
    write("archive/index.html",archive(articles))
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
