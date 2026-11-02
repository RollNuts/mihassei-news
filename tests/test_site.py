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
                  "sitemap.xml","issues/index.html","editorial/index.html","advertise/index.html","assets/style.css","assets/site.js",
                  "assets/article-extra.css","assets/newsroom.css","assets/editorial-polish.css","assets/engagement.js",
                  "assets/future-poll.css",
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

    def test_illustrations_reserve_fixed_aspect_ratio_before_loading(self):
        """Every editorial image carries intrinsic dimensions to avoid layout shift."""
        import re
        rendered=[ROOT/"dist/index.html",ROOT/"dist/archive/index.html"]
        rendered += [ROOT/"dist/articles"/a["slug"]/"index.html"
                     for a in self.articles]
        checked=0
        for path in rendered:
            markup=path.read_text(encoding="utf-8")
            for match in re.finditer(r'<img\b[^>]*>',markup):
                tag=match.group(0)
                if not any(c in tag for c in ('hero-image','story-thumb','article-visual')):
                    continue
                self.assertIn('width="1200"',tag,str(path))
                self.assertIn('height="675"',tag,str(path))
                self.assertIn('decoding="async"',tag,str(path))
                checked+=1
        self.assertGreaterEqual(checked,len(self.articles))

    def test_live_social_card_verification_uses_real_image_path(self):
        from scripts.build import social_image
        from scripts.check_live import verify_launch_card
        root_page=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        verify_launch_card(root_page,social_image(None))
        with self.assertRaises(RuntimeError):
            verify_launch_card('<html><head></head></html>',social_image(None))

    def test_public_sponsorship_contact_with_no_premature_ad_placement(self):
        import html
        commercial=(ROOT/"dist/advertise/index.html").read_text(encoding="utf-8")
        for item in ("広告・協賛のご案内", "フィクション", "広告", "PR",
                     "記事の文章", "掲載期間", "保証しません",
                     "一般に公開", "個人のメールアドレス",
                     "github.com/RollNuts/mihassei-news/issues/new?template=sponsorship.yml"):
            self.assertIn(item,html.unescape(commercial))
        for prohibited in ("adsbygoogle.js", "data-ad-client=", "広告を表示中"):
            self.assertNotIn(prohibited, commercial)
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        editorial=(ROOT/"dist/editorial/index.html").read_text(encoding="utf-8")
        self.assertIn('href="/advertise/"',home)
        self.assertIn('href="/advertise/"',editorial)
        sitemap=(ROOT/"dist/sitemap.xml").read_text(encoding="utf-8")
        self.assertIn("https://veripsa.com/advertise/",sitemap)
        template=(ROOT/".github/ISSUE_TEMPLATE/sponsorship.yml").read_text(encoding="utf-8")
        for token in ("公開される", "機密情報", "required: true", "public-name"):
            self.assertIn(token,template)

    def test_newest_articles_are_easy_to_find_before_interactive_sections(self):
        """Mobile readers see stories and categories before optional engagement widgets."""
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        hero=home.index('<section class="hero-grid">')
        latest=home.index('<section class="section-block">')
        genres=home.index('<section class="genre-explore"')
        random=home.index('<section class="special-edition"')
        discussions=home.index('<section class="reader-discussions"')
        about=home.index('<aside class="banner">')
        self.assertTrue(hero < latest < genres < random < discussions < about)
        self.assertIn("<h2>新着記事を読む</h2>",home)
        self.assertIn('data-issue-picker',home)
        self.assertIn('data-comment-discussions',home)
        self.assertIn('class="story-grid"',home)

    def test_homepage_does_not_repeat_lead_or_sidebar_news(self):
        """News grid fills with different headlines rather than showing the same story twice."""
        import re
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        lead=re.search(r'<h1><a href="/articles/([^/]+)/">',home)
        self.assertIsNotNone(lead)
        aside=home.split('<aside class="side-brief">',1)[1].split('</aside>',1)[0]
        sidebar=re.findall(r'<h3><a href="/articles/([^/]+)/">',aside)
        section=home.split('<section class="section-block">',1)[1].split('</section>',1)[0]
        cards=re.findall(r'<h3><a href="/articles/([^/]+)/">',section)
        from scripts.build import load
        # The JSON file is not necessarily in publication order.
        # Compare against the same actual-publication sorting used by the builder.
        published=load()
        self.assertEqual(lead.group(1),published[0]["slug"])
        self.assertEqual(sidebar,[a["slug"] for a in published[1:4]])
        self.assertEqual(cards,[a["slug"] for a in published[4:13]])
        shown=[lead.group(1),*sidebar,*cards]
        self.assertEqual(len(shown),len(set(shown)))
        self.assertEqual(len(shown),min(len(published),13))

    def test_ticker_is_swipeable_on_mobile_and_keyboard_accessible(self):
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        css=(ROOT/"dist/assets/editorial-polish.css").read_text(encoding="utf-8")
        self.assertIn('class="wire-viewport" tabindex="0" role="region"',home)
        self.assertIn("新着ニュースを横にスクロール",home)
        self.assertIn(".news-wire .wire-track:focus-within",css)
        self.assertIn(".news-wire .wire-viewport",css)
        self.assertIn("overflow-x:auto",css)
        self.assertIn(".news-wire .wire-copy{display:none}",css)
        self.assertIn("prefers-reduced-motion",css)
        self.assertEqual(css.count("{"),css.count("}"))

    def test_modern_readable_editorial_layout_without_paid_assets(self):
        """Preserve all pages while improving reading comfort and mobile touch UI."""
        styles=(ROOT/"dist/assets/editorial-polish.css").read_text(encoding="utf-8")
        self.assertEqual(styles.count("{"),styles.count("}"))
        for marker in (
            ".hero-grid", ".story-grid", ".story-card", ".article-body",
            ".article-header h1", ".reader-comments-form",
            ".nav-inner", ".source-context", ".footer",
            "@media(max-width:660px)", "@media(max-width:380px)",
            "prefers-reduced-motion", "focus-visible",
            "font-size:17px", "line-height:1.98", "min-height:44px",
        ):
            self.assertIn(marker,styles)
        for unsafe in ("@import", "fonts.googleapis.com", "display:none!important"):
            self.assertNotIn(unsafe,styles)
        for name in ("index.html","archive/index.html","about/index.html",
                     "editorial/index.html","advertise/index.html"):
            page=(ROOT/"dist"/name).read_text(encoding="utf-8")
            self.assertEqual(page.count('href="/assets/editorial-polish.css"'),1,name)
            self.assertIn('現実の話題から生まれる、5年後の創作ニュース。',page)
            self.assertLess(
                page.index('href="/assets/comments.css"'),
                page.index('href="/assets/editorial-polish.css"'),
            )
        for article in self.articles:
            path=ROOT/"dist/articles"/article["slug"]/"index.html"
            page=path.read_text(encoding="utf-8")
            self.assertIn('href="/assets/editorial-polish.css"',page)
            self.assertIn('class="article-body"',page)
            self.assertIn('class="reader-comments"',page)
            self.assertIn('class="future-poll"',page)

    def test_daily_issue_archives_are_real_and_discoverable(self):
        from scripts.build import daily_issues, modification_date
        groups=daily_issues(self.articles)
        self.assertTrue(groups,"The production edition must have a substantial day")
        index=(ROOT/"dist/issues/index.html").read_text(encoding="utf-8")
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        archive=(ROOT/"dist/archive/index.html").read_text(encoding="utf-8")
        sitemap=ET.parse(ROOT/"dist/sitemap.xml").getroot()
        namespace={"s":"http://www.sitemaps.org/schemas/sitemap/0.9"}
        locations={el.findtext("s:loc",namespaces=namespace):el.findtext("s:lastmod",namespaces=namespace)
                   for el in sitemap.findall("s:url",namespace)}
        self.assertIn("https://veripsa.com/issues/",locations)
        self.assertIn('href="/issues/"',home)
        self.assertIn('href="/issues/"',archive)
        self.assertIn('class="daily-issues-section"',home)
        for day,items in groups.items():
            url="https://veripsa.com/issues/"+day+"/"
            self.assertIn(url,locations)
            self.assertEqual(locations[url][:10],
                             max(modification_date(a) for a in items)[:10])
            self.assertIn('href="/issues/'+day+'/"',index)
            page=ROOT/"dist/issues"/day/"index.html"
            self.assertTrue(page.exists(),day)
            markup=page.read_text(encoding="utf-8")
            self.assertIn('<link rel="canonical" href="'+url+'"',markup)
            self.assertIn('datetime="'+day+'"',markup)
            for article in items:
                slug=article["slug"]
                self.assertIn('href="/articles/'+slug+'/"',markup)
                story=(ROOT/"dist/articles"/slug/"index.html").read_text(encoding="utf-8")
                self.assertIn('href="/issues/'+day+'/"',story)
        all_days={a["published_at"][:10] for a in self.articles}
        for day in all_days-set(groups):
            self.assertNotIn("https://veripsa.com/issues/"+day+"/",locations,
                             "Two-article days must not create thin indexed editions")

    def test_adsense_ownership_without_running_ads(self):
        line="google.com, pub-6754575901141756, DIRECT, f08c47fec0942fa0"
        self.assertEqual((ROOT/"dist/ads.txt").read_text(encoding="utf-8").strip(),line)
        html=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        self.assertIn('<meta name="google-adsense-account" content="ca-pub-6754575901141756">',html)
        self.assertNotIn("pagead2.googlesyndication.com/pagead/js/adsbygoogle.js",html)

    def test_continuation_metrics_ride_existing_visit_api_only(self):
        script=(ROOT/"assets/engagement.js").read_text(encoding="utf-8")
        worker=(ROOT/"src/worker.js").read_text(encoding="utf-8")
        metrics=(ROOT/"scripts/record_metrics.py").read_text(encoding="utf-8")
        self.assertIn("function precedingArticle()",script)
        self.assertIn('if(action==="visit" && fromArticle)payload.fromArticle=fromArticle;',script)
        self.assertIn('this.insertOnce("continued",previousArticle,visitor)',worker)
        self.assertIn('continuedReaders:this.count("continued:"+slug)',worker)
        self.assertIn('"continued_readers": item["continuedReaders"]',metrics)
        self.assertNotIn("/api/continuation",script,
                         "A separate endpoint would create unnecessary traffic")

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

    def test_future_verdict_poll_is_genuine_and_available_on_all_stories(self):
        css=(ROOT/"dist/assets/future-poll.css").read_text(encoding="utf-8")
        script=(ROOT/"dist/assets/engagement.js").read_text(encoding="utf-8")
        self.assertIn(".future-poll",css)
        self.assertIn('apiPost("vote"',script)
        self.assertIn('selectedPoll=result.vote',script)
        for item in self.articles:
            page=(ROOT/"dist/articles"/item["slug"]/"index.html").read_text(encoding="utf-8")
            self.assertIn('data-future-poll data-poll-article="'+item["slug"]+'"',page)
            self.assertIn('data-poll-choice="plausible"',page)
            self.assertIn('data-poll-choice="unlikely"',page)
            self.assertIn('data-poll-results hidden',page)
            self.assertIn('data-poll-status role="status"',page)
            self.assertIn('href="/assets/future-poll.css"',page)
            self.assertNotIn("50% / 50%",page)
        privacy=(ROOT/"dist/privacy/index.html").read_text(encoding="utf-8")
        self.assertIn("5年後の予想投票について",privacy)
        self.assertIn("個人の回答は公開しません",privacy)

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
        self.assertIn("参照元URL全体",privacy)
        self.assertIn("記事から次の記事への移動",privacy)
        self.assertIn("移動先の組み合わせ",privacy)
        self.assertIn("検索した言葉",privacy)
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
            from scripts.media_policy import usable_cached_photo
            if usable_cached_photo(a,root=ROOT):
                self.assertIn(a["photo"]["source"],html)
                self.assertIn(a["photo"]["credit"],html)
                self.assertIn(a["photo"]["alt"],html)
                self.assertIn('data-fallback=',html)
                self.assertIn("イメージ写真",html)
                self.assertIn('src="/photos/'+a["slug"]+'.jpg"',html)
            else:
                self.assertIn('src="/images/'+a["slug"]+'.svg"',html)
                self.assertIn("オリジナル表紙",html)
                if a.get("photo"):
                    self.assertNotIn(a["photo"]["source"],html)
                    self.assertNotIn(a["photo"]["credit"]+" / Unsplash",html)

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

    def test_latest_issue_is_dated_by_real_publication_not_the_clock(self):
        import re
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        latest=max(self.articles,key=lambda x:(
            x.get("published_at") or x["source"]["published_at"],
            x["source"]["published_at"]
        ))
        latest_date=latest["published_at"][:10]
        daily=sum(a.get("published_at","")[:10]==latest_date for a in self.articles)
        self.assertIn('data-edition-status data-last-edition="'+latest_date+'"',home)
        self.assertIn('data-edition-count="'+str(daily)+'"',home)
        self.assertIn('data-edition-title>最新の未発生ニュース</strong>',home)
        self.assertIn('data-edition-updated>最終発行：',home)
        self.assertIn("（現実時間）",home)
        self.assertIn("5年先の時計",home)
        self.assertNotIn("● LIVE",home)
        self.assertNotIn("本日の未発生ニュース",home,
                         "The no-JS fallback must not claim freshness")
        self.assertNotIn("今日の号外をめくる",home,
                         "Random archive articles are not necessarily from today")

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
        self.assertIn("号外をめくる",home)

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
        self.assertIn("src%3Dx-launch-20261102-v3",home)

    def test_image_review_gate_applies_to_cached_photos_and_ogp(self):
        """Cached images are not automatically editorially appropriate."""
        from scripts.media_policy import photo_is_approved, usable_cached_photo
        from scripts.build import social_image
        import xml.etree.ElementTree as ET
        approved=0
        rejected=0
        for article in self.articles:
            slug=article["slug"]
            svg=(ROOT/"dist/images"/(slug+".svg")).read_text(encoding="utf-8")
            tree=ET.fromstring(svg)
            self.assertEqual(tree.attrib["viewBox"],"0 0 1200 675")
            self.assertIn("EDITORIAL ILLUSTRATION",svg)
            self.assertIn("MIHASSEI NEWS",svg)
            self.assertIn("創作",svg)
            self.assertTrue(tree.findall("{http://www.w3.org/2000/svg}text"))
            self.assertNotIn("<image",svg)
            self.assertNotIn("<script",svg)
            page=(ROOT/"dist/articles"/slug/"index.html").read_text(encoding="utf-8")
            if usable_cached_photo(article,root=ROOT):
                approved+=1
                self.assertTrue(photo_is_approved(article,root=ROOT))
                self.assertEqual(social_image(article),"/photos/"+slug+".jpg")
                self.assertIn('src="/photos/'+slug+'.jpg"',page)
            else:
                rejected+=1
                self.assertEqual(social_image(article),"/og/articles/"+slug+".png")
                self.assertIn('src="/images/'+slug+'.svg"',page)
                self.assertTrue((ROOT/"dist/og/articles"/(slug+".png")).is_file())
        self.assertGreaterEqual(rejected,1)
        self.assertGreaterEqual(approved,1)

    def test_no_photo_articles_have_distinct_offline_png_social_cards(self):
        """Future articles automatically get meaningful, non-identical raster cards."""
        from scripts.social_cards import render_social_card
        from scripts.build import social_image
        import struct
        import tempfile
        no_photo=[a for a in self.articles
                  if not (ROOT/"assets/photos"/(a["slug"]+".jpg")).exists()]
        self.assertGreaterEqual(len(no_photo),1,"Expected an article needing fallback preview")
        images=set()
        for a in no_photo:
            path=ROOT/"dist"/"og"/"articles"/(a["slug"]+".png")
            self.assertTrue(path.exists(),a["slug"])
            data=path.read_bytes()
            self.assertEqual(data[:8],b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II",data[16:24]),(1200,630))
            self.assertGreater(len(data),1500)
            self.assertLess(len(data),200000,"Social card should stay small for free hosting")
            self.assertNotIn(data,images,"Each article should have a unique preview")
            images.add(data)
            self.assertEqual(social_image(a),"/og/articles/"+a["slug"]+".png")
            with tempfile.TemporaryDirectory() as directory:
                rerender=Path(directory)/"check.png"
                render_social_card(a,rerender)
                self.assertEqual(data,rerender.read_bytes(),"Cards should render deterministically")
            page=(ROOT/"dist/articles"/a["slug"]/"index.html").read_text(encoding="utf-8")
            self.assertIn('property="og:image" content="https://veripsa.com/og/articles/'+a["slug"]+'.png"',page)
            self.assertIn('name="twitter:image" content="https://veripsa.com/og/articles/'+a["slug"]+'.png"',page)
        self.assertEqual(sum(1 for _ in images),len(no_photo))

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
