"""Offline gates for optional, clearly labelled local AI comedy-news scenes."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import build, local_scene_photos
from scripts.generated_photos import (
    article_fingerprint, usable_generated_scene, generated_scene_entry, generated_url,
)


def fixture():
    return {
        "slug": "family-dinner-gacha-243-rejections",
        "title": "夕飯ガチャ、243種類すべて却下",
        "summary": "献立を決めるガチャを家族全員が真顔で却下。",
        "category": "暮らし",
        "art": "generic",
        "fiction_at": "2031-11-03T08:20:00+09:00",
    }


def fake_jpeg():
    """Header fixture for hash/dimension validation; not a generated photograph."""
    sof = (b"\xff\xd8\xff\xc0\x00\x11\x08"
           + (675).to_bytes(2, "big") + (1200).to_bytes(2, "big")
           + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00")
    return sof + b"\x00" * 17000 + b"\xff\xd9"


class LocalComedySceneTests(unittest.TestCase):
    def test_a_story_gets_specific_deadpan_photo_direction(self):
        a = fixture()
        direction = local_scene_photos.scene_description(a)
        self.assertIn("capsule lottery machine", direction)
        prompt = local_scene_photos.scene_prompt(a)
        self.assertIn("STAGED SATIRICAL NEWS COMEDY", prompt)
        self.assertIn("fictional", prompt)
        self.assertIn("No title text", prompt)
        different = {**a, "slug": "silent-show", "title": "スベるほど黒字のお笑い番組"}
        self.assertIn("comedian", local_scene_photos.scene_description(different))
        self.assertNotEqual(local_scene_photos.stable_seed(a),
                            local_scene_photos.stable_seed(different))

    def test_comfyui_workflow_is_offline_and_uses_only_standard_nodes(self):
        a = fixture()
        workflow = local_scene_photos.workflow(a, "sd_xl_base_1.0.safetensors")
        self.assertEqual(workflow["1"]["class_type"], "CheckpointLoaderSimple")
        self.assertEqual(workflow["4"]["inputs"]["width"], 1024)
        self.assertEqual(workflow["4"]["inputs"]["height"], 576)
        self.assertEqual(workflow["5"]["class_type"], "KSampler")
        self.assertEqual(workflow["7"]["class_type"], "SaveImage")
        self.assertEqual(workflow["5"]["inputs"]["seed"], local_scene_photos.stable_seed(a))
        for filename in ("https://image-api.example/a", "../model.safetensors", "bad.ckpt"):
            with self.assertRaises(ValueError):
                local_scene_photos.workflow(a, filename)

    def test_remote_or_redirected_endpoints_are_forbidden(self):
        for url in ("https://comfy.example.com", "http://example.com:8188",
                    "https://127.0.0.1:8188", "http://127.0.0.1:8188/path",
                    "http://127.0.0.1:8188?token=abc"):
            with self.assertRaises(ValueError):
                local_scene_photos.loopback_base(url)
        self.assertEqual(local_scene_photos.loopback_base("http://127.0.0.1:8188"),
                         "http://127.0.0.1:8188")
        self.assertIsNone(local_scene_photos.NoRedirect().redirect_request(
            None, None, 302, "redirect", {}, "https://expensive-service.example"))

    def test_generated_scene_must_match_exact_story_and_file_hash(self):
        a = fixture()
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            data = fake_jpeg()
            path = local_scene_photos.write_generated(a, data, "sd_xl_base_1.0.safetensors", root=root)
            self.assertTrue(path.exists())
            self.assertTrue(usable_generated_scene(a, root=root))
            self.assertEqual(generated_url(a), "/generated/" + a["slug"] + ".jpg")
            entry = generated_scene_entry(a, root=root)
            self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(entry["article_sha256"], article_fingerprint(a))
            self.assertFalse(usable_generated_scene({**a, "summary": "編集済みの文章"}, root=root))
            path.write_bytes(data + b"\x00")
            self.assertFalse(usable_generated_scene(a, root=root))
            path.write_bytes(data)
            manifest = root / "assets/generated/manifest.json"
            manifest.write_text("{bad-json", encoding="utf-8")
            self.assertFalse(usable_generated_scene(a, root=root))

    def test_builder_prefers_valid_generated_still_without_misleading_attribution(self):
        a = fixture()
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            local_scene_photos.write_generated(a, fake_jpeg(), "sd_xl_base_1.0.safetensors", root=root)
            with patch.object(build, "BASE", root):
                self.assertEqual(build.editorial_image(a), generated_url(a))
                self.assertEqual(build.social_image(a), generated_url(a))
                tag = build.image_tag(a, "story-thumb")
                self.assertIn('src="'+generated_url(a)+'"', tag)
                self.assertIn('data-generated-scene="true"', tag)
                self.assertIn('data-fallback="/images/'+a["slug"]+'.svg"', tag)
                self.assertIn("AI生成", tag)
                credit = build.photo_credit(a)
                self.assertIn("AI生成", credit)
                self.assertIn("実際の事件", credit)
                card = build.card(a)
                self.assertIn("story-image-link--generated", card)
                self.assertIn(a["title"], card)

    def test_dry_run_never_connects_or_generates_assets(self):
        a = fixture()
        output = io.StringIO()
        with patch.object(local_scene_photos, "load_articles", return_value=[a]), \
             patch.object(local_scene_photos, "create_png", side_effect=AssertionError("network called")), \
             contextlib.redirect_stdout(output):
            code = local_scene_photos.main(["--dry-run", "--limit", "1"])
        self.assertEqual(code, 0)
        self.assertIn(a["slug"], output.getvalue())
        self.assertIn("capsule lottery machine", output.getvalue())


if __name__ == "__main__":
    unittest.main()
