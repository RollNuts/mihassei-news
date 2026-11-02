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
    for token in ("未発生新聞","data-future-clock","data-issue-picker","data-pick-issue"):
        if token not in body:
            raise RuntimeError("Newest homepage has not reached production: "+token)

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
        if f'"datePublished": "{item["fiction_at"]}"' in page:
            raise RuntimeError(slug+": fictional year leaked into SEO date")
        for token in ('class="reader-next"', 'class="reader-follow"', '"@type": "BreadcrumbList"'):
            if token not in page:
                raise RuntimeError(slug + ": reader path / breadcrumb missing: " + token)
        photo=item.get("photo")
        cached=ROOT/"assets"/"photos"/(slug+".jpg")
        if not cached.is_file():
            image_url=SITE+"/og/articles/"+slug+".png"
            if '<meta property="og:image" content="'+image_url+'">' not in page:
                raise RuntimeError(slug+": article-specific OGP image metadata missing")
            picture,kind=get("/og/articles/"+slug+".png")
            if (not picture.startswith(bytes((137,80,78,71,13,10,26,10)))
                or "image/png" not in kind or len(picture)<1500):
                raise RuntimeError(slug+": article-specific social PNG inaccessible")
        if photo:
            if photo["source"] not in page or photo["credit"] not in page:
                raise RuntimeError(slug+": photo attribution missing")
            cached=ROOT/"assets"/"photos"/(slug+".jpg")
            if cached.exists():
                image,mime=get("/photos/"+slug+".jpg")
                if image[:3]!=b"\xff\xd8\xff" or len(image)<16000 or "image/jpeg" not in mime:
                    raise RuntimeError(slug+": invalid public photo")
                checked_photos+=1
            elif photo["url"] not in page and "/photos/"+slug+".jpg" not in page:
                raise RuntimeError(slug+": photo URL or fallback missing")
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
