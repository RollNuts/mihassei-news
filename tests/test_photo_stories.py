"""Focused photo-story source, publication and safe-markup contracts."""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from scripts import photo_stories as stories

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class PhotoStoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.current = json.loads((ROOT / "content/photo_issues.json").read_text())
        cls.initial = json.loads((ROOT / "content/photo_stories.json").read_text())

    def setUp(self):
        self.catalog = copy.deepcopy(self.current)
        self.data = copy.deepcopy(self.initial)
        self.data["stories"] = self.data["stories"][:1]
        self.issue = next(issue for issue in self.catalog["issues"]
                          if issue["slug"] == self.data["stories"][0]["source"]["issue_slug"])
        self.published = [self.issue]

    def validate(self, **kwargs):
        return stories.validate_photo_stories(self.data, catalog=self.catalog,
                                              published=self.published, now=NOW, **kwargs)

    def test_initial_post_matches_the_actual_accepted_published_photo_bytes(self):
        before = copy.deepcopy(self.catalog)
        source = self.data["stories"][0]["source"]
        image = self.issue["images"][source["cut_id"]]
        self.assertEqual(hashlib.sha256((ROOT / image["path"]).read_bytes()).hexdigest(), source["image_sha256"])
        result = self.validate()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["image"], "/photo/weekly/winter-coat-swimsuit-saya-001/01.jpg?v=7d6077a0f2ef")
        self.assertEqual((result[0]["width"], result[0]["height"]), (800, 1200))
        self.assertEqual(result[0]["dateLabel"], "2026年11月15日")
        self.assertEqual(result[0]["settingLabel"], "舞台：2031年11月")
        self.assertEqual(self.catalog, before)
        self.assertNotIn("master", result[0])
        self.assertNotIn("review", result[0])

    def test_default_loader_validates_current_published_catalog(self):
        result = stories.load_photo_stories(ROOT, now=NOW)
        self.assertEqual(len(result), 8)
        self.assertEqual(result[0]["modelId"], "nagino-saya")
        self.assertEqual({post["modelId"] for post in result}, {model["id"] for model in self.catalog["models"]})
        self.assertEqual(sum("motion" in post for post in result), 1)

    def test_home_timeline_has_seven_readable_posts_and_source_bound_video(self):
        result = stories.load_photo_stories(ROOT, now=NOW)
        page = stories.render_photo_timeline(result)
        parser = Tags(); parser.feed(page)
        self.assertEqual(sum(tag == "article" for tag, _ in parser.tags), 7)
        timeline_videos = [attrs for tag, attrs in parser.tags
                           if tag == "video" and "data-story-video" not in attrs]
        self.assertEqual(len(timeline_videos), 1)
        video = timeline_videos[0]
        self.assertEqual(video["preload"], "none")
        self.assertIn("playsinline", video)
        self.assertIn("muted", video)
        self.assertNotIn("autoplay", video)
        self.assertTrue(video["poster"].startswith("/photo/weekly/"))
        self.assertIn("写真モーション · 12秒・音声なし", page)
        for post in result[:7]:
            self.assertIn(post["name"], page)
            self.assertIn(post["text"], page)
            self.assertIn(post["profileUrl"], page)
            self.assertIn(post["sourceUrl"], page)
        self.assertNotIn("assets/generated/", page)
        self.assertNotIn("article_sha256", page)
        self.assertNotIn("assets/photo-story-clips", page)

    def test_unpublished_unlisted_and_cross_model_sources_are_rejected(self):
        self.issue["state"] = "draft"
        with self.assertRaisesRegex(ValueError, "current published"):
            self.validate()
        self.issue["state"] = "published"
        self.published = []
        with self.assertRaisesRegex(ValueError, "current published"):
            self.validate()
        self.published = [self.issue]
        self.data["stories"][0]["model_id"] = "shioba-rio"
        with self.assertRaisesRegex(ValueError, "current published"):
            self.validate()

    def test_source_hash_review_cut_and_article_or_master_paths_cannot_be_substituted(self):
        for field, value in (("image_sha256", "0" * 64), ("cut_id", "99")):
            with self.subTest(field=field):
                data = copy.deepcopy(self.data)
                self.data["stories"][0]["source"][field] = value
                with self.assertRaises(ValueError):
                    self.validate()
                self.data = data
        image = self.issue["images"]["01"]
        image["review"]["decision"] = "rejected"
        with self.assertRaisesRegex(ValueError, "accepted published cut"):
            self.validate()
        image["review"]["decision"] = "accepted"
        for path in (self.catalog["models"][0]["master"]["path"], "assets/generated/news-article.jpg"):
            image["path"] = path
            with self.assertRaisesRegex(ValueError, "accepted published cut"):
                self.validate()

    def test_changed_actual_bytes_fail_even_if_catalog_still_claims_original_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / self.issue["images"]["01"]["path"]
            path.parent.mkdir(parents=True)
            path.write_bytes(b"changed fixture bytes")
            with self.assertRaisesRegex(ValueError, "JPEG bytes changed"):
                self.validate(root=folder)

    def test_future_story_is_omitted_and_real_date_setting_order_are_checked(self):
        post = self.data["stories"][0]
        post["published_at"] = "2026-11-17T09:40:00+09:00"
        self.assertEqual(self.validate(), ())
        post["published_at"] = "2026-11-13T09:40:00+09:00"
        with self.assertRaisesRegex(ValueError, "predates"):
            self.validate()
        post["published_at"] = "2026-11-15T09:40:00"
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.validate()
        post["published_at"] = self.initial["stories"][0]["published_at"]
        post["setting"]["year"] = 2026
        with self.assertRaisesRegex(ValueError, "fictional source setting"):
            self.validate()

    def test_unsafe_or_empty_plaintext_is_rejected(self):
        for text in ("", "私", "私の<script>alert(1)</script>です", "投稿\rです", "私" * 181):
            with self.subTest(text=text):
                self.data["stories"][0]["text"] = text
                with self.assertRaisesRegex(ValueError, "text"):
                    self.validate()

    def test_personal_posts_can_omit_pronouns_and_keep_natural_line_breaks(self):
        for text in ("今日は道草をして帰ることにしました。", "焦げそう。\n味見ばかりしてる。", "おつかれさま。"):
            with self.subTest(text=text):
                self.data["stories"][0]["text"] = text
                result = self.validate()
                self.assertEqual(result[0]["text"], text)
                self.assertEqual(result[0]["modelId"], "nagino-saya")

    def test_render_has_only_posted_models_and_safe_json_html_with_no_audio_autoplay(self):
        self.data["stories"][0]["text"] = '私のメモは「散歩 & お茶」。寄り道の予定は、まだ内緒です。'
        result = self.validate()
        page = stories.render_story_strip(result)
        parser = Tags()
        parser.feed(page)
        links = [attrs for tag, attrs in parser.tags if tag == "a" and "data-story-open" in attrs]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["data-story-open"], "nagino-saya")
        self.assertIn("創作投稿・端末の合成音声", page)
        self.assertIn("\\u0026", page)
        self.assertIn("&amp;", page)
        self.assertNotIn("<audio", page)
        self.assertNotIn("autoplay", page)
        dialog_video = next(attrs for tag, attrs in parser.tags if tag == "video")
        self.assertIn("data-story-video", dialog_video)
        self.assertEqual(dialog_video["preload"], "metadata")
        for field in ("hidden", "controls", "playsinline", "muted"):
            self.assertIn(field, dialog_video)
        self.assertNotIn("src", dialog_video)  # Reader opening selects the validated clip.
        self.assertIn("data-story-previous", page)
        self.assertNotIn("24時間", page)
        self.assertNotIn("assets/generated/", page)
        self.assertEqual(stories.render_model_stories("shioba-rio", result), "")
        self.assertIn("ストーリーズ", stories.render_model_stories("nagino-saya", result))
        for model in self.catalog["models"]:
            self.assertIn(model["id"], stories.VOICE_PROFILES)
        self.assertEqual(stories.render_story_strip(()), "")


if __name__ == "__main__":
    unittest.main()
