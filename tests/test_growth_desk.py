"""Safe, no-cost tests for a factual browser-level growth review."""
import html
import json
import subprocess
import sys
import unittest
from pathlib import Path

from scripts.growth_desk import MAX_STORIES, listed_articles, render

ROOT=Path(__file__).resolve().parents[1]


class GrowthDeskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Independent of unittest discovery order or test_site's setup.
        subprocess.run([sys.executable,str(ROOT/"scripts/build.py")],
                       cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
        cls.articles=json.loads((ROOT/"content/articles.json").read_text(encoding="utf-8"))

    def test_bounded_real_article_listing_uses_actual_publication_dates(self):
        articles=[
            {"slug":"article-"+str(i),"title":"見出し "+str(i),"category":"社会",
             "published_at":"2026-11-"+str(i%25+1).zfill(2)+"T10:00:00+09:00",
             "fiction_at":"2031-11-03T10:00:00+09:00",
             "source":{"published_at":"2026-11-01T09:00:00+09:00"}}
            for i in range(45)
        ]
        rows=listed_articles(articles)
        self.assertEqual(len(rows),MAX_STORIES)
        self.assertTrue(all(a["published"].startswith("2026") for a in rows))
        self.assertNotIn("fiction_at",rows[0])
        self.assertGreaterEqual(rows[0]["published"],rows[-1]["published"])
        self.assertEqual(len({a["slug"] for a in rows}),MAX_STORIES)

    def test_markup_escapes_article_names_and_hides_secretless_public_dashboard(self):
        story={"slug":"safe-slug","title":'記事 & <script>alert("x")</script>',
               "category":"社会","published_at":"2026-11-02T19:00:00+09:00",
               "source":{"published_at":"2026-11-02T18:00:00+09:00"}}
        output=render([story])
        self.assertIn('name="robots" content="noindex,nofollow"',output)
        self.assertNotIn('<script>alert("x")</script>',output)
        self.assertIn("&lt;script&gt;",output)
        self.assertIn("公開済み",output)
        self.assertIn("data-growth-stories=",output)
        self.assertIn('/assets/growth-desk.js',output)
        self.assertIn("PVはページの表示回数",output)
        self.assertIn("サイト累計PV",output)
        self.assertIn("直近7暦日PV",output)
        self.assertIn("今日の伸び方",output)
        self.assertIn("今日のPV・ここまで",output)
        self.assertIn("ピーク時間PV",output)
        self.assertIn("data-growth-peak-sources",output)
        self.assertIn("data-growth-peak-kinds",output)
        self.assertIn("ピーク時刻とX投稿時刻が近いだけでは原因と断定せず",output)
        self.assertIn("未計測は0に置き換えず",output)
        self.assertIn('option value="weekPv"',output)
        self.assertIn('option value="pageViews"',output)
        self.assertIn("計測開始以前の過去データは復元できません",output)
        self.assertIn('href="/share-desk/"',output)
        self.assertNotIn("/api/engagement?articles=",output,
                         "Static HTML must not contain a guessed live counter")
        self.assertIn("本番サイトには公開しない編集用ビュー",output)
        self.assertNotIn("本ページは公開URL",output)
        self.assertNotIn("広告を表示",output)

    def test_built_site_contains_review_and_noindex_http_header(self):
        self.assertFalse((ROOT/"dist/growth-desk/index.html").exists(),
                         "Growth desk must not be published as a public web page")
        data=render(self.articles)
        js=(ROOT/"dist/assets/growth-desk.js").read_text(encoding="utf-8")
        rows=listed_articles(self.articles)
        self.assertIn('name="robots" content="noindex,nofollow"',data)
        self.assertIn(str(len(rows))+"本",data)
        for row in rows:
            self.assertIn(html.escape(row["title"],quote=True),data)
            self.assertIn(row["slug"],data)
        self.assertIn("fetch(url,{cache:'no-store'",js)
        self.assertIn("metric.arrivals.x",js)
        self.assertIn("metric.arrivals.search",js)
        self.assertIn("last7DaysPageViews",js)
        self.assertIn("data-growth-page-views",js)
        self.assertIn("data-growth-peak-hour",js)
        self.assertIn("pageViewSourcesCoverage",js)
        self.assertIn("pageViewKindsCoverage",js)
        self.assertIn("topArticles",js)
        self.assertIn("&hour=",js)
        self.assertIn("未計測の記事は0ではなく",js)
        self.assertIn("replaceChildren",js)
        self.assertNotIn(".innerHTML=",js)
        self.assertNotIn("method:'POST'",js)
        self.assertNotIn("navigator.sendBeacon",js)
        headers=(ROOT/"dist/_headers").read_text(encoding="utf-8")
        self.assertIn("/growth-desk/*\n  X-Robots-Tag: noindex, nofollow",headers)
        sitemap=(ROOT/"dist/sitemap.xml").read_text(encoding="utf-8")
        self.assertNotIn("https://veripsa.com/growth-desk/",sitemap)
        self.assertFalse((ROOT/"dist/share-desk/index.html").exists())


if __name__=="__main__":
    unittest.main()
