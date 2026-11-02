#!/usr/bin/env python3
"""Build original, clearly labelled fictional-news pages. No third-party packages."""
from __future__ import annotations

import html
import json
import re
import shutil
import struct
import zlib
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import quote, urlencode
from xml.etree import ElementTree as ET

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "dist"
SITE = "https://veripsa.com"
CATEGORIES = ("すべて", "社会", "経済", "技術", "政治", "暮らし", "スポーツ", "エンタメ")
ARTS = ("notice", "cards", "tower", "robot", "meeting", "generic")
CATEGORY_PATHS = {
    "社会":"society", "経済":"economy", "技術":"technology",
    "政治":"politics", "暮らし":"lifestyle",
    "スポーツ":"sports", "エンタメ":"entertainment"
}


def curated_categories(articles):
    """Create only substantial category archives, not thin or empty SEO pages."""
    return {name: [a for a in articles if a["category"]==name]
            for name in CATEGORY_PATHS
            if sum(a["category"]==name for a in articles)>=3}


def category_url(name):
    return "/category/"+CATEGORY_PATHS[name]+"/"


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
    d = datetime.fromisoformat(a["fiction_at"])
    label = escape(a.get("flash", "速報"))
    return (
        '<div class="story-meta">'
        + '<span class="ticker">' + label + "</span>"
        + '<span class="category">' + escape(a["category"]) + "</span>"
        + f'<time datetime="{escape(a["fiction_at"])}">{d.month}月{d.day}日 {d.hour:02}:{d.minute:02}</time>'
        + "</div>"
    )


def share_url(a):
    url = SITE + article_url(a) + "?utm_source=x&utm_medium=social&utm_campaign=reader_share"
    # Small standalone hook for the reader's X draft. Does not post automatically.
    teaser = a["summary"][:75]
    text = "【未発生新聞】\n" + a["title"] + "\n\n" + teaser + "\n\n#未発生新聞 （創作）"
    return "https://x.com/intent/tweet?" + urlencode({"text": text, "url": url})


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
    art = a["art"]
    bg, tint, bright = "#192b31", "#31565d", "#f56a53"
    if art == "notice":
        scene = (
            '<rect x="300" y="106" width="600" height="438" rx="24" fill="#e8e8df"/>'
            '<rect x="331" y="143" width="538" height="365" rx="13" fill="#dce4e0"/>'
            '<circle cx="600" cy="279" r="75" fill="#b43532"/>'
            '<path d="M600 230v64m0 35v7" stroke="#fff1e9" stroke-width="17" stroke-linecap="round"/>'
            '<rect x="411" y="402" width="378" height="13" rx="6" fill="#66808a"/>'
            '<rect x="455" y="436" width="286" height="10" rx="5" fill="#b1c2be"/>'
        )
    elif art == "cards":
        scene = "".join(
            f'<rect x="{290+i*240}" y="{170+(i%2)*43}" width="190" height="305" rx="16" fill="{["#e1e7df","#c5d5da","#efd9ce"][i]}"/>'
            f'<circle cx="{385+i*240}" cy="{255+(i%2)*43}" r="40" fill="{["#28444b","#b74e3d","#405e55"][i]}"/>'
            f'<rect x="{335+i*240}" y="{341+(i%2)*43}" width="100" height="14" rx="7" fill="#77928f"/>'
            f'<rect x="{315+i*240}" y="{371+(i%2)*43}" width="140" height="12" rx="6" fill="#92aaa7"/>'
            for i in range(3)
        )
        scene += '<path d="M500 318h195" stroke="#f9c4a0" stroke-width="9" stroke-dasharray="14 15"/>'
    elif art == "tower":
        scene = (
            '<rect x="330" y="140" width="235" height="423" fill="#9cb5b5"/>'
            '<rect x="565" y="86" width="258" height="477" fill="#dce3dc"/>'
            '<rect x="823" y="225" width="115" height="338" fill="#5d8588"/>'
            + "".join(f'<path d="M{365+j*41} 150v390" stroke="#436873" stroke-width="8"/>' for j in range(5))
            + "".join(f'<path d="M585 {142+i*56}h219" stroke="#8bada8" stroke-width="10"/>' for i in range(8))
            + '<path d="M200 560h800" stroke="#e5d4bb" stroke-width="12"/>'
            '<circle cx="735" cy="135" r="39" fill="#f56a53" opacity=".9"/>'
        )
    elif art == "robot":
        scene = (
            '<path d="M0 492h1200v180H0z" fill="#2d4b4d"/>'
            '<path d="M180 515h850" stroke="#d3ded1" stroke-width="25" stroke-dasharray="48 15"/>'
            '<rect x="180" y="182" width="175" height="295" rx="24" fill="#d7e3df"/>'
            '<circle cx="270" cy="244" r="42" fill="#f56a53"/>'
            '<path d="M335 290L520 255L600 370" fill="none" stroke="#d7e3df" stroke-width="54" stroke-linejoin="round"/>'
            '<circle cx="520" cy="255" r="37" fill="#f56a53"/>'
            '<rect x="570" y="354" width="90" height="95" rx="14" fill="#d2dbc9"/>'
            '<path d="M810 165l-88 158 100 95" stroke="#8ab0ac" fill="none" stroke-width="65" stroke-linejoin="round"/>'
            '<circle cx="722" cy="323" r="40" fill="#f56a53"/>'
            '<rect x="808" y="418" width="140" height="82" rx="14" fill="#e7c6b0"/>'
        )
    elif art == "meeting":
        scene = (
            '<rect x="240" y="128" width="720" height="295" rx="18" fill="#d8e6e2"/>'
            '<rect x="283" y="165" width="634" height="217" rx="10" fill="#244750"/>'
            '<circle cx="600" cy="260" r="70" stroke="#ff7762" stroke-width="14" fill="none"/>'
            '<path d="M568 260l22 22 48-54" stroke="#f6dec8" stroke-width="13" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
            '<path d="M160 558h880l-102-118H262z" fill="#9bb4ac"/>'
            '<circle cx="346" cy="470" r="30" fill="#d69b7d"/>'
            '<circle cx="606" cy="470" r="30" fill="#d69b7d"/>'
            '<circle cx="855" cy="470" r="30" fill="#d69b7d"/>'
            '<path d="M295 560v-56q50-56 100 0v56m160 0v-56q50-56 100 0v56m150 0v-56q50-56 100 0v56" fill="#243a3c"/>'
        )
    else:
        scene = (
            '<circle cx="600" cy="330" r="220" stroke="#f56a53" stroke-width="52" fill="none"/>'
            '<path d="M400 380h400M420 270h360" stroke="#d4e0d9" stroke-width="22"/>'
        )
    guide = "".join(f'<path d="M0 {y}h1200" stroke="#cce7df" stroke-opacity=".06" stroke-width="2"/>' for y in range(0,676,42))
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 675" role="img" aria-label="架空ニュース用に制作したオリジナルイラスト">'
        '<defs><linearGradient id="bg" x1="0" x2="1" y1="0" y2="1">'
        f'<stop offset="0" stop-color="{bg}"/><stop offset="1" stop-color="{tint}"/>'
        '</linearGradient></defs><rect width="1200" height="675" fill="url(#bg)"/>'
        + guide + '<circle cx="1016" cy="105" r="180" fill="#ffb87c" opacity=".10"/>'
        + scene +
        '<rect x="0" y="632" width="1200" height="43" fill="#121f23" opacity=".78"/>'
        '<text x="35" y="659" font-family="sans-serif" font-size="18" letter-spacing="4" fill="#faf4ed">MIHASSEI SHIMBUN / FICTIONAL ILLUSTRATION</text>'
        '<circle cx="1120" cy="650" r="11" fill="#f56a53"/></svg>'
    )


