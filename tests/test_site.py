"""Production-ready checks, no external packages or API access."""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from datetime import datetime
from xml.etree import ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]

class SiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([sys.executable,str(ROOT/"scripts/build.py")],cwd=ROOT,check=True)
        cls.articles=json.loads((ROOT/"content/articles.json").read_text(encoding="utf-8"))

    def test_core_files(self):
        self.assertGreaterEqual(len(self.articles),5)
        for p in ["index.html","archive/index.html","about/index.html","feed.xml",
                  "sitemap.xml","assets/style.css","assets/site.js",
                  "assets/article-extra.css","assets/newsroom.css","assets/engagement.js",
                  "og/brand.png","404.html","robots.txt"]:
            self.assertTrue((ROOT/"dist"/p).exists(),p)

    def test_adsense_ownership_without_running_ads(self):
        line="google.com, pub-6754575901141756, DIRECT, f08c47fec0942fa0"
        self.assertEqual((ROOT/"dist/ads.txt").read_text(encoding="utf-8").strip(),line)
        html=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        self.assertIn('<meta name="google-adsense-account" content="ca-pub-6754575901141756">',html)
        self.assertNotIn("pagead2.googlesyndication.com/pagead/js/adsbygoogle.js",html)

    def test_site_has_dynamic_clock_with_no_fake_visits(self):
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        self.assertIn("data-future-clock",home)
        self.assertIn("data-observer-widget",home)
        self.assertIn("data-observer-count",home)
        self.assertIn("※ブラウザ単位の参考値",home)
        self.assertIn("hidden",home)
        self.assertIn("創作報道",home)
        self.assertIn("data-like-slug",home)
        self.assertIn("x.com/intent/tweet",home)
        self.assertIn(self.articles[0]["title"],home)

    def test_articles_are_original_and_shareable(self):
        for a in self.articles:
            page=ROOT/"dist/articles"/a["slug"]/"index.html"
            image=ROOT/"dist/images"/(a["slug"]+".svg")
            self.assertTrue(page.exists(),a["slug"])
            self.assertTrue(image.exists(),a["slug"])
            text=page.read_text(encoding="utf-8")
            self.assertIn(a["title"],text)
            self.assertIn(a["source"]["url"],text)
            self.assertIn(a["source"]["topic"],text)
            self.assertIn("創作ニュース",text)
            self.assertIn("data-article-readers",text)
            self.assertIn("data-like-slug",text)
            self.assertIn("x.com/intent/tweet",text)
            self.assertNotIn("Source: Unknown",text)
            original=datetime.fromisoformat(a["source"]["published_at"])
            fiction=datetime.fromisoformat(a["fiction_at"])
            self.assertEqual(fiction.year,original.year+5)
            self.assertEqual((fiction.month,fiction.day,fiction.hour,fiction.minute),
                             (original.month,original.day,original.hour,original.minute))

    def test_editorial_photography_and_article_schema(self):
        import re
        for a in self.articles:
            page = (ROOT / "dist/articles" / a["slug"] / "index.html").read_text(encoding="utf-8")
            self.assertIn("data-fallback", page if a.get("photo") else "data-fallback")
            if a.get("photo"):
                photo=a["photo"]
                self.assertTrue(photo["source"].startswith("https://unsplash.com/photos/"))
                self.assertTrue(photo["url"].startswith("https://images.unsplash.com/photo-"))
                self.assertIn(photo["credit"],page)
                self.assertIn(photo["alt"],page)
                self.assertIn(photo["source"],page)
                self.assertIn("イメージ写真",page)
                self.assertIn('property="og:image"',page)
            if a.get("published_on"):
                self.assertIn('"datePublished": "'+a["published_on"]+'"',page)
            self.assertIn('"@type": "Article"',page)
        archive=(ROOT/"dist/archive/index.html").read_text(encoding="utf-8")
        self.assertIn("記事一覧",archive)

    def test_privacy_and_sitemap(self):
        self.assertTrue((ROOT/"dist/privacy/index.html").is_file())
        privacy=(ROOT/"dist/privacy/index.html").read_text(encoding="utf-8")
        self.assertIn("アクセスといいね",privacy)
        self.assertIn("Cloudflare",privacy)
        sitemap=(ROOT/"dist/sitemap.xml").read_text(encoding="utf-8")
        self.assertIn("https://veripsa.com/privacy/",sitemap)

    def test_opengraph_and_feed(self):
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        self.assertIn('name="twitter:card" content="summary_large_image"',home)
        self.assertIn('og/brand.png',home)
        image=(ROOT/"dist/og/brand.png").read_bytes()
        self.assertEqual(image[:8],b"\x89PNG\r\n\x1a\n")
        ET.parse(ROOT/"dist/sitemap.xml")
        ET.parse(ROOT/"dist/feed.xml")

if __name__=="__main__":
    unittest.main()
