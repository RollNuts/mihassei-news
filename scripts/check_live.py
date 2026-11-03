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
    from .media_policy import usable_cached_photo, jpeg_dimensions
    from .generated_photos import usable_generated_scene, generated_url, generated_scene_entry
    from .motion_clips import usable_clip, clip_url
    from .share_desk import select_candidates
    from .serial_discovery import choose_serial, primary_episode
    from .editorial_starter import homepage_starter_picks
    from .build import editorial_lead, CATEGORIES
else:
    from media_policy import usable_cached_photo, jpeg_dimensions
    from generated_photos import usable_generated_scene, generated_url, generated_scene_entry
    from motion_clips import usable_clip, clip_url
    from share_desk import select_candidates
    from serial_discovery import choose_serial, primary_episode
    from editorial_starter import homepage_starter_picks
    from build import editorial_lead, CATEGORIES

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


def verify_generated_image(article, image: bytes, mime: str, *, root=ROOT) -> None:
    """Detect stale CDN bytes as well as malformed or oversized public JPEGs."""
    record = generated_scene_entry(article, root=root)
    if (not record or "image/jpeg" not in mime
            or len(image) != record["bytes"]
            or jpeg_dimensions(image) != (1200, 675)
            or hashlib.sha256(image).hexdigest() != record["sha256"]):
        raise RuntimeError(article["slug"] + ": production photo differs from the validated generation")


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
    body=html.unescape(home.decode("utf-8"))
    for token in ("未発生新聞","data-future-clock","data-issue-picker","data-pick-issue","fiction-mark"):
        if token not in body:
            raise RuntimeError("Newest homepage has not reached production: "+token)
    # The visible counters now reset by JST date; do not require the
    # retired lifetime-counter label or imply that browser IDs are people.
    for token in ("data-observer-widget","data-observer-count",
                  "data-observer-yesterday","data-observer-reads",
                  "今日の来訪","昨日の来訪","今日の記事閲覧",
                  "1ブラウザ・1記事につき1日1回の集計"):
        if token not in body:
            raise RuntimeError("Daily audience display missing: "+token)
    if "未来を見にきた人" in body:
        raise RuntimeError("Homepage must not label browser counters as people")

    # Three hand-selected, real stories replace the duplicate category portals.
    # This is not a popularity claim; no new calls, events, or privacy tracking.
    chosen_lead = editorial_lead(sorted_articles)
    if not chosen_lead or chosen_lead["slug"] != sorted_articles[0]["slug"]:
        raise RuntimeError("Latest publication is not the front-page lead")
    if ('<h1><a href="/articles/'+chosen_lead["slug"]+'/"' not in body):
        raise RuntimeError("Published front-page lead differs from newest real article")
    starters=homepage_starter_picks(sorted_articles,chosen_lead,base=ROOT)
    if starters:
        section=re.search(r'<section class="starter-editorial".*?</section>',body,re.S)
        if not section:
            raise RuntimeError("Editorial starter choices not deployed")
        if section.group(0).count('class="starter-pick-card"')!=len(starters):
            raise RuntimeError("Editorial starter card count incorrect")
        if "SELECTION" not in section.group(0):
            raise RuntimeError("Editorial curation label missing")
        for choice in starters:
            slug=choice["article"]["slug"]
            if ('href="/articles/'+slug+'/"' not in section.group(0) or
                choice["hook"] not in section.group(0)):
                raise RuntimeError("Published editorial choice not visible: "+slug)
        if 'class="reading-ways"' in body:
            raise RuntimeError("Duplicate starter portals were not removed")
        news=re.search(r'<section class="section-block">.*?</section>',body,re.S)
        if not news:
            raise RuntimeError("Fresh-news grid is missing")
        for choice in starters:
            if 'href="/articles/'+choice["article"]["slug"]+'/"' in news.group(0):
                raise RuntimeError("Editorial starter duplicated in fresh news")
    
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

    # Genuine reader discovery is absent for low sample sizes and only
    # appears when confirmed browser counts arrive from the existing API.
    for token in ('data-popular-section', 'data-popular-options=', 'data-popular-list',
                  'readers-choice-title" hidden'):
        if token not in body:
            raise RuntimeError("Reader-discovery layout not deployed: "+token)
    if "実測ブラウザ単位の参考集計。実人数・PVではありません。" not in body:
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
        for needle in (item["title"],item["summary"],*item["paragraphs"],
                       item["source"]["url"],"創作ニュース",f'data-like-count="{slug}"'):
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
        recorded_clip = usable_clip(item, root=ROOT)
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
                             "この記事の架空の出来事を裏付けるものではなく" if chosen_serial["kind"]=="analysis"
                             else "この記事の続報ではなく"):
                if required not in page:
                    raise RuntimeError(slug+": editorial serial discovery is not published: "+required)
        elif 'class="article-serial-bridge"' in page:
            raise RuntimeError(slug+": unrelated serial was forced into fictional news")
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
        for token in ('class="reader-next"', 'class="share-control"', '"@type": "BreadcrumbList"'):
            if token not in page:
                raise RuntimeError(slug + ": reader path / breadcrumb missing: " + token)
        # Publicly inspect the actual image picked by the review gate.
        # Merely having a cached stock file must NEVER imply it is suitable.
        if usable_generated_scene(item,root=ROOT):
            image_path=generated_url(item)
            if 'src="'+image_path+'"' not in page:
                raise RuntimeError(slug+": locally generated staged photograph not displayed")
            if "再現イメージ（合成）" not in page or "実際の出来事を撮影した報道写真ではありません" not in page:
                raise RuntimeError(slug+": staged photo needs a concise synthetic-image disclosure")
            if ('<meta property="og:image" content="'+SITE+image_path+'">' not in page):
                raise RuntimeError(slug+": local staged photograph missing from OGP")
            image,kind=get(image_path)
            verify_generated_image(item, image, kind, root=ROOT)
        elif usable_cached_photo(item,root=ROOT):
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
            if b"data-visual-topic=" not in cover or b"<text" in cover or b"<image" in cover:
                raise RuntimeError(slug+": text-free editorial illustration not deployed")
            try:
                parsed=ET.fromstring(cover)
                if parsed.attrib.get("viewBox")!="0 0 1200 675" or parsed.attrib.get("role")!="img":
                    raise RuntimeError(slug+": illustration aspect ratio or image role is wrong")
            except ET.ParseError:
                raise RuntimeError(slug+": original illustration XML is invalid")
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
