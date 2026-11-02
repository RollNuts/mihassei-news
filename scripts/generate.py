#!/usr/bin/env python3
"""One safe, source-linked original fictional news story per day (optional GEMINI_API_KEY)."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ARTICLE_FILE = ROOT / "content/articles.json"
SEED_FILE = ROOT / "content/source_seeds.json"
TZ = ZoneInfo("Asia/Tokyo")
API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
CATEGORIES = ("社会", "経済", "技術", "政治", "暮らし", "スポーツ", "エンタメ")
ARTS = ("notice", "cards", "tower", "robot", "meeting", "generic")
FEEDS = (
    ("news", "https://news.google.com/rss?hl=ja&gl=JP&ceid=JP:ja"),
    ("news", "https://news.google.com/rss/search?q=AI%20OR%20%E6%8A%80%E8%A1%93&hl=ja&gl=JP&ceid=JP:ja"),
    ("news", "https://news.google.com/rss/search?q=%E6%9A%AE%E3%82%89%E3%81%97%20OR%20%E8%A9%B1%E9%A1%8C&hl=ja&gl=JP&ceid=JP:ja"),
    # Public social/community RSS: can be unavailable, so failure is harmless.
    ("social", "https://www.reddit.com/r/japan/top/.rss?t=day"),
)


def request_bytes(url, *, data=None, headers=None):
    default_headers = {
        "User-Agent": "MihasseiShimbun/1.0 (fictional original-content publisher; contact via veripsa.com)",
        "Accept": "application/json, application/xml, text/xml, */*",
    }
    if headers:
        default_headers.update(headers)
    req = urllib.request.Request(url, headers=default_headers, data=data)
    with urllib.request.urlopen(req, timeout=18) as response:
        return response.read(1024 * 1024)


def clean(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", text or ""))).strip()


def as_jst(iso):
    when = datetime.fromisoformat(iso)
    if when.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return when.astimezone(TZ)


def parse_datetime(raw):
    try:
        return parsedate_to_datetime(raw).astimezone(TZ)
    except (ValueError, TypeError, IndexError):
        return None


def get_candidates(now):
    out = []
    # Reviewed manual links are useful for X, 5ch and other media without an authorized RSS API.
    try:
        manual = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manual = []
    for item in manual:
        try:
            date = as_jst(item["published_at"])
            if date.date() == now.date():
                out.append({
                    "kind":item.get("kind","social"),
                    "publisher":item.get("publisher","公開投稿"),
                    "url":item["url"],
                    "published_at":date.isoformat(),
                    "topic":clean(item["topic"])[:240],
                    "priority":0,
                })
        except (KeyError, ValueError, TypeError):
            continue

    for kind, feed in FEEDS:
        try:
            xml = request_bytes(feed)
            root = ET.fromstring(xml)
            if root.tag.endswith("rss"):
                entries = root.findall(".//item")
                get = lambda e, key: e.findtext(key, default="")
                for pos, entry in enumerate(entries[:45]):
                    date = parse_datetime(get(entry, "pubDate"))
                    link = get(entry, "link").strip()
                    topic = clean(get(entry, "title"))
                    provider = clean(entry.findtext("source", default="") or urllib.parse.urlsplit(link).netloc)
                    if (date and date.date() == now.date() and link.startswith("https://") and topic):
                        out.append({"kind":kind, "publisher":provider, "url":link,
                                    "published_at":date.isoformat(), "topic":topic[:220], "priority":pos+10})
            else:
                atom = "{http://www.w3.org/2005/Atom}"
                for pos, entry in enumerate(root.findall(f".//{atom}entry")[:40]):
                    raw = entry.findtext(f"{atom}updated", default="") or entry.findtext(f"{atom}published",default="")
                    try:
                        date = as_jst(raw.replace("Z","+00:00"))
                    except ValueError:
                        continue
                    links = entry.findall(f"{atom}link")
                    url = next((x.get("href") for x in links if x.get("href","").startswith("https://")), "")
                    title = clean(entry.findtext(f"{atom}title", default=""))
                    if date.date() == now.date() and url and title:
                        out.append({"kind":kind, "publisher":"公開コミュニティ投稿", "url":url,
                                    "published_at":date.isoformat(), "topic":title[:220], "priority":pos+30})
        except (OSError, ValueError, ET.ParseError, urllib.error.URLError) as error:
            print("Source unavailable, skipping: "+str(error)[:150], file=sys.stderr)
    out.sort(key=lambda x: (x["priority"], -as_jst(x["published_at"]).timestamp()))
    seen = set()
    return [x for x in out if not (x["url"] in seen or seen.add(x["url"]))]


def get_model_story(candidate):
    prompt = f"""あなたは『未発生新聞』の編集長。以下の実在する話題を出発点に、ちょうど5年後に起きたとしたら面白い架空の続報を1本考える。