def illustration_path(a):
    return "/images/" + a["slug"] + ".svg"


def editorial_image(a):
    """Editorial photos are licensed stock photos, never photographs of fictional incidents."""
    local = BASE / "assets" / "photos" / (a["slug"] + ".jpg")
    if local.exists():
        return "/photos/" + a["slug"] + ".jpg"
    photo = a.get("photo")
    return photo["url"] if photo else illustration_path(a)


def image_tag(a, cls, extra=""):
    url = editorial_image(a)
    photo = a.get("photo")
    alt = photo["alt"] if photo else "記事をイメージしたオリジナルイラスト"
    fallback = f' data-fallback="{escape(illustration_path(a))}"' if photo else ""
    return (f'<img class="{cls}" src="{escape(url)}" alt="{escape(alt)}"'
            f' {extra}{fallback}>')


def photo_credit(a):
    p = a.get("photo")
    if not p:
        return "記事のイメージ画像（オリジナル制作）"
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
        f'<article class="story-card"{attrs}><a class="story-image-link" href="{article_url(a)}">'
        + image_tag(a,"story-thumb",extra='loading="lazy" decoding="async"') + '</a>'
        + category_meta(a)
        + f'<h3><a href="{article_url(a)}">{escape(a["title"])}</a></h3>'
        + f'<p>{escape(a["summary"])}</p>'
        + '<div class="story-footer">'
        + f'<span class="reporter">未発生新聞 {escape(a["category"])}部</span>'
        + actions(a, compact=True)
        + '</div></article>'
    )

