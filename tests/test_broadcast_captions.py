"""Current editorial captions gate new videos without altering their MP4 bytes."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import build
from scripts.broadcast_captions import story_cues
from scripts.generated_photos import article_fingerprint


ROOT = Path(__file__).resolve().parents[1]


class BroadcastCaptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = json.loads((ROOT / "content/broadcasts.json").read_text(encoding="utf-8"))
        cls.article = next(article for article in build.load()
                           if source["stories"].get(article["slug"], {}).get("article_sha256")
                           == article_fingerprint(article))
        cls.story = source["stories"][cls.article["slug"]]

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "content").mkdir()
        self.path = self.root / "content/broadcasts.json"
        self.payload = {"version": 1, "stories": {self.article["slug"]: deepcopy(self.story)}}
        self.write(self.payload)

    def write(self, payload):
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_current_file_returns_all_three_cues_without_modifying_it(self):
        before = self.path.read_bytes()
        cues = story_cues(self.article, root=self.root)
        self.assertEqual(cues, self.story["cues"])
        self.assertEqual([cue["at"] for cue in cues], [0, 4, 8])
        self.assertEqual(self.path.read_bytes(), before)

    def test_revised_story_cannot_reuse_old_captions(self):
        for field, value in (("title", "改稿した見出し"), ("summary", "改稿した要約"),
                             ("paragraphs", ["本文を改稿した。"]),
                             ("scene_prompt", "新しい画像の場面"),
                             ("slug", "different-story")):
            with self.subTest(field=field):
                self.assertEqual(story_cues({**self.article, field: value}, root=self.root), [])

    def test_missing_unreadable_and_invalid_json_fail_closed(self):
        self.path.unlink()
        self.assertEqual(story_cues(self.article, root=self.root), [])
        self.path.mkdir()
        self.assertEqual(story_cues(self.article, root=self.root), [])
        self.path.rmdir()
        for raw in (b"", b"not json", b'{"version":', b"\xff\xfe"):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                self.assertEqual(story_cues(self.article, root=self.root), [])

    def test_malformed_document_and_story_records_fail_closed(self):
        malformed = [None, [], "captions", {}, {"version": 1},
                     {"version": 1, "stories": []}, {"version": 1, "stories": {}}]
        for version in (None, True, 1.0, "1", 2):
            malformed.append({**self.payload, "version": version})
        for record in (None, [], "story", {}, {"article_sha256": "0" * 64,
                                               "cues": self.story["cues"]}):
            malformed.append({"version": 1, "stories": {self.article["slug"]: record}})
        for index, document in enumerate(malformed):
            with self.subTest(case=index):
                self.write(document)
                self.assertEqual(story_cues(self.article, root=self.root), [])

    def test_missing_extra_or_malformed_cues_reject_the_whole_set(self):
        for cues in (None, {}, [], self.story["cues"][:2], self.story["cues"] * 2,
                     [self.story["cues"][0], None, self.story["cues"][2]],
                     [self.story["cues"][0], {}, self.story["cues"][2]]):
            with self.subTest(cues=cues):
                self.payload["stories"][self.article["slug"]]["cues"] = cues
                self.write(self.payload)
                self.assertEqual(story_cues(self.article, root=self.root), [])

    def test_timing_must_be_exact_integer_zero_four_eight(self):
        for index, value in ((0, False), (0, 0.0), (0, "0"), (0, None),
                             (0, -1), (1, 0), (1, 5), (2, 4), (2, 12)):
            with self.subTest(index=index, value=value):
                payload = deepcopy(self.payload)
                payload["stories"][self.article["slug"]]["cues"][index]["at"] = value
                self.write(payload)
                self.assertEqual(story_cues(self.article, root=self.root), [])

    def test_caption_text_requires_nonblank_strings_within_character_limits(self):
        for field, maximum in (("label", 12), ("text", 40)):
            for value in ("", " \t\n\u3000", "あ" * (maximum + 1), None, False, 1, [], {}):
                with self.subTest(field=field, value=value):
                    payload = deepcopy(self.payload)
                    payload["stories"][self.article["slug"]]["cues"][1][field] = value
                    self.write(payload)
                    self.assertEqual(story_cues(self.article, root=self.root), [])
        for length in (1, 12):
            with self.subTest(valid_label_length=length):
                payload = deepcopy(self.payload)
                cue = payload["stories"][self.article["slug"]]["cues"][1]
                cue.update(label="あ" * length, text="い" * (1 if length == 1 else 40))
                self.write(payload)
                self.assertEqual(story_cues(self.article, root=self.root),
                                 payload["stories"][self.article["slug"]]["cues"])

    def test_symlink_is_rejected_even_when_target_contains_valid_captions(self):
        target = self.root / "valid-captions.json"
        self.path.rename(target)
        before = target.read_bytes()
        self.path.symlink_to(target)
        self.assertEqual(story_cues(self.article, root=self.root), [])
        self.assertEqual(target.read_bytes(), before)

    def test_style_three_requires_current_file_but_legacy_clips_do_not(self):
        record = {"sha256": "a" * 64, "created_at": "2026-11-04T00:00:00+00:00",
                  "motion_style_version": 3, "duration_ms": 12000}
        with patch.object(build, "BASE", self.root), \
             patch.object(build, "reviewed_generated_scene", return_value=True), \
             patch.object(build, "usable_clip", return_value=record):
            self.assertEqual(build.article_clip(self.article), record)
            self.assertEqual(build.broadcast_cues(self.article, record), self.story["cues"])
            self.path.unlink()
            self.assertIsNone(build.article_clip(self.article))
            self.assertEqual(build.broadcast_cues(self.article, record), [])
            for style in (None, 1, 2):
                with self.subTest(legacy_style=style):
                    legacy = {**record, "duration_ms": 4000}
                    if style is None:
                        legacy.pop("motion_style_version")
                    else:
                        legacy["motion_style_version"] = style
                    with patch.object(build, "usable_clip", return_value=legacy):
                        self.assertEqual(build.article_clip(self.article), legacy)
                        self.assertEqual(build.broadcast_cues(self.article, legacy), [])

    def test_missing_captions_remove_new_video_from_all_reader_surfaces(self):
        article = {**self.article, "broadcast_feature": True}
        record = {"sha256": "b" * 64, "created_at": "2026-11-04T00:00:00+00:00",
                  "motion_style_version": 3, "duration_ms": 12000}
        with patch.object(build, "BASE", self.root), \
             patch.object(build, "reviewed_generated_scene", return_value=True), \
             patch.object(build, "usable_clip", return_value=record), \
             patch.object(build, "asset_url", side_effect=lambda name: f"/assets/{name}"), \
             patch.object(build, "stylesheet_url", return_value="/assets/test.css"):
            # Establish a working positive case before removing only the captions.
            self.assertIn("<video", build.article_hero_media(article))
            self.assertIn("story-clip-label", build.card(article))
            self.assertEqual(len(build.broadcast_playlist([article])), 1)
            page = build.layout(article["title"], article["summary"],
                                build.article_url(article), "<p>記事本文</p>",
                                kind="story", article=article)
            self.assertIn('"@type": "VideoObject"', page)
            self.path.unlink()
            self.assertNotIn("<video", build.article_hero_media(article))
            self.assertIn("<img", build.article_hero_media(article))
            self.assertEqual(build.clip_caption(article), "")
            self.assertNotIn("story-clip-label", build.card(article))
            self.assertEqual(build.broadcast_playlist([article]), [])
            self.assertEqual(build.broadcast_feature([article]), "")
            page = build.layout(article["title"], article["summary"],
                                build.article_url(article), "<p>記事本文</p>",
                                kind="story", article=article)
            self.assertIn('"@type": "BlogPosting"', page)
            self.assertNotIn('"@type": "VideoObject"', page)
            self.assertNotIn(build.clip_url(article), page)
            self.assertNotIn("/assets/news-broadcast.js", page)

    def test_valid_captions_cannot_bypass_an_unreviewed_image(self):
        with patch.object(build, "BASE", self.root), \
             patch.object(build, "reviewed_generated_scene", return_value=False), \
             patch.object(build, "usable_clip") as raw_clip:
            self.assertTrue(story_cues(self.article, root=self.root))
            self.assertIsNone(build.article_clip(self.article))
            raw_clip.assert_not_called()


if __name__ == "__main__":
    unittest.main()
