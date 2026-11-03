"""Cost-free optional video pipeline with integrity, storage and still-image fallbacks."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
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
                self.assertIn('preload="none"', page)
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

    def test_real_approved_still_generates_bounded_silent_mp4(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("No local ffmpeg tools installed; production static build still works.")
        suitable = next((a for a in self.articles if usable_generated_scene(a, root=ROOT)), None)
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
            self.assertEqual(generate(root, limit=1, slug=slug, min_free_gib=0), [slug])
            self.assertEqual(candidates(root), [])
            verified = usable_clip(suitable, root=root)
            self.assertIsNotNone(verified)
            self.assertLessEqual(verified["bytes"], MAX_CLIP_BYTES)
            self.assertEqual(verified["duration_ms"], 4000)
            movie = root / clip_url(suitable).lstrip("/").replace("clips/","assets/clips/",1)
            self.assertTrue(movie.is_file())
            self.assertEqual(hashlib.sha256(movie.read_bytes()).hexdigest(), verified["sha256"])
            with patch.object(build, "BASE", root):
                html = build.article_hero_media(suitable)
                self.assertIn('<video class="article-visual article-video"', html)
                self.assertIn('controls playsinline muted loop preload="none"', html)
                self.assertIn('poster="/generated/'+slug+'.jpg"', html)
                self.assertIn('<source src="/clips/'+slug+'.mp4" type="video/mp4">', html)
                self.assertIn('src="/generated/'+slug+'.jpg"', html)
                self.assertNotIn("autoplay", html)
                page = build.layout("記事", "架空", build.article_url(suitable),
                                    "<p>記事</p>", kind="story", article=suitable)
                self.assertIn('"@type": "VideoObject"', page)
                self.assertIn('https://veripsa.com/clips/'+slug+'.mp4', page)
                self.assertIn('"duration": "PT4S"', page)
                self.assertIn('"@type": "BlogPosting"', page)
            movie.write_bytes(movie.read_bytes() + b"tampered")
            self.assertIsNone(usable_clip(suitable, root=root))
            with patch.object(build, "BASE", root):
                self.assertNotIn("<video", build.article_hero_media(suitable))
            self.assertEqual(manifest_entries(root)[slug]["bytes"], verified["bytes"])


if __name__ == "__main__":
    unittest.main()
