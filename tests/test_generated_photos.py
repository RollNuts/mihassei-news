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
from scripts.visual_review import reviewed_generated_scene


def fixture():
    return {
        "slug": "family-dinner-gacha-243-rejections",
        "title": "夕飯ガチャ、243種類すべて却下",
        "summary": "献立を決めるガチャを家族全員が真顔で却下。",
        "published_at": "2026-11-03T10:00:00+09:00",
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


def write_review_fixture(root: Path, article: dict, **changes) -> Path:
    """Synthetic gate input in a temporary directory, never a real approval."""
    record = {
        "decision": "accepted",
        "article_sha256": article_fingerprint(article),
        "image_sha256": generated_scene_entry(article, root=root)["sha256"],
        "reviewed_at": "2026-11-04T01:30:05+00:00",
        "note": "Synthetic test fixture; not an actual visual review.",
        **changes,
    }
    path = root / "content/visual_reviews.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1, "reviews": {article["slug"]: record},
    }), encoding="utf-8")
    return path


class SceneQualityHoldTests(unittest.TestCase):
    def hold(self, root: Path, article: dict) -> Path:
        manifest = root / "assets/generated/manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({
            "version": 1, "images": {}, "rejected": {
                article["slug"]: {"article_sha256": article_fingerprint(article),
                                  "reason": "Visual content did not match the article"},
            },
        }))
        return manifest

    def test_exact_rejected_fingerprint_keeps_fallback_and_allows_manual_retry(self):
        article = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.hold(root, article)
            before = manifest.read_bytes()
            self.assertTrue(local_scene_photos.held_scene(article, root=root))
            self.assertEqual(local_scene_photos.pending_articles([article], root=root), [])
            self.assertEqual(local_scene_photos.pending_articles([article], root=root, force=True), [article])
            self.assertEqual(manifest.read_bytes(), before)

    def test_story_or_scene_edit_unlocks_rejected_fingerprint(self):
        article = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.hold(root, article)
            for changed in ({**article, "scene_prompt": "A new visual direction for the joke"},
                            {**article, "paragraphs": ["A different ending"]}):
                self.assertFalse(local_scene_photos.held_scene(changed, root=root))
                self.assertEqual(local_scene_photos.pending_articles([changed], root=root), [changed])

    def test_held_article_does_not_block_others_and_survives_other_image_writes(self):
        article = fixture()
        other = {**article, "slug": "another-article"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.hold(root, article)
            self.assertEqual(local_scene_photos.pending_articles([article, other], root=root), [other])
            local_scene_photos.write_generated(other, fake_jpeg(), "sd_xl_base_1.0.safetensors", root=root)
            self.assertTrue(local_scene_photos.held_scene(article, root=root))
            self.assertTrue(usable_generated_scene(other, root=root))
            local_scene_photos.write_generated(article, fake_jpeg(), "sd_xl_base_1.0.safetensors", root=root)
            self.assertFalse(local_scene_photos.held_scene(article, root=root))


class LocalComedySceneTests(unittest.TestCase):
    def test_a_story_gets_specific_deadpan_photo_direction(self):
        a = fixture()
        direction = local_scene_photos.scene_description(a)
        self.assertIn("capsule lottery machine", direction)
        prompt = local_scene_photos.scene_prompt(a)
        self.assertTrue(prompt.startswith("Candid color photograph"))
        self.assertNotIn("sketch", prompt)
        self.assertIn("fictional", prompt)
        self.assertIn("No title text", prompt)
        different = {**a, "slug": "silent-show", "title": "スベるほど黒字のお笑い番組"}
        self.assertIn("comedian", local_scene_photos.scene_description(different))
        self.assertNotEqual(local_scene_photos.stable_seed(a),
                            local_scene_photos.stable_seed(different))

    def test_visual_punchline_is_frontloaded_in_latest_photo_directions(self):
        """Visual premise must be present early enough for short CLIP windows."""
        from scripts.build import load
        items = {a["slug"]: a for a in load()}
        scenes = (
            ("volleyball-team-lunch-plate-merch",
             ("ceramic dinner plate", "volleyball net")),
            ("family-dinner-gacha-243-rejections",
             ("red toy capsule vending machine", "plastic capsules")),
        )
        for slug, props in scenes:
            with self.subTest(slug=slug):
                article = items[slug]
                direction = local_scene_photos.scene_description(article)
                first_words = " ".join(direction.split()[:65]).lower()
                self.assertTrue(all(prop in first_words for prop in props))
                # The first part of the actual ComfyUI prompt, before CLIP
                # truncation, must describe the central joke and its setting.
                actual = " ".join(local_scene_photos.scene_prompt(article).split()[:75]).lower()
                self.assertTrue(all(prop in actual for prop in props))
                self.assertIn("fictional", actual)

    def test_fallback_sport_selection_never_treats_all_players_as_baseball(self):
        """A generic athlete headline is not evidence of a baseball story."""
        for title, expected, forbidden in (
            ("バレー選手がユニフォームを交換", "volleyball net", "baseball stadium"),
            ("プロ野球選手がグラウンドに登場", "baseball stadium", "volleyball net"),
        ):
            with self.subTest(title=title):
                article = {**fixture(), "slug": "another-sport",
                           "title": title, "summary": "練習の途中で珍事が起きた"}
                text = local_scene_photos.scene_description(article).lower()
                self.assertIn(expected, text)
                self.assertNotIn(forbidden, text)
        soccer = {**fixture(), "slug": "soccer-story",
                  "title": "サッカー選手が初出場",
                  "summary": "サッカーの試合中におかしな出来事があった"}
        self.assertNotIn("baseball stadium",
                         local_scene_photos.scene_description(soccer).lower())

    def test_visual_rejection_disables_original_still_and_any_motion_clip(self):
        """An actual visually mismatched photo must not reach the public page."""
        from scripts.generated_photos import visually_held_scene, generated_scene_entry
        from scripts.motion_clips import usable_clip
        article = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local_scene_photos.write_generated(
                article, fake_jpeg(), "sd_xl_base_1.0.safetensors", root=root)
            self.assertTrue(usable_generated_scene(article, root=root))
            write_review_fixture(root, article)
            self.assertTrue(reviewed_generated_scene(article, root=root))
            manifest = root / "assets/generated/manifest.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["rejected"] = {
                article["slug"]: {
                    "article_sha256": article_fingerprint(article),
                    "reason": "Reviewer saw the wrong scene"
                }
            }
            manifest.write_text(json.dumps(data), encoding="utf-8")
            self.assertTrue(visually_held_scene(article, root=root))
            self.assertFalse(usable_generated_scene(article, root=root))
            self.assertFalse(reviewed_generated_scene(article, root=root))
            self.assertIsNone(generated_scene_entry(article, root=root))
            self.assertIsNone(usable_clip(article, root=root))
            self.assertEqual(local_scene_photos.pending_articles(
                [article], root=root), [])
            with patch.object(build, "BASE", root):
                self.assertEqual(build.editorial_image(article),
                                 "/images/" + article["slug"] + ".svg")
                self.assertEqual(build.social_image(article),
                                 "/og/articles/" + article["slug"] +
                                 ".png?v=20261103-topic2")
            # A new editorial visual direction releases the old hold, but
            # the old JPEG is still invalidated by the changed fingerprint.
            altered = {**article, "scene_prompt": "Completely different scene"}
            self.assertFalse(visually_held_scene(altered, root=root))
            self.assertFalse(usable_generated_scene(altered, root=root))

    def test_comfyui_workflow_is_offline_and_uses_only_standard_nodes(self):
        a = fixture()
        workflow = local_scene_photos.workflow(a, "sd_xl_base_1.0.safetensors")
        self.assertEqual(workflow["1"]["class_type"], "CheckpointLoaderSimple")
        dimensions = workflow["4"]["inputs"]
        self.assertEqual(dimensions["width"] * 9, dimensions["height"] * 16)
        self.assertLessEqual(dimensions["width"] * dimensions["height"], 1024**2)
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

    def test_builder_prefers_reviewed_generated_still_without_misleading_attribution(self):
        a = fixture()
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            local_scene_photos.write_generated(a, fake_jpeg(), "sd_xl_base_1.0.safetensors", root=root)
            write_review_fixture(root, a)
            self.assertTrue(usable_generated_scene(a, root=root))
            self.assertTrue(reviewed_generated_scene(a, root=root))
            with patch.object(build, "BASE", root):
                self.assertEqual(build.editorial_image(a), generated_url(a))
                self.assertEqual(build.social_image(a), generated_url(a))
                tag = build.image_tag(a, "story-thumb")
                self.assertIn('src="'+generated_url(a)+'"', tag)
                self.assertIn('data-generated-scene="true"', tag)
                self.assertIn('data-fallback="/images/'+a["slug"]+'.svg"', tag)
                self.assertIn("再現イメージ", tag)
                credit = build.photo_credit(a)
                self.assertIn("再現イメージ", credit)
                self.assertIn("（合成）", credit)
                card = build.card(a)
                self.assertIn("story-image-link--generated", card)
                self.assertIn(a["title"], card)

    def test_missing_rejected_or_stale_review_never_reaches_pages_og_or_rss(self):
        """Byte-valid JPEGs are not public until the exact scene is accepted."""
        article = fixture()
        cases = (
            ("missing", None),
            ("rejected", {"decision": "rejected"}),
            ("stale article", {"article_sha256": "0" * 64}),
            ("different image", {"image_sha256": "0" * 64}),
        )
        for label, changes in cases:
            with self.subTest(review=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                local_scene_photos.write_generated(
                    article, fake_jpeg(), "sd_xl_base_1.0.safetensors", root=root)
                if changes is not None:
                    write_review_fixture(root, article, **changes)
                # Retain the separate generation/byte-integrity contract.
                self.assertTrue(usable_generated_scene(article, root=root))
                self.assertFalse(reviewed_generated_scene(article, root=root))
                with patch.object(build, "BASE", root), \
                     patch.object(build, "stylesheet_url", return_value="/assets/newspaper.css?v=test"):
                    fallback = "/images/" + article["slug"] + ".svg"
                    social = "/og/articles/" + article["slug"] + ".png?v=20261103-topic2"
                    self.assertEqual(build.editorial_image(article), fallback)
                    self.assertEqual(build.social_image(article), social)
                    tag = build.image_tag(article, "article-visual")
                    self.assertIn('src="' + fallback + '"', tag)
                    self.assertNotIn(generated_url(article), tag)
                    self.assertNotIn("data-generated-scene", tag)
                    self.assertEqual(build.photo_credit(article), "創作イラスト")
                    card = build.card(article)
                    self.assertNotIn("story-image-link--generated", card)
                    self.assertNotIn(generated_url(article), card)
                    page = build.layout(article["title"], article["summary"],
                                        build.article_url(article), tag,
                                        kind="story", article=article)
                    self.assertIn('property="og:image" content="' + build.SITE + social + '"', page)
                    self.assertIn('name="twitter:image" content="' + build.SITE + social + '"', page)
                    self.assertIn('property="og:image:type" content="image/png"', page)
                    self.assertNotIn(generated_url(article), page)
                    rss = build.generate_rss([article], "test", "創作ニュース",
                                             build.SITE + "/", "/feed.xml")
                    self.assertIn(build.SITE + social, rss)
                    self.assertIn('type="image/png"', rss)
                    self.assertNotIn(generated_url(article), rss)

    def test_new_valid_jpeg_does_not_inherit_previous_visual_acceptance(self):
        """Regeneration updates byte provenance but cannot approve new pixels."""
        article = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local_scene_photos.write_generated(
                article, fake_jpeg(), "sd_xl_base_1.0.safetensors", root=root)
            review = write_review_fixture(root, article)
            previous_review = review.read_bytes()
            self.assertTrue(reviewed_generated_scene(article, root=root))
            # This is still a well-formed header fixture, with a new byte hash.
            replacement = fake_jpeg()[:-2] + b"\x01\x02" + b"\xff\xd9"
            local_scene_photos.write_generated(
                article, replacement, "sd_xl_base_1.0.safetensors", root=root)
            self.assertTrue(usable_generated_scene(article, root=root))
            self.assertEqual(review.read_bytes(), previous_review)
            self.assertFalse(reviewed_generated_scene(article, root=root))
            with patch.object(build, "BASE", root):
                self.assertEqual(build.editorial_image(article), build.illustration_path(article))
                self.assertTrue(build.social_image(article).startswith("/og/articles/"))

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
