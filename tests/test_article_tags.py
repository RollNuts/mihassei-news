"""Reader-facing article tags stay small, explicit and useful across surfaces."""
from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree as ET

from scripts import build
from scripts.social_text import X_TEXT_LIMIT, article_hashtags, x_weighted_length


class ArticleTagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles = build.load()

    def test_every_published_article_has_small_distinct_topics_and_safe_satire_lenses(self):
        self.assertTrue(self.articles)
        for article in self.articles:
            tags = article["tags"]
            satire_tags = article.get("satire_tags", [])
            self.assertGreaterEqual(len(tags), 1)
            self.assertLessEqual(len(tags), 3)
            self.assertLessEqual(len(satire_tags), 2)
            self.assertEqual(len(tags), len(set(tags)))
            self.assertEqual(len(satire_tags), len(set(satire_tags)))
            self.assertFalse(set(tags) & set(satire_tags))
            self.assertNotIn(article["category"], tags)
            self.assertNotIn(article["category"], satire_tags)
            for tag in tags + satire_tags:
                self.assertEqual(tag, tag.strip())
                self.assertLessEqual(len(tag), 12)
                self.assertNotRegex(tag, r"[\\s#|/?&]")
            self.assertFalse(set(satire_tags) & build.PERSON_ATTACKING_SATIRE_TAGS)

    def test_archive_exposes_shareable_tag_filters_and_search_data(self):
        page = build.archive(self.articles)
        self.assertIn('data-tag-panel', page)
        self.assertIn('data-tag-filter="AI"', page)
        self.assertIn('data-tag-filter="タイパ"', page)
        ai_article = next(article for article in self.articles if "AI" in article["tags"])
        encoded_tags = "|".join(build.article_tags(ai_article))
        self.assertIn('data-tags="' + encoded_tags + '"', page)
        self.assertIn('data-tag-filter="仕事してる感"', page)
        self.assertIn('/topics/', build.tag_url("タイパ"))

    def test_story_links_tags_and_exposes_structured_keywords(self):
        article = next(article for article in self.articles
                       if article["slug"] == "music-ai-friends-monopolize-the-song")
        page = build.story(article, self.articles)
        self.assertIn('aria-label="話題・風刺タグ"', page)
        self.assertIn('href="/topics/AI/"', page)
        self.assertIn('property="article:tag" content="AI"', page)
        self.assertIn('property="article:tag" content="AIあるある"', page)
        self.assertIn('"keywords": ["AI", "音楽", "AIあるある", "会話が止まらない"]', page)

    def test_rss_keeps_newspaper_category_and_topic_tags(self):
        article = next(article for article in self.articles if "AI" in article["tags"])
        xml = build.generate_rss(
            [article], "test", "test", build.SITE, "/feed.xml", limit=1
        )
        root = ET.fromstring(xml)
        values = [node.text for node in root.findall("./channel/item/category")]
        self.assertIn(article["category"], values)
        for tag in build.article_tags(article):
            self.assertIn(tag, values)

    def test_topic_pages_index_only_after_three_articles(self):
        groups = build.topic_groups(self.articles)
        self.assertGreaterEqual(len(groups["AI任せ"]), 3)
        indexed = build.topic_page("AI任せ", groups["AI任せ"])
        self.assertNotIn('name="robots" content="noindex', indexed)

        self.assertEqual(len(groups["考察沼"]), 1)
        thin = build.topic_page("考察沼", groups["考察沼"])
        self.assertIn('name="robots" content="noindex,follow"', thin)

    def test_indexable_topic_pages_link_only_to_established_cooccurring_topics(self):
        groups = build.topic_groups(self.articles)
        neighbors = build.related_topics("AI", groups["AI"], self.articles)
        self.assertTrue(neighbors)
        established = build.curated_topics(self.articles)
        self.assertTrue(set(neighbors) <= set(established))
        ai_slugs = {item["slug"] for item in groups["AI"]}
        for tag in neighbors:
            self.assertTrue(any(item["slug"] in ai_slugs for item in established[tag]))
        page = build.topic_page("AI", groups["AI"], self.articles)
        self.assertIn('id="topic-neighbors-title"', page)
        for tag in neighbors:
            self.assertIn('href="' + build.tag_url(tag) + '"', page)

        thin = build.topic_page("考察沼", groups["考察沼"], self.articles)
        self.assertNotIn('id="topic-neighbors-title"', thin)

    def test_topic_index_explains_satire_axis_without_person_attacks(self):
        page = build.topic_index(self.articles)
        self.assertIn("何を風刺しているか", page)
        self.assertIn("#仕事してる感", page)
        for blocked in build.PERSON_ATTACKING_SATIRE_TAGS:
            self.assertNotIn("#" + blocked, page)

    def test_reader_x_share_reuses_topics_and_fits_with_the_link(self):
        for article in self.articles:
            with self.subTest(slug=article["slug"]):
                params = parse_qs(urlparse(build.share_url(article)).query)
                text, url = params["text"][0], params["url"][0]
                expected = " ".join(article_hashtags(article)) or "#未発生新聞"
                self.assertEqual(text.splitlines()[-1], expected)
                self.assertIn(article["title"], text)
                self.assertEqual(url.split("?")[0], build.SITE + build.article_url(article))
                self.assertLessEqual(x_weighted_length(text + "\n\n" + url), X_TEXT_LIMIT)

    def test_long_reader_share_preserves_topics_and_url(self):
        article = {**self.articles[0], "title": "長い見出し" * 100,
                   "summary": "長い要約" * 100, "tags": ["メディア", "ドラマ", "テレビ"],
                   "x_tags": ["タイパ", "ドラマ沼"]}
        params = parse_qs(urlparse(build.share_url(article)).query)
        text, url = params["text"][0], params["url"][0]
        self.assertIn("…", text)
        self.assertTrue(text.endswith("#タイパ #ドラマ沼"))
        self.assertNotIn("#テレビ", text)
        self.assertNotIn("#メディア", text)
        self.assertEqual(url.split("?")[0], build.SITE + build.article_url(article))
        self.assertLessEqual(x_weighted_length(text + "\n\n" + url), X_TEXT_LIMIT)


if __name__ == "__main__":
    unittest.main()
