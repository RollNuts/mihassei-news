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

    def test_archive_filters_have_an_accessible_group_name(self):
        # A plain div has the implicit generic role: aria-label alone is
        # ignored. Screen-reader users need a named group for these toggles.
        markup = archive(self.articles)
        group = re.search(
            r'<div class="filters" role="group" aria-label="記事のカテゴリー">(.*?)</div>',
            markup, re.S,
        )
        self.assertIsNotNone(group)
        buttons = re.findall(r'data-category-filter="([^"]+)"', group.group(1))
        expected = ["すべて"] + [
            cat for cat in CATEGORIES[1:]
            if any(a["category"] == cat for a in self.articles)
        ]
        self.assertEqual(buttons, expected)
        self.assertEqual(group.group(1).count('aria-pressed='), len(buttons))
        self.assertEqual(group.group(1).count('type="button"'), len(buttons))

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

    def test_body_precedes_save_and_sources_are_opt_in(self):
        item = self.articles[0]
        page = story(item, self.articles)
        image = page.index('<figure class="article-hero-figure">')
        body = page.index('<div class="article-body">')
        end = page.index('class="article-endmark"')
        save = page.index('<div class="article-save-inline">')
        details = page.index('<details class="source-context" id="story-source" aria-label="着想した実際の話題">')
        self.assertTrue(image < body < end < save < details)
        self.assertEqual(page.count('data-bookmark-slug="' + item["slug"] + '"'), 1)
        self.assertEqual(page.count('<details class="source-context"'), 1)
        self.assertIn('<summary>着想元を見る</summary>', page)
        self.assertIn(item["source"]["url"], page)
        from scripts.editorial_copy import source_reader_copy
        self.assertIn(source_reader_copy(item["source"]["topic"]), page)
        self.assertIn("着想元は、この記事の出来事を報じたものではありません", page)
        self.assertIn('class="fiction-mark">創作報道', page)
        self.assertNotIn('class="story-fiction-context"', page)

    def test_small_category_still_has_a_relevant_archive_link(self):
        from urllib.parse import quote
        article = self.articles[0]
        page = story(article, [article])
        expected = '/archive/?category=' + quote(article["category"])
        self.assertIn('href="' + expected + '"', page)
        self.assertIn(article["category"] + 'の記事を読む', page)
        self.assertNotIn('href="/category/', page,
                         "One story is not a substantive category index")

    def test_next_story_is_visible_immediately_after_article_ending(self):
        item = self.articles[0]
        html = story(item, self.articles)
        ending = html.index('class="article-endmark"')
        next_story = html.index('class="reader-next"')
        poll = html.index('class="future-poll"')
        source = html.index('class="source-context"')
        self.assertTrue(ending < next_story < poll < source)
        self.assertEqual(html.count('class="reader-next"'), 1)
        self.assertIn('<span class="reader-next-kicker">次の記事</span>', html)
        self.assertIn('<span class="reader-next-meta">続けて読む · ', html)
        target = re.search(r'<a href="([^"]+)" class="reader-next-title">', html)
        self.assertIsNotNone(target)
        self.assertNotEqual(target.group(1), '/articles/' + item["slug"] + '/')
        self.assertTrue(
            target.group(1).startswith('/articles/') or target.group(1) == '/archive/'
        )


if __name__ == "__main__":
    unittest.main()
