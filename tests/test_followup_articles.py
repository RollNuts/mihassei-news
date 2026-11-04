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
        child = copy.deepcopy(self.articles[0])
        parent = copy.deepcopy(self.articles[1])
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
