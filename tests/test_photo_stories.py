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
        self.assertEqual(result[0]["dateLabel"], "2026年11月15日 09:40")
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

    def test_same_person_cannot_repost_exact_text_under_another_id_or_date(self):
        original = copy.deepcopy(self.data["stories"][0])
        for date in ("2026-11-15T10:40:00+09:00", "2026-11-20T10:40:00+09:00"):
            with self.subTest(date=date):
                repeat = copy.deepcopy(original)
                repeat.update(id="same-words-another-post", published_at=date)
                self.data["stories"] = [original, repeat]
                with self.assertRaisesRegex(ValueError, "Duplicate photo story text"):
                    self.validate()

    def test_same_person_can_publish_different_original_words(self):
        another = copy.deepcopy(self.data["stories"][0])
        another.update(id="another-original-post", text="今日はいつもと違う道を歩いた。\n帰りは、いつもの道。")
        self.data["stories"].append(another)
        self.assertEqual(len(self.validate()), 2)

    def test_different_people_can_use_the_same_words(self):
        original = self.data["stories"][0]
        another = copy.deepcopy(next(post for post in self.initial["stories"]
                                     if post["model_id"] != original["model_id"]))
        another["text"] = original["text"]
        self.data["stories"].append(another)
        self.published.append(next(issue for issue in self.catalog["issues"]
                                   if issue["slug"] == another["source"]["issue_slug"]))
        self.assertEqual(len(self.validate()), 2)

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

    def test_timeline_shows_three_recent_posts_and_keeps_four_in_native_details(self):
        result = stories.load_photo_stories(ROOT, now=NOW)
        page = stories.render_photo_timeline(result)
        visible, earlier = page.split('<details class="photo-post-archive">', 1)
        before, after = Tags(), Tags()
        before.feed(visible); after.feed(earlier)
        self.assertEqual(sum(tag == "article" for tag, _ in before.tags), 3)
        self.assertEqual(sum(tag == "article" for tag, _ in after.tags), 4)
        self.assertIn('<summary>以前の投稿（4件）</summary>', earlier)
        self.assertIn('<ol class="photo-post-list" start="4">', earlier)
        self.assertNotIn('<details class="photo-post-archive" open', page)
        self.assertNotIn('hidden', earlier)
        self.assertNotIn('photo-post-archive', stories.render_photo_timeline(result[:3]))

    def mixed_history(self):
        own = self.data["stories"][0]
        other = next(post for post in self.initial["stories"] if post["model_id"] != own["model_id"])
        history = []
        for index in range(9):
            post = copy.deepcopy(own)
            post.update(id=f'own-post-{index + 1}',
                        published_at=f'2026-11-15T{10 + index:02d}:00:00+09:00',
                        text=f'公開した近況{index + 1}。\n帰り道の記録です。')
            history.append(post)
        other = copy.deepcopy(other)
        other.update(id='other-person-post', published_at='2026-11-15T18:30:00+09:00',
                     text='別の人だけが話した近況です。')
        history.insert(4, other)
        data = {"schema_version": 1, "stories": history}
        return stories.validate_photo_stories(data, catalog=self.catalog,
                                              published=self.catalog["issues"], now=NOW)

    def test_person_history_retains_all_own_posts_while_home_keeps_latest_seven(self):
        result = self.mixed_history()
        before = copy.deepcopy(result)
        own_id = self.data["stories"][0]["model_id"]
        own = [post for post in result if post["modelId"] == own_id]
        other = next(post for post in result if post["modelId"] != own_id)
        profile = stories.render_model_stories(own_id, result)
        home = stories.render_photo_timeline(result)
        parsed_profile, parsed_home = Tags(), Tags()
        parsed_profile.feed(profile); parsed_home.feed(home)
        article_ids = lambda parsed: [attrs["id"] for tag, attrs in parsed.tags if tag == "article"]
        self.assertEqual(article_ids(parsed_profile), ["post-" + post["id"] for post in own])
        self.assertEqual(len(set(article_ids(parsed_profile))), 9)
        self.assertEqual(article_ids(parsed_home), ["post-" + post["id"] for post in result[:7]])
        self.assertNotIn("post-" + own[-1]["id"], article_ids(parsed_home))
        visible, older = profile.split('<details class="photo-post-archive">', 1)
        parsed_visible, parsed_older = Tags(), Tags()
        parsed_visible.feed(visible); parsed_older.feed(older)
        self.assertEqual(len(article_ids(parsed_visible)), 3)
        self.assertEqual(len(article_ids(parsed_older)), 6)
        self.assertIn('<summary>以前の投稿（6件）</summary>', older)
        for value in (other["name"], other["text"], other["image"], other["sourceUrl"]):
            self.assertNotIn(value, profile)
        other_profile = stories.render_model_stories(other["modelId"], result)
        self.assertIn(other["text"], other_profile)
        self.assertNotIn(own[0]["name"], other_profile)
        self.assertEqual(result, before)

    def test_each_dated_post_links_to_its_own_profile_anchor_and_keeps_original_context(self):
        result = self.mixed_history()
        own_id = self.data["stories"][0]["model_id"]
        profile = stories.render_model_stories(own_id, result)
        own = [post for post in result if post["modelId"] == own_id]
        for page, posts in ((profile, own), (stories.render_photo_timeline(result), result[:7])):
            for post in posts:
                with self.subTest(post=post["id"]):
                    target = post["profileUrl"] + "#post-" + post["id"]
                    self.assertIn(f'<a href="{target}"><time datetime="{post["publishedAt"]}">'
                                  f'{post["dateLabel"]}</time></a>', page)
                    self.assertEqual(page.count(f'id="post-{post["id"]}"'), 1)
                    self.assertIn(post["text"], page)
                    self.assertIn(post["settingLabel"], page)
                    self.assertIn(f'href="{post["sourceUrl"]}">写真の掲載特集', page)
        self.assertIn('datetime="2026-11-15T10:00:00+09:00">2026年11月15日 10:00', profile)
        self.assertIn('舞台：2031年11月', profile)

    def test_person_timeline_keeps_reviewed_motion_still_fallback_and_modal_history(self):
        result = stories.load_photo_stories(ROOT, now=NOW)
        post = next(post for post in result if "motion" in post)
        own = [item for item in result if item["modelId"] == post["modelId"]]
        profile = stories.render_model_stories(post["modelId"], result)
        parsed = Tags(); parsed.feed(profile)
        video = next(attrs for tag, attrs in parsed.tags
                     if tag == "video" and "data-story-video" not in attrs)
        self.assertEqual(video["poster"], post["image"])
        self.assertEqual(video["preload"], "none")
        for field in ("controls", "playsinline", "muted"):
            self.assertIn(field, video)
        self.assertNotIn("autoplay", video)
        fallback = next(attrs for tag, attrs in parsed.tags
                        if tag == "a" and "data-story-motion-fallback" in attrs)
        self.assertEqual(fallback["href"], post["sourceUrl"])
        self.assertIn("hidden", fallback)
        payload = profile.split('<script type="application/json" data-photo-story-data>', 1)[1].split('</script>', 1)[0]
        self.assertEqual(json.loads(payload), own)
        self.assertIn('data-story-dialog', profile)

    def test_story_rings_expose_actual_local_post_time_and_accessible_context(self):
        result = stories.load_photo_stories(ROOT, now=NOW)
        markup = stories.render_story_strip(result)
        parser = Tags(); parser.feed(markup)
        links = [attrs for tag, attrs in parser.tags if tag == "a" and "data-story-open" in attrs]
        timestamps = [attrs for tag, attrs in parser.tags
                      if tag == "time" and attrs.get("class") == "photo-story-posted"]
        self.assertEqual(len(links), 7)
        self.assertEqual(len(timestamps), 7)
        newest = result[0]
        self.assertIn(newest["dateLabel"], links[0]["aria-label"])
        self.assertIn(newest["name"], links[0]["aria-label"])
        self.assertEqual(timestamps[0]["datetime"], newest["publishedAt"])
        label = datetime.fromisoformat(newest["publishedAt"]).astimezone(stories.JST).strftime("%m/%d %H:%M")
        self.assertIn('>' + label + '</time>', markup)

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
