"""Offline checks for topic-aware X drafts and conservative text budgets."""
from __future__ import annotations

import copy
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from scripts.build import load, share_url
from scripts.serials import _share_link, episode_url, load_serials
from scripts.social_text import truncate_x_text, x_weighted_length
from scripts.tags import article_hashtags, tags_for_episode, topic_hashtags


ROOT = Path(__file__).resolve().parents[1]


class SocialTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles = load()
        cls.serials = load_serials(ROOT)

    def draft(self, link):
        parsed = urlsplit(link)
        self.assertEqual((parsed.scheme, parsed.netloc, parsed.path),
                         ("https", "x.com", "/intent/tweet"))
        query = parse_qs(parsed.query)
        text, target = query["text"][0], query["url"][0]
        self.assertLessEqual(x_weighted_length(text + "\n" + target), 280)
        return text, target

    def test_unicode_and_generated_urls_have_conservative_weights(self):
        self.assertEqual(x_weighted_length("AIタイパ"), 8)
        self.assertEqual(x_weighted_length("a" * 280), 280)
        self.assertEqual(x_weighted_length("日" * 140), 280)
        self.assertEqual(x_weighted_length("🤔"), 2)
        self.assertEqual(x_weighted_length(
            "https://veripsa.com/articles/" + "a" * 250 + "/"), 23)

    def test_shortening_reserves_the_ellipsis_and_never_splits_a_url(self):
        self.assertEqual(truncate_x_text("日本語ABC", 7), "日本…")
        self.assertEqual(truncate_x_text("日本語", 6), "日本語")
        self.assertEqual(truncate_x_text("日本語", 0), "")
        self.assertEqual(truncate_x_text("日本語", 1), "")
        self.assertEqual(truncate_x_text("日本語", 2), "…")
        url = "https://veripsa.com/tags/ai/"
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
                     "https://veripsa.com.evil.test/" + "a" * 300):
            with self.subTest(text=text[:35]):
                self.assertGreater(x_weighted_length(text), 280)
                self.assertLessEqual(x_weighted_length(truncate_x_text(text, 150)), 150)
                article = {**self.articles[0], "title": text}
                draft, _target = self.draft(share_url(article))
                self.assertIn("…", draft)

    def test_article_share_drafts_keep_registered_themes_and_article_target(self):
        for article in self.articles:
            with self.subTest(slug=article["slug"]):
                text, target = self.draft(share_url(article))
                self.assertEqual(text.splitlines()[-1], " ".join(article_hashtags(article)))
                self.assertEqual(urlsplit(target).path, "/articles/" + article["slug"] + "/")
                self.assertEqual(parse_qs(urlsplit(target).query)["utm_campaign"], ["reader_share"])
                self.assertNotIn("#未発生新聞", text)

    def test_long_article_copy_keeps_tags_and_link_including_maximum_label_lengths(self):
        article = copy.deepcopy(self.articles[0])
        article["title"] = "長い見出し日本語AI🤔" * 70
        article["summary"] = "長い説明文" * 100
        text, target = self.draft(share_url(article))
        self.assertIn("…", text)
        self.assertEqual(text.splitlines()[-1], " ".join(article_hashtags(article)))
        long_tags = ["#" + "長" * 24, "#" + "別" * 24]
        with patch("scripts.build.article_hashtags", return_value=long_tags):
            text, long_tag_target = self.draft(share_url(article))
        self.assertEqual(long_tag_target, target)
        self.assertEqual(text.splitlines()[-1], " ".join(long_tags))

    def test_serial_share_drafts_preserve_kind_themes_and_complete_target(self):
        for series in self.serials:
            for original in series["episodes"]:
                for long_title in (False, True):
                    with self.subTest(series=series["slug"], episode=original["slug"], long=long_title):
                        episode = copy.deepcopy(original)
                        if long_title:
                            episode["title"] = "長い連載の見出しAI🤔" * 70
                        text, target = self.draft(_share_link(series, episode))
                        expected = topic_hashtags(tags_for_episode(series, episode))
                        self.assertEqual(text.splitlines()[-1], " ".join(expected))
                        self.assertEqual(target, "https://veripsa.com" + episode_url(series, episode))
                        label = ("創作推理小説" if series["kind"] == "fiction" else
                                 "出典付き市場シナリオ（投資助言ではありません）")
                        self.assertIn(label, text)
                        self.assertNotIn("#未発生新聞", text)


if __name__ == "__main__":
    unittest.main()
