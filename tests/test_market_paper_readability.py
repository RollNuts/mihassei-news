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
from tests.test_market_outlooks import original, review

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
        self.assertIn('id="market-focus"', rendered)
        self.assertIn('id="market-stories"', rendered)
        self.assertIn('href="/serials/market-lab/briefings/"', rendered)
        self.assertIn('href="/serials/market-lab/outlooks/"', rendered)
        self.assertIn('id="forecast-ledger"', rendered)
        self.assertLess(rendered.index('id="market-focus"'),
                        rendered.index('id="market-stories"'))
        self.assertLess(rendered.index('id="market-stories"'),
                        rendered.index('id="forecast-ledger"'))
        self.assertIn('href="/serials/market-lab/' + latest["slug"] + '/"', rendered)
        self.assertIn(latest["title"], rendered)
        self.assertIn('最終掲載 <time datetime="' + latest["published_at"] + '">', rendered)
        self.assertIn("今の予想", rendered)
        self.assertIn('href="/serials/market-lab/' + latest["slug"] + '/#article-outlook"', rendered)
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

    def test_front_page_forecast_shows_its_original_reason_and_latest_assessment(self):
        fc={**original(),'reason':'受注が増加した。営業利益率も改善した。'}
        latest=review(stance='弱気',reason='新たな費用増で利益率が低下した。売りが広がった。')
        events=[fc,latest];before=copy.deepcopy(events)
        page=render_series_page(self.market,passthrough,daily_outlooks=events)
        follow=page.split('<aside class="market-paper-follow market-reader-focus"',1)[1].split('</aside>',1)[0]
        self.assertIn('11月17日のTOPIXは上昇を予想。',follow)
        self.assertIn('JSTの当初の予想',follow)
        self.assertIn(fc['reason'],follow)
        self.assertIn(latest['reason'],follow)
        self.assertIn('最新の評価：',follow)
        self.assertIn('弱気（暫定的に逆行）',follow)
        self.assertIn('評価の出典：',follow)
        self.assertIn('集計時点：<time datetime="2026-11-16T12:40:00+09:00"',follow)
        self.assertIn('href="/serials/market-lab/outlooks/#forecast-2026-11-16-topix"',follow)
        self.assertIn('確定0件',page)
        self.assertIn('<strong>—</strong><span class="market-reader-count">確定0件</span>',page)
        self.assertLess(page.index('11月17日のTOPIXは上昇を予想。'),page.index('class="market-reader-accuracy"'))
        self.assertLess(page.index('</aside></section>'),page.index('class="market-reader-accuracy"'))
        self.assertLess(page.index('class="market-reader-accuracy"'),page.index('id="market-stories"'))
        self.assertEqual(events,before)

    def test_front_page_reports_missing_assessment_without_borrowing_another_forecasts_review(self):
        older={**original(),'id':'2026-11-15-topix','at':'2026-11-15T08:30:00+09:00',
               'reviewed_on':'2026-11-16'}
        fc={**original(),'reason':'受注が増加した。営業利益率も改善した。'}
        unrelated=review(forecast_id=older['id'],stance='弱気')
        events=[older,fc,unrelated]
        page=render_series_page(self.market,passthrough,daily_outlooks=events)
        follow=page.split('<aside class="market-paper-follow market-reader-focus"',1)[1].split('</aside>',1)[0]
        self.assertIn('この予想の更新評価はまだありません。',follow)
        self.assertIn(fc['reason'],follow)
        self.assertIn('集計時点：<time datetime="2026-11-16T12:40:00+09:00"',follow)
        self.assertNotIn('最新の評価：',follow)
        self.assertNotIn(unrelated['reason'],follow)
        self.assertNotIn('弱気（',follow)

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

    def test_intraday_story_leads_with_a_short_outlook_then_evidence(self):
        bulletin = {
            "slug": "2026-11-16-noon", "slot": "noon", "day": "2026-11-16",
            "published_at": "2026-11-16T12:40:00+09:00",
            "title": "後場の市場動向を読む", "summary": "公開資料で確かめた市場動向。",
            "lead": "前場の情勢を説明します。",
            "sections": [{"heading": "何が動いたか", "paragraphs": ["確認済みの数値について説明します。"]}],
            "sources": [], "figures": [], "picks": [],
        }
        prior = [{
            "type": "forecast", "id": "2026-11-16-topix",
            "at": "2026-11-16T08:10:00+09:00",
            "statement": "TOPIXの将来の方向を資料に基づき検討する。",
            "stance": "中立", "reviewed_on": "2026-11-17",
            "subject": "TOPIX", "direction": "横ばい",
        }]
        result = render_market_bulletin(bulletin, passthrough, outlooks=prior)
        self.assertIn("何が動いたか", result)
        self.assertIn('class="market-briefing-outlook market-reader-focus"', result)
        self.assertNotIn(prior[0]["statement"], result)
        self.assertIn("TOPIX", result)
        self.assertIn("横ばい", result)
        self.assertIn("結果日", result)
        self.assertLess(result.index('id="daily-forecasts"'), result.index("何が動いたか"))
        self.assertNotIn("その後の変更", result)
        self.assertNotIn("検証記録", result)
        self.assertNotIn("daily-forecast-card", result)
        self.assertNotIn("PUBLIC FORECAST LOG", result)

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
        self.assertIn(".market-paper-lead{display:grid", css)
        self.assertIn("@media(max-width:780px)", css)
        self.assertIn(".market-paper-lead{grid-template-columns:minmax(0,1fr)", css)
        self.assertIn(".market-article-jump", css)
        self.assertIn(".market-paper-ledger", css)
        self.assertNotIn("@import", css)


if __name__ == "__main__":
    unittest.main()
