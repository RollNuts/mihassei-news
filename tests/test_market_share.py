"""Daily factual-market sharing is article-specific and never auto-posts."""
import html
import json
import re
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from scripts.market_bulletins import render_market_bulletin, render_market_bulletins_archive
from scripts.market_outlooks import render_market_outlook_archive
from scripts.market_sharing import render_market_share
from scripts.serials import render_series_page, render_episode_page


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
        m = re.search(r'<a href="([^"]+)"[^>]+>Xで共有', output)
        self.assertIsNotNone(m)
        dest = urlsplit(html.unescape(m.group(1)))
        self.assertEqual((dest.scheme, dest.netloc, dest.path),
                         ("https", "x.com", "/intent/tweet"))
        q = parse_qs(dest.query)
        self.assertEqual(q["url"], ["https://veripsa.com/serials/market-lab/briefings/2026-11-16-am/"])
        self.assertIn("TOPIX", q["text"][0])
        self.assertIn('target="_blank" rel="noopener noreferrer external"', output)
        self.assertIn('data-comments data-article="market-briefing-2026-11-16-am"', output)  # real moderated comments

    def test_all_five_market_page_types_have_one_canonical_share_bar_in_the_header(self):
        root = Path(__file__).resolve().parents[1]
        market = next(series for series in json.loads((root / "content/serials.json").read_text())
                      if series["slug"] == "market-lab")
        ep = market["episodes"][-1]
        report = {"slug": "2026-11-16-am", "slot": "am", "day": "2026-11-16",
                  "published_at": "2026-11-16T08:30:00+09:00", "title": "TOPIXの見通し",
                  "summary": "日本株の実データに基づく観測。", "lead": "一次資料の解説です。",
                  "sections": [], "sources": [], "figures": []}
        pages = [
            (render_series_page(market, content_layout), "/serials/market-lab/", market["title"]),
            (render_episode_page(market, ep, content_layout), "/serials/market-lab/" + ep["slug"] + "/", ep["title"]),
            (render_market_bulletin(report, content_layout), "/serials/market-lab/briefings/2026-11-16-am/", report["title"]),
            (render_market_bulletins_archive([], content_layout), "/serials/market-lab/briefings/", "日次市況"),
            (render_market_outlook_archive([], content_layout), "/serials/market-lab/outlooks/", "日々の予測・振り返り"),
        ]
        for markup, route, title in pages:
            with self.subTest(route=route):
                canonical = "https://veripsa.com" + route
                self.assertEqual(markup.count('class="market-share"'), 1)
                self.assertLess(markup.index('class="market-share"'), markup.index('</header>'))
                self.assertEqual(markup.count('data-copy-url='), 1)
                self.assertIn('data-copy-url="' + canonical + '"', markup)
                self.assertIn('data-share-url="' + canonical + '"', markup)
                self.assertIn('data-share-title="' + html.escape(title, quote=True) + '" hidden', markup)
                intent = html.unescape(re.search(r'<a href="([^"]+)"[^>]+>Xで共有', markup)[1])
                query = parse_qs(urlsplit(intent).query)
                self.assertEqual(query["url"], [canonical])
                self.assertEqual(query["text"], [title + "｜実データの市場分析"])

    def test_shared_url_omits_analytics_query_and_rejects_other_destinations(self):
        markup = render_market_share("https://veripsa.com/serials/market-lab/?analytics=off#market-stories", "相場分析")
        self.assertNotIn("analytics", markup)
        self.assertNotIn("#market-stories", markup)
        self.assertIn('data-copy-url="https://veripsa.com/serials/market-lab/"', markup)
        for bad in ("https://elsewhere.example/serials/market-lab/", "/articles/other/", "/serials/market-lab/../private/"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                render_market_share(bad, "相場分析")


if __name__ == "__main__":
    unittest.main()