def social_image(article):
    """Crawlers receive a first-party PNG/JPEG, never an SVG or an external image."""
    if article is not None:
        photo = BASE / "assets" / "photos" / (article["slug"] + ".jpg")
        if photo.is_file():
            return "/photos/" + article["slug"] + ".jpg"
    # Version is part of the URL to refresh external card caches after brand updates.
    return "/og/brand.png?v=20261102-2"


def layout(title, desc, path, main, active="", kind="website", article=None):
    complete = "未発生新聞｜5年後の架空ニュース" if path == "/" else title + " | 未発生新聞（創作ニュース）"
    nav = [
        ("/", "トップ", "home"),
        ("/archive/", "新着記事", "archive"),
        ("/archive/?category=" + quote("社会"), "社会", ""),
        ("/archive/?category=" + quote("経済"), "経済", ""),
        ("/archive/?category=" + quote("技術"), "テクノロジー", ""),
        ("/archive/?category=" + quote("暮らし"), "暮らし", ""),
        ("/archive/?category=" + quote("スポーツ"), "スポーツ", ""),
        ("/archive/?category=" + quote("エンタメ"), "エンタメ", ""),
        ("/about/", "本紙について", "about"),
    ]
    navhtml = "".join(f'<a href="{escape(u)}"{" aria-current=page" if key == active and key else ""}>{escape(text)}</a>'
                      for u,text,key in nav)
    # Use a share-intent link without assuming ownership of an X account.
    site_share = escape("https://x.com/intent/tweet?" + urlencode({
        "text": "5年後の架空ニュースを真顔で読む、未発生新聞。 #未発生新聞 （創作）",
        "url": SITE + "/",
    }))
    og_image = SITE + social_image(article)
    photo = article.get("photo") if article else None
    local_photo = og_image.split("?",1)[0].endswith(".jpg")
    og_alt = photo["alt"]+"（イメージ写真）" if local_photo and photo else "未発生新聞（創作ニュース）"
    og_mime = "image/jpeg" if local_photo else "image/png"
    og_size = "" if local_photo else '<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">'
    metadata = {"@context":"https://schema.org",
                "@type":"WebSite" if path=="/" else "WebPage",
                "name":title,"url":SITE+path,"description":desc,
                "inLanguage":"ja","genre":"風刺・フィクション"}
    if kind == "story" and article is not None:
        metadata.update({
            "@type":"BlogPosting",
            "headline":title,
            "datePublished":publication_date(article),
            "author":{"@type":"Organization","name":"未発生新聞","url":SITE+"/about/"},
            "publisher":{"@type":"Organization","name":"未発生新聞","url":SITE},
            "isAccessibleForFree":True,
            "image":[og_image],
            "articleSection":article["category"]
        })
        if article.get("modified_at"):
            metadata["dateModified"] = article["modified_at"]
    schemas = [metadata]
    if kind == "story" and article is not None:
        schemas.append({
            "@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "トップ", "item": SITE + "/"},
                {"@type": "ListItem", "position": 2, "name": "記事一覧", "item": SITE + "/archive/"},
                {"@type": "ListItem", "position": 3, "name": article["title"], "item": SITE + path},
            ]
        })
    ld = json.dumps(schemas if len(schemas) > 1 else metadata, ensure_ascii=False).replace("</", "<\\/")
    return f'''<!doctype html><html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#f7f4ec">
<meta name="google-adsense-account" content="ca-pub-6754575901141756">
<meta name="description" content="{escape(desc)}">
<meta property="og:title" content="{escape(complete)}">
<meta property="og:description" content="{escape(desc)}">
<meta property="og:site_name" content="未発生新聞">
<meta property="og:locale" content="ja_JP">
<meta property="og:type" content="{"article" if kind=="story" else "website"}">
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
<link rel="stylesheet" href="/assets/style.css">
<link rel="stylesheet" href="/assets/article-extra.css">
<link rel="stylesheet" href="/assets/newsroom.css">
<link rel="stylesheet" href="/assets/comments.css">
<script defer src="/assets/site.js"></script>
<script defer src="/assets/engagement.js"></script>
<script defer src="/assets/comments.js"></script>
<script defer src="/assets/comment-activity.js"></script>
<script type="application/ld+json">{ld}</script>
<title>{escape(complete)}</title>
</head><body>
<a class="skip" href="#main">本文へ</a>
<div class="strip"><div class="container strip-content">
<a href="/" class="strip-brand">未発生新聞 <span class="fiction-mark">創作報道</span></a>
<div class="clock-line"><span class="live-pulse" aria-hidden="true"></span><span>5年先の現在時刻</span>
<time data-future-clock aria-label="5年先の架空の現在時刻">--:--:--</time></div>
</div></div>
<header class="container"><div class="masthead">
<div class="mast-kicker">THE UNHAPPENED TIMES</div>
<a class="brand" href="/" aria-label="未発生新聞トップへ">未発生<span class="brand-last">新聞</span></a>
<span class="brand-en">MIHASSEI SHIMBUN</span>
<p class="mast-sub">まだ起きていないニュースを、どこよりも真面目に。</p>
</div></header>
<div class="container observer-shell">
<div class="observer-widget" data-observer-widget hidden>
<span class="observer-eye" aria-hidden="true">◉</span><span>未来を見にきた人</span>
<strong data-observer-count>—</strong><span class="observer-suffix">人</span>
<small>※ブラウザ単位の参考値</small>
</div></div><nav class="nav-bar" aria-label="メインナビゲーション"><div class="container nav-inner">{navhtml}</div></nav>
<main id="main">{main}</main>
<footer class="footer"><div class="container"><div class="footer-grid">
<div><a class="footer-brand" href="/">未発生新聞</a>
<p class="footer-copy">5年後の世界からお届けする、少しおかしな新聞。</p></div>
<div class="footer-nav"><a href="/archive/">新着記事</a><a href="/about/">本紙について</a>
<a href="/editorial/">編集方針</a><a href="/feed.xml">RSS</a><a href="{site_share}" target="_blank" rel="noopener noreferrer external">Xで紹介する</a><a href="/privacy/">プライバシー</a></div></div>
<div class="footer-bottom"><span>© 未発生新聞 / veripsa.com</span>
<span>※本紙の記事・登場する団体・出来事は創作です。</span></div></div></footer>
</body></html>'''

