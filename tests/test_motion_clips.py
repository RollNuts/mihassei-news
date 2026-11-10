"""Cost-free optional video pipeline with integrity, storage and still-image fallbacks."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import build
from scripts import motion_clips
from scripts.broadcast_captions import story_cues
from scripts.generated_photos import generated_manifest, generated_url
from scripts.visual_review import reviewed_generated_scene
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
            approved = build.article_clip(article)
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
        # Illustration-only stories must not enter the JPEG/video pipeline,
        # even when the photo acceptance gate is mocked to return true.
        article = next(a for a in self.articles
                       if a.get("visual_strategy", "cpu-photo") == "cpu-photo")
        with patch.object(build, "reviewed_generated_scene", return_value=True), \
             patch.object(build, "usable_clip", return_value={
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
        with patch.object(build, "reviewed_generated_scene", return_value=True), \
             patch.object(build, "usable_clip", return_value=None):
            self.assertNotIn("<video", build.article_hero_media(article))
            self.assertNotIn('"@type": "VideoObject"',
                             build.layout(article["title"],article["summary"],
                                          build.article_url(article),"<p>test</p>",
                                          kind="story", article=article))

    def test_unreviewed_still_blocks_valid_clip_player_schema_and_broadcast(self):
        """A valid optional MP4 cannot bypass its source image's visual review."""
        article = {**self.articles[0], "broadcast_feature": True}
        article.pop("photo", None)
        record = {
            "created_at": "2026-11-03T08:00:00+00:00", "sha256": "a" * 64,
            "duration_ms": 4000, "fps": 12,
        }
        with patch.object(build, "reviewed_generated_scene", return_value=False), \
             patch.object(build, "usable_clip", return_value=record) as raw_clip:
            self.assertIsNone(build.article_clip(article))
            self.assertNotIn("<video", build.article_hero_media(article))
            self.assertEqual(build.clip_caption(article), "")
            self.assertNotIn("story-clip-label", build.card(article))
            self.assertEqual(build.broadcast_playlist([article]), [])
            page = build.layout(article["title"], article["summary"],
                                build.article_url(article), "<p>test</p>",
                                kind="story", article=article)
            self.assertIn('"@type": "BlogPosting"', page)
            self.assertNotIn('"@type": "VideoObject"', page)
            self.assertNotIn(clip_url(article), page)
            self.assertNotIn("/generated/" + article["slug"] + ".jpg", page)
            raw_clip.assert_not_called()

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
                 patch.object(motion_clips, "reviewed_generated_scene", return_value=True), \
                 patch.object(motion_clips, "story_cues", return_value=[{"text": "Ready"}]), \
                 patch.object(motion_clips, "usable_clip", return_value=None):
                self.assertEqual([a["slug"] for a in candidates(root)], ["chosen-news-story"])

    def test_generation_rechecks_review_and_captions_before_encoding(self):
        article = {**self.articles[0], "broadcast_feature": True}
        for reviewed, cues in ((False, [{"text": "Ready"}]), (True, [])):
            with self.subTest(reviewed=reviewed, captions=bool(cues)), \
                 tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                # Model a source that was eligible at planning time, then lost
                # its review or captions before the encoder could start.
                with patch.object(motion_clips, "candidates", return_value=[article]), \
                     patch.object(motion_clips, "generated_scene_entry", return_value={"sha256": "a" * 64}), \
                     patch.object(motion_clips, "usable_generated_scene", return_value=True), \
                     patch.object(motion_clips, "reviewed_generated_scene", return_value=reviewed), \
                     patch.object(motion_clips, "story_cues", return_value=cues), \
                     patch.object(motion_clips.shutil, "which", return_value="test-encoder"), \
                     patch.object(motion_clips, "require_mac_not_stopped"), \
                     patch.object(motion_clips, "mac_resource_guard", return_value=None), \
                     patch.object(motion_clips, "encode_clip") as encode:
                    self.assertEqual(generate(root, min_free_gib=0), [])
                    encode.assert_not_called()
                self.assertEqual(list(root.rglob("*.mp4")), [])
                self.assertFalse((root / "assets/clips/manifest.json").exists())

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
        if sys.platform == "darwin":
            from scripts.mac_scene_resources import sample
            snapshot = sample()
            if not snapshot["allowed"]:
                self.skipTest("Actual Mac resource guard deferred encoding: " + snapshot["reason"])
            try:
                motion_clips.require_mac_not_stopped()
            except RuntimeError as exc:
                self.skipTest("Actual Mac stop switch deferred encoding: " + str(exc))
        if shutil.disk_usage(ROOT).free < motion_clips.MIN_FREE_GIB * 1024**3:
            self.skipTest("Actual free storage is below the 12 GiB encoding floor.")
        suitable = next((a for a in self.articles
                         if a.get("broadcast_feature") is True
                         and reviewed_generated_scene(a, root=ROOT)
                         and story_cues(a, root=ROOT)), None)
        if suitable is None:
            self.skipTest("No visually reviewed selected still with current captions at this revision.")
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
            self.assertEqual(candidates(root), [])
            self.assertEqual(motion_clips.preparation_state(suitable, root=root), "visual_review_required")
            # Copy real source evidence verbatim; never manufacture approval
            # metadata or rewrite production image fingerprints for a test.
            reviews = root / "content/visual_reviews.json"
            shutil.copyfile(ROOT / "content/visual_reviews.json", reviews)
            self.assertEqual(candidates(root), [])
            self.assertEqual(motion_clips.preparation_state(suitable, root=root), "captions_required")
            captions = root / "content/broadcasts.json"
            shutil.copyfile(ROOT / "content/broadcasts.json", captions)
            self.assertEqual([a["slug"] for a in candidates(root)], [slug])
            # Real encoding obeys the operator's actual stop/resource policy;
            # neither the platform nor its guard is mocked to force execution.
            self.assertEqual(generate(root, limit=1, slug=slug), [slug])
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
                "ffmpeg", "-nostdin", "-v", "error", "-threads", "1",
                "-filter_threads", "1", "-i", str(movie),
                "-vf", f"select='{selected}',scale=64:36,format=gray",
                "-vsync", "0", "-an", "-threads", "1", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
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
            # Removing an approval also retracts an already encoded movie from
            # public surfaces without touching the valid original MP4 bytes.
            review_bytes = reviews.read_bytes()
            reviews.unlink()
            with patch.object(build, "BASE", root):
                self.assertIsNotNone(usable_clip(suitable, root=root))
                self.assertIsNone(build.article_clip(suitable))
                self.assertNotIn("<video", build.article_hero_media(suitable))
                self.assertEqual(build.broadcast_playlist([suitable]), [])
            reviews.write_bytes(review_bytes)
            with patch.object(build, "BASE", root), \
                 patch.object(build, "stylesheet_url", return_value="/assets/newspaper.css?v=test"):
                html = build.article_hero_media(suitable)
                self.assertIn('<video class="article-visual article-video"', html)
                self.assertIn('controls playsinline muted loop preload="metadata"', html)
                self.assertIn('poster="'+generated_url(suitable, root=root)+'"', html)
                self.assertIn('<source src="/clips/'+slug+'.mp4?v='+verified["sha256"][:12]+'" type="video/mp4">', html)
                self.assertIn('src="'+generated_url(suitable, root=root)+'"', html)
                self.assertNotIn("autoplay", html)
                page = build.layout("記事", "架空", build.article_url(suitable),
                                    "<p>記事</p>", kind="story", article=suitable)
                self.assertIn('"@type": "VideoObject"', page)
                self.assertIn('https://veripsa.com/clips/'+slug+'.mp4', page)
                self.assertIn('"duration": "PT12S"', page)
                self.assertIn('"@type": "BlogPosting"', page)
            before_movie = movie.read_bytes()
            before_manifest = (root / "assets/clips/manifest.json").read_bytes()
            changed_captions = json.loads(captions.read_text(encoding="utf-8"))
            changed_captions["stories"][slug]["cues"][2]["text"] = "字幕だけを修正した確認用の文"
            captions.write_text(json.dumps(changed_captions, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(motion_clips.preparation_state(suitable, root=root), "current")
            with patch.object(motion_clips, "encode_clip") as encode:
                self.assertEqual(generate(root, limit=1, slug=slug), [])
                encode.assert_not_called()
            self.assertEqual(movie.read_bytes(), before_movie)
            self.assertEqual((root / "assets/clips/manifest.json").read_bytes(), before_manifest)
            movie.write_bytes(movie.read_bytes() + b"tampered")
            self.assertIsNone(usable_clip(suitable, root=root))
            with patch.object(build, "BASE", root):
                self.assertNotIn("<video", build.article_hero_media(suitable))
            self.assertEqual(manifest_entries(root)[slug]["bytes"], verified["bytes"])


class CheckedMotionTests(unittest.TestCase):
    """Synthetic subprocesses exercise interruption; no encoder is launched."""

    class Child:
        def __init__(self, *, finish_after=None, code=0, ignore_terminate=False):
            self.returncode = None
            self.finish_after = finish_after
            self.code = code
            self.ignore_terminate = ignore_terminate
            self.communications = self.terminations = self.kills = 0
            self.reaped = False

        def poll(self):
            return self.returncode

        def communicate(self, timeout=None):
            self.communications += 1
            if (self.returncode is None and self.finish_after is not None
                    and self.communications >= self.finish_after):
                self.returncode = self.code
            if self.returncode is None:
                raise subprocess.TimeoutExpired("synthetic-child", timeout)
            self.reaped = True
            return "child stdout", "child stderr"

        def terminate(self):
            self.terminations += 1
            if not self.ignore_terminate:
                self.returncode = -15

        def kill(self):
            self.kills += 1
            self.returncode = -9

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.article = {"slug": "guard-fixture", "title": "Fixture", "summary": "Fixture",
                        "paragraphs": ["Fixture"], "scene_prompt": "Fixture"}
        self.folder = self.root / "assets/clips"
        self.folder.mkdir(parents=True)
        self.movie = self.folder / "guard-fixture.mp4"
        self.registry = self.folder / "manifest.json"
        # Preservation sentinels, not a claimed real video or visual approval.
        self.old_movie = b"existing movie remains unchanged"
        self.old_registry = json.dumps({"version": 1, "clips": {
            "guard-fixture": {"bytes": len(self.old_movie)}}}).encode()
        self.movie.write_bytes(self.old_movie)
        self.registry.write_bytes(self.old_registry)

    def planning(self, stack, guard):
        for name, value in (("candidates", [self.article]),
                            ("generated_scene_entry", {"sha256": "a" * 64}),
                            ("usable_generated_scene", True),
                            ("reviewed_generated_scene", True),
                            ("story_cues", [{"text": "Ready"}]),
                            ("mac_resource_guard", guard)):
            stack.enter_context(patch.object(motion_clips, name, return_value=value))
        stack.enter_context(patch.object(motion_clips.shutil, "which", return_value="fake-encoder"))
        stack.enter_context(patch.object(motion_clips.shutil, "disk_usage",
                                         return_value=SimpleNamespace(free=64 * 1024**3)))
        return stack.enter_context(patch.object(motion_clips, "require_mac_not_stopped"))

    def preserved(self):
        self.assertEqual(self.movie.read_bytes(), self.old_movie)
        self.assertEqual(self.registry.read_bytes(), self.old_registry)
        self.assertEqual(list(self.folder.glob(".clip-*")), [])

    def test_pressure_preflight_starts_no_child_and_preserves_published_files(self):
        from scripts.mac_scene_resources import ResourceDeferred
        guard = Mock()
        guard.check.side_effect = ResourceDeferred("macOS memory pressure warning")
        with ExitStack() as stack:
            self.planning(stack, guard)
            child = stack.enter_context(patch.object(motion_clips.subprocess, "Popen"))
            with self.assertRaisesRegex(ResourceDeferred, "memory pressure"):
                generate(self.root)
            child.assert_not_called()
            guard.check.assert_called_once_with(force=True)
        self.preserved()

    def test_mid_encode_stop_pressure_or_low_storage_reaps_only_owned_child(self):
        from scripts.mac_scene_resources import ResourceDeferred
        for reason in ("stop", "pressure", "storage"):
            with self.subTest(reason=reason), ExitStack() as stack:
                guard = Mock()
                stop = self.planning(stack, guard)
                process = self.Child(ignore_terminate=True)
                started = []

                def spawn(command, **kwargs):
                    Path(command[-1]).write_bytes(b"partial candidate")
                    started.append(True)
                    return process

                if reason == "stop":
                    def check_stop():
                        if started:
                            raise RuntimeError("Image generation is OFF")
                    stop.side_effect = check_stop
                elif reason == "pressure":
                    def check_pressure(**kwargs):
                        if started:
                            raise ResourceDeferred("macOS memory pressure warning")
                    guard.check.side_effect = check_pressure
                else:
                    stack.enter_context(patch.object(motion_clips.shutil, "disk_usage",
                        side_effect=lambda path: SimpleNamespace(free=0 if started else 64 * 1024**3)))
                popen = stack.enter_context(patch.object(motion_clips.subprocess, "Popen", side_effect=spawn))
                broad_signal = stack.enter_context(patch.object(motion_clips.os, "killpg"))
                # A broken callback must fail promptly, rather than spin the
                # synthetic child until the encoder's real 120-second limit.
                stack.enter_context(patch.object(motion_clips.time, "monotonic", side_effect=[0, 121]))
                with self.assertRaises(RuntimeError):
                    generate(self.root)
                popen.assert_called_once()
                broad_signal.assert_not_called()
                self.assertEqual((process.terminations, process.kills), (1, 1))
                self.assertTrue(process.reaped)
            self.preserved()

    def test_guard_rechecks_after_encoding_before_replacing_saved_files(self):
        from scripts.mac_scene_resources import ResourceDeferred
        guard = Mock()
        encoded = []

        def encode(image, output, **kwargs):
            output.write_bytes(b"candidate bytes" * 200)
            encoded.append(True)

        def check(**kwargs):
            if encoded:
                raise ResourceDeferred("pressure changed before publish")

        guard.check.side_effect = check
        with ExitStack() as stack:
            self.planning(stack, guard)
            stack.enter_context(patch.object(motion_clips, "encode_clip", side_effect=encode))
            with self.assertRaisesRegex(ResourceDeferred, "before publish"):
                generate(self.root)
            self.assertEqual(guard.check.call_args.kwargs, {"force": True})
        self.preserved()

    def test_idle_never_creates_a_resource_guard_or_encoder_child(self):
        with patch.object(motion_clips, "require_mac_not_stopped"), \
             patch.object(motion_clips.shutil, "which", return_value="fake-encoder"), \
             patch.object(motion_clips.shutil, "disk_usage", return_value=SimpleNamespace(free=64 * 1024**3)), \
             patch.object(motion_clips, "candidates", return_value=[]), \
             patch.object(motion_clips, "mac_resource_guard") as guard, \
             patch.object(motion_clips.subprocess, "Popen") as child:
            self.assertEqual(generate(self.root), [])
            guard.assert_not_called()
            child.assert_not_called()
        self.preserved()

    def test_checked_child_runs_callbacks_and_unchecked_path_keeps_run_contract(self):
        process = self.Child(finish_after=2)
        check = Mock()
        with patch.object(motion_clips.subprocess, "Popen", return_value=process):
            result = motion_clips.run_checked(["fake-command"], timeout=3, check=check)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "child stdout", "child stderr"))
        self.assertGreaterEqual(check.call_count, 3)  # Before, during, after.
        self.assertEqual((process.terminations, process.kills), (0, 0))
        completed = subprocess.CompletedProcess(["fake-command"], 0, "out", "err")
        with patch.object(motion_clips.subprocess, "run", return_value=completed) as run, \
             patch.object(motion_clips.subprocess, "Popen") as popen:
            self.assertIs(motion_clips.run_checked(["fake-command"], timeout=3), completed)
            run.assert_called_once_with(["fake-command"], capture_output=True, text=True, timeout=3, check=True)
            popen.assert_not_called()

    def test_checked_child_timeout_terminates_kills_and_reaps(self):
        process = self.Child(ignore_terminate=True)
        with patch.object(motion_clips.subprocess, "Popen", return_value=process), \
             patch.object(motion_clips.time, "monotonic", side_effect=[0, 0, 2]):
            with self.assertRaises(subprocess.TimeoutExpired):
                motion_clips.run_checked(["fake-command"], timeout=1, check=Mock())
        self.assertEqual((process.terminations, process.kills), (1, 1))
        self.assertTrue(process.reaped)

    def test_checked_child_nonzero_preserves_error_output_without_signalling_finished_child(self):
        process = self.Child(finish_after=1, code=7)
        with patch.object(motion_clips.subprocess, "Popen", return_value=process):
            with self.assertRaises(subprocess.CalledProcessError) as error:
                motion_clips.run_checked(["fake-command"], timeout=3, check=Mock())
        self.assertEqual((error.exception.returncode, error.exception.output, error.exception.stderr),
                         (7, "child stdout", "child stderr"))
        self.assertEqual((process.terminations, process.kills), (0, 0))

    def test_encoder_limits_decoder_encoder_and_filters_to_one_thread(self):
        calls = []
        output = self.root / "candidate.mp4"
        check = Mock()
        probe = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 640,
                 "height": 360, "pix_fmt": "yuv420p", "avg_frame_rate": "24/1", "nb_frames": "288"}],
                 "format": {"duration": "12.0"}}

        def fake_run(command, *, timeout, check=None):
            calls.append((command, timeout, check))
            if command[0] == "fake-ffmpeg":
                output.write_bytes(b"synthetic candidate" * 100)
            return subprocess.CompletedProcess(command, 0, json.dumps(probe), "")

        with patch.object(motion_clips, "run_checked", side_effect=fake_run):
            motion_clips.encode_clip(self.root / "input.jpg", output,
                                     ffmpeg="fake-ffmpeg", ffprobe="fake-ffprobe", check=check)
        command = calls[0][0]
        input_position = command.index("-i")
        thread_positions = [i for i, arg in enumerate(command) if arg == "-threads"]
        self.assertTrue(any(i < input_position and command[i + 1] == "1" for i in thread_positions))
        self.assertTrue(any(i > input_position and command[i + 1] == "1" for i in thread_positions))
        self.assertEqual(command[command.index("-filter_threads") + 1], "1")
        self.assertEqual(command[command.index("-c:v") + 1], "libx264")
        self.assertEqual([timeout for _, timeout, _ in calls], [120, 15])
        self.assertTrue(all(callback is check for _, _, callback in calls))


if __name__ == "__main__":
    unittest.main()
