"""Readers can follow published market reports and the forecast they actually read."""
import copy
import re
import unittest

from scripts.market_bulletins import (
    build_market_bulletins,
    render_market_bulletin,
    render_market_bulletins_archive,
    render_market_bulletins_index,
)


def only_content(_title, _desc, _url, content, **_kwargs):
    return content


class MarketBulletinArchiveTests(unittest.TestCase):
    @staticmethod
    def editions(count=20):
        # Synthetic records never enter content/ or production.
        return [
            {"slug": f"2026-10-{i:02d}-am",
             "day": f"2026-10-{i:02d}",
             "slot": "am",
             "title": f"第{i}号の市況",
             "summary": f"第{i}号の確認した市況解説。"}
            for i in range(1, count + 1)
        ]

    def test_front_page_only_shows_twelve_latest_reports(self):
        rows = self.editions()
        html = render_market_bulletins_index(rows)
        self.assertEqual(html.count('<li><a href="/serials/market-lab/briefings/'), 12)
        self.assertIn('/serials/market-lab/briefings/2026-10-20-am/', html)
        self.assertNotIn('/serials/market-lab/briefings/2026-10-08-am/', html)
        self.assertIn('市況記事をすべて読む', html)

    def test_archive_keeps_every_edition_without_self_reload_link(self):
        rows = self.editions()
        html = render_market_bulletins_archive(rows, only_content)
        self.assertEqual(html.count('<li><a href="/serials/market-lab/briefings/'), 20)
        self.assertIn('/serials/market-lab/briefings/2026-10-01-am/', html)
        self.assertIn('/serials/market-lab/briefings/2026-10-20-am/', html)
        self.assertLess(html.index('/serials/market-lab/briefings/2026-10-20-am/'),
                        html.index('/serials/market-lab/briefings/2026-10-01-am/'))
        self.assertNotIn('市況記事をすべて読む', html)
        self.assertIn('/feeds/market-bulletins.xml', html)

    def test_no_bulletins_does_not_invent_a_market_report(self):
        main = render_market_bulletins_index([])
        archive = render_market_bulletins_archive([], only_content)
        for page in (main, archive):
            self.assertIn('現在、掲載中の市況記事はありません', page)
            self.assertEqual(page.count('<li><a href="/serials/market-lab/briefings/'), 0)
            self.assertNotIn('市況記事をすべて読む', page)

    @staticmethod
    def dispatches():
        # There is deliberately no opening report; navigation must not invent one.
        return [
            {"slug": f"2026-11-17-{slot}", "day": "2026-11-17", "slot": slot,
             "published_at": f"2026-11-17T{clock}:00+09:00",
             "title": title, "summary": "公開資料で確かめた市況のテスト原稿。",
             "lead": "確認した市場の動きを説明します。",
             "sections": [], "figures": [], "sources": [], "picks": []}
            for slot, clock, title in (
                ("am", "08:43", "寄り前に確認した市況"),
                ("noon", "13:17", "後場に変わった市場の材料"),
                ("close", "16:24", "引け後に確かめた一日の動き"),
            )
        ]

    def test_published_stream_has_adjacent_links_and_actual_times(self):
        reports = self.dispatches()
        before = copy.deepcopy(reports)
        pages = {}
        build_market_bulletins(reports, only_content, pages.__setitem__)
        navigation = [
            re.search(r'<nav class="serial-next-prev market-bulletin-nav".*?</nav>',
                      pages[f'serials/market-lab/briefings/{item["slug"]}/index.html']).group()
            for item in reports
        ]
        self.assertNotIn('rel="prev"', navigation[0])
        self.assertIn('rel="next" href="/serials/market-lab/briefings/2026-11-17-noon/"', navigation[0])
        self.assertIn('rel="prev" href="/serials/market-lab/briefings/2026-11-17-am/"', navigation[1])
        self.assertIn('rel="next" href="/serials/market-lab/briefings/2026-11-17-close/"', navigation[1])
        self.assertIn('rel="prev" href="/serials/market-lab/briefings/2026-11-17-noon/"', navigation[2])
        self.assertNotIn('rel="next"', navigation[2])
        self.assertIn('2026-11-17 13:17', navigation[0])
        self.assertIn(reports[1]["title"], navigation[0])
        self.assertNotIn('2026-11-17-open', ''.join(navigation))
        self.assertEqual(reports, before)

    def test_single_report_keeps_archive_without_an_unpublished_next_link(self):
        pages = {}
        report = self.dispatches()[0]
        build_market_bulletins([report], only_content, pages.__setitem__)
        page = pages[f'serials/market-lab/briefings/{report["slug"]}/index.html']
        self.assertNotIn('market-bulletin-nav', page)
        self.assertIn('日次市況一覧へ', page)
        self.assertNotIn('次回は未公開', page)

    def test_forecast_link_points_to_original_snapshot_not_the_newest_forecast(self):
        report = self.dispatches()[0]
        earlier = {
            "type": "forecast", "id": "2026-11-16-topix",
            "at": "2026-11-16T08:30:00+09:00", "reviewed_on": "2026-11-17",
            "subject": "TOPIX", "direction": "横ばい",
        }
        later = dict(earlier, id="2026-11-17-topix", at="2026-11-17T09:00:00+09:00",
                     reviewed_on="2026-11-18", direction="上昇")
        events = [earlier, later]
        before = copy.deepcopy(events)
        page = render_market_bulletin(report, only_content, outlooks=events)
        self.assertIn('href="/serials/market-lab/outlooks/#forecast-2026-11-16-topix"', page)
        self.assertNotIn('#forecast-2026-11-17-topix', page)
        self.assertIn('掲載時点の予想', page)
        self.assertIn('datetime="2026-11-16T08:30:00+09:00">2026-11-16 08:30</time> JST', page)
        self.assertIn('11月17日のTOPIXは横ばいを予想。', page)
        self.assertNotIn('11月18日のTOPIXは上昇を予想。', page)
        self.assertEqual(events, before)


if __name__ == "__main__":
    unittest.main()
