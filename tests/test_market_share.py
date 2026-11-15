"""Daily factual-market sharing is article-specific and never auto-posts."""
import html
import re
import unittest
from urllib.parse import parse_qs, urlsplit

from scripts.market_bulletins import render_market_bulletin


def content_layout(_title, _summary, _route, content, **_kwargs):
    return content


class MarketShareTests(unittest.TestCase):
    def test_daily_bulletin_has_x_intent_for_its_own_canonical_page(self):
        report = {
            "slug": "2026-11-16-am",
            "slot": "am",
            "day": "2026-11-16",
            "published_at": "2026-11-16T08:30:00+09:00",
            "title": "TOPIXの見通しと市況の整理",
            "summary": "投資助言ではない、日本株の実データに基づく観測。",
            "lead": "一次資料だけを確認した市場の解説です。",
            "sections": [], "sources": [], "figures": [],
        }
        output = render_market_bulletin(report, content_layout)
        m = re.search(r'<a href="([^"]+)"[^>]+>この記事をXで共有する', output)
        self.assertIsNotNone(m)
        dest = urlsplit(html.unescape(m.group(1)))
        self.assertEqual((dest.scheme, dest.netloc, dest.path),
                         ("https", "x.com", "/intent/tweet"))
        q = parse_qs(dest.query)
        self.assertEqual(q["url"], ["https://veripsa.com/serials/market-lab/briefings/2026-11-16-am/"])
        self.assertIn("TOPIX", q["text"][0])
        self.assertIn('target="_blank" rel="noopener noreferrer external"', output)
        self.assertNotIn("data-comments", output)  # no fictitious comments action


if __name__ == "__main__":
    unittest.main()
