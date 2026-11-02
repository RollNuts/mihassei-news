#!/usr/bin/env python3
"""Read-only smoke test of the actual newspaper deployment and recent articles.

The script never sends POSTs, creates visits or likes, or invokes a paid API.
It runs after GitHub pushes; Cloudflare may take a little time to deploy.
"""
from __future__ import annotations

import html
import json
import os
import sys
import time
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

def check():
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
        'name="twitter:image" content="https://veripsa.com/og/brand.png?v=20261102-2"',
        'property="og:image" content="https://veripsa.com/og/brand.png?v=20261102-2"',
    ):
        if tag not in body:
            raise RuntimeError("X card metadata missing or not yet deployed: " + tag)
    image, image_mime=get("/og/brand.png?v=20261102-2")
    if not image.startswith(b"\\x89PNG\\r\\n\\x1a\\n") or "image/png" not in image_mime or len(image) < 1024:
        raise RuntimeError("X card brand image missing or invalid PNG")

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
        photo=item.get("photo")
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
    print(f"OK: {len(articles)} indexed article URLs; {len(recent)} recent pages; {checked_photos} cached images; real SEO dates; random edition; free hosting.")

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
