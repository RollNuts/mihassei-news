"""Offline coverage for article hashtags and the ordinary X post length limit."""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from scripts.social_text import (
    X_TEXT_LIMIT, article_hashtags, truncate_x_text, x_weighted_length,
)


ROOT = Path(__file__).resolve().parents[1]


class SocialTextTests(unittest.TestCase):
    def test_ascii_japanese_and_link_budgets_at_the_280_boundary(self):
        self.assertEqual(X_TEXT_LIMIT, 280)
        self.assertEqual(x_weighted_length("AIタイパ"), 8)
        self.assertEqual(x_weighted_length("a" * 280), 280)
        self.assertEqual(x_weighted_length("日" * 140), 280)
        self.assertEqual(x_weighted_length("🤔"), 2)
        url = "https://veripsa.com/articles/" + "a" * 250 + "/"
        self.assertEqual(x_weighted_length(url), 23)
        boundary = "日" * 128 + "\n" + url
        self.assertEqual(x_weighted_length(boundary), 280)
        self.assertEqual(truncate_x_text(boundary, 280), boundary)
        self.assertEqual(x_weighted_length("a" + boundary), 281)

    def test_shortening_preserves_ellipsis_and_complete_first_party_links(self):
        self.assertEqual(truncate_x_text("日本語ABC", 7), "日本…")
        self.assertEqual(truncate_x_text("日本語", 6), "日本語")
        self.assertEqual(truncate_x_text("日本語", 0), "")
        self.assertEqual(truncate_x_text("日本語", 1), "")
        self.assertEqual(truncate_x_text("日本語", 2), "…")
        url = "https://veripsa.com/archive/?tag=AI"
        self.assertEqual(truncate_x_text(url, 22), "…")
        self.assertEqual(truncate_x_text(url, 23), url)
        self.assertLessEqual(x_weighted_length(
            truncate_x_text("日本語とAI🤔 " * 70, 231)), 231)
        for invalid in (-1, 2.5, True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                truncate_x_text("見出し", invalid)

    def test_url_looking_prose_cannot_bypass_the_text_budget(self):
        for text in ("https://" + "日" * 300,
                     "https://veripsa.com/" + "日" * 300,
                     "https://veripsa.com.evil.test/" + "a" * 300,
                     "https://elsewhere.example/" + "a" * 300):
            with self.subTest(text=text[:35]):
                plain_weight = sum(1 if ord(char) < 128 else 2 for char in text)
                self.assertEqual(x_weighted_length(text), plain_weight)
                self.assertGreater(plain_weight, 280)
                shortened = truncate_x_text(text, 150)
                self.assertTrue(shortened.endswith("…"))
                self.assertLessEqual(x_weighted_length(shortened), 150)

    def test_ellipsis_after_a_url_preserves_its_separator_and_budget(self):
        url = "https://veripsa.com/articles/" + "a" * 250 + "/"
        for separator in (" ", "\n", "\u3000"):
            for budget, expected in ((25, "…"), (26, url + " …"), (27, url + " …")):
                with self.subTest(separator=repr(separator), budget=budget):
                    shortened = truncate_x_text(url + separator + "日本語の説明", budget)
                    self.assertEqual(shortened, expected)
                    self.assertLessEqual(x_weighted_length(shortened), budget)
                    self.assertNotIn(url + "…", shortened)

    def test_multiple_urls_are_kept_whole_at_each_truncation_boundary(self):
        first = "https://veripsa.com/articles/" + "a" * 250 + "/"
        second = "https://veripsa.com/articles/" + "b" * 250 + "/"
        text = first + " " + second + " 日本語の説明"
        for budget, expected in ((49, first + " …"),
                                 (50, first + " " + second + " …"),
                                 (51, first + " " + second + " …")):
            with self.subTest(budget=budget):
                self.assertEqual(truncate_x_text(text, budget), expected)
        for budget in range(x_weighted_length(text) + 1):
            with self.subTest(budget=budget):
                shortened = truncate_x_text(text, budget)
                self.assertLessEqual(x_weighted_length(shortened), budget)
                # Every retained URL starts and ends intact, even when there is
                # room for a URL token but not its following ellipsis separator.
                self.assertEqual(shortened.count("https://"),
                                 shortened.count(first) + shortened.count(second))
                self.assertNotIn(first + "…", shortened)
                self.assertNotIn(second + "…", shortened)

    def test_long_title_budget_preserves_both_topics_and_full_article_url(self):
        prefix = "【未発生新聞】\n"
        article = {"tags": ["AI", "働き方"], "x_tags": ["長" * 12, "別" * 12, "ポイ活"]}
        hashtags = " ".join(article_hashtags(article))
        url = "https://veripsa.com/articles/" + "a" * 100 + "/"
        ending = "\n\n" + hashtags + "\n\n" + url
        budget = X_TEXT_LIMIT - x_weighted_length(prefix + ending)
        headline = truncate_x_text("日本語の長い見出しAI🤔" * 80, budget)
        text = prefix + headline + ending
        self.assertTrue(headline.endswith("…"))
        self.assertLessEqual(x_weighted_length(text), 280)
        self.assertTrue(text.endswith(ending))
        self.assertEqual(article_hashtags(article), ["#" + "長" * 12, "#" + "別" * 12])

    def test_topic_order_duplicates_and_two_tag_cap(self):
        article = {
            "tags": ["SNS", "アプリ"],
            "x_tags": ["タイパ", "タイパ", "推し活", "炎上"],
        }
        before = copy.deepcopy(article)
        self.assertEqual(article_hashtags(article), ["#タイパ", "#推し活"])
        self.assertEqual(article_hashtags(article, limit=8), ["#タイパ", "#推し活"])
        self.assertEqual(article_hashtags(article, limit=1), ["#タイパ"])
        self.assertEqual(article_hashtags(article, limit=0), [])
        self.assertEqual(article_hashtags(article, limit=-1), [])
        self.assertEqual(article, before)
        self.assertEqual(
            article_hashtags({"x_tags": ["SNS", "アプリ", "ポイ活"]}),
            ["#ポイ活"],
        )
        self.assertEqual(
            article_hashtags({"x_tags": ["生成AI", "生成AI", "推し活"]}),
            ["#生成AI", "#推し活"],
        )
        self.assertEqual(
            article_hashtags({"satire_tags": ["家族あるある", "本末転倒"]}),
            ["#家族あるある"],
        )

    def test_invalid_labels_are_skipped_whole_without_changing_the_topic(self):
        invalid = ["#AI", "＃AI", "AI news", "AI/仕事", "C++", "猫!", "☀",
                   "123", "___", "日" * 13, "AI\n仕事", " AI", "", 3, None, {}]
        self.assertEqual(article_hashtags({"x_tags": invalid}), [])
        self.assertEqual(
            article_hashtags({"x_tags": invalid + ["SNS", "アプリ", "ポイ活", "タイパ"]}),
            ["#ポイ活", "#タイパ"],
        )
        self.assertEqual(article_hashtags({"x_tags": ["AI_仕事", "3D"]}),
                         ["#AI_仕事", "#3D"])

    def test_missing_or_malformed_topics_do_not_invent_hashtags(self):
        for article in ({}, {"x_tags": None}, {"x_tags": "ポイ活"}, {"x_tags": {}},
                        {"x_tags": [], "tags": ["SNS", "アプリ"]}):
            with self.subTest(article=article):
                self.assertEqual(article_hashtags(article), [])
        for invalid_limit in (True, 1.5, "2"):
            with self.subTest(invalid=invalid_limit), self.assertRaises(ValueError):
                article_hashtags({"x_tags": ["ポイ活"]}, limit=invalid_limit)

    def test_current_articles_use_context_terms_not_broad_categories(self):
        articles = json.loads((ROOT / "content/articles.json").read_text(encoding="utf-8"))
        self.assertTrue(articles)
        generic = {"#SNS", "#アプリ", "#社会", "#生活", "#暮らし", "#家族",
                   "#イベント", "#仕事", "#働き方", "#メディア", "#AI"}
        for article in articles:
            with self.subTest(slug=article["slug"]):
                hashtags = article_hashtags(article)
                self.assertLessEqual(len(hashtags), 2)
                self.assertFalse(generic.intersection(hashtags))
        walking = next(
            item for item in articles
            if item["slug"] == "walking-points-past-own-front-door"
        )
        self.assertIn("#ポイ活", article_hashtags(walking))


if __name__ == "__main__":
    unittest.main()
