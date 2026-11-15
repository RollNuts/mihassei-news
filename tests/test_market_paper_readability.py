"""Reader-first newspaper layout for factual markets; preserves auditable sources."""
import json
from datetime import date
from pathlib import Path
import unittest

from scripts.serials import render_series_page, render_episode_page
from scripts.market_figures import load_market_figures, render_market_svg
from scripts.market_outlook import load_market_outlook
from scripts.market_bulletins import render_market_bulletins_index
from scripts.market_outlooks import render_market_outlook_board

ROOT = Path(__file__).resolve().parents[1]


def passthrough(_title, _description, _path, body, **_kwargs):
    return body


class MarketPaperReadabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        serials = json.loads((ROOT / "content/serials.json").read_text(encoding="utf-8"))
        cls.market = next(s for s in serials if s["slug"] == "market-lab")
        cls.figure_data = load_market_figures(ROOT, [cls.market], today=date(2026, 11, 15))
        cls.outlook = load_market_outlook(ROOT, [cls.market], today=date(2026, 11, 15))

    def test_front_page_starts_with_a_real_article_not_empty_data_panels(self):
        rendered = render_series_page(self.market, passthrough, self.figure_data, [], self.outlook, [])
        latest = self.market["episodes"][-1]
        self.assertIn('class="market-paper"', rendered)
        self.assertIn('id="market-focus"', rendered)
        self.assertIn('id="market-stories"', rendered)
        self.assertIn('id="market-outlook"', rendered)
        self.assertIn('id="forecast-ledger"', rendered)
        self.assertLess(rendered.index('id="market-focus"'),
                        rendered.index('id="market-stories"'))
        self.assertLess(rendered.index('id="market-stories"'),
                        rendered.index('id="forecast-ledger"'))
        self.assertIn('href="/serials/market-lab/' + latest["slug"] + '/"', rendered)
        self.assertIn(latest["title"], rendered)
        self.assertIn('最終掲載 <time datetime="' + latest["published_at"] + '">', rendered)
        self.assertIn("今後の焦点", rendered)
        self.assertIn('href="/serials/market-lab/' + latest["slug"] + '/#forecast-detail"', rendered)
        self.assertNotIn("確認済みの日次市況記事はまだありません", rendered)
        self.assertNotIn("日々の予測と判断の変化", rendered)
        self.assertNotIn("判定材料不足", rendered)
        self.assertNotIn("PUBLIC FORECAST LOG", rendered)
        self.assertNotIn("<main", rendered)  # the global template already owns the main landmark

    def test_front_page_preserves_links_rss_history_and_read_progress(self):
        rendered = render_series_page(self.market, passthrough, self.figure_data, [], self.outlook, [])
        self.assertIn("公開済み 3回", rendered)
        self.assertIn('data-serial-progress-summary', rendered)
        self.assertIn('data-serial-episode-state', rendered)
        self.assertIn('data-serial-progress-complete', rendered)
        self.assertIn('href="/feeds/series-market-lab.xml"', rendered)
        self.assertIn('href="/serials/market-lab/#market-proof"', (
            render_episode_page(self.market, self.market["episodes"][-1], passthrough)))
        for episode in self.market["episodes"]:
            self.assertIn('/serials/market-lab/' + episode["slug"] + '/', rendered)

    def test_article_explains_evidence_before_outlook_and_leaves_math_inspectable(self):
        episode = self.market["episodes"][-1]
        page = render_episode_page(self.market, episode, passthrough,
                                   self.figure_data[episode["slug"]],
                                   self.outlook.get(episode["slug"], []))
        for marker in ('id="article-evidence"', 'id="article-outlook"',
                       'id="forecast-detail"', 'id="article-sources"',
                       'id="forecast-progress"', 'id="reader-comments"'):
            self.assertIn(marker, page)
        self.assertLess(page.index('id="article-evidence"'), page.index('id="article-outlook"'))
        self.assertLess(page.index('id="article-outlook"'), page.index('id="article-sources"'))
        self.assertIn(episode["forecast"]["method"], page)
        self.assertIn(episode["forecast"]["statement"], page)
        self.assertIn(episode["forecast"]["reviewed_on"], page)
        self.assertIn("予測の判定方法と基準値を読む", page)
        self.assertIn("この回をXで共有する", page)
        self.assertNotIn("PUBLIC FORECAST LOG", page)
        self.assertEqual(page.count('<figure class="market-figure">'), 3)

    def test_figures_headline_the_actual_metric_instead_of_technical_placeholder(self):
        for figures in self.figure_data.values():
            for figure in figures:
                src = render_market_svg(figure, mobile=True)
                # All chart titles fit the 15-character line-wrapped graphic.
                self.assertIn(figure["title"][:15], src)
                self.assertNotIn("公開資料の数値比較", src)
                self.assertIn("単位：", src)
                self.assertIn("<desc>", src)

    def test_empty_bulletin_archive_does_not_expose_the_internal_publish_schedule(self):
        output = render_market_bulletins_index([])
        self.assertIn("日々の市況", output)
        self.assertIn("最新の企業分析を読む", output)
        self.assertNotIn("8:30 寄り付き前", output)
        self.assertNotIn("見送ります", output)
        forecast_empty = render_market_outlook_board([])
        self.assertIn("企業分析を読む", forecast_empty)

    def test_mobile_paper_has_single_column_and_no_external_css(self):
        css = (ROOT / "assets/serials.css").read_text(encoding="utf-8")
        self.assertIn(".market-paper-lead{display:grid", css)
        self.assertIn("@media(max-width:780px)", css)
        self.assertIn(".market-paper-lead{grid-template-columns:minmax(0,1fr)", css)
        self.assertIn(".market-article-jump", css)
        self.assertIn(".market-paper-ledger", css)
        self.assertNotIn("@import", css)


if __name__ == "__main__":
    unittest.main()
