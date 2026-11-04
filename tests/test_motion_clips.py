"""Cost-free optional video pipeline with integrity, storage and still-image fallbacks."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from scripts import build
from scripts.generated_photos import generated_manifest, usable_generated_scene
from scripts.motion_clips import (
    MAX_CLIP_BYTES, MAX_TOTAL_BYTES, clip_url, candidates, generate,
    manifest_entries, usable_clip,
)

ROOT = Path(__file__).resolve().parents[1]


class MotionClipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles = build.load()

    def test_default_has_no_extra_video_request(self):
        from scripts.motion_clips import RELATIVE_DIR
        current = manifest_entries(ROOT)
        for article in self.articles:
            page = build.article_hero_media(article)
            approved = usable_clip(article, root=ROOT)
            if approved is None:
                self.assertNotIn("<video", page)
                self.assertIn('class="article-visual"', page)
            else:
                self.assertIn('preload="metadata"', page)
                self.assertNotIn("autoplay", page)
        self.assertTrue((ROOT / RELATIVE_DIR / "manifest.json").is_file())
        self.assertIsInstance(current, dict)
        self.assertLessEqual(sum(x.get("bytes", 0) for x in current.values()), MAX_TOTAL_BYTES)

    def test_unsafe_slugs_and_budgets_fail_closed(self):
        with self.assertRaises(ValueError):
            clip_url({"slug": "../external"})
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            clips = root / "assets/clips"
            clips.mkdir(parents=True)
            (clips/"manifest.json").write_text(json.dumps({
                "version": 1, "clips": {"bad": {"bytes": MAX_TOTAL_BYTES + 1}}
            }), encoding="utf-8")
            self.assertEqual(manifest_entries(root), {})
            (clips/"manifest.json").write_text(json.dumps({
                "version": 1, "clips": {"bad": {"bytes": 100, "sha256": "not-a-hash"}}
            }), encoding="utf-8")
            self.assertIsNone(usable_clip(self.articles[0], root=root))
            (clips/"manifest.json").unlink()
            (clips/"manifest.json").symlink_to("/etc/hosts")
            self.assertEqual(manifest_entries(root), {})

    def test_player_and_video_schema_are_optional_even_without_ffmpeg(self):
        # Static publishing must work on runners that cannot encode a movie.
        article = self.articles[0]
        with patch.object(build, "usable_clip", return_value={
            "created_at": "2026-11-03T08:00:00+00:00", "sha256": "a"*64,
            "duration_ms": 4000, "fps": 12,
        }):
            player = build.article_hero_media(article)
            self.assertIn('controls playsinline muted loop preload="metadata"', player)
            self.assertIn('poster="', player)
            self.assertIn('<source src="'+clip_url(article)+'?v='+("a"*12)+'" type="video/mp4">', player)
            self.assertIn('class="article-visual"', player)
            self.assertNotIn("autoplay", player)
            meta = build.layout(article["title"], article["summary"], build.article_url(article),
                                "<p>test</p>", kind="story", article=article)
            self.assertIn('"@type": "BlogPosting"', meta)
            self.assertIn('"@type": "VideoObject"', meta)
            self.assertIn('"duration": "PT4S"', meta)
            self.assertIn('"contentUrl": "https://veripsa.com'+clip_url(article)+'"', meta)
            card = build.card(article)
            self.assertIn("▶ 4秒映像", card)
            self.assertNotIn("<video", card)
        with patch.object(build, "usable_clip", return_value=None):
            self.assertNotIn("<video", build.article_hero_media(article))
            self.assertNotIn('"@type": "VideoObject"',
                             build.layout(article["title"],article["summary"],
                                          build.article_url(article),"<p>test</p>",
                                          kind="story", article=article))

    def test_new_clip_candidates_need_explicit_editor_opt_in(self):
        from scripts import motion_clips
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "content").mkdir()
            source = self.articles[0]
            chosen = {**source, "slug": "chosen-news-story", "broadcast_feature": True}
            ordinary = {**source, "slug": "ordinary-story", "broadcast_feature": False}
            (root / "content/articles.json").write_text(
                json.dumps([ordinary, chosen], ensure_ascii=False), encoding="utf-8")
            with patch.object(motion_clips, "usable_generated_scene", return_value=True), \
                 patch.object(motion_clips, "usable_clip", return_value=None):
                self.assertEqual([a["slug"] for a in candidates(root)], ["chosen-news-story"])

    def test_legacy_clips_stay_usable_but_mixed_profiles_fail_closed(self):
        current = manifest_entries(ROOT)
        legacy = next((a for a in self.articles
                       if current.get(a["slug"], {}).get("duration_ms") == 4000
                       and usable_clip(a, root=ROOT)), None)
        if legacy is None:
            self.skipTest("No unchanged approved legacy clip at this revision.")
        slug = legacy["slug"]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            photos = root / "assets/generated"
            clips = root / "assets/clips"
            photos.mkdir(parents=True)
            clips.mkdir()
            (photos / "manifest.json").write_text(json.dumps({
                "version": 1, "images": {slug: generated_manifest(ROOT)[slug]}
            }), encoding="utf-8")
            shutil.copyfile(ROOT / "assets/generated" / (slug + ".jpg"), photos / (slug + ".jpg"))
            shutil.copyfile(ROOT / "assets/clips" / (slug + ".mp4"), clips / (slug + ".mp4"))
            original = current[slug]

            def write_record(record):
                (clips / "manifest.json").write_text(json.dumps({
                    "version": 1, "clips": {slug: record}
                }), encoding="utf-8")

            # Existing v1 clips omit the style field; both old encoder styles
            # must remain playable while a selected article waits for an update.
            for style in (None, 1, 2):
                record = {**original}
                if style is None:
                    record.pop("motion_style_version", None)
                else:
                    record["motion_style_version"] = style
                write_record(record)
                with self.subTest(legacy_style=style):
                    self.assertIsNotNone(usable_clip(legacy, root=root))
            for incompatible in (
                {"motion_style_version": 3}, {"motion_style_version": 99},
                {"duration_ms": 12000}, {"fps": 24},
                {"motion_style_version": True}, {"fps": 12.0},
            ):
                write_record({**original, **incompatible})
                with self.subTest(incompatible=incompatible):
                    self.assertIsNone(usable_clip(legacy, root=root))

    def test_real_approved_still_generates_bounded_silent_mp4(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("No local ffmpeg tools installed; production static build still works.")
        suitable = next((a for a in self.articles if a.get("broadcast_feature") is True and usable_generated_scene(a, root=ROOT)), None)
        if suitable is None:
            self.skipTest("No approved locally generated stills at this revision.")
        slug = suitable["slug"]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "assets/generated"
            source.mkdir(parents=True)
            (root / "content").mkdir()
            entry = generated_manifest(ROOT)[slug]
            (source/"manifest.json").write_text(
                json.dumps({"version": 1, "images": {slug: entry}}),
                encoding="utf-8",
            )
            shutil.copyfile(ROOT/"assets/generated"/(slug+".jpg"), source/(slug+".jpg"))
            (root/"content/articles.json").write_text(
                json.dumps([suitable], ensure_ascii=False), encoding="utf-8"
            )
            self.assertEqual([a["slug"] for a in candidates(root)], [slug])
            # Encoding this temporary fixture must not depend on the operator's
            # real persistent OFF switch, or change it during the test.
            with patch("scripts.mac_scene_control.APP_DIR", root / "test-control"):
                self.assertEqual(generate(root, limit=1, slug=slug, min_free_gib=0), [slug])
            self.assertEqual(candidates(root), [])
            verified = usable_clip(suitable, root=root)
            self.assertIsNotNone(verified)
            self.assertLessEqual(verified["bytes"], MAX_CLIP_BYTES)
            self.assertEqual(verified["duration_ms"], 12000)
            self.assertEqual(verified["fps"], 24)
            self.assertEqual(verified["motion_style_version"], 3)
            movie = root / clip_url(suitable).lstrip("/").replace("clips/","assets/clips/",1)
            self.assertTrue(movie.is_file())
            self.assertEqual(hashlib.sha256(movie.read_bytes()).hexdigest(), verified["sha256"])
            info = json.loads(subprocess.check_output([
                "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(movie)
            ], timeout=15))
            self.assertEqual(len(info["streams"]), 1)  # No audio or extra media.
            stream = info["streams"][0]
            self.assertEqual(stream["codec_name"], "h264")
            self.assertEqual(stream["pix_fmt"], "yuv420p")
            # Older ffprobe omits the implicit limited-range H.264 default.
            # Keep the strict pixel format above and reject explicit PC range.
            self.assertIn(stream.get("color_range"), (None, "tv"))
            self.assertEqual((stream["width"], stream["height"]), (640, 360))
            self.assertEqual(Fraction(stream["avg_frame_rate"]), 24)
            self.assertEqual(int(stream["nb_frames"]), 288)
            self.assertAlmostEqual(float(info["format"]["duration"]), 12, places=2)
            # Inspect actual decoded pixels, not only the filter expression:
            # movement within a shot and distinct clean cuts at 4s and 8s.
            frame_numbers = (0, 48, 94, 95, 96, 144, 190, 191, 192, 240, 287)
            selected = "+".join(f"eq(n,{n})" for n in frame_numbers)
            pixels = subprocess.check_output([
                "ffmpeg", "-nostdin", "-v", "error", "-i", str(movie),
                "-vf", f"select='{selected}',scale=64:36,format=gray",
                "-vsync", "0", "-an", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
            ], timeout=15)
            frame_size = 64 * 36
            self.assertEqual(len(pixels), frame_size * len(frame_numbers))
            frames = dict(zip(frame_numbers, [
                pixels[i:i + frame_size] for i in range(0, len(pixels), frame_size)
            ]))

            def difference(first, second):
                return sum(abs(a - b) for a, b in zip(frames[first], frames[second])) / frame_size

            self.assertGreater(difference(0, 48), 0.5, "Opening must visibly move")
            self.assertGreater(difference(96, 144), 0.5, "Detail shot must pan")
            self.assertGreater(difference(192, 287), 0.5, "Closing must pull back")
            self.assertGreater(difference(95, 96), difference(94, 95) * 2 + 1)
            self.assertGreater(difference(191, 192), difference(190, 191) * 2 + 1)
            for frame in frames.values():
                # No blank white/black flash at the cut boundaries.
                self.assertGreater(max(frame) - min(frame), 20)
            with patch.object(build, "BASE", root), \
                 patch.object(build, "stylesheet_url", return_value="/assets/newspaper.css?v=test"):
                html = build.article_hero_media(suitable)
                self.assertIn('<video class="article-visual article-video"', html)
                self.assertIn('controls playsinline muted loop preload="metadata"', html)
                self.assertIn('poster="/generated/'+slug+'.jpg"', html)
                self.assertIn('<source src="/clips/'+slug+'.mp4?v='+verified["sha256"][:12]+'" type="video/mp4">', html)
                self.assertIn('src="/generated/'+slug+'.jpg"', html)
                self.assertNotIn("autoplay", html)
                page = build.layout("記事", "架空", build.article_url(suitable),
                                    "<p>記事</p>", kind="story", article=suitable)
                self.assertIn('"@type": "VideoObject"', page)
                self.assertIn('https://veripsa.com/clips/'+slug+'.mp4', page)
                self.assertIn('"duration": "PT12S"', page)
                self.assertIn('"@type": "BlogPosting"', page)
            movie.write_bytes(movie.read_bytes() + b"tampered")
            self.assertIsNone(usable_clip(suitable, root=root))
            with patch.object(build, "BASE", root):
                self.assertNotIn("<video", build.article_hero_media(suitable))
            self.assertEqual(manifest_entries(root)[slug]["bytes"], verified["bytes"])


if __name__ == "__main__":
    unittest.main()
