#!/usr/bin/env python3
"""Build original, clearly labelled fictional-news pages. No third-party packages."""
from __future__ import annotations

import html
import json
import re
import shutil
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "dist"
SITE = "https://veripsa.com"
CATEGORIES = ("すべて", "社会", "経済", "技術", "政治", "暮らし", "スポーツ", "エンタメ")
ARTS = ("notice", "cards", "tower", "robot", "meeting", "generic")


def escape(value):
    return html.escape(str(value), quote=True)


def date_jp(iso):
    d = datetime.fromisoformat(iso)
    return f"{d.year}年{d.month}月{d.day}日 {d.hour:02}:{d.minute:02}"


def five_years_later(iso):
    date = datetime.fromisoformat(iso)
    return date.replace(year=date.year + 5).isoformat()


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
        if len(a["paragraphs"]) < 2 or len(a["paragraphs"]) > 6:
            raise ValueError("Article must contain 2-6 paragraphs")
    return sorted(articles, key=lambda a: a["source"]["published_at"], reverse=True)


def write(path, text):
    target = OUT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def article_url(a):
    return "/articles/" + a["slug"] + "/"


def category_meta(a, compact=False):
    label = escape(a.get("flash", "架空記事"))
    return (
        '<div class="story-meta">'
        + '<span class="ticker">' + label + "</span>"
        + '<span class="category">' + escape(a["category"]) + "</span>"
        + '<span class="fiction-time">' + date_jp(a["fiction_at"]) + "</span>"
        + "</div>"
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


def card(a, archive=False):
    attrs = ""
    if archive:
        category = a["category"]
        decade = a["fiction_at"][:3] + "0"
        searchable = " ".join((a["title"], a["summary"], a["fiction_company"], category))
        attrs = (f' data-archive-card data-category="{escape(category)}" '
                 f'data-decade="{decade}" data-search="{escape(searchable)}"')
    return (
        f'<article class="story-card"{attrs}><a href="{article_url(a)}">'
        f'<img class="story-thumb" src="/images/{a["slug"]}.svg" loading="lazy" alt="架空ニュースのオリジナルイラスト"></a>'
        + category_meta(a) +
        f'<h3><a href="{article_url(a)}">{escape(a["title"])}</a></h3>'
        f'<p>{escape(a["summary"])}</p>'
        f'<div class="story-foot">着想元：{escape(a["source"]["publisher"])} / 5年後の仮想報道</div></article>'
    )


def layout(title, desc, path, main, active="", kind="website"):
    complete = "【架空ニュース】" + title + " | 未発生新聞"
    nav = [
        ("/", "トップ", "home"),
        ("/archive/", "記事一覧", "archive"),
        ("/archive/?category=" + quote("社会"), "社会", ""),
        ("/archive/?category=" + quote("経済"), "経済", ""),
        ("/archive/?category=" + quote("技術"), "技術", ""),
        ("/archive/?category=" + quote("スポーツ"), "スポーツ", ""),
        ("/about/", "本紙について", "about"),
    ]
    navhtml = "".join(f'<a href="{escape(u)}"{" aria-current=page" if key == active and key else ""}>{escape(text)}</a>' for u,text,key in nav)
    metadata = {"@context":"https://schema.org","@type":"CreativeWork" if kind=="story" else "WebSite",
                "name":title,"url":SITE+path,"description":desc,"inLanguage":"ja",
                "genre":"架空報道・フィクション"}
    ld = json.dumps(metadata, ensure_ascii=False).replace("</", "<\\/")
    return f'''<!doctype html><html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#f7f4ec">
<meta name="description" content="{escape(desc)}">
<meta property="og:title" content="{escape(complete)}">
<meta property="og:description" content="{escape(desc)}">
<meta property="og:site_name" content="未発生新聞">
<meta property="og:type" content="website">
<meta property="og:url" content="{SITE+path}">
<meta name="twitter:card" content="summary">
<link rel="canonical" href="{SITE+path}">
<link rel="icon" type="image/svg+xml" href="/assets/favicon.svg">
<link rel="alternate" type="application/rss+xml" title="未発生新聞 RSS" href="/feed.xml">
<link rel="stylesheet" href="/assets/style.css"><link rel="stylesheet" href="/assets/article-extra.css">
<script defer src="/assets/site.js"></script>
<script type="application/ld+json">{ld}</script>
<title>{escape(complete)}</title>
</head><body>
<a class="skip" href="#main">本文へ</a>
<div class="strip"><div class="container"><span><strong>FICTION / 架空報道</strong>　この記事・見出し・登場する会社は創作です</span>
<span class="mobile-hide">実際のニュースと混同しないでください。</span></div></div>
<header class="container"><div class="masthead"><div class="mast-kicker">THE UNHAPPENED TIMES</div>
<a class="brand" href="/" aria-label="未発生新聞トップへ">未発生<span class="brand-last">新聞</span></a>
<span class="brand-en">MIHASSEI SHIMBUN</span>
<p class="mast-sub">5年後の続きが、ここにある。</p></div></header>
<nav class="nav-bar" aria-label="メインナビゲーション"><div class="container nav-inner">{navhtml}</div></nav>
<main id="main">{main}</main>
<footer class="footer"><div class="container"><div class="footer-grid">
<div><a class="footer-brand" href="/">未発生新聞</a><p class="footer-copy">今日のニュースやネットの話題を起点に、ちょうど5年後の同じ日時を報道する架空メディア。元ネタと創作は明確に分けて掲載しています。</p></div>
<div class="footer-nav"><a href="/archive/">記事一覧</a><a href="/about/">本紙について</a><a href="/feed.xml">RSS</a></div></div>
<div class="footer-bottom"><span>© 未発生新聞 / veripsa.com</span>
<span>全記事フィクション。人物・団体・事象は創作であり、将来の事実を保証しません。</span></div></div></footer>
</body></html>'''


def home(articles):
    a = articles[0]
    side = "".join('<article class="brief-card">' + category_meta(x) +
                   f'<h3><a href="{article_url(x)}">{escape(x["title"])}</a></h3><p>{escape(x["summary"])}</p></article>'
                   for x in articles[1:3])
    recent = "".join(card(x) for x in articles[1:7])
    content = f'''<div class="container index-main">
<div class="section-head"><h2>今日から5年後の見出し</h2><span class="small-label red">FIVE YEARS LATER / FICTION</span></div>
<section class="hero-grid"><article class="lead">
<a class="lead-media" href="{article_url(a)}"><img src="/images/{a["slug"]}.svg" alt="架空ニュースのオリジナルイラスト">
<div class="overlay"><span class="issue-code">SPECIAL / FICTION</span><span class="large-year">2031</span></div></a>
<div style="margin-top:20px">{category_meta(a)}</div>
<h1><a href="{article_url(a)}">{escape(a["title"])}</a></h1>
<p class="dek">{escape(a["summary"])}</p><a class="read-more" href="{article_url(a)}">架空の記事を読む</a>
</article>
<aside class="side-brief"><div class="side-title"><h2>HEADLINES</h2><span>まだ起きていない話。</span></div>{side}</aside></section>
<section class="section-block"><div class="section-head"><h2>最近の未発生</h2><a href="/archive/">すべて見る ↗</a></div>
<div class="story-grid">{recent}</div></section>
<aside class="banner"><div><div class="small-label red">ABOUT THIS PAPER</div>
<h2>バカみたいだけど、5年後ならありそう。</h2>
<p>報道された現実を起点に、架空の企業と架空の社会で「その先」を描く。記事本文と画像はすべてオリジナルのフィクションです。</p></div><a href="/about/">本紙について ↗</a></aside>
</div>'''
    return layout("未発生新聞", "今日の現実から5年後の同じ時刻へ。記事はすべて架空のニュースです。", "/", content, "home")


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
<output class="result-counter" data-result-count>{len(articles)}件の記事</output></div>
<div class="archive-list">{''.join(card(a,True) for a in articles)}</div>
<p class="no-results" data-no-results hidden>一致する記事がありません。</p></div>'''
    return layout("記事一覧","未発生新聞の記事一覧。すべてフィクションです。","/archive/",content,"archive")


def story(a, articles):
    image_path = "/images/" + a["slug"] + ".svg"
    paras = "".join(f'<p>{escape(p)}</p>' for p in a["paragraphs"])
    src = a["source"]
    related = "".join(card(x) for x in articles if x["slug"] != a["slug"])[:20000]
    content = f'''<div class="container article-page">
<div class="article-shell"><article>
<header class="article-header"><span class="eyebrow">FIVE YEARS LATER / FICTIONAL NEWS</span>
<div style="margin-top:20px">{category_meta(a)}</div>
<h1>{escape(a["title"])}</h1>
<p class="dek">{escape(a["summary"])}</p>
<div class="story-meta"><span>未発生新聞 編集部</span><span>架空の企業：{escape(a["fiction_company"])}</span></div></header>
<p class="primary-disclaimer">【創作記事】以下は実際には起きていない出来事です。登場する企業・談話・数値は架空です。</p>
<figure class="article-hero-figure"><img class="article-visual" src="{image_path}" alt="架空記事のために独自制作したイメージ画像">
<figcaption>未発生新聞によるオリジナルイラスト（実際の報道写真ではありません）</figcaption></figure>
<div class="article-body">{paras}</div>
<section class="banner-source" aria-label="現実の着想元">
<h2>この架空記事のきっかけになった現実の話題</h2>
<p>{escape(src["topic"])}</p>
<p class="seed-label">現実の掲載日時：{date_jp(src["published_at"])} ／ 出典：{escape(src["publisher"])} ／ 未来の日時：{date_jp(a["fiction_at"])}</p>
<a href="{escape(src["url"])}" target="_blank" rel="noopener noreferrer external">元の話題を読む（外部サイト） ↗</a>
<p style="margin-top:10px">リンク先の実際の記事と、本紙の架空の記事に直接の関係はありません。本文・画像の転載ではありません。</p>
</section>
<div class="article-end"><span class="small-label red">— END OF FICTION —</span><a href="/archive/">一覧へ戻る ↗</a></div>
</article></div>
<section class="article-related"><div class="section-head"><h2>ほかの架空ニュース</h2><a href="/archive/">一覧 ↗</a></div>
<div class="story-grid">{related}</div></section>
</div>'''
    return layout(a["title"], a["summary"]+" ※本記事はフィクションです。", article_url(a), content,kind="story")


def about():
    content = '''<div class="container archive-page">
<header class="page-intro"><span class="eyebrow">ABOUT THE UNHAPPENED TIMES</span>
<h1>未発生新聞について</h1>
<p>5年後の同じ日、同じ時刻に、世界は何を報じているのか。</p></header>
<div class="about-copy">
<p>未発生新聞は、今日のニュースやネット上で注目された話題を手掛かりに、「ちょうど5年後の同じ日時なら、こんなことが起きているかも」と想像した物語を、新聞記事の形式で掲載する架空メディアです。</p>
<h2>すべてフィクションです</h2>
<p>記事中の未来の日付・企業・事件・発言・統計は創作です。現実のニュースを装う意図はありません。記事は実際の報道・投資判断・意思決定の根拠にしないでください。</p>
<h2>着想元の著作物を転載しません</h2>
<p>元ニュースやSNS投稿の文章、画像、スクリーンショットを転載せず、話題のテーマをもとに独立した物語を創作します。可能な範囲で記事ごとに着想元へのリンクを示し、事実と虚構を分けます。</p>
<h2>なぜ5年後？</h2>
<p>50年後では何でもあり。5年後なら、少し無理があるのに、まったくの嘘とも思えない。その微妙な距離が本紙の取材対象です。</p>
<h2>画像について</h2>
<p>記事用の図版はオリジナル生成イラストです。実在する人や企業の写真、他媒体の報道写真は使いません。AIを制作補助に用いる場合があります。</p>
<h2>配信</h2><p>記事は<a href="/feed.xml" style="text-decoration:underline">RSS</a>でもご覧いただけます。</p></div></div>'''
    return layout("本紙について","未発生新聞は現実の話題の5年後を真顔で描くフィクションメディアです。","/about/",content,"about")


def other_files(articles):
    routes = ["/", "/about/", "/archive/"] + [article_url(a) for a in articles]
    xmlns = "http://www.sitemaps.org/schemas/sitemap/0.9"
    root = ET.Element("urlset", xmlns=xmlns)
    for url in routes:
        node=ET.SubElement(root,"url")
        ET.SubElement(node,"loc").text=SITE+url
    write("sitemap.xml", ET.tostring(root, encoding="unicode"))
    rss=ET.Element("rss", version="2.0")
    channel=ET.SubElement(rss,"channel")
    for name, value in (("title","未発生新聞【フィクション】"),("description","すべて架空のニュースです。今日の話題から5年後の続報を創作します。"),("link",SITE),("language","ja")):
        ET.SubElement(channel,name).text=value
    for a in articles[:30]:
        n=ET.SubElement(channel,"item")
        for tag,value in (("title","【架空ニュース】"+a["title"]),("link",SITE+article_url(a)),
                          ("guid",SITE+article_url(a)),("description",a["summary"]+" ※本記事はフィクションです。"),
                          ("pubDate",format_datetime(datetime.fromisoformat(a["source"]["published_at"]))))):
            ET.SubElement(n,tag).text=value
    write("feed.xml",ET.tostring(rss,encoding="unicode"))
    write("robots.txt","User-agent: *\nAllow: /\nSitemap: "+SITE+"/sitemap.xml\n")
    write("404.html",layout("記事がありません","お探しの架空ニュースはありません。","/404.html",
                         '<div class="container not-found"><h1>404</h1><p>まだ起きていないどころか、記事が存在しません。</p><a href="/">トップに戻る ↗</a></div>'))
    write("_headers","/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n  X-Frame-Options: DENY\n")


def build():
    articles = load()
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT/"assets").mkdir(parents=True)
    (OUT/"images").mkdir(parents=True)
    for f in (BASE/"assets").iterdir():
        if f.is_file():
            shutil.copy2(f, OUT/"assets"/f.name)
    write("index.html",home(articles))
    write("about/index.html",about())
    write("archive/index.html",archive(articles))
    for a in articles:
        write("articles/"+a["slug"]+"/index.html",story(a,articles))
        write("images/"+a["slug"]+".svg",cover(a))
    other_files(articles)
    print(f"Built {len(articles)} fictional articles to {OUT}")


if __name__ == "__main__":
    build()
