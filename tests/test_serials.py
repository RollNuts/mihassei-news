"""Contract tests for the zero-cost, clearly labelled serial feature."""
from __future__ import annotations

import json
import re
import struct
import subprocess
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


class SerialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([sys.executable, str(ROOT / "scripts" / "build.py")],
                       cwd=ROOT, check=True)
        cls.data = json.loads((ROOT / "content" / "serials.json").read_text(encoding="utf-8"))

    def test_two_distinct_editorial_types(self):
        from scripts.serials import load_serials
        series = load_serials(ROOT)
        self.assertEqual({s["kind"] for s in series}, {"fiction", "analysis"})
        self.assertEqual(len({s["slug"] for s in series}), len(series))
        self.assertEqual(len(series), 2)

    def test_hub_and_navigation_are_readable_without_js(self):
        home = (ROOT / "dist/index.html").read_text(encoding="utf-8")
        hub = (ROOT / "dist/serials/index.html").read_text(encoding="utf-8")
        self.assertIn('href="/serials/"', home)
        self.assertIn('class="serials-feature"', home)
        self.assertIn('class="serial-grid"', home)
        self.assertIn('href="/assets/serials.css"', hub)
        self.assertIn("市場", hub)
        self.assertIn("推理", hub)
        for s in self.data:
            self.assertIn('href="/serials/'+s["slug"]+'/"', hub)
            self.assertIn(s["title"], home)
            series_html = (ROOT / "dist/serials" / s["slug"] / "index.html").read_text(encoding="utf-8")
            self.assertIn(s["title"], series_html)
            self.assertIn("公開済み 1話" if s["kind"]=="fiction" else "公開済み 1回", series_html)
            self.assertNotIn('href="/serials/'+s["slug"]+'/nonexistent', series_html)

    def test_episode_labels_match_editorial_kind_and_future_artwork(self):
        from unittest.mock import patch
        from scripts.serials import episode_label, render_social_preview
        from pathlib import Path

        mystery=next(x for x in self.data if x["kind"]=="fiction")
        market=next(x for x in self.data if x["kind"]=="analysis")
        self.assertEqual(episode_label(mystery,2),"第2話")
        self.assertEqual(episode_label(market,2),"第2回")

        for serial,expected in ((mystery,"第1話"),(market,"第1回")):
            html=(ROOT/"dist/serials"/serial["slug"]/serial["episodes"][0]["slug"]/"index.html").read_text(encoding="utf-8")
            self.assertIn(expected,html)
            self.assertIn("市場シナリオ" if serial["kind"]=="analysis" else "創作推理小説",
                          __import__("scripts.serials",fromlist=["_share_link"])._share_link(serial,serial["episodes"][0]))

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

        for serial in (mystery,market):
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
                self.assertIn('次回は未公開', page)
                self.assertNotIn('href="/serials/'+s["slug"]+'/02-', page)
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
        self.assertIn("現実の市場", page)
        self.assertIn("株式市場のシナリオ分析", page)
        self.assertNotIn("架空ニュースはすべて創作です", page)

    def test_mystery_clues_are_fair_and_no_fake_episodes(self):
        s = next(x for x in self.data if x["kind"] == "fiction")
        e = s["episodes"][0]
        page = (ROOT / "dist/serials" / s["slug"] / e["slug"] / "index.html").read_text(encoding="utf-8")
        self.assertIn("読者への手掛かり", page)
        self.assertIn("あなたへの問い", page)
        for clue in e["clues"]:
            self.assertIn(clue, page)
        self.assertIn("すべて創作", page)

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

    def test_editorial_disclosure_covers_both_types(self):
        about = (ROOT / "dist/about/index.html").read_text(encoding="utf-8")
        policy = (ROOT / "dist/editorial/index.html").read_text(encoding="utf-8")
        for page in (about, policy):
            self.assertIn("推理小説", page)
            self.assertIn("市場", page)
            self.assertIn("創作", page)
        css = (ROOT / "dist/assets/serials.css").read_text(encoding="utf-8")
        self.assertIn("@media(max-width:560px)", css)
        self.assertNotIn("@import", css)


if __name__ == "__main__":
    unittest.main()
