"""Reader-facing article tags stay small, explicit and useful across surfaces."""
from __future__ import annotations

import unittest
from xml.etree import ElementTree as ET

from scripts import build


class ArticleTagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles = build.load()

    def test_every_published_article_has_small_distinct_topics(self):
        self.assertTrue(self.articles)
        for article in self.articles:
            tags = article["tags"]
            self.assertGreaterEqual(len(tags), 1)
            self.assertLessEqual(len(tags), 3)
            self.assertEqual(len(tags), len(set(tags)))
            self.assertNotIn(article["category"], tags)
            for tag in tags:
                self.assertEqual(tag, tag.strip())
                self.assertLessEqual(len(tag), 12)
                self.assertNotRegex(tag, r"[\\s#|]")

    def test_archive_exposes_shareable_tag_filters_and_search_data(self):
        page = build.archive(self.articles)
        self.assertIn('data-tag-panel', page)
        self.assertIn('data-tag-filter="AI"', page)
        self.assertIn('data-tag-filter="タイパ"', page)
        ai_article = next(article for article in self.articles if "AI" in article["tags"])
        encoded_tags = "|".join(ai_article["tags"])
        self.assertIn('data-tags="' + encoded_tags + '"', page)
        self.assertIn(build.tag_url("タイパ"), page)

    def test_story_links_tags_and_exposes_structured_keywords(self):
        article = next(article for article in self.articles
                       if article["slug"] == "music-ai-friends-monopolize-the-song")
        page = build.story(article, self.articles)
        self.assertIn('aria-label="話題タグ"', page)
        self.assertIn('href="/archive/?tag=AI"', page)
        self.assertIn('property="article:tag" content="AI"', page)
        self.assertIn('"keywords": ["AI", "音楽"]', page)

    def test_rss_keeps_newspaper_category_and_topic_tags(self):
        article = next(article for article in self.articles if "AI" in article["tags"])
        xml = build.generate_rss(
            [article], "test", "test", build.SITE, "/feed.xml", limit=1
        )
        root = ET.fromstring(xml)
        values = [node.text for node in root.findall("./channel/item/category")]
        self.assertIn(article["category"], values)
        for tag in article["tags"]:
            self.assertIn(tag, values)


if __name__ == "__main__":
    unittest.main()
