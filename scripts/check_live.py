#!/usr/bin/env python3
"""Read-only smoke test of the actual newspaper deployment and recent articles.

The script never sends POSTs, creates visits or likes, or invokes a paid API.
It runs after GitHub pushes; Cloudflare may take a little time to deploy.
"""
from __future__ import annotations

import html
import json
import re
import os
import sys
import time
import subprocess
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

if __package__:
    from .media_policy import usable_cached_photo
    from .share_desk import select_candidates
else:
    from media_policy import usable_cached_photo
    from share_desk import select_candidates

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
    recent=sorted(articles,key=lambda a:a["source"]["published_at"],reverse=True)[:10]
    home,mime=get("/")
    body=html.unescape(home.decode("utf-8"))
    for token in ("未発生新聞","data-future-clock","data-issue-picker","data-pick-issue","edition-time-key"):
        if token not in body:
            raise RuntimeError("Newest homepage has not reached production: "+token)

    # A saved reading list is opt-in, browser-local and not a thin SEO archive.
    # Only GETs: no visits, bookmark writes, analytics POSTs or paid services.
    saved_html, saved_mime = get("/saved/")
    saved_markup = saved_html.decode("utf-8")
    if ("text/html" not in saved_mime or
        'name="robots" content="noindex,follow"' not in saved_markup or
        'data-saved-list' not in saved_markup or
        'data-clear-bookmarks' not in saved_markup or
        'src="/assets/bookmarks.js"' not in saved_markup):
        raise RuntimeError("Private browser-only reading list is missing from production")
    if 'href="/saved/"' not in body:
        raise RuntimeError("Saved reading list is not discoverable from homepage")
    bookmark_js, bookmark_mime = get("/assets/bookmarks.js")
    if ("javascript" not in bookmark_mime and "text/plain" not in bookmark_mime):
        raise RuntimeError("Bookmark script is missing or served with wrong MIME")
    if (b"mihassei-saved-stories-v1" not in bookmark_js or
        b"localStorage.setItem" not in bookmark_js):
        raise RuntimeError("Bookmark script missing expected device-local functionality")

    # Serial long-form reading, links and share cards must ship alongside the news.
    # Static GETs only; do not generate visits, votes or any paid API traffic.
    serials=json.loads((ROOT/"content/serials.json").read_text(encoding="utf-8"))
    if 'class="serials-feature"' not in body or 'href="/serials/"' not in body:
        raise RuntimeError("New serials not discoverable on published homepage")
    serial_hub, serial_type=get("/serials/")
    serial_index=serial_hub.decode("utf-8")
    if "text/html" not in serial_type or "物語を追う。未来を検証する。" not in serial_index:
        raise RuntimeError("Live serial index was not deployed")
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
        svg,svg_mime=get(prefix+"cover.svg")
        if not svg.startswith(b"<svg") or "svg" not in svg_mime:
            raise RuntimeError("Generated original serial cover unavailable: "+prefix)
        expected_kind="創作ミステリー" if serial["kind"]=="fiction" else "実際の市場に関する考察"
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
            png,png_mime=get("/og/serials/"+serial["slug"]+"-"+ep["slug"]+".png")
            if not png.startswith(b"\x89PNG\r\n\x1a\n") or "image/png" not in png_mime or len(png)<1500:
                raise RuntimeError("Serial X card not served as a proper PNG: "+path)
            if serial["kind"]=="fiction" and "すべて創作" not in page:
                raise RuntimeError("Fiction episode missing disclosure: "+path)
            if serial["kind"]=="analysis" and ("投資助言" not in page or "参照した公開資料" not in page):
                raise RuntimeError("Market analysis missing sources/disclosure: "+path)

    # Verify the manual promotion desk without creating any X posts.
    desk, desk_mime=get("/share-desk/")
    markup=desk.decode("utf-8")
    if ("text/html" not in desk_mime or
        'name="robots" content="noindex,nofollow"' not in markup or
        'data-copy-post' not in markup or
        'Xで下書きを開く' not in markup or
        '画像を保存・開く' not in markup):
        raise RuntimeError("Manual X promotion desk missing or misconfigured")
    for article in select_candidates(articles):
        if (html.escape(article["title"]) not in markup or
            '/articles/'+article["slug"]+'/' not in markup):
            raise RuntimeError("Promotion desk missing published story "+article["slug"])

    # Read-only aggregate measurements: neither the review page nor the API
    # GET should create a visitor or reaction event.
    growth,growth_type=get("/growth-desk/")
    page=growth.decode("utf-8")
    if ("text/html" not in growth_type or
        'name="robots" content="noindex,nofollow"' not in page or
        'data-growth-stories=' not in page or
        'data-growth-articles' not in page or
        "集客の実測" not in page):
        raise RuntimeError("Noindex growth review page is not deployed")
    js,js_type=get("/assets/growth-desk.js")
    if ("javascript" not in js_type and "text/plain" not in js_type):
        raise RuntimeError("Growth review script has invalid Content-Type")
    if b"/api/engagement?articles=" not in js or b"metric.arrivals.x" not in js:
        raise RuntimeError("Growth review is not reading honest browser counters")
    if "https://veripsa.com/growth-desk/" in get("/sitemap.xml")[0].decode("utf-8"):
        raise RuntimeError("Growth desk must not enter search sitemap")

    # This stylesheet is last in the cascade. A successful HTML deploy must
    # not silently leave the refreshed editorial design without its actual CSS.
    if 'href="/assets/editorial-polish.css"' not in body:
        raise RuntimeError("Modern editorial design stylesheet not linked in homepage")
    design, design_mime = get("/assets/editorial-polish.css")
    if "text/css" not in design_mime:
        raise RuntimeError("Modern editorial design stylesheet served with invalid MIME")
    design_text = design.decode("utf-8")
    for selector in (".article-body", ".story-card", ".nav-inner",
                     "@media(max-width:660px)", "prefers-reduced-motion"):
        if selector not in design_text:
            raise RuntimeError("Modern editorial design CSS missing: "+selector)

    # Genuine reader discovery is absent for low sample sizes and only
    # appears when confirmed browser counts arrive from the existing API.
    for token in ('data-popular-section', 'data-popular-options=', 'data-popular-list',
                  'readers-choice-title" hidden'):
        if token not in body:
            raise RuntimeError("Reader-discovery layout not deployed: "+token)
    if '実人数・PV・週間ランキングではありません' not in body:
        raise RuntimeError("Readership ranking must explain its actual counting unit")

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
                  'data-edition-updated>最終発行：'):
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
    if 'class="free-follow"' not in body or 'href="/feed.xml" type="application/rss+xml"' not in body:
        raise RuntimeError("Free repeat-reader RSS discovery is missing from production")
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
    site_js, _ = get("/assets/site.js")
    if b"PAGE_SIZE = 18" not in site_js or b"visibleLimit" not in site_js:
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
    if "text/html" not in index_mime or "未発生新聞の発行号" not in index_markup:
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
    if any(item.findtext("category") not in {
        "社会", "経済", "技術", "政治", "暮らし", "スポーツ", "エンタメ"
    } for item in feed_entries):
        raise RuntimeError("RSS feed category metadata missing")
    namespace={"media":"http://search.yahoo.com/mrss/"}
    for entry in feed_root.findall("./channel/item")[:min(5,len(articles))]:
        thumb=entry.find("media:thumbnail",namespace)
        media=entry.find("media:content",namespace)
        if thumb is None or media is None or thumb.get("url")!=media.get("url"):
            raise RuntimeError("RSS thumbnail metadata is missing or inconsistent")
        if not thumb.get("url","").startswith(SITE+"/"):
            raise RuntimeError("RSS image is not first-party: "+str(thumb.get("url")))
    review,review_type=get("/image-review/")
    review_markup=review.decode("utf-8")
    if ("text/html" not in review_type or
        'name="robots" content="noindex,nofollow"' not in review_markup or
        "公開画像の検品一覧" not in review_markup):
        raise RuntimeError("Non-indexed visual image review page missing")
    for article in articles:
        if article["title"] not in review_markup:
            raise RuntimeError("Image review gallery missing article "+article["slug"])

    checked_photos=0
    for item in recent:
        slug=item["slug"]
        data,mime=get("/articles/"+slug+"/")
        page=html.unescape(data.decode("utf-8"))
        for needle in (item["title"],item["source"]["url"],"創作ニュース",f'data-like-count="{slug}"'):
            if needle not in page:
                raise RuntimeError(slug+": missing published material "+needle[:75])
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
        next_link=re.search(r'<a href="(/articles/([^/]+)/|/archive/)" class="reader-next-title">',page)
        if not next_link or next_link.group(2)==slug:
            raise RuntimeError(slug+": article continuation links to itself or is missing")
        section=re.search(r'<section class="article-related">(.+?)</section>',page,re.S)
        if section:
            cards=re.findall(r'<h3><a href="/articles/([^/]+)/">',section.group(1))
            if len(cards)!=len(set(cards)) or slug in cards or next_link.group(2) in cards:
                raise RuntimeError(slug+": duplicate article recommendations")
        for widget in ('data-future-poll data-poll-article="'+slug+'"',
                       'data-poll-choice="plausible"', 'data-poll-choice="unlikely"'):
            if widget not in page:
                raise RuntimeError(slug+": future prediction UI not deployed: "+widget)
        for token in ('class="reader-next"', 'class="reader-follow"', '"@type": "BreadcrumbList"'):
            if token not in page:
                raise RuntimeError(slug + ": reader path / breadcrumb missing: " + token)
        # Publicly inspect the actual image picked by the review gate.
        # Merely having a cached stock file must NEVER imply it is suitable.
        if usable_cached_photo(item,root=ROOT):
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
                raise RuntimeError(slug+": safe original cover not displayed")
            if "オリジナル表紙" not in page:
                raise RuntimeError(slug+": image not clearly marked as a fictional cover")
            og_url=SITE+"/og/articles/"+slug+".png?v=20261103-topic2"
            if '<meta property="og:image" content="'+og_url+'">' not in page:
                raise RuntimeError(slug+": original story PNG metadata missing")
            picture,kind=get("/og/articles/"+slug+".png?v=20261103-topic2")
            if (not picture.startswith(bytes((137,80,78,71,13,10,26,10)))
                or "image/png" not in kind or len(picture)<1500):
                raise RuntimeError(slug+": original social PNG unavailable")
            cover,kind=get("/images/"+slug+".svg")
            if "image/svg+xml" not in kind:
                raise RuntimeError(slug+": original SVG has incorrect Content-Type")
            if b"MIHASSEI NEWS" not in cover or b"EDITORIAL ILLUSTRATION" not in cover:
                raise RuntimeError(slug+": headline-led safe cover not deployed")
            try:
                ET.fromstring(cover)
            except ET.ParseError:
                raise RuntimeError(slug+": original cover XML is invalid")
        print("Public article OK:",slug)

    category_paths={
        "社会":"society","経済":"economy","技術":"technology",
        "政治":"politics","暮らし":"lifestyle",
        "スポーツ":"sports","エンタメ":"entertainment",
    }
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
    votes=payload.get("articles",{}).get(articles[0]["slug"],{}).get("votes")
    if not isinstance(votes,dict) or any(type(votes.get(k)) is not int or votes[k]<0
                                           for k in ("plausible","unlikely")):
        raise RuntimeError("Future verdict counters missing or invalid")
    print(f"OK: {len(articles)} indexed article URLs; {len(recent)} recent pages; {checked_photos} cached images; real SEO dates; free channel attribution; free hosting.")

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