def home(articles):
    a = articles[0]
    side = "".join('<article class="brief-card">' + category_meta(x) +
                   f'<h3><a href="{article_url(x)}">{escape(x["title"])}</a></h3>'
                   f'<p>{escape(x["summary"])}</p>{actions(x,compact=True)}</article>'
                   for x in articles[1:4])
    recent = "".join(card(x) for x in articles[1:10])
    featured_categories = curated_categories(articles)
    genre_links = []
    for cat in CATEGORIES[1:]:
        count = sum(x["category"] == cat for x in articles)
        if count == 0:
            continue
        href = category_url(cat) if cat in featured_categories else "/archive/?category=" + quote(cat)
        genre_links.append(f'<a href="{escape(href)}">{escape(cat)} <span>{count}本</span></a>')
    genre_links = "".join(genre_links)
    line = "".join(f'<a href="{article_url(x)}"><span>{escape(x.get("flash","新着"))}</span> '
                   f'{escape(x["title"])}</a>' for x in articles[:5])
    # A busy newsroom should not ship the entire archive in the homepage HTML.
    # Keep the interactive edition fresh without letting page weight grow forever.
    issue_options = escape(json.dumps(
        [{"url": article_url(x), "headline": x["title"], "category": x["category"]}
         for x in articles[:40]], ensure_ascii=False, separators=(",", ":")))
    discussion_options = escape(json.dumps(
        [{"slug": x["slug"], "title": x["title"], "category": x["category"]}
         for x in articles[:20]], ensure_ascii=False, separators=(",", ":")))
    content = f'''<div class="container index-main">
<div class="news-briefing"><div><span class="small-label red">TODAY'S HEADLINES</span>
<strong>本日の未発生ニュース</strong></div>
<div class="edition-clock"><span>現実から5年先</span><time data-future-clock>--:--:--</time></div></div>
<div class="news-wire" aria-label="新着ニュース"><span class="wire-label">LATEST</span>
<div class="wire-viewport"><div class="wire-track">{line}<span class="wire-copy" aria-hidden="true">{line}</span></div></div></div>
<section class="hero-grid">
<article class="lead">
<a class="lead-media" href="{article_url(a)}">{image_tag(a, "hero-image", extra='fetchpriority="high" decoding="async"')}
<div class="overlay"><span class="issue-code">TOP STORY</span><span class="hero-live">● LIVE <span data-future-hms>--:--:--</span></span></div></a>
<div class="lead-info">{category_meta(a)}</div>
<h1><a href="{article_url(a)}">{escape(a["title"])}</a></h1>
<p class="dek">{escape(a["summary"])}</p>
<div class="hero-actions"><a class="read-more" href="{article_url(a)}">記事を読む</a>{actions(a,compact=True)}</div>
</article>
<aside class="side-brief"><div class="side-title"><h2>新着ニュース</h2><span>THE LATEST</span></div>{side}</aside>
</section>

<section class="special-edition" aria-label="ランダムに号外記事を読む" data-issue-picker data-issue-options="{issue_options}">
  <div class="special-edition-heading">
    <span class="special-edition-kicker">EXTRA EDITION / 号外</span>
    <h2>何が起きるか、まだ分からない。</h2>
    <p>気になる号外を一枚めくってみよう。</p>
  </div>
  <div class="special-edition-machine">
    <button type="button" class="issue-picker-button" data-pick-issue>今日の号外をめくる <span aria-hidden="true">↗</span></button>
    <div class="issue-picker-result" data-issue-result aria-live="polite">
      <span class="small-label">まだ開封されていません</span>
      <p data-issue-headline>ニュースの続きは、めくってからのお楽しみ。</p>
      <a data-issue-link href="/archive/" hidden>記事を読む ↗</a>
    </div>
  </div>
</section>
<section class="reader-discussions" data-comment-discussions data-comment-discussion-options="{discussion_options}" aria-labelledby="reader-discussions-title">
<div class="reader-discussions-copy"><span class="small-label red">READER PARTICIPATION / みんなの未来予想</span>
<h2 id="reader-discussions-title">このニュース、あなたならどうツッコむ？</h2>
<p data-discussion-intro>記事の最後から、5年後の世界にひとこと。ニックネームで参加できます。</p>
<a class="reader-discussions-cta" href="{article_url(a)}#reader-comments">新着記事にコメントする ↗</a></div>
<nav class="reader-discussions-list" data-discussion-list aria-label="コメントの付いた記事" hidden></nav>
</section>
<section class="genre-explore" aria-label="ジャンル別に記事を探す">
<div><span class="small-label red">DISCOVER</span><h2>好きなジャンルから、5年後へ。</h2></div>
<nav class="genre-links" aria-label="ジャンルから探す">{genre_links}</nav>
</section>
<section class="section-block"><div class="section-head"><h2>ほかのニュース</h2>
<a href="/archive/">記事一覧を見る ↗</a></div>
<div class="story-grid">{recent}</div></section>
<aside class="banner"><div><div class="small-label red">EDITORIAL</div>
<h2>今日の常識は、5年後の非常識。</h2>
<p>ニュースの続きは、たぶん誰も予想していない。</p></div>
<a href="/about/">未発生新聞について ↗</a></aside>
</div>'''
    return layout("未発生新聞", "まだ起きていないことを、真面目に報じる。5年後のニュースを読む創作メディア。", "/", content,"home")

