"""Reader-visible PV presentation uses verified counts without small-card placeholders."""
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
    def test_home_shows_publishing_volume_and_only_conditional_cumulative_pv(self):
        articles = build.load()
        html = build.home(articles)
        header = html.split('<nav class="nav-bar"', 1)[0]
        self.assertNotIn("data-pv-widget", header)
        section = html.split('<section class="newsroom-snapshot"', 1)[1].split("</section>", 1)[0]
        for token in ("掲載記事", "発行号", "累計PV", "直近7日",
                      "data-site-pv-total", "data-site-pv-week-stat",
                      "data-site-pv-week", "data-pv-tracking-note"):
            self.assertIn(token, section)
        self.assertIn('href="/privacy/#pageviews"', section)
        self.assertIn("hidden", section)
        self.assertIn(f"{len(articles):,}<small>本", section)
        self.assertNotIn("今日のPV", html)
        self.assertNotIn("昨日のPV", html)

    def test_archive_cards_do_not_show_pv_but_story_header_can_display_it(self):
        articles = build.load()
        self.assertEqual(PvBadges(build.archive(articles)).slugs, [])
        article = articles[0]
        html = build.story(article, articles)
        header = html.split('<header class="article-header">', 1)[1].split("</header>", 1)[0]
        self.assertEqual(PvBadges(header).slugs, [article["slug"]])
        self.assertIn('<strong data-article-pv-count></strong>', header)
        self.assertNotIn('<strong data-article-pv-count>—</strong>', header)
        self.assertNotIn("data-article-readers", html)

    def test_privacy_explains_reload_measurement_start_and_local_exclusion(self):
        html = build.privacy()
        for token in ('id="pageviews"', "再読み込みも数え", "計測開始から", "二重計上",
                      "トップ・記事・アーカイブ・連載・その他", "完全な閲覧URLは保存しません",
                      "data-analytics-opt-out", "data-analytics-status", "このブラウザの閲覧を集計しない"):
            self.assertIn(token, html)
        self.assertIn("未取得や未計測の値は公開画面に表示しません", html)


if __name__ == "__main__":
    unittest.main()
