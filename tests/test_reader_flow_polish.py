"""Regression checks for a lean, easy-to-browse satirical newspaper.

All checks are local: no analytics writes, paid APIs, or deployment needed.
"""
import copy
import re
import unittest

from scripts.build import CATEGORIES, archive, load, story


class ReaderFlowPolishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles = load()

    def test_archive_does_not_offer_empty_categories_or_one_choice_decade(self):
        html = archive(self.articles)
        present = {a["category"] for a in self.articles}
        for category in CATEGORIES[1:]:
            self.assertEqual(
                f'data-category-filter="{category}"' in html,
                category in present,
                category,
            )
        self.assertIn('data-category-filter="すべて"', html)
        self.assertRegex(
            html, r'<select class="decade-select" data-decade-filter[^>]* hidden>',
            "The single-decade archive should not waste screen space",
        )
        # The filter becomes available again automatically if content spans decades.
        multi = copy.deepcopy(self.articles)
        multi[0]["fiction_at"] = "2041-11-03T08:20:00+09:00"
        self.assertRegex(
            archive(multi),
            r'<select class="decade-select" data-decade-filter[^>]*>',
        )
        self.assertNotRegex(
            archive(multi), r'<select class="decade-select" data-decade-filter[^>]* hidden>'
        )

    def test_no_results_can_be_cleared_without_scrolling_past_rss(self):
        html = archive(self.articles)
        start = html.index('id="archive-results"')
        empty = html.index('data-no-results hidden')
        reset = html.index('data-reset-filters')
        rss = html.index('class="category-feed-subscriptions"')
        self.assertLess(start, empty)
        self.assertLess(empty, reset)
        self.assertLess(reset, rss)
        self.assertIn('type="button" data-reset-filters', html)
        self.assertIn("絞り込みを解除", html)

    def test_next_story_is_visible_immediately_after_article_ending(self):
        item = self.articles[0]
        html = story(item, self.articles)
        ending = html.index('class="article-endmark"')
        next_story = html.index('class="reader-next"')
        poll = html.index('class="future-poll"')
        source = html.index('class="source-context"')
        self.assertTrue(ending < next_story < poll < source)
        self.assertEqual(html.count('class="reader-next"'), 1)
        target = re.search(r'<a href="([^"]+)" class="reader-next-title">', html)
        self.assertIsNotNone(target)
        self.assertNotEqual(target.group(1), '/articles/' + item["slug"] + '/')
        self.assertTrue(
            target.group(1).startswith('/articles/') or target.group(1) == '/archive/'
        )


if __name__ == "__main__":
    unittest.main()