def archive(articles):
    buttons = "".join(f'<button class="filter-button{" active" if cat=="すべて" else ""}" '
                      f'data-category-filter="{cat}" aria-pressed="{"true" if cat=="すべて" else "false"}" type="button">{cat}</button>'
                      for cat in CATEGORIES)
    decades = sorted({a["fiction_at"][:3] + "0" for a in articles})
    options = '<option value="すべて">すべての年代</option>' + "".join(
        f'<option value="{x}">{x}年代</option>' for x in decades)
    content = f'''<div class="container archive-page">
<header class="page-intro"><span class="eyebrow">FICTION ARCHIVE</span><h1>未発生の記事一覧</h1>
<p>全部作り話です。着想元の現実の記事とは分けてお読みください。</p></header>
<div class="filters" aria-label="カテゴリー">{buttons}</div>
<div class="search-row"><input class="search-input" data-search-input type="search" aria-label="記事検索" placeholder="見出しやキーワードで検索">
<select class="decade-select" data-decade-filter aria-label="年代フィルター">{options}</select>
<output class="result-counter" data-result-count aria-live="polite">{len(articles)}件の記事</output></div>
<div id="archive-results" class="archive-list">{''.join(card(a,True) for a in articles)}</div>
<div class="archive-load-more" data-archive-more hidden>
  <button type="button" data-archive-more-button aria-controls="archive-results">さらに記事を見る</button>
</div>
{''.join('<p><a class="read-more" href="'+category_url(k)+'">'+escape(k)+'の関連記事を読む</a></p>' for k in curated_categories(articles))}
<p class="no-results" data-no-results hidden>一致する記事がありません。</p></div>'''
    return layout("記事一覧","未発生新聞の記事一覧。すべてフィクションです。","/archive/",content,"archive")


def category_page(category, items):
    title = category + "の未発生ニュース"
    content = (
        '<div class="container archive-page">'
        '<header class="page-intro"><span class="eyebrow">CATEGORY / FICTIONAL NEWS</span>'
        f'<h1>{escape(title)}</h1>'
        f'<p>{escape(category)}をめぐる、まだ起きていない出来事。</p></header>'
        '<div class="archive-list">' + "".join(card(a) for a in items) + '</div>'
        '<p style="margin-top:26px"><a class="read-more" href="/archive/">すべての記事を見る</a></p>'
        '</div>'
    )
    return layout(title,category + "の風刺・フィクション記事。未発生新聞が届ける5年後のニュース。",
                  category_url(category),content,active="archive")


