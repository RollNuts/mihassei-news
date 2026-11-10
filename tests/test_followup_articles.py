"""Follow-up article chronology and reader navigation regression tests."""
from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from scripts import build
from scripts.audit_search_index import check_followup_search_paths


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

    def test_full_three_story_chain_is_crawlable_and_independently_indexed(self):
        # Use the real HTML renderer and static pages, not mocked links.
        first, middle, last = [copy.deepcopy(a) for a in self.articles[3:0:-1]]
        for article in (first, middle, last):
            article.pop("followup_of", None)
        middle["followup_of"] = first["slug"]
        last["followup_of"] = middle["slug"]
        chain = [last, middle, first]
        build.validate_followup_relationships(chain)
        urls = {build.article_url(a) for a in chain}

        with tempfile.TemporaryDirectory() as temp:
            dist = Path(temp)
            for item in chain:
                route = dist / build.article_url(item).lstrip("/") / "index.html"
                route.parent.mkdir(parents=True, exist_ok=True)
                route.write_text(build.story(item, chain), encoding="utf-8")

            errors, count = check_followup_search_paths(dist, chain, urls)
            self.assertEqual(errors, [])
            self.assertEqual(count, 2)

            # The middle story must link backward and forward via anchors.
            middle_file = dist / build.article_url(middle).lstrip("/") / "index.html"
            original = middle_file.read_text(encoding="utf-8")
            self.assertIn('class="story-followup-context"', original)
            self.assertIn('class="story-followups"', original)

            middle_file.write_text(
                original.replace('class="story-followups"',
                                 'class="missing-followups"'), encoding="utf-8"
            )
            errors, _ = check_followup_search_paths(dist, chain, urls)
            self.assertTrue(any("Parent lacks explicit ordinary follow-up link" in x
                                for x in errors))
            middle_file.write_text(original, encoding="utf-8")

            last_file = dist / build.article_url(last).lstrip("/") / "index.html"
            original_last = last_file.read_text(encoding="utf-8")
            last_file.write_text(
                original_last.replace('class="story-followup-context"',
                                      'class="missing-context"'), encoding="utf-8"
            )
            errors, _ = check_followup_search_paths(dist, chain, urls)
            self.assertTrue(any("Follow-up lacks explicit ordinary previous-story link" in x
                                for x in errors))
            last_file.write_text(original_last, encoding="utf-8")

            # A sitemap omission fails even when both HTML files exist.
            errors, _ = check_followup_search_paths(
                dist, chain, urls - {build.article_url(middle)}
            )
            self.assertTrue(any("missing from sitemap" in x for x in errors))

            last_file.write_text(
                original_last.replace('class="followup-label"',
                                      'class="missing-label"'), encoding="utf-8"
            )
            errors, _ = check_followup_search_paths(dist, chain, urls)
            self.assertTrue(any("visible badge missing" in x for x in errors))

    def test_followup_auditor_rejects_exact_body_or_summary_recycling(self):
        first, middle = [copy.deepcopy(a) for a in self.articles[2:0:-1]]
        for article in (first, middle):
            article.pop("followup_of", None)
        middle["followup_of"] = first["slug"]
        middle["paragraphs"] = copy.deepcopy(first["paragraphs"])
        articles = [middle, first]
        with tempfile.TemporaryDirectory() as temp:
            dist = Path(temp)
            for item in articles:
                target = dist / build.article_url(item).lstrip("/") / "index.html"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(build.story(item, articles), encoding="utf-8")
            errors, checked = check_followup_search_paths(
                dist, articles, {build.article_url(a) for a in articles}
            )
            self.assertEqual(checked, 1)
            self.assertTrue(any("duplicates parent" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
