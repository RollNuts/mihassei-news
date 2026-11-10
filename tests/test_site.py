"""Production-ready checks, no external packages or API access."""
import json
import html
import subprocess
import unittest
from pathlib import Path
from datetime import datetime
from xml.etree import ElementTree as ET
from tests.site_build_fixture import ensure_site_build

ROOT=Path(__file__).resolve().parents[1]

class SiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_site_build()
        cls.articles=json.loads((ROOT/"content/articles.json").read_text(encoding="utf-8"))

    def test_core_files(self):
        self.assertGreaterEqual(len(self.articles),5)
        for p in ["index.html","archive/index.html","about/index.html","feed.xml",
                  "sitemap.xml","issues/index.html","editorial/index.html","advertise/index.html","assets/style.css","assets/site.js",
                  "assets/article-extra.css","assets/newsroom.css","assets/editorial-polish.css","assets/reader-first.css","assets/newspaper.css","assets/engagement.js",
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

    def test_dist_contains_only_stock_photos_selected_by_current_policy(self):
        """Unused cached candidates must not inflate or leak into the deployment."""
        from scripts.build import load, uses_stock_photo
        expected = {a["slug"] + ".jpg" for a in load() if uses_stock_photo(a)}
        folder = ROOT / "dist" / "photos"
        actual = {p.name for p in folder.glob("*.jpg")} if folder.exists() else set()
        self.assertEqual(actual, expected)

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
                     "記事を書き換えません", "掲載期間", "料金",
                     "投稿・返信は公開されます", "個人情報や非公開の契約条件",
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
        topics=home.index('<section class="topic-explore genre-explore"')
        genres=home.index('<section class="genre-explore"')
        serials=home.index('<section class="serials-feature"')
        popular=home.index('<section class="readers-choice"')
        timeline=home.index('<section class="press-timeline"')
        random=home.index('<section class="special-edition"')
        extras=home.index('<details class="extras-shelf">')
        discussions=home.index('<section class="reader-discussions"')
        self.assertTrue(hero < latest < popular < timeline < topics < genres < serials < extras < random < discussions)
        self.assertIn('<aside class="side-brief"><div class="side-title"><h2>新着記事</h2>',home)
        self.assertIn('<section class="section-block"><div class="section-head"><h2>さらに読む</h2>',home)
        self.assertNotIn("<h2>新着の見出し</h2>",home)
        self.assertIn("<h2>話題から読む</h2>",home)
        self.assertIn('class="story-tags story-tags--home"',home)
        self.assertNotIn('<aside class="banner">',home)
        self.assertNotIn("この新聞の読み方",home)
        self.assertIn('data-issue-picker',home)
        self.assertIn('data-comment-discussions',home)
        self.assertIn('class="story-grid"',home)

    def test_activity_rail_shows_real_publication_history_without_repeats(self):
        """The interactive discovery rail must not duplicate the main story grid."""
        import re
        from scripts.build import load, publication_date
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        styles=(ROOT/"dist/assets/editorial-polish.css").read_text(encoding="utf-8")
        rail=home.split('<section class="press-timeline"',1)[1].split('</section>',1)[0]
        links=re.findall(r'<li><a href="/articles/([^/]+)/">',rail)
        published=load()
        self.assertEqual(len(links),len(set(links)))
        hero=re.search(r'<h1><a href="/articles/([^/]+)/">',home).group(1)
        above=home.split('<section class="hero-grid">',1)[1].split(
            '<section class="press-timeline"',1)[0]
        earlier=set(re.findall(r'href="/articles/([^/]+)/"',above))
        playlist=re.search(r'data-broadcast-playlist="([^"]+)"',above)
        if playlist:
            earlier.update(item["slug"] for item in json.loads(html.unescape(playlist.group(1))))
        available={article["slug"] for article in published}-earlier
        self.assertEqual(len(links),min(3,len(available)))
        self.assertNotIn(hero,links)
        self.assertTrue(earlier.isdisjoint(links))
        for slug in links:
            article=next(a for a in published if a["slug"]==slug)
            self.assertIn('datetime="'+publication_date(article)+'"',rail)
        self.assertIn('scroll-snap-type:x proximity',styles)
        self.assertIn('.index-main>.special-edition',styles)
        self.assertEqual(home.count('data-issue-picker data-issue-options='),1)
        self.assertNotIn('<div class="news-wire"',home)

    def test_activity_rail_excludes_all_video_choices_when_a_new_clip_leads(self):
        """A newly delivered clip must not repeat a story in the discovery rail."""
        import re
        from unittest.mock import patch
        from scripts import build
        articles=build.load()
        playlist=build.broadcast_playlist(articles)
        self.assertGreater(len(playlist),1)
        broadcast_slugs={item["slug"] for item in playlist}
        for offset in range(len(playlist)):
            reordered=playlist[offset:]+playlist[:offset]
            with self.subTest(first=reordered[0]["slug"]), \
                 patch.object(build,"broadcast_playlist",return_value=reordered):
                home=build.home(articles)
                rail=home.split('<section class="press-timeline"',1)[1].split('</section>',1)[0]
                links=re.findall(r'<li><a href="/articles/([^/]+)/">',rail)
                self.assertEqual(len(links),3,"fill the rail with different published stories")
                self.assertTrue(broadcast_slugs.isdisjoint(links),
                                "all selectable videos are already shown above the rail")

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
        from scripts.build import load, editorial_lead
        published=load()
        chosen=editorial_lead(published)
        remaining=[a for a in published if a["slug"]!=chosen["slug"]]
        self.assertEqual(lead.group(1),chosen["slug"])
        lead_markup=home.split('<article class="lead">',1)[1].split('</article>',1)[0]
        if chosen.get("tags"):
            from scripts.build import tag_links
            self.assertIn(tag_links(chosen,compact=True),lead_markup)
        self.assertEqual(sidebar,[a["slug"] for a in remaining[:3]])
        expected=[a["slug"] for a in remaining[3:7]]
        self.assertEqual(cards,expected)
        shown=[lead.group(1),*sidebar,*cards]
        self.assertEqual(len(shown),len(set(shown)))
        self.assertEqual(len(shown),min(len(published),8))

    def test_sidebar_emphasizes_recent_headlines_not_duplicate_actions(self):
        """Homepage headline rail stays compact on mobile and keeps real dates."""
        from scripts.build import load, editorial_lead, category_meta
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        aside=home.split('<aside class="side-brief">',1)[1].split('</aside>',1)[0]
        remaining=[a for a in load() if a["slug"] != editorial_lead(load())["slug"]]
        self.assertEqual(aside.count('<article class="brief-card">'),min(3,len(remaining)))
        self.assertNotIn('class="engagement-actions',aside)
        self.assertNotIn('class="fiction-dateline"',aside)
        for article in remaining[:3]:
            self.assertIn(category_meta(article,compact=True),aside)
        self.assertIn('.story-meta--compact', (ROOT/"dist/assets/editorial-polish.css").read_text(encoding="utf-8"))

    def test_article_previews_are_readable_without_duplicate_action_strips(self):
        """The compact list encourages reading; comments, likes and shares stay in stories."""
        from scripts.build import card, story
        article=self.articles[0]
        href='/articles/'+article["slug"]+'/'
        for preview in (card(article),card(article,archive=True)):
            self.assertEqual(preview.count('href="'+href+'"'),2)
            self.assertRegex(preview, r'class="story-image-link(?: story-image-link--generated)?"')
            self.assertIn('class="story-thumb"',preview)
            self.assertIn('class="story-meta story-meta--compact"',preview)
            self.assertIn('<h3><a href="'+href+'">',preview)
            self.assertNotIn('class="engagement-actions',preview)
            self.assertNotIn('class="story-footer"',preview)
            self.assertNotIn('data-like-slug=',preview)
        self.assertIn('data-archive-card',card(article,archive=True))
        full=story(article,self.articles)
        self.assertIn('class="engagement-actions"',full)
        self.assertIn('data-like-slug="'+article["slug"]+'"',full)
        self.assertIn('href="'+href+'#reader-comments"',full)
        styles=(ROOT/"dist/assets/editorial-polish.css").read_text(encoding="utf-8")
        self.assertIn('Mobile editorial list: one compact preview per story',styles)
        self.assertIn('grid-template-areas:"image meta" "image headline" "image summary"',styles)
        self.assertIn('-webkit-line-clamp:2',styles)
        self.assertIn('@media(max-width:360px)',styles)

    def test_homepage_focus_and_secondary_content_is_accessible(self):
        """No duplicate ticker; optional features remain accessible on demand."""
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        css=(ROOT/"dist/assets/editorial-polish.css").read_text(encoding="utf-8")
        self.assertNotIn('<div class="news-wire"',home)
        self.assertIn('<details class="extras-shelf">',home)
        self.assertIn("今日の未発生新聞",home)
        self.assertIn("号外・コメント",home)
        self.assertTrue(home.index('class="daily-issues-section"') <
                        home.index('<details class="extras-shelf">'))
        self.assertIn('.extras-shelf>summary:focus-visible',css)
        self.assertTrue(home.index('<section class="genre-explore"') <
                        home.index('<details class="extras-shelf">'))
        self.assertEqual(css.count("{"),css.count("}"))

    def test_homepage_motion_is_fresh_truthful_and_optional(self):
        """Movement uses real publication dates and preserves no-JS readability."""
        from scripts.build import load, publication_date
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        css=(ROOT/"dist/assets/editorial-polish.css").read_text(encoding="utf-8")
        js=(ROOT/"dist/assets/engagement.js").read_text(encoding="utf-8")
        latest=load()[0]
        self.assertIn('data-return-update',home)
        self.assertIn('data-latest-published="'+publication_date(latest)+'"',home)
        self.assertIn('href="/articles/'+latest["slug"]+'/" hidden>新着記事を読む',home)
        # No extra ticker, fake impression counts, or new API dependency.
        self.assertNotIn('<div class="news-wire"',home)
        self.assertIn('mihassei-latest-published-v1',js)
        self.assertIn('window.IntersectionObserver',js)
        self.assertIn('returnNotice.hidden=false',js)
        self.assertIn('className="reading-progress"',js)
        self.assertIn('@media(prefers-reduced-motion:no-preference)',css)
        self.assertIn('@media(prefers-reduced-motion:reduce)',css)
        self.assertIn('.reading-progress{',css)

    def test_homepage_lead_always_follows_newest_publication_not_curated_pin(self):
        """A new edition must change the hero even if an older story is curated."""
        from scripts.build import editorial_lead, publication_date, home
        original=next(a for a in self.articles
                      if a["slug"]=="family-dinner-gacha-243-rejections")
        older={**original,"published_at":"2026-11-03T09:35:30+09:00"}
        newer={**self.articles[0],"slug":"synthetic-newest",
               "published_at":"2026-11-03T12:00:00+09:00"}
        newest_next={**self.articles[0],"slug":"synthetic-next-edition",
                     "published_at":"2026-11-03T15:30:00+09:00"}
        # The selection must not depend on the input order, the invented
        # 2031 dateline, source dates, or a revised older article.
        self.assertEqual(editorial_lead([older,newer])["slug"],newer["slug"])
        self.assertEqual(editorial_lead([newer,older])["slug"],newer["slug"])
        self.assertEqual(editorial_lead([older,newer,newest_next])["slug"],
                         newest_next["slug"])
        revised={**older,"modified_at":"2026-11-03T23:59:00+09:00"}
        self.assertEqual(editorial_lead([revised,newer])["slug"],newer["slug"])
        self.assertIsNone(editorial_lead([]))
        self.assertEqual(editorial_lead(self.articles)["slug"],self.articles[0]["slug"])
        # The front-page headline really uses the same article as the
        # newest publication, while measured recommendations remain separate.
        published=home(self.articles)
        self.assertIn('<h1><a href="/articles/'+self.articles[0]["slug"]+'/">',published)
        self.assertIn('data-popular-section',published)
        self.assertNotIn('class="starter-editorial"',published)
        self.assertIn('data-last-edition="'+publication_date(self.articles[0])[:10]+'"',
                      published)

    def test_homepage_is_fresh_even_if_articles_arrive_out_of_order(self):
        """A content ingestion reorder must never pin yesterday's lead."""
        from scripts.build import home, publication_date
        import re
        newest=self.articles[0]
        normal=home(self.articles)
        reversed_page=home(list(reversed(self.articles)))
        newest_link='<h1><a href="/articles/'+newest["slug"]+'/">'
        self.assertIn(newest_link,normal)
        self.assertIn(newest_link,reversed_page)
        self.assertEqual(normal,reversed_page)
        self.assertIn('data-last-edition="'+publication_date(newest)[:10]+'"',
                      reversed_page)
        self.assertIn('data-latest-published="'+publication_date(newest)+'"',
                      reversed_page)
        # The front-page article and its realtime status cannot disagree.
        match=re.search(r'<h1><a href="/articles/([^/]+)/">',reversed_page)
        self.assertEqual(match.group(1),newest["slug"])

    def test_mobile_search_and_actions_have_usable_touch_targets(self):
        """Avoid iOS auto-zoom on search inputs without disabling native pinch zoom."""
        styles=(ROOT/"dist/assets/editorial-polish.css").read_text(encoding="utf-8")
        start=styles.index("Mobile ergonomics: preserve pinch zoom")
        block=styles[start:]
        self.assertIn("@media(max-width:660px)",block)
        for part in (".search-input,", ".decade-select{", "font-size:16px",
                     "min-height:48px", ".filter-button,", ".genre-links a,",
                     ".engagement-actions.compact button,", ".footer-nav a",
                     ".article-end a{"):
            self.assertIn(part,block)
        self.assertNotIn("user-scalable=no",styles)
        self.assertNotIn("maximum-scale=1",styles)
        self.assertEqual(styles.count("{"),styles.count("}"))
        homepage=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        archive=(ROOT/"dist/archive/index.html").read_text(encoding="utf-8")
        self.assertIn('name="viewport" content="width=device-width,initial-scale=1"',homepage)
        self.assertIn('class="search-input"',archive)
        self.assertIn('class="decade-select"',archive)

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
        from scripts.build import bundled_stylesheets, stylesheet_url, SITE_STYLESHEETS
        import re
        import hashlib
        stylesheet=(ROOT/"dist/assets/newspaper.css").read_text(encoding="utf-8")
        self.assertEqual(stylesheet, bundled_stylesheets())
        self.assertTrue(stylesheet.startswith('@charset "UTF-8";'))
        self.assertEqual(stylesheet.count('@charset "UTF-8";'),1)
        self.assertEqual(stylesheet.count("/* SOURCE "),len(SITE_STYLESHEETS))
        self.assertEqual(stylesheet_url(),
                         "/assets/newspaper.css?v="+hashlib.sha256(stylesheet.encode("utf-8")).hexdigest()[:12])
        sources=["/* SOURCE "+name+" */" for name in SITE_STYLESHEETS]
        self.assertEqual([stylesheet.index(marker) for marker in sources],
                         sorted(stylesheet.index(marker) for marker in sources))
        self.assertIn(".reader-comments",stylesheet)
        self.assertIn(".future-poll",stylesheet)
        self.assertIn(".serial-card",stylesheet)
        self.assertIn(".article-body",stylesheet)
        pages=("index.html","archive/index.html","about/index.html",
               "editorial/index.html","advertise/index.html")
        pages += tuple("articles/"+a["slug"]+"/index.html" for a in self.articles)
        for name in pages:
            page=(ROOT/"dist"/name).read_text(encoding="utf-8")
            self.assertEqual(page.count('rel="stylesheet"'),1,name)
            self.assertIn('href="'+stylesheet_url()+'"',page)
            self.assertNotIn('href="/assets/editorial-polish.css"',page)
            self.assertIn('まだ起きていないニュースを、真顔で。',page)
        for article in self.articles:
            page=(ROOT/"dist/articles"/article["slug"]/"index.html").read_text(encoding="utf-8")
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

    def test_reader_discovery_does_not_fake_popularity(self):
        import re
        from scripts.build import home as generate_home
        markup=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        self.assertIn('class="readers-choice" data-popular-section',markup)
        self.assertIn('data-popular-list',markup)
        self.assertIn('aria-labelledby="readers-choice-title" hidden',markup)
        self.assertIn('data-popular-period>過去7日間のPV順',markup)
        self.assertIn('id="readers-choice-title">よく読まれている記事',markup)
        self.assertEqual(markup.count('data-popular-section'),1)
        section=markup.split('<section class="readers-choice"',1)[1].split('</section>',1)[0]
        self.assertNotIn('<li',section,"Static markup cannot invent ranks or PVs")
        self.assertNotIn('ブラウザ',section)
        m=re.search(r'data-popular-options="([^"]+)"',markup)
        self.assertIsNotNone(m)
        candidates=json.loads(html.unescape(m.group(1)))
        self.assertEqual([x["slug"] for x in candidates],
                         [a["slug"] for a in sorted(
                             self.articles,
                             key=lambda a:(a.get("published_at") or a["source"]["published_at"],
                                           a["source"]["published_at"]),
                             reverse=True)])
        self.assertEqual(len(candidates),len(set(x["slug"] for x in candidates)))
        self.assertEqual(len(candidates),len(self.articles))
        synthetic=[{**self.articles[0],"slug":"sample-"+str(i)}
                   for i in range(25)]
        sample=generate_home(synthetic)
        raw=re.search(r'data-popular-options="([^"]+)"',sample)
        self.assertIsNotNone(raw)
        self.assertEqual(len(json.loads(html.unescape(raw.group(1)))),25)
        script=(ROOT/"assets/engagement.js").read_text(encoding="utf-8")
        self.assertIn("const popularCandidates=",script.replace("let popularCandidates=","const popularCandidates="))
        self.assertIn("last7DaysPageViews",script)
        self.assertNotIn("readers>=5",script)
        self.assertIn("popularSection.hidden=false",script)
        self.assertIn("popularList.replaceChildren",script)
        self.assertIn("showPopular(data,pvHealthy)",script)
        self.assertNotIn("innerHTML=",script)

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

    def test_site_has_real_pv_without_decorative_fake_clock(self):
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        self.assertNotIn("data-future-clock",home)
        self.assertNotIn("紙面の架空時計",home)
        self.assertIn("data-observer-widget",home)
        self.assertIn("data-pv-widget",home)
        self.assertIn("今日のPV",home)
        self.assertIn("昨日のPV",home)
        self.assertIn("data-site-pv-week",home)
        self.assertIn("data-pv-tracking-note",home)
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
            from scripts.editorial_copy import source_reader_copy
            self.assertIn(source_reader_copy(a["source"]["topic"]),text)
            self.assertIn("創作ニュース",text)
            self.assertIn('data-article-pv="'+a["slug"]+'"',text)
            self.assertIn("data-like-slug",text)
            self.assertIn("x.com/intent/tweet",text)
            self.assertIn("未発生新聞",text)
            self.assertIn("%23%E6%9C%AA%E7%99%BA%E7%94%9F%E6%96%B0%E8%81%9E",text)
            self.assertIn('data-like-count="'+a["slug"]+'"',text)
            self.assertNotIn('data-like-count="{slug}"',text)
            self.assertNotIn("Source: Unknown",text)
            published=datetime.fromisoformat(a["published_at"])
            fiction=datetime.fromisoformat(a["fiction_at"])
            self.assertEqual(fiction, published.replace(year=published.year+5))

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
            self.assertIn('href="/assets/newspaper.css?v=',page)
            self.assertNotIn("50% / 50%",page)
        privacy=(ROOT/"dist/privacy/index.html").read_text(encoding="utf-8")
        self.assertIn("<h2>投票</h2>",privacy)
        self.assertIn("個人の回答は公開しません",privacy)

    def test_reader_comments_are_present_and_privacy_is_explained(self):
        for article in self.articles:
            page=ROOT/"dist/articles"/article["slug"]/"index.html"
            text=page.read_text(encoding="utf-8")
            self.assertIn('data-comments data-article="'+article["slug"]+'"',text)
            self.assertIn("reader-comments-form",text)
            self.assertIn("/assets/comments.js",text)
            self.assertIn("/assets/newspaper.css?v=",text)
            self.assertIn('name="body"',text)
            self.assertIn("問題", (ROOT/"assets/comments.js").read_text(encoding="utf-8"))
        privacy=(ROOT/"dist/privacy/index.html").read_text(encoding="utf-8")
        self.assertIn("<h2>コメント</h2>",privacy)
        self.assertIn("参照元URL全体",privacy)
        self.assertIn("記事から別の記事へ進んだブラウザ数",privacy)
        self.assertIn("移動先との組み合わせ",privacy)
        self.assertIn("検索語",privacy)
        self.assertIn("投稿日時を公開",privacy)

    def test_genuine_reader_participation_discovery(self):
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        self.assertIn('data-comment-discussions',home)
        self.assertIn('data-comment-discussion-options=',home)
        self.assertIn('data-discussion-list',home)
        from scripts.build import asset_url
        self.assertIn('href="' + asset_url("comment-activity.js") + '"',
                      home.replace('src=', 'href='))
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
        self.assertIn("IntersectionObserver",highlights)
        self.assertIn("rootMargin:'600px'",highlights)
        self.assertIn("Date.now()+600000",highlights)
        self.assertNotIn("Date.now()+90000",highlights)
        self.assertNotIn(".innerHTML=",highlights)

    def test_real_publication_time_is_not_fictional_dateline(self):
        for a in self.articles:
            page=(ROOT/"dist/articles"/a["slug"]/"index.html").read_text(encoding="utf-8")
            expected=a.get("published_at") or a["source"]["published_at"][:10]
            self.assertIn('"datePublished": "'+expected+'"',page)
            self.assertNotIn('"datePublished": "'+a["fiction_at"]+'"',page)
            self.assertIn('"@type": "BlogPosting"',page)

    def test_visible_dates_and_social_metadata_disambiguate_fiction(self):
        """Every card clearly separates real publication from future story time."""
        from scripts.build import category_meta, publication_date
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        archive=(ROOT/"dist/archive/index.html").read_text(encoding="utf-8")
        css=(ROOT/"dist/assets/editorial-polish.css").read_text(encoding="utf-8")
        self.assertIn('class="fiction-mark">創作報道',home)
        self.assertNotIn("この新聞の読み方",home)
        for selector in (".story-meta .real-published-at",
                         ".story-meta .fiction-dateline"):
            self.assertIn(selector,css)
        for article in self.articles:
            published=publication_date(article)
            fictional=article["fiction_at"]
            actual_tag='class="real-published-at" datetime="'+published+'"'
            fictional_tag='class="fiction-dateline" datetime="'+fictional+'"'
            markup=category_meta(article)
            self.assertIn(actual_tag,markup)
            self.assertIn(fictional_tag,markup)
            self.assertIn("公開 ",markup)
            self.assertIn("紙面 ",markup)
            compact_markup=category_meta(article, compact=True)
            self.assertIn('class="story-meta story-meta--compact"',compact_markup)
            self.assertIn(actual_tag,compact_markup)
            self.assertIn('class="category">'+article["category"]+'</span>',compact_markup)
            self.assertNotIn(fictional_tag,compact_markup)
            self.assertNotIn('class="ticker"',compact_markup)
            self.assertIn(compact_markup,archive)
            self.assertNotIn(fictional_tag,archive)
            page=(ROOT/"dist/articles"/article["slug"]/"index.html").read_text(encoding="utf-8")
            self.assertIn(actual_tag,page)
            self.assertIn(fictional_tag,page)
            self.assertIn('property="article:published_time" content="'+published+'"',page)
            self.assertIn('property="article:section" content="'+article["category"]+'"',page)
            self.assertNotIn('property="article:published_time" content="'+fictional+'"',page)
        legacy={**self.articles[0], "published_at": None}
        legacy_meta=category_meta(legacy)
        self.assertIn('datetime="'+legacy["source"]["published_at"][:10]+'"',legacy_meta)
        self.assertIn("公開 ",legacy_meta)
        self.assertNotIn("公開 "+legacy["source"]["published_at"][:4]+"/11/2 00:00",legacy_meta)

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
            from scripts.build import uses_stock_photo
            from scripts.visual_review import reviewed_generated_scene
            if reviewed_generated_scene(a,root=ROOT):
                from scripts.generated_photos import generated_url
                self.assertIn('src="'+generated_url(a,root=ROOT)+'"',html)
                self.assertIn('data-generated-scene="true"',html)
                self.assertIn("再現イメージ（合成）",html)
                self.assertIn("<figcaption>再現イメージ（合成）",html)
                self.assertIn('property="og:image:type" content="image/jpeg"',html)
            elif uses_stock_photo(a):
                self.assertIn(a["photo"]["source"],html)
                self.assertIn(a["photo"]["credit"],html)
                self.assertIn(a["photo"]["alt"],html)
                self.assertIn('data-fallback=',html)
                self.assertIn("イメージ写真",html)
                self.assertIn('src="/photos/'+a["slug"]+'.jpg"',html)
            else:
                self.assertIn('src="/images/'+a["slug"]+'.svg"',html)
                self.assertIn("創作イラスト",html)
                if a.get("photo"):
                    self.assertNotIn(a["photo"]["source"],html)
                    self.assertNotIn(a["photo"]["credit"]+" / Unsplash",html)

    def test_newest_publication_controls_feed_while_editor_controls_lead(self):
        import re
        from scripts.build import load, editorial_lead
        home=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        lead=re.search(r'<h1><a href="/articles/([^/]+)/">',home)
        self.assertIsNotNone(lead)
        published=load()
        newest=published[0]
        self.assertEqual(lead.group(1),editorial_lead(published)["slug"])
        feed=ET.parse(ROOT/"dist/feed.xml")
        first=feed.find("./channel/item/link")
        self.assertIsNotNone(first)
        self.assertEqual(first.text,"https://veripsa.com/articles/"+newest["slug"]+"/")

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
        self.assertIn('data-edition-title>最新記事</strong>',home)
        self.assertIn('data-edition-updated>更新：',home)
        self.assertIn('data-last-edition="'+latest_date+'"',home)
        self.assertNotIn("紙面の架空時計",home)
        self.assertNotIn("● LIVE",home)
        self.assertNotIn("本日の記事",home,
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
        self.assertIn("ランダムに1本選ぶ",home)

        # Do not grow the homepage's embedded interactive JSON indefinitely.
        from scripts.build import home as render_home
        synthetic=[{**self.articles[0],"slug":"demo-"+str(i)} for i in range(75)]
        large_markup=render_home(synthetic)
        large_options=re.search(r'data-issue-options="([^"]+)"',large_markup)
        self.assertIsNotNone(large_options)
        self.assertEqual(len(json.loads(html.unescape(large_options.group(1)))),40)

    def test_topic_feeds_have_only_real_matching_stories(self):
        from email.utils import parsedate_to_datetime
        from scripts.build import (load, CATEGORIES, CATEGORY_PATHS, SITE,
                                   category_url, category_feed_url, social_image,
                                   curated_categories, generate_rss)
        from scripts.build import uses_stock_photo
        from scripts.visual_review import reviewed_generated_scene
        articles=load()
        ns={"media":"http://search.yahoo.com/mrss/",
            "atom":"http://www.w3.org/2005/Atom"}
        populated=[cat for cat in CATEGORIES[1:]
                   if any(a["category"]==cat for a in articles)]
        created={p.name for p in (ROOT/"dist/feeds").glob("*.xml")
                 if not p.name.startswith("series-")}
        expected={CATEGORY_PATHS[cat]+".xml" for cat in populated}
        self.assertEqual(created,expected)
        homepage=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        archive=(ROOT/"dist/archive/index.html").read_text(encoding="utf-8")
        self.assertIn('href="/archive/#category-feeds"',homepage)
        self.assertIn('id="category-feeds"',archive)
        for cat in populated:
            feed=category_feed_url(cat)
            self.assertIn('href="'+feed+'" type="application/rss+xml"',archive)
            channel=ET.parse(ROOT/"dist"/feed.lstrip("/")).getroot().find("channel")
            self.assertIn(cat,channel.findtext("title"))
            self.assertIn("フィクション",channel.findtext("description"))
            self.assertEqual(channel.findtext("language"),"ja")
            self.assertEqual(channel.find("atom:link",ns).get("href"),SITE+feed)
            expected_articles=[a for a in articles if a["category"]==cat][:30]
            entries=channel.findall("item")
            self.assertEqual(len(entries),len(expected_articles))
            for item,a in zip(entries,expected_articles):
                url=SITE+"/articles/"+a["slug"]+"/"
                self.assertEqual(item.findtext("link"),url)
                self.assertEqual(item.findtext("guid"),url)
                self.assertIn("創作",item.findtext("description"))
                posted=a.get("published_at") or a["source"]["published_at"][:10]
                if "T" in posted:
                    self.assertEqual(
                        parsedate_to_datetime(item.findtext("pubDate")).isoformat(),
                        datetime.fromisoformat(posted).isoformat())
                self.assertNotIn(a["fiction_at"][:4],item.findtext("pubDate") or "")
                thumbnail=item.find("media:thumbnail",ns)
                image=item.find("media:content",ns)
                self.assertIsNotNone(thumbnail)
                self.assertIsNotNone(image)
                self.assertEqual(thumbnail.get("url"),SITE+social_image(a))
                self.assertEqual(image.get("url"),SITE+social_image(a))
                mime="image/jpeg" if (reviewed_generated_scene(a,root=ROOT) or uses_stock_photo(a)) else "image/png"
                self.assertEqual(image.get("type"),mime)
            if cat in curated_categories(articles):
                category=(ROOT/"dist"/category_url(cat).lstrip("/")/"index.html")
                page=category.read_text(encoding="utf-8")
                self.assertIn('href="'+feed+'" type="application/rss+xml"',page)
                self.assertIn('title="'+cat+'の記事 RSS"',page)
        main=ET.parse(ROOT/"dist/feed.xml").getroot()
        self.assertEqual(len(main.findall("./channel/item")),min(90,len(articles)))
        self.assertEqual(main.find("channel").find("atom:link",ns).get("href"),
                         SITE+"/feed.xml")

    def test_topic_rss_caps_growth_without_extra_api_requests(self):
        from scripts.build import generate_rss
        a=self.articles[0]
        result=generate_rss([a]*65,"test","創作ニュース",
                            "https://veripsa.com/","/feeds/test.xml")
        self.assertEqual(len(ET.fromstring(result).findall("./channel/item")),30)

    def test_rss_contains_licensed_first_party_preview_images(self):
        """Feed clients can find safe image previews with the Media RSS standard."""
        from scripts.build import social_image, uses_stock_photo
        from scripts.visual_review import reviewed_generated_scene
        namespace={"media":"http://search.yahoo.com/mrss/"}
        root=ET.parse(ROOT/"dist/feed.xml").getroot()
        entries=root.findall("./channel/item")
        self.assertEqual(len(entries),min(90,len(self.articles)))
        by_url={n.findtext("link"):n for n in entries}
        for a in self.articles[:90]:
            url="https://veripsa.com/articles/"+a["slug"]+"/"
            self.assertIn(url,by_url)
            item=by_url[url]
            thumbnail=item.find("media:thumbnail",namespace)
            full=item.find("media:content",namespace)
            self.assertIsNotNone(thumbnail,a["slug"])
            self.assertIsNotNone(full,a["slug"])
            expected="https://veripsa.com"+social_image(a)
            self.assertEqual(thumbnail.get("url"),expected)
            self.assertEqual(full.get("url"),expected)
            self.assertEqual(full.get("medium"),"image")
            generated=reviewed_generated_scene(a,root=ROOT)
            photo=not generated and uses_stock_photo(a)
            self.assertEqual(full.get("type"),"image/jpeg" if (photo or generated) else "image/png")
            credit=item.find("media:credit",namespace)
            if photo:
                self.assertIsNotNone(credit)
                self.assertEqual(credit.text,a["photo"]["credit"])
            else:
                self.assertIsNone(credit)
                self.assertEqual(thumbnail.get("width"),"1200")
                from scripts.social_cards import reviewed_scene_version
                self.assertEqual(thumbnail.get("height"),"675" if generated or reviewed_scene_version(a,root=ROOT) else "630")
            self.assertTrue((ROOT/"dist"/social_image(a).split("?",1)[0].lstrip("/")).is_file())

    def test_visual_review_gallery_is_complete_and_nonindexable(self):
        from scripts.build import social_image, editorial_image
        from scripts.build import image_review_page
        self.assertFalse((ROOT/"dist/image-review/index.html").exists(),
                         "Private review gallery must not be published")
        review=image_review_page(self.articles)
        self.assertIn('name="robots" content="noindex,nofollow"',review)
        self.assertIn('class="review-grid"',review)
        self.assertIn(str(len(self.articles))+" 本",review)
        headers=(ROOT/"dist/_headers").read_text(encoding="utf-8")
        self.assertIn("/image-review/*",headers)
        self.assertIn("X-Robots-Tag: noindex, nofollow",headers)
        sitemap=(ROOT/"dist/sitemap.xml").read_text(encoding="utf-8")
        self.assertNotIn("https://veripsa.com/image-review/",sitemap)
        for a in self.articles:
            self.assertIn(a["title"],review)
            self.assertIn('src="'+html.escape(editorial_image(a),quote=True)+'"',review)
            self.assertIn('src="'+html.escape(social_image(a),quote=True)+'"',review)
            self.assertIn('href="/articles/'+a["slug"]+'/"',review)
        self.assertNotIn("noindex,nofollow",(ROOT/"dist/index.html").read_text(encoding="utf-8"))

    def test_social_motif_follows_article_topic_not_arbitrary_art_template(self):
        import tempfile
        from scripts.editorial_covers import story_subject
        from scripts.social_cards import render_social_card, SUBJECT_LABELS
        # Test a known topic, rather than assuming the newest article has
        # familiar words. Future culture headlines must not break site CI.
        article={**self.articles[0],"title":"迷惑電話の確認電話","art":"notice"}
        changed={**article,"art":"generic"}
        self.assertEqual(story_subject(article),story_subject(changed))
        # Strong headline keywords should override misleading old style labels.
        examples={
            "迷惑電話はもうかけない":"telephone",
            "低気圧で上司が休む":"weather",
            "プロ野球の新制度":"baseball",
            "地元の土産に規制":"parcel",
            "ドラマの脚本が追いつかない":"movie",
            "情報漏洩より先に謝罪":"security",
            "工場ロボットが退職":"robot",
            "会見の例え話がバズる":"meeting",
            "推しコラボのアイスが保存される":"fandom"
        }
        for title,subject in examples.items():
            self.assertEqual(story_subject({"title":title,"art":"cards"}),subject)
            self.assertIn(subject,SUBJECT_LABELS)
        with tempfile.TemporaryDirectory() as tmp:
            a={**article,"slug":"social-topic-a","title":"迷惑電話の確認電話","art":"notice"}
            b={**a,"title":"低気圧で上司の機嫌予報"}
            img_a=Path(tmp)/"telephone.png"
            img_b=Path(tmp)/"weather.png"
            render_social_card(a,img_a)
            render_social_card(b,img_b)
            self.assertNotEqual(img_a.read_bytes(),img_b.read_bytes())
            self.assertLess(img_a.stat().st_size,200000)
            self.assertLess(img_b.stat().st_size,200000)

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
        from scripts.media_policy import photo_is_approved
        from scripts.visual_review import reviewed_generated_scene
        from scripts.generated_photos import generated_scene_entry
        from scripts.build import social_image, uses_stock_photo
        import xml.etree.ElementTree as ET
        approved=0
        generated=0
        rejected=0
        for article in self.articles:
            slug=article["slug"]
            svg=(ROOT/"dist/images"/(slug+".svg")).read_text(encoding="utf-8")
            tree=ET.fromstring(svg)
            self.assertEqual(tree.attrib["viewBox"],"0 0 1200 675")
            self.assertEqual(tree.attrib["role"],"img")
            self.assertIn("創作イラスト",tree.attrib["aria-label"])
            self.assertIn("data-visual-topic=",svg)
            self.assertNotIn("<text",svg,
                             "On-site illustrations must not repeat the HTML headline")
            self.assertNotIn(article["title"],svg)
            self.assertNotIn("<image",svg)
            self.assertNotIn("<script",svg)
            page=(ROOT/"dist/articles"/slug/"index.html").read_text(encoding="utf-8")
            if reviewed_generated_scene(article,root=ROOT):
                generated+=1
                record=generated_scene_entry(article,root=ROOT)
                photo_url="/generated/"+slug+".jpg?v="+record["sha256"][:12]
                self.assertEqual(social_image(article),photo_url)
                self.assertIn('src="'+photo_url+'"',page)
                self.assertIn('property="og:image" content="https://veripsa.com'+photo_url+'"',page)
                self.assertIn('name="twitter:image" content="https://veripsa.com'+photo_url+'"',page)
                self.assertIn("再現イメージ（合成）",page)
                self.assertTrue((ROOT/"dist/generated"/(slug+".jpg")).is_file())
            elif uses_stock_photo(article):
                approved+=1
                self.assertTrue(photo_is_approved(article,root=ROOT))
                self.assertEqual(social_image(article),"/photos/"+slug+".jpg")
                self.assertIn('src="/photos/'+slug+'.jpg"',page)
            else:
                rejected+=1
                from scripts.social_cards import reviewed_scene_version
                version=reviewed_scene_version(article,root=ROOT) or "20261103-topic2"
                self.assertEqual(social_image(article),"/og/articles/"+slug+".png?v="+version)
                self.assertIn('src="/images/'+slug+'.svg"',page)
                self.assertTrue((ROOT/"dist/og/articles"/(slug+".png")).is_file())
                self.assertFalse((ROOT/"dist/generated"/(slug+".jpg")).exists())
                self.assertFalse((ROOT/"dist/clips"/(slug+".mp4")).exists())
        self.assertEqual(generated+approved+rejected,len(self.articles))

    def test_build_does_not_copy_generated_media_when_visual_review_is_missing(self):
        """The export gate must protect actual public files as well as HTML."""
        import tempfile
        from unittest.mock import patch
        from scripts import build
        from scripts.generated_photos import usable_generated_scene
        from scripts.motion_clips import usable_clip
        generated=[a for a in self.articles if usable_generated_scene(a,root=ROOT)]
        clips=[a for a in generated if usable_clip(a,root=ROOT)]
        self.assertTrue(generated,"Exercise real byte-valid source JPEGs")
        self.assertTrue(clips,"Exercise real byte-valid derived MP4s")
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/"dist"
            # Denial at the public review boundary simulates a lost review
            # ledger without changing the real ledger or the byte validators.
            with patch.object(build,"OUT",output), \
                 patch.object(build,"reviewed_generated_scene",return_value=False):
                build.build()
                for article in generated:
                    slug=article["slug"]
                    self.assertFalse((output/"generated"/(slug+".jpg")).exists(),slug)
                    self.assertFalse((output/"clips"/(slug+".mp4")).exists(),slug)
                    self.assertFalse((output/"clips"/(slug+".vtt")).exists(),slug)
                    page=(output/"articles"/slug/"index.html").read_text(encoding="utf-8")
                    self.assertNotIn("/generated/"+slug+".jpg",page,slug)
                    self.assertNotIn("/clips/"+slug+".mp4",page,slug)
                    self.assertNotIn('"@type": "VideoObject"',page,slug)
                    if not build.uses_stock_photo(article):
                        self.assertTrue((output/"og/articles"/(slug+".png")).is_file(),slug)
                        self.assertIn('property="og:image:type" content="image/png"',page,slug)
                for relative in ("index.html","archive/index.html","feed.xml","sitemap.xml"):
                    markup=(output/relative).read_text(encoding="utf-8")
                    for article in generated:
                        self.assertNotIn("/generated/"+article["slug"]+".jpg",markup,relative)
                        self.assertNotIn("/clips/"+article["slug"]+".mp4",markup,relative)

    def test_no_photo_articles_have_distinct_offline_png_social_cards(self):
        """Future articles automatically get meaningful, non-identical raster cards."""
        from scripts.social_cards import render_social_card, reviewed_scene_version
        from scripts.build import social_image, uses_stock_photo
        import struct
        import tempfile
        from scripts.visual_review import reviewed_generated_scene
        no_photo=[a for a in self.articles
                  if not reviewed_generated_scene(a,root=ROOT) and not uses_stock_photo(a)]
        images=set()
        for a in no_photo:
            path=ROOT/"dist"/"og"/"articles"/(a["slug"]+".png")
            self.assertTrue(path.exists(),a["slug"])
            data=path.read_bytes()
            self.assertEqual(data[:8],b"\x89PNG\r\n\x1a\n")
            version=reviewed_scene_version(a,root=ROOT)
            self.assertEqual(struct.unpack(">II",data[16:24]),(1200,675 if version else 630))
            self.assertGreater(len(data),1500)
            self.assertLess(len(data),200000,"Social card should stay small for free hosting")
            self.assertNotIn(data,images,"Each article should have a unique preview")
            images.add(data)
            expected="/og/articles/"+a["slug"]+".png?v="+(version or "20261103-topic2")
            self.assertEqual(social_image(a),expected)
            with tempfile.TemporaryDirectory() as directory:
                rerender=Path(directory)/"check.png"
                render_social_card(a,rerender,root=ROOT)
                self.assertEqual(data,rerender.read_bytes(),"Cards should render deterministically")
            page=(ROOT/"dist/articles"/a["slug"]/"index.html").read_text(encoding="utf-8")
            self.assertIn('property="og:image" content="https://veripsa.com'+expected+'"',page)
            self.assertIn('name="twitter:image" content="https://veripsa.com'+expected+'"',page)
        self.assertEqual(sum(1 for _ in images),len(no_photo))

    def test_reader_growth_and_editorial_transparency(self):
        import re
        index=(ROOT/"dist/index.html").read_text(encoding="utf-8")
        self.assertIn('class="genre-explore"',index)
        self.assertIn('class="topic-explore genre-explore"',index)
        self.assertIn("話題から読む",index)
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
            self.assertNotIn('class="reader-follow"',page,
                             "RSS and share links remain available without duplicate promo panels")
            self.assertIn('class="share-control"',page)
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
            self.assertIn('name="twitter:image" content="https://veripsa.com'+social_image(article)+'"',page)
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
        self.assertIn('name="twitter:title" content="未発生新聞｜5年後を勝手に想像する風刺ニュース"',home)
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

