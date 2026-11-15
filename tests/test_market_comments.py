"""Factual market comments reuse the existing moderation UI and Worker API."""
from pathlib import Path
import json
import unittest

from scripts.build import layout
from scripts.serials import render_episode_page
from scripts.market_bulletins import render_market_bulletin

ROOT = Path(__file__).resolve().parents[1]
MARKET = next(x for x in json.loads((ROOT / "content/serials.json").read_text(encoding="utf-8")) if x["slug"] == "market-lab")


class MarketCommentsTests(unittest.TestCase):
    def test_all_published_market_episodes_have_moderated_reader_comment_forms(self):
        for ep in MARKET["episodes"]:
            with self.subTest(slug=ep["slug"]):
                markup = render_episode_page(MARKET, ep, layout)
                key = "market-episode-" + ep["slug"]
                self.assertIn('data-comments data-article="' + key + '"', markup)
                self.assertIn('id="reader-comments"', markup)
                self.assertIn("/assets/comments.js?v=", markup)
                self.assertIn("この市場分析へのコメント", markup)
                self.assertIn("Xで共有する", markup)
                self.assertIn('data-copy-url="https://veripsa.com/serials/market-lab/' + ep["slug"] + '/"', markup)
                self.assertNotIn("投票フォーム", markup)

    def test_daily_bulletin_has_comments_and_its_own_x_share(self):
        sample = {
            "slug": "2026-11-16-am", "slot": "am", "day": "2026-11-16",
            "title": "TOPIXと為替の見通し", "summary": "取引所の実データを確認する相場解説です。",
            "lead": "実際の資料に沿った解説です。",
            "published_at": "2026-11-16T08:30:00+09:00",
            "figures": [], "sources": [], "sections": [], "picks": [],
        }
        markup = render_market_bulletin(sample, layout)
        self.assertIn('data-comments data-article="market-briefing-2026-11-16-am"', markup)
        self.assertIn('id="reader-comments"', markup)
        self.assertIn("/assets/comments.js?v=", markup)
        self.assertIn("この市況へのコメント", markup)
        self.assertIn('https://x.com/intent/tweet?', markup.replace("&amp;", "&"))
        self.assertIn('data-copy-url="https://veripsa.com/serials/market-lab/briefings/2026-11-16-am/"', markup)
        self.assertIn("架空ニュースではありません", markup)

    def test_shared_comment_form_preserves_original_news_behavior(self):
        from scripts.reader_comments import render_reader_comments
        news = render_reader_comments("example-story")
        market = render_reader_comments("market-episode-power-grid-after-ai-01")
        self.assertIn('data-article="example-story"', news)
        self.assertIn('data-article="market-episode-power-grid-after-ai-01"', market)
        self.assertIn('maxlength="180"', market)
        self.assertEqual(news.count('id="reader-comments"'), 1)


if __name__ == "__main__":
    unittest.main()
