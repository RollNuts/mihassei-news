"""Regression checks for a lean, easy-to-browse satirical newspaper.

All checks are local: no analytics writes, paid APIs, or deployment needed.
"""
import copy
import re
from html import escape as escape_html
import unittest

from scripts.build import CATEGORIES, archive, load, story
from scripts.publication_inputs import publication_json_snapshot
from tests.site_build_fixture import built_text


class ReaderFlowPolishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles = load()
        cls.archive_html = built_text("archive/index.html")

    def test_archive_does_not_offer_empty_categories_or_one_choice_decade(self):
        html = self.archive_html
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
        with publication_json_snapshot():
            multi_html = archive(multi)
        self.assertRegex(
            multi_html,
            r'<select class="decade-select" data-decade-filter[^>]*>',
        )
        self.assertNotRegex(
            multi_html, r'<select class="decade-select" data-decade-filter[^>]* hidden>'
        )

    def test_archive_filters_have_an_accessible_group_name(self):
        # A plain div has the implicit generic role: aria-label alone is
        # ignored. Screen-reader users need a named group for these toggles.
        markup = self.archive_html
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
        html = self.archive_html
        start = html.index('id="archive-results"')
        empty = html.index('data-no-results hidden')
        reset = html.index('data-reset-filters')
        rss = html.index('class="category-feed-subscriptions"')
        self.assertLess(start, empty)
        self.assertLess(empty, reset)
        self.assertLess(reset, rss)
        self.assertIn('type="button" data-reset-filters', html)
        self.assertIn("絞り込みを解除", html)

    def test_visible_source_belongs_to_story_before_reader_tools(self):
        item = self.articles[0]
        page = story(item, self.articles)
        image = page.index('<figure class="article-hero-figure">')
        body = page.index('<div class="article-body">')
        end = page.index('class="article-endmark"')
        save = page.index('<div class="article-save-inline">')
        source = page.index('<aside class="source-context" id="story-source" aria-labelledby="story-source-heading">')
        self.assertTrue(image < body < end < source < save)
        self.assertEqual(page.count('data-bookmark-slug="' + item["slug"] + '"'), 1)
        self.assertEqual(page.count('<aside class="source-context"'), 1)
        self.assertIn('<h2 id="story-source-heading">この創作の着想元</h2>', page)
        note = page[source:page.index("</aside>", source)]
        self.assertEqual(note.count(f'href="{escape_html(item["source"]["url"], quote=True)}"'), 1)
        self.assertIn(escape_html(item["source"]["publisher"]), note)
        self.assertIn(f'<time datetime="{item["source"]["published_at"][:10]}">', note)
        from scripts.editorial_copy import source_reader_copy
        self.assertIn(escape_html(source_reader_copy(item["source"]["topic"])), note)
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

    def test_next_story_follows_source_before_interactions(self):
        item = self.articles[0]
        html = story(item, self.articles)
        ending = html.index('class="article-endmark"')
        next_story = html.index('class="reader-next"')
        poll = html.index('class="future-poll"')
        source = html.index('class="source-context"')
        self.assertTrue(ending < source < next_story < poll)
        self.assertEqual(html.count('class="reader-next"'), 1)
        self.assertIn('<span class="reader-next-kicker">次の記事</span>', html)
        self.assertRegex(
            html,
            r'<span class="reader-next-meta">(同じ話題|続けて読む) · ',
        )
        self.assertRegex(
            html,
            r'data-reader-next-mode="(related-topic|sequential|archive)"',
        )
        target = re.search(r'<a href="([^"]+)" class="reader-next-title">', html)
        self.assertIsNotNone(target)
        self.assertNotEqual(target.group(1), '/articles/' + item["slug"] + '/')
        self.assertTrue(
            target.group(1).startswith('/articles/') or target.group(1) == '/archive/'
        )


if __name__ == "__main__":
    unittest.main()
