"""Production-ready checks, no external packages or API access."""
import json
import html
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
            self.assertIn(a["source"]["url"],html.unescape(text))
            self.assertIn(a["source"]["topic"],text)
            self.assertIn("創作ニュース",text)
            self.assertIn("data-article-readers",text)
            self.assertIn("data-like-slug",text)
            self.assertIn("x.com/intent/tweet",text)
            self.assertIn("未発生新聞",text)
            self.assertIn("%23%E6%9C%AA%E7%99%BA%E7%94%9F%E6%96%B0%E8%81%9E",text)
            self.assertIn('data-like-count="'+a["slug"]+'"',text)
            self.assertNotIn('data-like-count="{slug}"',text)
            self.assertNotIn("Source: Unknown",text)
            original=datetime.fromisoformat(a["source"]["published_at"])
            fiction=datetime.fromisoformat(a["fiction_at"])
            self.assertEqual(fiction.year,original.year+5)
            self.assertEqual((fiction.month,fiction.day,fiction.hour,fiction.minute),
                             (original.month,original.day,original.hour,original.minute))

    def test_real_publication_time_is_not_fictional_dateline(self):
        for a in self.articles:
            page=(ROOT/"dist/articles"/a["slug"]/"index.html").read_text(encoding="utf-8")
            expected=a.get("published_at") or a["source"]["published_at"][:10]
            self.assertIn('"datePublished": "'+expected+'"',page)
            self.assertNotIn('"datePublished": "'+a["fiction_at"]+'"',page)
            self.assertIn('"@type": "BlogPosting"',page)

    def test_sitemap_and_category_archives(self):
        root=ET.parse(ROOT/"dist/sitemap.xml").getroot()
        ns={"s":"http://www.sitemaps.org/schemas/sitemap/0.9"}
        mapped={n.findtext("s:loc",namespaces=ns):n.findtext("s:lastmod",namespaces=ns)
                for n in root.findall("s:url",ns)}
        for a in self.articles:
            url="https://veripsa.com/articles/"+a["slug"]+"/"
            self.assertIn(url,mapped)
            self.assertEqual(mapped[url][:10],(a.get("modified_at") or a.get("published_at") or a["source"]["published_at"][:10])[:10])
        economies=[a for a in self.articles if a["category"]=="経済"]
        if len(economies)>=3:
            category=ROOT/"dist/category/economy/index.html"
            self.assertTrue(category.exists())
            for a in economies:
                self.assertIn(a["title"],category.read_text(encoding="utf-8"))
            self.assertIn("https://veripsa.com/category/economy/",mapped)

    def test_real_photos_privacy_and_search_dates(self):
        index=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        self.assertIn('href="/privacy/"',index)
        self.assertTrue((ROOT/"dist/privacy/index.html").exists())
        sitemap=(ROOT/"dist/sitemap.xml").read_text(encoding="utf-8")
        self.assertIn("https://veripsa.com/privacy/",sitemap)
        for a in self.articles:
            html=(ROOT/"dist/articles"/a["slug"]/"index.html").read_text(encoding="utf-8")
            self.assertIn('"datePublished": "'+a["published_at"]+'"',html)
            self.assertNotIn('"datePublished": "'+a["fiction_at"]+'"',html)
            if a.get("photo"):
                self.assertIn(a["photo"]["source"],html)
                self.assertIn(a["photo"]["credit"],html)
                self.assertIn(a["photo"]["alt"],html)
                self.assertIn('data-fallback=',html)
                self.assertIn("イメージ写真",html)

    def test_latest_publication_not_source_time_controls_lead(self):
        import re
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        lead=re.search(r'<h1><a href="/articles/([^/]+)/">',home)
        self.assertIsNotNone(lead)
        expected=max(self.articles,key=lambda a:(
            a.get("published_at") or a["source"]["published_at"],
            a["source"]["published_at"]
        ))
        self.assertEqual(lead.group(1),expected["slug"])
        feed=ET.parse(ROOT/"dist/feed.xml")
        first=feed.find("./channel/item/link")
        self.assertIsNotNone(first)
        self.assertEqual(first.text,"https://veripsa.com/articles/"+expected["slug"]+"/")

    def test_extra_edition_picker_links_to_real_articles(self):
        import re
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        self.assertIn('data-pick-issue',home)
        self.assertIn('data-issue-link',home)
        match=re.search(r'data-issue-options="([^"]+)"',home)
        self.assertIsNotNone(match)
        choices=json.loads(html.unescape(match.group(1)))
        self.assertEqual(len(choices),len(self.articles))
        self.assertEqual({a["slug"] for a in self.articles},
                         {item["url"].split("/")[2] for item in choices})
        self.assertIn("今日の号外をめくる",home)

    def test_social_previews_use_first_party_jpeg_or_png(self):
        """X and Open Graph crawlers must not receive SVG or third-party photo URLs."""
        import re
        from scripts.build import social_image
        self.assertTrue(social_image({"slug":"nonexistent-story"}).startswith("/og/brand.png"))
        pages=[ROOT/"dist/index.html"]
        pages += [ROOT/"dist/articles"/a["slug"]/"index.html" for a in self.articles]
        for page in pages:
            markup=page.read_text(encoding="utf-8")
            match=re.search(r'<meta property="og:image" content="([^"]+)">',markup)
            self.assertIsNotNone(match,str(page))
            url=match.group(1)
            self.assertTrue(url.startswith("https://veripsa.com/"),url)
            path=url.removeprefix("https://veripsa.com/").split("?",1)[0]
            self.assertTrue(path.endswith((".jpg",".png")),url)
            self.assertIn('name="twitter:card" content="summary_large_image"',markup)
            self.assertIn('name="twitter:image" content="'+url+'"',markup)
            self.assertIn('property="og:image:secure_url" content="'+url+'"',markup)
            self.assertIn('name="twitter:image:alt" content=',markup)
            mime="image/png" if path.endswith(".png") else "image/jpeg"
            self.assertIn('property="og:image:type" content="'+mime+'"',markup)
            self.assertNotIn('name="twitter:site" content="@Veripsa"',markup)
            asset=ROOT/"dist"/path
            self.assertTrue(asset.is_file(),url)
            self.assertGreater(asset.stat().st_size,1000,url)

    def test_opengraph_and_feed(self):
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        self.assertIn('name="twitter:card" content="summary_large_image"',home)
        self.assertIn('property="og:image" content="https://veripsa.com/og/brand.png?v=20261102-2"',home)
        self.assertIn('property="og:image:secure_url" content="https://veripsa.com/og/brand.png?v=20261102-2"',home)
        self.assertIn('name="twitter:image" content="https://veripsa.com/og/brand.png?v=20261102-2"',home)
        self.assertIn('name="twitter:title" content="未発生新聞｜5年後の架空ニュース"',home)
        self.assertNotIn('未発生新聞 | 未発生新聞',home)
        for a in self.articles:
            page=(ROOT/"dist/articles"/a["slug"]/"index.html").read_text(encoding="utf-8")
            self.assertIn('name="twitter:card" content="summary_large_image"',page)
            self.assertIn('name="twitter:image" content="https://',page)
            self.assertNotIn('.svg">',page.split('name="twitter:image" content="',1)[1].split('>',1)[0])
        image=(ROOT/"dist/og/brand.png").read_bytes()
        self.assertEqual(image[:8],b"\x89PNG\r\n\x1a\n")
        ET.parse(ROOT/"dist/sitemap.xml")
        ET.parse(ROOT/"dist/feed.xml")

if __name__=="__main__":
    unittest.main()
