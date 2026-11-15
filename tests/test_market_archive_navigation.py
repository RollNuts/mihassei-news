"""Readers can reach every published market bulletin, not just the front-page 12."""
import unittest

from scripts.market_bulletins import (
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


if __name__ == "__main__":
    unittest.main()
