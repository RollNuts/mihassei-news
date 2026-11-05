"""Follow-up article chronology and reader navigation regression tests."""
from __future__ import annotations

import copy
import unittest

from scripts import build


class FollowupArticleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles = build.load()

    def pair(self):
        # A real newest article can itself be a follow-up. Synthetic parent
        # fixtures must not inherit a followup_of link from editorial data.
        standalone = [copy.deepcopy(a) for a in self.articles if not a.get("followup_of")]
        child, parent = standalone[:2]
        self.assertGreater(build.publication_date(child), build.publication_date(parent))
        child["followup_of"] = parent["slug"]
        return child, parent

    def test_followup_relationship_requires_existing_older_parent(self):
        child, parent = self.pair()
        build.validate_followup_relationships([child, parent])

        missing = copy.deepcopy(child)
        missing["followup_of"] = "does-not-exist"
        with self.assertRaisesRegex(ValueError, "Invalid followup_of"):
            build.validate_followup_relationships([missing, parent])

        backwards = copy.deepcopy(parent)
        backwards["followup_of"] = child["slug"]
        with self.assertRaisesRegex(ValueError, "published after"):
            build.validate_followup_relationships([child, backwards])

    def test_followup_chain_stops_after_two_followups(self):
        first = copy.deepcopy(self.articles[3])
        second = copy.deepcopy(self.articles[2])
        third = copy.deepcopy(self.articles[1])
        fourth = copy.deepcopy(self.articles[0])
        # Synthetic chain fixtures must not inherit real editorial follow-up links.
        for article in (first, second, third, fourth):
            article.pop("followup_of", None)
        second["followup_of"] = first["slug"]
        third["followup_of"] = second["slug"]
        build.validate_followup_relationships([fourth, third, second, first])

        fourth["followup_of"] = third["slug"]
        with self.assertRaisesRegex(ValueError, "initial story plus two follow-ups"):
            build.validate_followup_relationships([fourth, third, second, first])

    def test_followup_article_links_back_to_previous_story(self):
        child, parent = self.pair()
        page = build.story(child, [child, parent])
        self.assertIn('class="followup-label">続報</span>', page)
        self.assertIn('class="story-followup-context"', page)
        self.assertIn('前の記事：' + parent["title"], page)
        self.assertIn('href="' + build.article_url(parent) + '"', page)

    def test_parent_article_links_forward_to_followup(self):
        child, parent = self.pair()
        page = build.story(parent, [child, parent])
        self.assertIn('id="story-followups-title">この話題の続報</h2>', page)
        self.assertIn(child["title"], page)
        self.assertIn('href="' + build.article_url(child) + '"', page)
        self.assertNotIn('class="followup-label">続報</span>', build.category_meta(parent, compact=True))

    def test_followup_badge_appears_on_cards_without_changing_category(self):
        child, parent = self.pair()
        meta = build.category_meta(child, compact=True)
        self.assertIn('class="followup-label">続報</span>', meta)
        self.assertIn('class="category">' + child["category"] + '</span>', meta)
        self.assertNotIn('class="followup-label">続報</span>', build.card(parent))


if __name__ == "__main__":
    unittest.main()
