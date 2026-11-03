"""Satire editing regression checks that survive genuine headline and body rewrites.

The seven-day experiment can use short or longer pieces. Tests protect publication
facts, source provenance and rendering, NOT a frozen joke or specific wording.
"""
import unittest
from datetime import datetime

from scripts.build import load, story


class SatirePilotTests(unittest.TestCase):
    def test_revised_issues_keep_real_dates_and_original_sources(self):
        known = {
            "press-conference-metaphor-viral": (
                "2026-11-03T08:08:49+09:00",
                "https://www.itmedia.co.jp/news/article/2611/02/2000002828/"),
            "idol-ice-kept-in-freezer": (
                "2026-11-03T08:08:49+09:00",
                "https://prtimes.jp/main/html/rd/p/000000097.000086324.html"),
            "supporter-guarantor-contract": (
                "2026-11-02T19:16:37+09:00",
                "https://kumanichi.com/articles/2053373"),
            "quiet-reply-dating-matches": (
                "2026-11-03T07:40:27+09:00",
                "https://www.reuters.com/technology/tinders-us-daily-active-users-return-growth-october-match-ramps-up-ai-efforts-2026-11-02/"),
            "local-souvenir-cannot-leave-town": (
                "2026-11-02T21:21:04+09:00",
                "https://news.livedoor.com/article/detail/32715693/"),
            "farm-friendship-across-species": (
                "2026-11-03T07:40:27+09:00",
                "https://www.theguardian.com/science/2026/nov/02/tender-bond-male-donkey-bull-baffles-scientists"),
            "robot-job-hopping": (
                "2026-11-02T17:36:39+09:00",
                "https://newsdig.tbs.co.jp/articles/sbc/3055301"),
        }
        articles = {a["slug"]: a for a in load()}
        for slug, (original_date, original_url) in known.items():
            with self.subTest(slug=slug):
                a = articles[slug]
                self.assertEqual(a["published_at"], original_date)
                self.assertEqual(a["source"]["url"], original_url)
                real = datetime.fromisoformat(a["source"]["published_at"])
                future = datetime.fromisoformat(a["fiction_at"])
                self.assertEqual(future, real.replace(year=real.year + 5))
                self.assertGreater(datetime.fromisoformat(a["modified_at"]),
                                   datetime.fromisoformat(a["published_at"]))
                self.assertTrue(2 <= len(a["paragraphs"]) <= 6)
                self.assertGreaterEqual(sum(len(p) for p in a["paragraphs"]), 180)

    def test_short_and_long_stories_render_without_changing_schema(self):
        articles = load()
        by_slug = {a["slug"]: a for a in articles}
        for slug in ("press-conference-metaphor-viral", "idol-ice-kept-in-freezer"):
            a = by_slug[slug]
            for paragraph_count in (2, len(a["paragraphs"])):
                with self.subTest(slug=slug, paragraphs=paragraph_count):
                    candidate = {**a, "paragraphs": a["paragraphs"][:paragraph_count]}
                    markup = story(candidate, articles)
                    body = markup.split('<div class="article-body">', 1)[1].split("</div>", 1)[0]
                    self.assertEqual(body.count("<p>"), paragraph_count)
                    self.assertEqual(body.count("</p>"), paragraph_count)
                    self.assertIn("着想元", markup)
                    self.assertIn("創作ニュース", markup)
                    self.assertIn(a["title"], markup)
                    self.assertIn('data-future-poll', markup)
                    self.assertIn('aria-label="着想した実際の話題"', markup)
                    self.assertIn('content="https://veripsa.com/og/articles/'+slug+'.png', markup)
        # This does not force all future stories to be two paragraphs.
        self.assertTrue(any(len(a["paragraphs"]) >= 3 for a in articles))


if __name__ == "__main__":
    unittest.main()
