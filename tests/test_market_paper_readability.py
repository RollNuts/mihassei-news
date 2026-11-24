"""Reader-first newspaper layout for factual markets; preserves auditable sources."""
import json
import copy
from datetime import date
from pathlib import Path
import unittest

from scripts.serials import render_series_page, render_episode_page
from scripts.market_figures import load_market_figures, render_market_svg
from scripts.market_outlook import load_market_outlook
from scripts.market_bulletins import render_market_bulletins_index, render_market_bulletin
from scripts.market_outlooks import render_market_outlook_board
from tests.test_market_forecast_summary import final_review
from tests.test_market_investor_coverage import report, stock
from tests.test_market_outlooks import original

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
        self.assertIn('class="container serials-page market-paper"', rendered)
        self.assertIn('<h1>市況と注目株</h1>', rendered)
        for section in ("market-focus", "market-stories", "market-proof"):
            self.assertEqual(rendered.count(f'id="{section}"'), 1)
        self.assertIn('href="/serials/market-lab/briefings/"', rendered)
        history = rendered.split('id="market-proof"', 1)[1]
        self.assertIn('href="/serials/market-lab/outlooks/"', history)
        self.assertIn('id="forecast-ledger"', rendered)
        self.assertLess(rendered.index('id="market-stories"'),
                        rendered.index('id="forecast-ledger"'))
        self.assertIn('href="/serials/market-lab/' + latest["slug"] + '/"', rendered)
        self.assertIn(latest["title"], rendered)
        fallback = rendered.split('id="market-focus"', 1)[1].split("</section>", 1)[0]
        self.assertIn(f'<time datetime="{latest["published_at"]}">{latest["published_at"]}</time>', fallback)
        self.assertNotIn("詳しい検証方法", rendered)
        self.assertNotIn("判定記録", rendered)
        self.assertNotIn("確認済みの日次市況記事はまだありません", rendered)
        self.assertNotIn("日々の予測と判断の変化", rendered)
        self.assertNotIn("判定材料不足", rendered)
        self.assertNotIn("PUBLIC FORECAST LOG", rendered)
        self.assertNotIn("<main", rendered)  # the global template already owns the main landmark

    def test_front_page_preserves_links_rss_history_and_read_progress(self):
        rendered = render_series_page(self.market, passthrough, self.figure_data, [], self.outlook, [])
        self.assertIn('datetime="2026-11-15"', rendered)
        self.assertIn('data-serial-progress-summary', rendered)
        self.assertIn('data-serial-episode-state', rendered)
        self.assertIn('data-serial-progress-complete', rendered)
        self.assertIn('href="/feeds/series-market-lab.xml"', rendered)
        self.assertLess(rendered.index('id="market-stories"'),rendered.index('data-serial-progress-summary'))
        self.assertLess(rendered.index('data-serial-progress-summary'),rendered.index('id="forecast-ledger"'))
        self.assertIn('href="/serials/market-lab/#market-proof"', (
            render_episode_page(self.market, self.market["episodes"][-1], passthrough)))
        for episode in self.market["episodes"]:
            self.assertIn('/serials/market-lab/' + episode["slug"] + '/', rendered)

    def test_fresh_real_day_bulletin_leads_when_daily_publications_exist(self):
        bulletin = {
            "slug": "2026-11-16-am", "slot": "am", "day": "2026-11-16",
            "title": "当日の市況を読み解く",
            "summary": "実際に確認した市場の値動きをもとにした解説。",
        }
        markup = render_series_page(self.market, passthrough, self.figure_data,
                                    [bulletin], self.outlook, [])
        self.assertIn('<h2 id="market-focus-heading"><a href="/serials/market-lab/briefings/2026-11-16-am/">当日の市況を読み解く</a></h2>', markup)
        self.assertIn("2026-11-16", markup)
        # The serial latest is still available in the older-analysis list.
        self.assertIn(self.market["episodes"][-1]["title"], markup)
        self.assertIn("企業分析", markup)
        self.assertNotIn("確認済みの日次市況記事はまだありません", markup)

    def test_latest_report_links_its_observed_stock_moves_and_material_before_the_stream(self):
        nasdaq = stock(code="AAPL", name="米国のテスト企業", close=113.50,
                       context="売上高の見通しを上方修正した。次の決算で営業利益率を確認する。")
        tokyo = stock(code="5016", name="日本のテスト企業", exchange="東証", currency="JPY",
                      observed_at="2026-11-16T15:30:00+09:00", close=98, direction="下落",
                      context="銅の販売単価が低下した。数量の変化は次の開示で確かめる。")
        bulletin = report([nasdaq, tokyo])
        forecast = original()
        events = [forecast, final_review(forecast)]
        before = copy.deepcopy([bulletin, events])
        page = render_series_page(self.market, passthrough, bulletins=[report(day="2026-11-16"), bulletin],
                                  daily_outlooks=events)
        anchors = ["market-focus", "market-picks", "market-desk", "market-stories", "market-proof"]
        offsets = [page.index(f'id="{anchor}"') for anchor in anchors]
        self.assertEqual(sorted(offsets), offsets)
        picks = page[offsets[1]:offsets[2]]
        self.assertIn(f'href="/serials/market-lab/briefings/{bulletin["slug"]}/#market-picks"', picks)
        for pick in (nasdaq, tokyo):
            self.assertIn(pick["name"], picks)
            self.assertIn(pick["context"].split("。")[0], picks)
        self.assertIn("<h2>注目株</h2>", picks)
        self.assertIn("+13.50%", picks)
        self.assertIn("-2.00%", picks)
        for indicator in ('id="market-next"', 'id="market-score"', 'class="market-reader-accuracy"',
                          forecast["statement"], forecast["reason"]):
            self.assertNotIn(indicator, page)
        self.assertEqual([bulletin, events], before)

    def test_latest_report_without_picks_does_not_borrow_an_older_company_or_create_an_empty_section(self):
        old_pick = stock(name="前号のみのテスト銘柄")
        older = report([old_pick], day="2026-11-16")
        newer = report(day="2026-11-17")
        page = render_series_page(self.market, passthrough, bulletins=[older, newer])
        self.assertNotIn('id="market-picks"', page)
        self.assertNotIn('href="#market-picks"', page)
        self.assertNotIn(old_pick["name"], page)
        self.assertIn(f'href="/serials/market-lab/briefings/{older["slug"]}/"', page)

    def test_article_leads_with_prediction_and_result_without_method_panels(self):
        episode = self.market["episodes"][-1]
        page = render_episode_page(self.market, episode, passthrough,
                                   self.figure_data[episode["slug"]],
                                   self.outlook.get(episode["slug"], []))
        for marker in ('id="article-evidence"', 'id="article-outlook"',
                       'id="article-sources"', 'id="reader-comments"'):
            self.assertIn(marker, page)
        self.assertLess(page.index('id="article-outlook"'), page.index('class="market-data-summary"'))
        self.assertLess(page.index('id="article-outlook"'), page.index('id="article-evidence"'))
        self.assertLess(page.index('id="article-outlook"'), page.index('id="article-sources"'))
        self.assertNotIn(episode["forecast"]["method"], page)
        self.assertIn(episode["forecast"]["statement"], page)
        self.assertIn(episode["forecast"]["reviewed_on"], page)
        self.assertNotIn("予測の判定方法と基準値を読む", page)
        self.assertNotIn('class="serial-scenarios"', page)
        self.assertNotIn('class="market-paper-history"', page)
        self.assertIn("結果：結果待ち", page)
        self.assertIn("Xで共有", page)
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

    def test_daily_story_leads_with_the_stock_material_and_preserves_the_original_prose(self):
        company = stock(name="テスト企業", context="新工場の稼働日を公表した。")
        bulletin = report([company])
        forecast = original()
        result = render_market_bulletin(bulletin, passthrough, outlooks=[forecast, final_review(forecast)])
        lead = result.index('class="market-dispatch-lead"')
        picks = result.index('id="market-picks"')
        prose = result.index('class="serial-prose-section"')
        self.assertLess(lead, picks)
        self.assertLess(picks, prose)
        self.assertIn(company["context"], result[picks:prose])
        self.assertIn(company["code"], result[picks:prose])
        for paragraph in bulletin["sections"][0]["paragraphs"]:
            self.assertIn(paragraph, result[prose:])
        self.assertNotIn('id="daily-forecasts"', result)
        self.assertNotIn('class="market-reader-accuracy"', result)
        self.assertNotIn(forecast["statement"], result)
        repeated = render_market_bulletin(dict(bulletin, summary=bulletin["lead"]), passthrough)
        self.assertEqual(repeated.count(bulletin["lead"]), 1)

    def test_front_page_compacts_older_company_articles_without_deleting_links(self):
        market = copy.deepcopy(self.market)
        template = market["episodes"][-1]
        market["episodes"] = [dict(template, number=i, slug=f"older-analysis-{i}",
                                   title=f"企業分析{i}", published_at=f"2026-10-{i:02d}")
                              for i in range(1, 7)]
        before = copy.deepcopy(market)
        page = render_series_page(market, passthrough)
        visible, archive = page.split('<details class="market-reader-archive">', 1)
        self.assertIn("過去の企業分析", archive)
        self.assertIn('href="/serials/market-lab/older-analysis-5/"', visible)
        self.assertIn('href="/serials/market-lab/older-analysis-4/"', visible)
        for i in (1, 2, 3):
            link = f'href="/serials/market-lab/older-analysis-{i}/"'
            self.assertNotIn(link, visible)
            self.assertIn(link, archive)
        self.assertEqual(market, before)

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
        mobile = css.split("@media(max-width:660px){", 1)[1].split("\n}", 1)[0]
        self.assertIn(".market-stock-grid{grid-template-columns:1fr}", mobile)
        self.assertIn(".market-article-jump", css)
        self.assertIn(".market-paper-ledger", css)
        self.assertNotIn("@import", css)


if __name__ == "__main__":
    unittest.main()