公開候補の元トピック（不正確な可能性もあるRSSメタデータ）：{candidate["topic"]}
元トピック種別：{candidate["kind"]}
重要：著作権を守る。元記事やSNS本文・見出し・画像の転載は絶対にしない。短く独自に言い換えた『元トピックの概要』だけ作る。
5年後に「すでに当たり前」である技術の紹介は禁止。既存技術から生まれるおかしな制度・企業行動・価値観の逆転を、冷静な日本語の新聞口調で報じる。
クスッと笑えて『ありえないだろ、でも5年ならあるかも』と思える奇妙な逆説が必要。テンプレ的なAI万能・ロボット支配話は避ける。
すべての架空企業名・会話・統計・事象は独自創作。元記事の実在人物・実在企業名・政治家・商標・投稿者ハンドルを架空記事側に登場させない。
犯罪・事故・倒産・不祥事を現実の実在人物や企業に創作して帰属させない。名誉毀損、個人情報漏洩や社会的混乱を招く偽速報は避ける。
掲載するジャンルは「{", ".join(CATEGORIES)}」から選択、図版テーマは「{", ".join(ARTS)}」から選ぶ。
見出しは35-65文字、要約は55-110文字、本文は3段落、各90-160文字。直接引用を使うなら架空の社員に限る。
JSONオブジェクトだけ出力。キー：title, summary, fiction_company, category, art, flash, paragraphs（3文字列）, source_topic（元話題の独自の要旨1文）。
面白い記事が成立しない場合は {{"skip":true}} のみ返す。"""
    body = {
        "contents":[{"parts":[{"text":prompt}]}],
        "generationConfig":{"responseMimeType":"application/json","temperature":1.0,"maxOutputTokens":1600}
    }
    endpoint = "https://generativelanguage.googleapis.com/v1beta/models/" + urllib.parse.quote(MODEL, safe="-") + ":generateContent"
    blob = request_bytes(endpoint,data=json.dumps(body,ensure_ascii=False).encode("utf-8"),
                         headers={"Content-Type":"application/json", "x-goog-api-key":API_KEY})
    result=json.loads(blob)
    parts=result["candidates"][0]["content"]["parts"]
    model_output="".join(p.get("text","") for p in parts)
    return json.loads(model_output)


def validate_story(obj, candidate, existing, now):
    if obj.get("skip"):
        return None
    for k in ("title","summary","fiction_company","category","art","paragraphs","source_topic"):
        if k not in obj:
            return None
    title, summary, company=obj["title"],obj["summary"],obj["fiction_company"]
    if not all(isinstance(x,str) and 8 <= len(x) <= 200 for x in (title,summary,company)):
        return None
    if obj["category"] not in CATEGORIES or obj["art"] not in ARTS:
        return None
    if not isinstance(obj["paragraphs"],list) or len(obj["paragraphs"]) != 3:
        return None
    if any(not isinstance(x,str) or len(x)<50 or len(x)>500 for x in obj["paragraphs"]):
        return None
    if not isinstance(obj["source_topic"],str) or not 15<=len(obj["source_topic"])<=240:
        return None
    if SequenceMatcher(None, title, candidate["topic"]).ratio()>0.60:
        return None
    if any(SequenceMatcher(None,title,a["title"]).ratio()>0.56 for a in existing):
        return None
    if any(x["source"]["url"]==candidate["url"] for x in existing):
        return None
    if any(a["source"]["published_at"][:10]==now.date().isoformat() for a in existing):
        return None
    source_time = as_jst(candidate["published_at"])
    try:
        future=source_time.replace(year=source_time.year+5).isoformat()
    except ValueError:
        return None
    slug="fiction-"+source_time.strftime("%Y%m%d-%H%M")+"-"+hashlib.sha256(candidate["url"].encode()).hexdigest()[:8]
    # The source link is context, not a reference for any fictional claims.
    return {
        "slug":slug, "category":obj["category"],"flash":obj.get("flash","速報")[:12],
        "fiction_company":company, "art":obj["art"],"fiction_at":future,
        "title":title,"summary":summary,"paragraphs":obj["paragraphs"],
        "source":{"kind":candidate["kind"],"publisher":candidate["publisher"],
                  "published_at":source_time.isoformat(),"url":candidate["url"],
                  "topic":obj["source_topic"]},
    }


def main():
    if not API_KEY:
        print("GEMINI_API_KEY is unset: no publication. Existing site is unaffected.")
        return
    existing = json.loads(ARTICLE_FILE.read_text(encoding="utf-8"))
    now = datetime.now(TZ)
    if any(a["source"]["published_at"][:10]==now.date().isoformat() for a in existing if a["slug"].startswith("fiction-")):
        print("Already auto-published today.")
        return
    candidates=get_candidates(now)
    candidates=[c for c in candidates if c["url"] not in {a["source"]["url"] for a in existing}]
    if not candidates:
        print("No suitable current sources; no article today.")
        return
    for c in candidates[:4]:
        try:
            generated=get_model_story(c)
            new=validate_story(generated,c,existing,now)
            if new:
                existing.insert(0,new)
                ARTICLE_FILE.write_text(json.dumps(existing,ensure_ascii=False,indent=2)+"\n", encoding="utf-8")
                print("Generated original fictional story from: "+c["url"])
                return
        except (KeyError,IndexError,ValueError,urllib.error.URLError) as error:
            print("Generation skipped: "+str(error)[:180], file=sys.stderr)
    print("No acceptable output; deliberately not publishing.")


if __name__ == "__main__":
    main()
