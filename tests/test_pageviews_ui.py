"""Public PV labels stay distinct from browser-based audience metrics."""
import unittest
from html.parser import HTMLParser

from scripts import build


class PvBadges(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.slugs = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "data-article-pv" in values:
            self.slugs.append(values["data-article-pv"])


class PageViewPresentationTests(unittest.TestCase):
    def test_masthead_labels_actual_page_views_without_static_fake_totals(self):
        html = build.layout("テスト", "説明", "/", "<p>記事</p>")
        header = html.split('data-pv-widget', 1)[1].split('</nav>', 1)[0]
        for token in ("今日のPV", "昨日のPV", "直近7日", "data-site-pv-today",
                      "data-site-pv-yesterday", "data-site-pv-week", "data-pv-tracking-note"):
            self.assertIn(token, header)
        self.assertIn('href="/privacy/#pageviews"', header)
        self.assertNotIn("今日の来訪", header)
        self.assertNotIn("今日の記事閲覧", header)
        self.assertNotIn('>0</strong>', header)

    def test_every_archive_article_and_article_header_has_its_own_pv_badge(self):
        articles = build.load()
        badges = PvBadges(build.archive(articles))
        self.assertEqual(set(badges.slugs), {a["slug"] for a in articles})
        self.assertEqual(len(badges.slugs), len(articles))
        article = articles[0]
        html = build.story(article, articles)
        header = html.split('<header class="article-header">', 1)[1].split('</header>', 1)[0]
        self.assertEqual(PvBadges(header).slugs, [article["slug"]])
        self.assertIn('<strong data-article-pv-count>—</strong>', header)
        self.assertNotIn("data-article-readers", html)

    def test_privacy_explains_reload_measurement_start_and_local_exclusion(self):
        html = build.privacy()
        for token in ('id="pageviews"', "再読み込みも数え", "計測開始から", "二重計上",
                      "data-analytics-opt-out", "data-analytics-status", "このブラウザの閲覧を集計しない"):
            self.assertIn(token, html)
        self.assertIn("未計測の期間は「—」", html)


if __name__ == "__main__":
    unittest.main()
