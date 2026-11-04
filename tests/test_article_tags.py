"""Reader-facing article tags stay small, explicit and useful across surfaces."""
from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree as ET

from scripts import build
from scripts.social_text import X_TEXT_LIMIT, x_weighted_length


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

    def test_reader_x_share_reuses_topics_and_fits_with_the_link(self):
        for article in self.articles:
            with self.subTest(slug=article["slug"]):
                params = parse_qs(urlparse(build.share_url(article)).query)
                text, url = params["text"][0], params["url"][0]
                self.assertEqual(text.splitlines()[-1],
                                 " ".join("#" + tag for tag in article["tags"][:2]))
                self.assertIn(article["title"], text)
                self.assertEqual(url.split("?")[0], build.SITE + build.article_url(article))
                self.assertLessEqual(x_weighted_length(text + "\n\n" + url), X_TEXT_LIMIT)

    def test_long_reader_share_preserves_topics_and_url(self):
        article = {**self.articles[0], "title": "長い見出し" * 100,
                   "summary": "長い要約" * 100, "tags": ["タイパ", "ドラマ", "テレビ"]}
        params = parse_qs(urlparse(build.share_url(article)).query)
        text, url = params["text"][0], params["url"][0]
        self.assertIn("…", text)
        self.assertTrue(text.endswith("#タイパ #ドラマ"))
        self.assertNotIn("#テレビ", text)
        self.assertEqual(url.split("?")[0], build.SITE + build.article_url(article))
        self.assertLessEqual(x_weighted_length(text + "\n\n" + url), X_TEXT_LIMIT)


if __name__ == "__main__":
    unittest.main()
