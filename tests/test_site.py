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
                  "sitemap.xml","editorial/index.html","assets/style.css","assets/site.js",
                  "assets/article-extra.css","assets/newsroom.css","assets/engagement.js",
                  "assets/comments.js","assets/comments.css","assets/comments-admin.js","assets/comments-admin.html",
                  "assets/comment-activity.js",
                  "og/brand.png","404.html","robots.txt","build-revision.txt"]:
            self.assertTrue((ROOT/"dist"/p).exists(),p)

    def test_static_output_identifies_exact_build_revision(self):
        import re
        marker=(ROOT/"dist/build-revision.txt").read_text(encoding="utf-8").strip()
        self.assertTrue(marker=="unknown" or re.fullmatch(r"[0-9a-f]{40}",marker))
        git=subprocess.run(["git","rev-parse","HEAD"],cwd=ROOT,text=True,
                           capture_output=True,check=False)
        if git.returncode==0 and re.fullmatch(r"[0-9a-f]{40}",git.stdout.strip()):
            self.assertEqual(marker,git.stdout.strip())
        self.assertIn("/build-revision.txt", (ROOT/"dist/_headers").read_text(encoding="utf-8"))
        self.assertIn("Cache-Control: no-store", (ROOT/"dist/_headers").read_text(encoding="utf-8"))

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

    def test_reader_comments_are_present_and_privacy_is_explained(self):
        for article in self.articles:
            page=ROOT/"dist/articles"/article["slug"]/"index.html"
            text=page.read_text(encoding="utf-8")
            self.assertIn('data-comments data-article="'+article["slug"]+'"',text)
            self.assertIn("reader-comments-form",text)
            self.assertIn("/assets/comments.js",text)
            self.assertIn("/assets/comments.css",text)
            self.assertIn('name="body"',text)
            self.assertIn("問題", (ROOT/"assets/comments.js").read_text(encoding="utf-8"))
        privacy=(ROOT/"dist/privacy/index.html").read_text(encoding="utf-8")
        self.assertIn("読者コメントについて",privacy)
        self.assertIn("投稿日時を公開",privacy)

    def test_genuine_reader_participation_discovery(self):
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        self.assertIn('data-comment-discussions',home)
        self.assertIn('data-comment-discussion-options=',home)
        self.assertIn('data-discussion-list',home)
        self.assertIn('href="/assets/comment-activity.js"',home.replace('src=', 'href='))
        self.assertIn("新着記事にコメントする",home)
        self.assertIn('data-comment-count=',home)
        for a in self.articles:
            page=(ROOT/"dist/articles"/a["slug"]/"index.html").read_text(encoding="utf-8")
            self.assertIn('id="reader-comments"',page)
            self.assertIn('data-comment-count="'+a["slug"]+'"',page)
            self.assertIn('href="/articles/'+a["slug"]+'/#reader-comments"',page)
        self.assertTrue((ROOT/"dist/assets/comment-activity.js").is_file())
        highlights=(ROOT/"assets/comment-activity.js").read_text(encoding="utf-8")
        self.assertIn("/api/comments/summary?",highlights)
        self.assertNotIn(".innerHTML=",highlights)

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
        self.assertIn("https://veripsa.com/editorial/",mapped)
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

    def test_archive_is_searchable_and_incremental_with_no_js_fallback(self):
        page=(ROOT/"dist/archive/index.html").read_text(encoding="utf-8")
        self.assertIn('id="archive-results" class="archive-list"',page)
        self.assertIn('data-archive-more hidden',page)
        self.assertIn('data-archive-more-button aria-controls="archive-results"',page)
        self.assertIn('data-result-count aria-live="polite"',page)
        # Every published article remains present in initial HTML for users
        # without JavaScript; JS only hides the older entries visually.
        for article in self.articles:
            self.assertIn('href="/articles/'+article["slug"]+'/"',page)
        javascript=(ROOT/"assets/site.js").read_text(encoding="utf-8")
        self.assertIn('PAGE_SIZE = 18',javascript)
        self.assertIn('moreButton?.addEventListener',javascript)

    def test_extra_edition_picker_links_to_real_articles(self):
        import re
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        self.assertIn('data-pick-issue',home)
        self.assertIn('data-issue-link',home)
        match=re.search(r'data-issue-options="([^"]+)"',home)
        self.assertIsNotNone(match)
        choices=json.loads(html.unescape(match.group(1)))
        latest=sorted(self.articles,key=lambda a:(
            a.get("published_at") or a["source"]["published_at"],
            a["source"]["published_at"]
        ),reverse=True)[:40]
        self.assertEqual(len(choices),len(latest))
        self.assertEqual({a["slug"] for a in latest},
                         {item["url"].split("/")[2] for item in choices})
        self.assertLessEqual(len(choices),40)
        self.assertIn("今日の号外をめくる",home)

        # Do not grow the homepage's embedded interactive JSON indefinitely.
        from scripts.build import home as render_home
        synthetic=[{**self.articles[0],"slug":"demo-"+str(i)} for i in range(75)]
        large_markup=render_home(synthetic)
        large_options=re.search(r'data-issue-options="([^"]+)"',large_markup)
        self.assertIsNotNone(large_options)
        self.assertEqual(len(json.loads(html.unescape(large_options.group(1)))),40)

    def test_social_previews_use_first_party_jpeg_or_png(self):
        """X and Open Graph crawlers must not receive SVG or third-party photo URLs."""
        import re
        from scripts.build import social_image
        self.assertEqual(social_image(None),"/og/brand-launch-20261102-v3.png")
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

    def test_x_launch_uses_fresh_card_image_and_url(self):
        """Old X cards had a cached missing preview. The new share gets a new URL."""
        import binascii
        import struct
        import zlib
        from urllib.parse import unquote
        old_image=ROOT/"dist/og/brand.png"
        new_image=ROOT/"dist/og/brand-launch-20261102-v3.png"
        self.assertTrue(old_image.is_file())
        self.assertTrue(new_image.is_file())
        data=new_image.read_bytes()
        self.assertEqual(data[:8],b"\\x89PNG\\r\\n\\x1a\\n".decode("unicode_escape").encode("latin1"))
        self.assertLess(len(data),5*1024*1024)
        offset=8
        raw_idat=b""
        size=None
        while offset < len(data):
            chunk_len=struct.unpack_from(">I",data,offset)[0]
            kind=data[offset+4:offset+8]
            payload=data[offset+8:offset+8+chunk_len]
            crc=struct.unpack_from(">I",data,offset+8+chunk_len)[0]
            self.assertEqual(binascii.crc32(kind+payload)&0xffffffff,crc)
            if kind==b"IHDR":
                size=struct.unpack(">II",payload[:8])
            if kind==b"IDAT":
                raw_idat+=payload
            offset+=12+chunk_len
        self.assertEqual(size,(1200,630))
        self.assertEqual(len(zlib.decompress(raw_idat)),(1200*3+1)*630)
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        self.assertIn('content="https://veripsa.com/og/brand-launch-20261102-v3.png"',home)
        # The footer share intent passes a distinct URL, not the cached plain root.
        self.assertIn("src%3Dx-launch-20261102-v3",unquote(home))

    def test_reader_growth_and_editorial_transparency(self):
        import re
        index=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        self.assertIn('class="genre-explore"',index)
        self.assertIn('class="genre-links"',index)
        self.assertIn('https://x.com/intent/tweet?',index)
        self.assertNotIn('https://x.com/Veripsa',index)
        editorial=(ROOT/"dist/editorial/index.html").read_text(encoding="utf-8")
        for marker in ("生成AI", "創作", "広告・アフィリエイト", "訂正", "github.com/RollNuts/mihassei-news/issues"):
            self.assertIn(marker,editorial)
        self.assertIn('href="/editorial/"',index)
        about=(ROOT/"dist/about/index.html").read_text(encoding="utf-8")
        self.assertIn('href="/editorial/"',about)
        for a in self.articles:
            page=(ROOT/"dist/articles"/a["slug"]/"index.html").read_text(encoding="utf-8")
            self.assertIn('class="breadcrumbs"',page)
            self.assertIn('"@type": "BreadcrumbList"',page)
            self.assertIn('class="reader-next"',page)
            self.assertIn('class="reader-follow"',page)
            self.assertIn('class="reader-follow-x"',page)
            self.assertIn("https://x.com/intent/tweet?",page)
            self.assertNotIn('https://x.com/Veripsa',page)
            self.assertIn('href="/feed.xml"',page)
            match=re.search(r'<a href="(/articles/[^"]+|/archive/)" class="reader-next-title">',page)
            self.assertIsNotNone(match,a["slug"])
            self.assertNotEqual(match.group(1),"/articles/"+a["slug"]+"/")
            self.assertNotIn("pagead2.googlesyndication.com/pagead/js/adsbygoogle.js",page)

    def test_unique_article_png_cards(self):
        from scripts.build import social_image
        seen=set()
        for article in self.articles:
            image_path=social_image(article).split("?",1)[0]
            self.assertTrue(image_path.endswith((".png",".jpg")))
            self.assertNotIn(image_path,seen)
            seen.add(image_path)
            page=(ROOT/"dist/articles"/article["slug"]/"index.html").read_text(encoding="utf-8")
            self.assertIn('name="twitter:image" content="https://veripsa.com'+image_path+'"',page)
            img=ROOT/"dist"/image_path.lstrip("/")
            self.assertTrue(img.exists(),image_path)
            self.assertGreater(img.stat().st_size,1000)
            if image_path.endswith(".png"):
                self.assertEqual(img.read_bytes()[:8],bytes((137,80,78,71,13,10,26,10)))

    def test_opengraph_and_feed(self):
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        self.assertIn('name="twitter:card" content="summary_large_image"',home)
        self.assertIn('property="og:image" content="https://veripsa.com/og/brand-launch-20261102-v3.png"',home)
        self.assertIn('property="og:image:secure_url" content="https://veripsa.com/og/brand-launch-20261102-v3.png"',home)
        self.assertIn('name="twitter:image" content="https://veripsa.com/og/brand-launch-20261102-v3.png"',home)
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
