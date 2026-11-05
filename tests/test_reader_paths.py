"""No-paid-API topic recommendations, SEO, and subscription regression tests."""
from __future__ import annotations

import copy
import html
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

    def test_pv_recommendations_cover_archive_and_next_link_advances_sequentially(self):
        available = {a["slug"] for a in self.articles}
        ordered = sorted(self.articles, key=lambda a: (
            build.publication_date(a), a["source"]["published_at"]
        ), reverse=True)
        for index, item in enumerate(ordered):
            page = build.story(item, self.articles)
            lead = re.search(r'<a href="([^"]+)" class="reader-next-title">', page)
            self.assertIsNotNone(lead, item["slug"])
            self.assertIn('data-popular-fallback', page)
            self.assertIn('<span class="reader-next-kicker">次の記事</span>', page)
            self.assertIn('<span class="reader-next-meta">続けて読む · ', page)
            expected = (
                build.article_url(ordered[index + 1])
                if index + 1 < len(ordered) else "/archive/"
            )
            self.assertEqual(lead.group(1), expected)
            raw = re.search(r'data-popular-options="([^"]+)"', page)
            self.assertIsNotNone(raw)
            candidates = json.loads(html.unescape(raw.group(1)))
            slugs = [a["slug"] for a in candidates]
            self.assertEqual(len(slugs), len(set(slugs)))
            self.assertEqual(set(slugs), available - {item["slug"]})
            self.assertIn('aria-labelledby="readers-choice-title" hidden', page)

        # The first two stories must not point back at each other.
        first = build.story(ordered[0], self.articles)
        second = build.story(ordered[1], self.articles)
        first_target = re.search(r'class="reader-next-title"[^>]*href="([^"]+)"|href="([^"]+)" class="reader-next-title"', first)
        second_target = re.search(r'class="reader-next-title"[^>]*href="([^"]+)"|href="([^"]+)" class="reader-next-title"', second)
        self.assertIsNotNone(first_target)
        self.assertIsNotNone(second_target)
        first_href = next(value for value in first_target.groups() if value)
        second_href = next(value for value in second_target.groups() if value)
        self.assertEqual(first_href, build.article_url(ordered[1]))
        self.assertEqual(second_href, build.article_url(ordered[2]))

    def test_related_topic_links_use_existing_shared_tags(self):
        source = next(
            item for item in self.articles
            if any(
                item["slug"] != other["slug"]
                and set(build.article_tags(item)) & set(build.article_tags(other))
                for other in self.articles
            )
        )
        ranked = suggested_articles(source, self.articles, limit=len(self.articles))
        expected = [
            item for item in ranked
            if set(build.article_tags(source)) & set(build.article_tags(item))
        ][:3]
        self.assertTrue(expected)
        page = build.story(source, self.articles)
        self.assertIn('class="topic-related"', page)
        related = page.split('<section class="topic-related"', 1)[1].split('</section>', 1)[0]
        links = re.findall(r'<h3><a href="([^"]+)">', related)
        self.assertEqual(links, [build.article_url(item) for item in expected])
        source_tags = set(build.article_tags(source))
        for item in expected:
            self.assertTrue(source_tags & set(build.article_tags(item)))

    def test_home_topic_navigation_only_links_indexable_clusters(self):
        page = build.home(self.articles)
        section = page.split('<section class="topic-explore genre-explore"', 1)[1].split('</section>', 1)[0]
        links = re.findall(r'href="([^"]+)"', section)
        indexable = {build.tag_url(tag) for tag in build.curated_topics(self.articles)}
        self.assertTrue(links)
        self.assertTrue(set(links) <= indexable)

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
