"""No-paid-API topic recommendations, SEO, and subscription regression tests."""
from __future__ import annotations

import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from scripts import build
from scripts.reader_paths import suggested_articles, topic_score


class ReaderPathsAndSEOTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles = build.load()

    def test_similar_subject_beats_unrelated_recent_same_category(self):
        article = {
            "slug": "work-ai", "title": "AIが会議と職場を管理",
            "summary": "会議を自動化する", "category": "技術",
            "source": {"published_at": "2026-11-02T10:00:00+09:00", "topic": ""},
            "published_at": "2026-11-02T12:00:00+09:00",
        }
        connected = {
            "slug": "work-a", "title": "AI議長が会議を自動化",
            "summary": "", "category": "経済",
            "source": {"published_at": "2026-11-01T08:00:00+09:00", "topic": ""},
            "published_at": "2026-11-01T08:00:00+09:00",
        }
        unrelated = {
            "slug": "food-b", "title": "地元の土産に新しい菓子",
            "summary": "", "category": "技術",
            "source": {"published_at": "2026-11-02T22:00:00+09:00", "topic": ""},
            "published_at": "2026-11-02T22:00:00+09:00",
        }
        self.assertGreater(topic_score(article, connected), topic_score(article, unrelated))
        suggestions = suggested_articles(article, [unrelated, article, connected], limit=2)
        self.assertEqual([item["slug"] for item in suggestions], ["work-a", "food-b"])

    def test_unique_recency_and_fictional_date_never_controls_recommendation(self):
        article = self.articles[0]
        choices = suggested_articles(article, self.articles + [article], limit=99)
        slugs = [item["slug"] for item in choices]
        self.assertNotIn(article["slug"], slugs)
        self.assertEqual(len(slugs), len(set(slugs)))
        self.assertEqual(len(choices), len(self.articles) - 1)
        self.assertEqual(suggested_articles(article, self.articles, limit=0), [])
        self.assertEqual(suggested_articles(article, [article]), [])

    def test_next_story_never_repeats_in_related_cards(self):
        available = {a["slug"] for a in self.articles}
        for item in self.articles:
            page = build.story(item, self.articles)
            lead = re.search(r'<a href="/articles/([^/]+)/" class="reader-next-title">', page)
            self.assertIsNotNone(lead, item["slug"])
            other_links = re.findall(
                r'<section class="article-related">(.+?)</section>', page, flags=re.S
            )
            self.assertEqual(len(other_links), 1)
            related = re.findall(
                r'<h3><a href="/articles/([^/]+)/">', other_links[0]
            )
            self.assertTrue(related, item["slug"])
            combined = [lead.group(1), *related]
            self.assertEqual(len(combined), len(set(combined)))
            self.assertNotIn(item["slug"], combined)
            self.assertTrue(set(combined) <= available)
            self.assertLessEqual(len(combined), 4)

    def test_article_structured_data_uses_real_dates_and_canonical(self):
        item = self.articles[0]
        page = build.story(item, self.articles)
        script = re.search(r'<script type="application/ld\+json">(.+?)</script>', page)
        self.assertIsNotNone(script)
        schemas = json.loads(script.group(1))
        article = next(s for s in schemas if s["@type"] == "BlogPosting")
        path = "https://veripsa.com/articles/" + item["slug"] + "/"
        self.assertEqual(article["mainEntityOfPage"]["@id"], path)
        self.assertEqual(article["datePublished"], build.publication_date(item))
        self.assertEqual(article["dateModified"], build.modification_date(item))
        self.assertNotEqual(article["datePublished"], item["fiction_at"])
        self.assertIn('href="' + path + '"', page)

    def test_issue_and_category_itemlist_is_only_real_published_stories(self):
        issues = build.daily_issues(self.articles)
        day, items = next(iter(issues.items()))
        samples = [(build.issue_page(day, items, issues), items)]
        cats = build.curated_categories(self.articles)
        for cat, entries in cats.items():
            samples.append((build.category_page(cat, entries), entries))
        for markup, entries in samples:
            script = re.search(r'<script type="application/ld\+json">(.+?)</script>', markup)
            self.assertIsNotNone(script)
            structures = json.loads(script.group(1))
            listing = next(s for s in structures if s["@type"] == "ItemList")
            actual = listing["itemListElement"]
            self.assertEqual(listing["numberOfItems"], min(len(entries), 30))
            self.assertEqual(
                [entry["url"] for entry in actual],
                ["https://veripsa.com" + build.article_url(a) for a in entries[:30]],
            )
            self.assertEqual([entry["position"] for entry in actual], list(range(1, len(actual)+1)))
            for entry in actual:
                self.assertIn('href="' + entry["url"].removeprefix("https://veripsa.com") + '"', markup)
        issue_html = samples[0][0]
        self.assertIn('"@type": "BreadcrumbList"', issue_html)
        self.assertIn('"@type": "CollectionPage"', issue_html)

    def test_rss_remains_recent_but_handles_ninety_stories(self):
        template = self.articles[0]
        copies = []
        for i in range(100):
            item = copy.deepcopy(template)
            item["slug"] = f"test-feed-{i:03d}"
            item["category"] = "社会"
            item["title"] = f"テスト創作記事{i}"
            copies.append(item)
        with tempfile.TemporaryDirectory() as tmp, patch.object(build, "OUT", Path(tmp)):
            build.other_files(copies, issues={})
            root = ET.parse(Path(tmp) / "feed.xml").getroot()
            entries = root.findall("./channel/item")
            self.assertEqual(len(entries), 90)
            self.assertEqual(entries[0].findtext("category"), "社会")
            self.assertEqual(entries[-1].findtext("title"), "テスト創作記事89 | 未発生新聞（創作）")

    def test_rss_remains_discoverable_without_a_large_follow_banner(self):
        home = build.home(self.articles)
        self.assertNotIn('class="free-follow"', home)
        self.assertIn('rel="alternate" type="application/rss+xml"', home)
        self.assertIn('<a href="/feed.xml">RSS</a>', home)
        self.assertIn('href="/archive/#category-feeds"', home)
        self.assertLess(home.index('class="genre-explore"'),
                        home.index('class="extras-shelf"'))
        self.assertNotIn('type="email"', home)
        css = (Path(__file__).resolve().parents[1] / "assets/reader-first.css").read_text(encoding="utf-8")
        self.assertIn(".extras-shelf", css)
        self.assertIn("details.category-feed-subscriptions", css)
        self.assertIn("@media(max-width:660px)", css)
        self.assertEqual(css.count("{"), css.count("}"))


if __name__ == "__main__":
    unittest.main()
