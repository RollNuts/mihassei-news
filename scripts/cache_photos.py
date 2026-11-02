#!/usr/bin/env python3
"""Cache attributed Unsplash editorial photographs in this public repository.

No image AI, tracking API, payment credential, or cloud storage is needed.
Verifies MIME and image signatures. Failed downloads keep the existing SVG fallback.
Only images with a reviewed Unsplash source page and photographer are eligible.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

if __package__:
    from .media_policy import photo_is_approved, usable_cached_photo, jpeg_dimensions
else:
    from media_policy import photo_is_approved, usable_cached_photo, jpeg_dimensions

ROOT=Path(__file__).resolve().parents[1]
MAX_BYTES=3*1024*1024
OUT=ROOT/"assets/photos"
SOURCE=ROOT/"content/articles.json"
USER_AGENT="MihasseiShimbun/1.0 (editorial image cache; attribution in article)"

def fetch_photo(item):
    slug=item["slug"]
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*",slug):
        raise ValueError("invalid slug")
    photo=item.get("photo")
    if not photo:
        return "not-requested"
    if not photo_is_approved(item, root=ROOT):
        # Automatic publication is preserved; missing review uses on-site art.
        return "not-approved (original editorial cover)"
    url=photo["url"]
    path=urllib.parse.urlsplit(url)
    link=urllib.parse.urlsplit(photo["source"])
    if (path.scheme,path.netloc)!=( "https","images.unsplash.com") or not path.path.startswith("/photo-"):
        raise ValueError("not an approved image host")
    if (link.scheme,link.netloc)!=("https","unsplash.com") or not link.path.startswith("/photos/"):
        raise ValueError("not an approved license source")
    if photo.get("license")!="Unsplash" or not photo.get("credit"):
        raise ValueError("missing license or contributor")
    path_url=urllib.parse.urlunsplit(("https",path.netloc,path.path,
                                     urllib.parse.urlencode({"fit":"crop","w":1400,"q":78,"fm":"jpg"}),""))
    filename=OUT/(slug+".jpg")
    if usable_cached_photo(item, root=ROOT):
        return "already-cached"
    request=urllib.request.Request(path_url,headers={"User-Agent":USER_AGENT,"Accept":"image/jpeg"})
    with urllib.request.urlopen(request,timeout=18) as response:
        content_type=response.headers.get("Content-Type","")
        if "image/jpeg" not in content_type and "image/jpg" not in content_type:
            raise ValueError("not JPEG: "+content_type)
        data=response.read(MAX_BYTES+1)
    if not (16000<len(data)<=MAX_BYTES and data[:3]==b"\xff\xd8\xff"):
        raise ValueError("invalid image payload")
    dimensions=jpeg_dimensions(data)
    if not dimensions or not (dimensions[0]>=900 and dimensions[1]>=440 and 1.15<=dimensions[0]/dimensions[1]<=2.65):
        raise ValueError("photo is too small or unsuitable for 16:9 article covers")
    OUT.mkdir(parents=True,exist_ok=True)
    filename.write_bytes(data)
    return f"saved {len(data)} bytes"

def main():
    items=json.loads(SOURCE.read_text(encoding="utf-8"))
    errors=0
    for item in items:
        try:
            result=fetch_photo(item)
            print(item["slug"]+": "+result)
        except (ValueError,KeyError,urllib.error.URLError,OSError) as exc:
            errors+=1
            print(item.get("slug","unknown")+": skipped ("+str(exc)[:160]+")",file=sys.stderr)
    if errors:
        # Failed images fall back on-site; do not break the newsroom or incur costs.
        print("Some source photos were unavailable; keeping safe original image fallbacks.")

if __name__=="__main__":
    main()