def story(a, articles):
    paras = "".join(f'<p>{escape(p)}</p>' for p in a["paragraphs"])
    src = a["source"]
    # Show a small edited set, never pretend these are actually the most-read stories.
    related_list = [x for x in articles if x["slug"] != a["slug"]]
    # Recommend actual published stories, favoring the same category; a newly
    # published story with an older inspiration source must not be buried.
    related_list.sort(key=publication_date, reverse=True)
    related_list.sort(key=lambda x: x["category"] != a["category"])
    related = "".join(card(x) for x in related_list[:3])
    next_story = related_list[0] if related_list else None
    next_url = article_url(next_story) if next_story else "/archive/"
    next_title = next_story["title"] if next_story else "記事一覧から次のニュースを探す"
    next_category = next_story["category"] if next_story else "新着記事"
    text_len = sum(len(p) for p in a["paragraphs"])
    read_minutes = max(1, (text_len + 449) // 450)
    content = f'''<div class="container article-page">
<div class="article-shell"><article>
<header class="article-header">
<nav class="breadcrumbs" aria-label="パンくずリスト"><a href="/">トップ</a><span aria-hidden="true"> / </span><a href="/archive/">記事一覧</a><span aria-hidden="true"> / </span><span aria-current="page">この記事</span></nav>
<div class="article-edition"><span>未発生新聞 {escape(a["category"])}部</span><span class="edition-tag">創作ニュース</span></div>
<div class="article-dateline">{category_meta(a)}<span class="article-year">{escape(a["fiction_at"][:4])}年号</span></div>
<h1>{escape(a["title"])}</h1>
<p class="dek">{escape(a["summary"])}</p>
<div class="story-meta"><span>未発生新聞 編集部</span><span>実際の掲載日 {escape(publication_date(a)[:10])}</span><span>読了目安 約{read_minutes}分</span></div>
</header>
<figure class="article-hero-figure">
{image_tag(a, "article-visual", extra='loading="eager" decoding="async"')}
<figcaption>{photo_credit(a)}</figcaption></figure>
<div class="article-body">{paras}</div>
<div class="article-endmark">◆</div>
<div class="article-reader-metric" data-article-readers hidden>この記事を読んだ人 <strong data-article-reader-count>—</strong><small>※端末単位</small></div>
{actions(a)}
<section class="source-context" aria-label="着想した実際の話題">
<h2>この記事の着想元</h2>
<p>{escape(src["topic"])}</p>
<a href="{escape(src["url"])}" target="_blank" rel="noopener noreferrer external">
{escape(src["publisher"])} の元記事を見る ↗</a>
</section>
<nav class="reader-next" aria-label="続けて読む記事">
<span class="reader-next-kicker">NEXT STORY / 続けて読む · {escape(next_category)}</span>
<a href="{escape(next_url)}" class="reader-next-title">{escape(next_title)} <span aria-hidden="true">→</span></a>
</nav>
<div class="article-end"><span class="reporter">未発生新聞</span><a href="/archive/">記事一覧へ ↗</a></div>
<section class="reader-follow" aria-label="更新を読む">
<div><span class="small-label">STAY CURIOUS</span><h2>明日も、まだ起きていないニュースを。</h2>
<p>新作はRSSで確認できます。気になった記事はXでも紹介できます。</p></div>
<div class="reader-follow-links">
<a class="reader-follow-x" href="{escape(share_url(a))}" target="_blank" rel="noopener noreferrer external">この記事をXで紹介 ↗</a>
<a href="/feed.xml">RSSで購読 ↗</a></div></section>
</article>
<section class="reader-comments" id="reader-comments" data-comments data-article="{escape(a["slug"])}" aria-labelledby="comments-heading">
<div class="reader-comments-heading">
<span class="eyebrow">READERS' VOICES / 読者の声</span>
<h2 id="comments-heading">未来の読者からの声 <small data-comments-count></small></h2>
<p>架空ニュースへのツッコミや未来予想をどうぞ。ニックネームで投稿できます。投稿内容は公開されます。</p>
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
<section class="article-related"><div class="section-head"><h2>あわせて読む</h2><a href="/archive/">ほかの記事 ↗</a></div>
<div class="story-grid">{related}</div></section>
</div>'''
    return layout(a["title"], a["summary"] + " ※創作ニュースです。", article_url(a), content,kind="story",article=a)

def about():
    content = '''<div class="container archive-page">
<header class="page-intro"><span class="eyebrow">ABOUT US</span>
<h1>未発生新聞について</h1>
<p>まだ起きていない出来事を、どこよりも真面目に。</p></header>
<div class="about-copy">
<h2>5年後の新聞です。</h2>
<p>今、世の中で話題になっていること。その続きを、ちょうど5年後の同じ日時のニュースにしてしまう新聞です。</p>
<p>「いや、それはないだろ」と笑ってもらえたら成功。少し考えて「でも、ないとも言えないかも」と思ってもらえたら、なお成功です。</p>
<h2>記事はすべて創作です。</h2>
<p>この新聞の記事、企業、人物、事件、発言、数値はフィクションです。実際のニュースや報道ではありません。</p>
<p>元の話題から着想して記事を書きますが、元記事やSNSの文章、画像、スクリーンショットは転載しません。参考にした実際の話題へは記事末尾から移動できます。</p>
<h2>今日も、未発生。</h2>
<p>小さなニュースから大きな事件まで。何も起きていない毎日に、ちょっとした号外を。</p>
<p>「未来を見にきた人」と「いいね」は匿名のブラウザ単位の集計です。実際の人数とは一致しない場合があります。</p>
<p><a href="/editorial/" style="text-decoration:underline">編集方針と記事の作り方を読む ↗</a></p>
<p><a href="/feed.xml" style="text-decoration:underline">RSSで購読する ↗</a></p>
</div></div>'''
    return layout("本紙について","未発生新聞は今日の話題を5年後の創作ニュースにするメディアです。","/about/",content,"about")

def editorial_policy():
    """Public disclosure of the creative process and commercial independence."""
    content = '''<div class="container archive-page">
<header class="page-intro"><span class="eyebrow">EDITORIAL POLICY</span>
<h1>編集方針・記事の作り方</h1><p>現実を入り口に、未来を創作する。その境界を明確にします。</p></header>
<div class="about-copy">
<h2>本紙の記事は、すべて創作です。</h2>
<p>未発生新聞は、現実の話題から着想した「5年後の架空ニュース」を届ける風刺・創作メディアです。記事に登場する企業、人物、発言、制度、事件、数値はフィクションであり、未来の予測や実際の報道ではありません。</p>
<h2>生成AIと自動化の利用</h2>
<p>公開されたニュースを着想元として探し、生成AIを文章づくりの補助に活用しています。更新や公開の工程にも自動化を取り入れています。AIによる文章や着想には誤りや表現の偏りが起こりうるため、元の話題へのリンク、記事の創作表示、形式・重複などの確認を設けています。掲載後も必要に応じて修正します。</p>
<p>実在する人物や企業が架空の不祥事を起こしたかのように見せることは避け、創作部分の固有名詞は原則として架空のものを用います。</p>
<h2>出典と創作の境界</h2>
<p>着想元の現実のニュースは、各記事末尾の「この記事の着想元」から確認できます。着想元の報道が、記事内の創作内容を裏付けているわけではありません。元記事の本文や写真を無断転載せず、写真素材は使用条件とクレジットを確認し、架空の出来事の記録写真と誤認されないように表示します。</p>
<h2>広告・アフィリエイトについて</h2>
<p>現在は広告の自動配信を行っていません。将来、広告やアフィリエイトリンクを掲載する場合は、記事内容との区別と広告・PRの表示を行い、読みにくいほど広告を増やす運用は避けます。収益や検索順位を保証するものではありません。</p>
<h2>運営者・訂正の連絡</h2>
<p>出典の誤り、権利上の問題、記事の訂正依頼は<a href="https://github.com/RollNuts/mihassei-news/issues" target="_blank" rel="noopener noreferrer external">公開GitHub Issues</a>からお知らせください。公開フォームのため個人情報は書かないでください。</p>
<p><a href="/about/" style="text-decoration:underline">未発生新聞について ↗</a>　<a href="/privacy/" style="text-decoration:underline">プライバシーについて ↗</a></p>
</div></div>'''
    return layout("編集方針・記事の作り方","未発生新聞の創作ニュース制作、生成AIの利用、出典、広告と訂正に関する編集方針。","/editorial/",content)


def privacy():
    content='''<div class="container archive-page"><header class="page-intro">
<span class="eyebrow">PRIVACY</span><h1>プライバシーについて</h1>
<p>ニュースは架空。数字は実測。</p></header>
<div class="about-copy">
<h2>アクセスといいね</h2>
<p>未発生新聞では、来訪端末のおおよその数や、記事の閲覧・いいねを把握するため、ブラウザ内にランダムな識別子を保存します。識別子をハッシュ化した値をCloudflareのデータ保存機能に保管し、サイト全体の訪問、記事閲覧、いいね、X共有ボタンのクリックを集計しています。アクセス計測のために氏名やメールアドレスの登録は求めません。</p>
<p>閲覧数は人数ではなくブラウザ単位の参考値です。ブラウザの保存データを削除すると、新しいブラウザとして集計される場合があります。</p>
<h2>読者コメントについて</h2>
<p>記事へのコメントはニックネーム（省略可能）と本文、投稿日時を公開します。本文は180文字以内で、URLや個人情報の記載は禁止しています。匿名のブラウザ識別子をハッシュ化して保存し、連続投稿防止・自分の投稿の削除・通報の重複防止に使用します。IPアドレスやメールアドレスをコメントデータベースに保存しません。</p>
<p>3件の異なるブラウザから通報があると、一時的に非表示になります。運営者は不適切な投稿を非表示・削除することがあります。自分の投稿は同じブラウザから削除できます。履歴を削除すると操作できなくなることがあります。</p>
<h2>外部サービス</h2>
<p>サイトの公開・集計にはCloudflareを使用します。記事の一部にはUnsplashで公開された商用利用可能な写真を使用しており、表示時にUnsplashへ通信する場合があります。Xへの共有を選択した場合、Xのページへ移動します。</p>
<h2>広告について</h2>
<p>現在は広告の審査準備中で、広告の自動表示はしていません。将来広告を掲載する際は、使用する事業者とプライバシー情報を更新します。</p>
<h2>お問い合わせ</h2>
<p>運営上の問題や権利に関するご連絡は、<a href="https://github.com/RollNuts/mihassei-news/issues" rel="noopener noreferrer external">公開GitHubのIssues</a>をご利用ください。公開される場所のため個人情報は投稿しないでください。</p>
</div></div>'''
    return layout("プライバシーについて","未発生新聞の閲覧計測と外部サービスの取り扱い。","/privacy/",content)


def other_files(articles):
    latest = max((modification_date(a) for a in articles), default=None)
    routes = [("/", latest), ("/archive/", latest), ("/about/", None), ("/editorial/", None), ("/privacy/", None)]
    routes.extend((category_url(category),
                   max(modification_date(a) for a in group))
                  for category, group in curated_categories(articles).items())
    routes.extend((article_url(a), modification_date(a)) for a in articles)
    xmlns = "http://www.sitemaps.org/schemas/sitemap/0.9"
    root = ET.Element("urlset", xmlns=xmlns)
    for url, modified in routes:
        node = ET.SubElement(root, "url")
        ET.SubElement(node, "loc").text = SITE + url
        if modified:
            ET.SubElement(node, "lastmod").text = modified
    write("sitemap.xml", ET.tostring(root, encoding="unicode"))
    rss=ET.Element("rss", version="2.0")
    channel=ET.SubElement(rss,"channel")
    for name, value in (("title","未発生新聞 | 創作ニュース"),("description","5年後の世界を真顔で報じる創作ニュース。"),("link",SITE),("language","ja")):
        ET.SubElement(channel,name).text=value
    for a in articles[:30]:
        n=ET.SubElement(channel,"item")
        fields = [("title",a["title"]+" | 未発生新聞（創作）"),
                  ("link",SITE+article_url(a)),("guid",SITE+article_url(a)),
                  ("description",a["summary"]+" ※創作記事です。")]
        posted = publication_date(a)
        # Older backfilled stories might have only a reliable date, not a timestamp.
        if "T" in posted:
            fields.append(("pubDate",format_datetime(datetime.fromisoformat(posted))))
        for tag, value in fields:
            ET.SubElement(n,tag).text=value
    write("feed.xml",ET.tostring(rss,encoding="unicode"))
    write("robots.txt","User-agent: *\nAllow: /\nSitemap: "+SITE+"/sitemap.xml\n")
    write("404.html",layout("記事がありません","お探しの架空ニュースはありません。","/404.html",
                         '<div class="container not-found"><h1>404</h1><p>まだ起きていないどころか、記事が存在しません。</p><a href="/">トップに戻る ↗</a></div>'))
    write("_headers","/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n  X-Frame-Options: DENY\n")



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


def build():
    articles = load()
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT/"assets").mkdir(parents=True)
    (OUT/"images").mkdir(parents=True)
    for f in (BASE/"assets").iterdir():
        if f.is_file():
            shutil.copy2(f, OUT/"assets"/f.name)
    shutil.copy2(BASE / "ads.txt", OUT / "ads.txt")
    photos = BASE / "assets" / "photos"
    if photos.exists():
        shutil.copytree(photos, OUT / "photos", dirs_exist_ok=True)
    write("index.html",home(articles))
    write("about/index.html",about())
    write("editorial/index.html",editorial_policy())
    write("privacy/index.html",privacy())
    write("archive/index.html",archive(articles))
    for category, items in curated_categories(articles).items():
        write("category/"+CATEGORY_PATHS[category]+"/index.html",
              category_page(category, items))
    for a in articles:
        write("articles/"+a["slug"]+"/index.html",story(a,articles))
        write("images/"+a["slug"]+".svg",cover(a))
    other_files(articles)
    make_brand_image()
    print(f"Built {len(articles)} fictional articles to {OUT}")


if __name__ == "__main__":
    build()
