"""Contract tests for the zero-cost, clearly labelled serial feature."""
from __future__ import annotations

import json
import tempfile
import re
import struct
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET
from tests.site_build_fixture import ensure_site_build

ROOT = Path(__file__).resolve().parents[1]


class SerialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_site_build()
        from scripts.serials import load_serials
        cls.data = load_serials(ROOT)

    def test_two_distinct_editorial_types(self):
        from scripts.serials import load_serials
        series = load_serials(ROOT)
        self.assertEqual({s["kind"] for s in series}, {"analysis"})
        self.assertEqual(len({s["slug"] for s in series}), len(series))
        self.assertEqual(len(series), 1)
        private = load_serials(ROOT, include_withdrawn=True)
        self.assertEqual({s["kind"] for s in private}, {"fiction", "analysis"})
        self.assertEqual(len(private), 2)

    def test_hub_and_navigation_are_readable_without_js(self):
        home = (ROOT / "dist/index.html").read_text(encoding="utf-8")
        hub = (ROOT / "dist/serials/index.html").read_text(encoding="utf-8")
        self.assertIn('href="/serials/market-lab/"', home)
        self.assertIn('class="serials-feature"', home)
        self.assertIn('class="serial-grid"', home)
        self.assertIn('href="/assets/newspaper.css?v=', hub)
        self.assertIn("/* SOURCE serials.css */", (ROOT/"dist/assets/newspaper.css").read_text(encoding="utf-8"))
        self.assertIn("市場", hub)
        self.assertNotIn("未発生事件簿", hub)
        for s in self.data:
            self.assertIn('href="/serials/'+s["slug"]+'/"', hub)
            if s["slug"] == "case-files":
                self.assertNotIn('data-series-id="case-files"', home)
            else:
                self.assertIn(s["title"], home)
            series_html = (ROOT / "dist/serials" / s["slug"] / "index.html").read_text(encoding="utf-8")
            self.assertIn(s["title"], series_html)
            self.assertIn("公開済み "+str(len(s["episodes"]))+("話" if s["kind"]=="fiction" else "回"), series_html)
            self.assertNotIn('href="/serials/'+s["slug"]+'/nonexistent', series_html)

    def test_episode_labels_match_editorial_kind_and_future_artwork(self):
        from unittest.mock import patch
        from scripts.serials import episode_label, render_social_preview
        from pathlib import Path

        from scripts.serials import load_serials
        mystery=next(x for x in load_serials(ROOT, include_withdrawn=True) if x["kind"]=="fiction")
        market=next(x for x in self.data if x["kind"]=="analysis")
        self.assertEqual(episode_label(mystery,2),"第2話")
        self.assertEqual(episode_label(market,2),"第2回")

        for serial,expected in ((market,"第1回"),):
            html=(ROOT/"dist/serials"/serial["slug"]/serial["episodes"][0]["slug"]/"index.html").read_text(encoding="utf-8")
            self.assertIn(expected,html)
            from urllib.parse import urlsplit, parse_qs
            from scripts.serials import _share_link
            share=_share_link(serial,serial["episodes"][0])
            draft=parse_qs(urlsplit(share).query)["text"][0]
            self.assertIn("市場シナリオ" if serial["kind"]=="analysis" else "創作推理小説", draft)

        class RecordingCanvas:
            def __init__(self):
                self.words=[]
            def rect(self,*args):
                pass
            def line(self,*args):
                pass
            def circle(self,*args):
                pass
            def text(self,value,*args):
                self.words.append(value)
            def save(self,*args):
                pass

        for serial in (market,):
            canvas=RecordingCanvas()
            with patch("scripts.serials.Canvas",return_value=canvas):
                render_social_preview(serial,Path("/tmp/ignored-serial-preview.png"),episode={"number":2})
            self.assertIn("EP 02",canvas.words)
            self.assertNotIn("EP 01",canvas.words)

    def test_episodes_canonical_dates_schema_and_first_party_images(self):
        images = set()
        for s in self.data:
            for e in s["episodes"]:
                path = ROOT / "dist/serials" / s["slug"] / e["slug"] / "index.html"
                self.assertTrue(path.is_file())
                page = path.read_text(encoding="utf-8")
                url = f'https://veripsa.com/serials/{s["slug"]}/{e["slug"]}/'
                png = f'/og/serials/{s["slug"]}-{e["slug"]}.png'
                self.assertIn(f'<link rel="canonical" href="{url}"', page)
                self.assertIn(f'<meta property="og:image" content="https://veripsa.com{png}"', page)
                self.assertIn('property="og:type" content="article"', page)
                self.assertIn('name="twitter:card" content="summary_large_image"', page)
                self.assertIn('"datePublished": "'+e["published_at"]+'"', page)
                self.assertIn('"@type": "Article"', page)
                self.assertIn('href="/serials/'+s["slug"]+'/"', page)
                self.assertIn('src="/serials/'+s["slug"]+'/cover.svg"', page)
                if e is s["episodes"][-1]:
                    self.assertIn("連載終了" if s["slug"] == "case-files" else "次回は未公開", page)
                else:
                    next_ep=s["episodes"][s["episodes"].index(e)+1]
                    self.assertIn('href="/serials/'+s["slug"]+'/'+next_ep["slug"]+'/"',page)
                image = (ROOT / "dist" / png.lstrip("/")).read_bytes()
                self.assertEqual(image[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(struct.unpack(">II", image[16:24]), (1200,630))
                self.assertNotIn(image, images)
                images.add(image)
                self.assertGreater(len(image), 1500)
                self.assertLess(len(image), 200000)

    def test_market_real_analysis_with_sourced_forecast(self):
        s = next(x for x in self.data if x["kind"]=="analysis")
        e = s["episodes"][0]
        page = (ROOT / "dist/serials" / s["slug"] / e["slug"] / "index.html").read_text(encoding="utf-8")
        for marker in ("強気", "中立", "弱気", "予想", "検証日", "2027-02-04", "出典", "投資助言", "未検証"):
            if marker == "出典":
                continue
            self.assertIn(marker, page)
        for source in e["sources"]:
            self.assertIn('href="'+source["url"]+'"', page)
            self.assertTrue(source["url"].startswith("https://"))
        self.assertIn("市場分析", page)
        self.assertIn("株式市場のシナリオ分析", page)
        self.assertNotIn("架空ニュースはすべて創作です", page)

    def test_withdrawn_mystery_has_no_public_episode_feed_or_artwork(self):
        self.assertFalse((ROOT / "dist/serials/case-files").exists())
        self.assertFalse((ROOT / "dist/feeds/series-case-files.xml").exists())
        self.assertFalse(list((ROOT / "dist/og/serials").glob("case-files*.png")))

    def test_og_and_site_map_include_live_serial_routes_only(self):
        from scripts.serials import serial_routes
        routes = serial_routes(self.data)
        sitemap = ET.parse(ROOT / "dist/sitemap.xml").getroot()
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        mapped = {
            el.findtext("s:loc", namespaces=ns): el.findtext("s:lastmod",namespaces=ns)
            for el in sitemap.findall("s:url", ns)
        }
        for path, modified in routes:
            self.assertEqual(mapped["https://veripsa.com"+path], modified)
        for s in self.data:
            cover = ROOT / "dist/serials" / s["slug"] / "cover.svg"
            self.assertTrue(cover.exists())
            root = ET.parse(cover).getroot()
            self.assertEqual(root.attrib["viewBox"], "0 0 1200 675")
            self.assertNotIn("<script", cover.read_text(encoding="utf-8"))
        self.assertNotIn("2027-02-04", " ".join(mapped.keys()))

    def test_serial_rss_is_specific_safe_and_updates_with_published_chapters(self):
        from scripts.serials import serial_feed_xml, serial_feed_url
        ns={"atom":"http://www.w3.org/2005/Atom",
            "media":"http://search.yahoo.com/mrss/"}
        hub=(ROOT/"dist/serials/index.html").read_text(encoding="utf-8")
        sitemap=(ROOT/"dist/sitemap.xml").read_text(encoding="utf-8")
        headers=(ROOT/"dist/_headers").read_text(encoding="utf-8")
        self.assertIn("/feeds/*",headers)
        self.assertIn("X-Robots-Tag: noindex",headers)
        for series in self.data:
            feed=serial_feed_url(series)
            self.assertEqual(feed,"/feeds/series-"+series["slug"]+".xml")
            self.assertIn('href="'+feed+'"',hub)
            overview=(ROOT/"dist/serials"/series["slug"]/"index.html").read_text(encoding="utf-8")
            self.assertIn('href="'+feed+'"',overview)
            self.assertIn('rel="alternate" type="application/rss+xml"',overview)
            self.assertNotIn("https://veripsa.com"+feed,sitemap,
                             "RSS shouldn't be indexed as an article page")
            source=ROOT/"dist"/feed.lstrip("/")
            self.assertTrue(source.is_file())
            rss=ET.parse(source).getroot()
            channel=rss.find("channel")
            self.assertIn(series["title"],channel.findtext("title"))
            self.assertEqual(channel.findtext("language"),"ja")
            atom=channel.find("atom:link",ns)
            self.assertIsNotNone(atom)
            self.assertEqual(atom.get("href"),"https://veripsa.com"+feed)
            self.assertEqual(atom.get("rel"),"self")
            self.assertEqual(atom.get("type"),"application/rss+xml")
            entries=channel.findall("item")
            self.assertEqual(len(entries),min(len(series["episodes"]),30))
            for entry,ep in zip(entries,reversed(series["episodes"][-30:])):
                url="https://veripsa.com/serials/"+series["slug"]+"/"+ep["slug"]+"/"
                self.assertEqual(entry.findtext("link"),url)
                self.assertEqual(entry.findtext("guid"),url)
                self.assertIn(series["title"],entry.findtext("title"))
                self.assertIn("投資助言" if series["kind"]=="analysis" else "創作",
                              entry.findtext("description"))
                self.assertIsNone(entry.find("pubDate"),
                    "Don't invent a midnight release time from a date-only record")
                thumb=entry.find("media:thumbnail",ns)
                media=entry.find("media:content",ns)
                self.assertIsNotNone(thumb)
                self.assertIsNotNone(media)
                image=("https://veripsa.com/og/serials/"+series["slug"]+"-"+ep["slug"]+".png")
                self.assertEqual(thumb.get("url"),image)
                self.assertEqual(media.get("url"),image)
                self.assertEqual(media.get("type"),"image/png")
                chapter=(ROOT/"dist/serials"/series["slug"]/ep["slug"]/"index.html").read_text(encoding="utf-8")
                self.assertIn('href="'+feed+'"',chapter)
                self.assertIn('rel="alternate" type="application/rss+xml"',chapter)
            large={**series,"episodes":[{**series["episodes"][0],
                                            "number":n,"slug":"chapter-"+str(n)}
                                           for n in range(1,35)]}
            capped=ET.fromstring(serial_feed_xml(large)).find("channel").findall("item")
            self.assertEqual(len(capped),30)
            self.assertTrue(capped[0].findtext("link").endswith("/chapter-34/"))

    def test_future_dated_unreleased_chapters_are_rejected(self):
        from scripts.serials import load_serials
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/"content").mkdir()
            altered=json.loads(json.dumps(self.data))
            altered[0]["episodes"][0]["published_at"]="2999-01-01"
            (root/"content/serials.json").write_text(
                json.dumps(altered,ensure_ascii=False),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"future-dated"):
                load_serials(root)

    def test_editorial_disclosure_covers_both_types(self):
        about = (ROOT / "dist/about/index.html").read_text(encoding="utf-8")
        policy = (ROOT / "dist/editorial/index.html").read_text(encoding="utf-8")
        for page in (about, policy):
            self.assertNotIn("推理小説", page)
            self.assertIn("市場", page)
            self.assertIn("創作", page)
        css = (ROOT / "dist/assets/serials.css").read_text(encoding="utf-8")
        self.assertIn("@media(max-width:560px)", css)
        self.assertNotIn("@import", css)


if __name__ == "__main__":
    unittest.main()
