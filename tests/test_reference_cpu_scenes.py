"""Offline integrity/provenance checks; synthetic bytes are not visual approvals."""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.cpu_photo_model import (
    MODEL, LEGACY_MODEL, ORIGIN, REFERENCE_ORIGIN, REFERENCE_INFERENCE,
)
from scripts.generated_photos import (
    article_fingerprint, generated_scene_entry, usable_generated_scene,
)
from scripts.guard_generated_media_history import incompatible_rewrites, verify_new_images
from scripts.local_scene_photos import stable_seed, write_generated
from scripts.publish_cpu_scenes import publish_bundle, validate_bundle
from scripts.visual_review import reviewed_generated_scene


SOURCE = {
    "slug": "reference-photo-fixture", "title": "返品箱の加湿器",
    "summary": "返品された加湿器が倉庫に積み上がった。",
    "paragraphs": ["故障した加湿器が次々と返品された。"],
    "scene_prompt": "A store worker stands among returned humidifier boxes.",
}
ARTICLE = {**SOURCE, "scene_prompt": "A humidifier inside an open return box.",
           "visual_strategy": "cpu-photo"}
# Test-only header fixture, never a real generated or visually reviewed photo.
JPEG = (b"\xff\xd8\xff\xc0\x00\x11\x08"
        + (675).to_bytes(2, "big") + (1200).to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        + b"\x00" * 17000 + b"\xff\xd9")


def reference_record(article=ARTICLE):
    return {
        "origin": REFERENCE_ORIGIN, "model": MODEL,
        "inference": copy.deepcopy(REFERENCE_INFERENCE),
        "article_sha256": article_fingerprint(article),
        "source_article_sha256": article_fingerprint(SOURCE),
        "init_svg_sha256": "a" * 64, "init_rgb_sha256": "b" * 64,
        "renderer": "CairoSVG", "renderer_version": "2.7.1",
        "seed": stable_seed(article), "prompt_version": 3,
        "sha256": hashlib.sha256(JPEG).hexdigest(), "bytes": len(JPEG),
        "generated_at": "2026-11-08T10:00:00+00:00",
    }


def save_bundle(root, article=ARTICLE, record=None):
    (root / "content").mkdir(exist_ok=True)
    (root / "content/articles.json").write_text(json.dumps([article], ensure_ascii=False))
    directory = root / "assets/generated"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (article["slug"] + ".jpg")).write_bytes(JPEG)
    payload = {"version": 1, "images": {article["slug"]: record or reference_record(article)}}
    (directory / "manifest.json").write_text(json.dumps(payload))
    return payload


class ReferenceCPUIntegrityTests(unittest.TestCase):
    def test_reference_provenance_is_immutable_input_history_not_a_photo_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            save_bundle(root)
            self.assertEqual(validate_bundle(root), [ARTICLE])
            self.assertTrue(usable_generated_scene(ARTICLE, root=root))
            self.assertFalse(reviewed_generated_scene(ARTICLE, root=root))
            # A later removal/edit of the historical SVG must not invalidate
            # the reviewed JPEG. It is no longer the article's active asset.
            directory = root / "assets/editorial-scenes"
            directory.mkdir()
            svg = directory / (ARTICLE["slug"] + ".svg")
            svg.write_text("<svg>historical source is no longer current</svg>")
            review = {"decision": "accepted", "article_sha256": article_fingerprint(ARTICLE),
                      "image_sha256": hashlib.sha256(JPEG).hexdigest(),
                      "reviewed_at": "2026-11-08T10:01:00+00:00",
                      "note": "Synthetic gate fixture, not an actual visual review."}
            (root / "content/visual_reviews.json").write_text(json.dumps({
                "version": 1, "reviews": {ARTICLE["slug"]: review}}))
            self.assertTrue(reviewed_generated_scene(ARTICLE, root=root))
            svg.unlink()
            self.assertTrue(reviewed_generated_scene(ARTICLE, root=root))
            changed = {**ARTICLE, "paragraphs": ["Different story."]}
            self.assertFalse(reviewed_generated_scene(changed, root=root))

    def test_reference_input_hashes_renderer_and_original_source_are_required(self):
        record = reference_record()
        invalid = []
        for key in ("source_article_sha256", "init_svg_sha256", "init_rgb_sha256",
                    "renderer", "renderer_version"):
            missing = copy.deepcopy(record)
            missing.pop(key)
            invalid.append(("missing " + key, missing))
        for key in ("source_article_sha256", "init_svg_sha256", "init_rgb_sha256"):
            for value in (None, 1, "A" * 64, "f" * 63):
                invalid.append((key + repr(value), {**record, key: value}))
        invalid.extend((
            ("same source and clone", {**record, "source_article_sha256": article_fingerprint(ARTICLE)}),
            ("renderer", {**record, "renderer": "untracked converter"}),
            ("renderer version", {**record, "renderer_version": "2.8.0"}),
        ))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, changed in invalid:
                with self.subTest(label=label):
                    save_bundle(root, record=changed)
                    self.assertIsNone(generated_scene_entry(ARTICLE, root=root))
                    with self.assertRaises(ValueError):
                        validate_bundle(root)

    def test_reference_profile_model_and_exact_integer_seed_fail_closed(self):
        original = reference_record()
        invalid = [
            {**original, "model": LEGACY_MODEL},
            {**original, "seed": stable_seed(ARTICLE) + 1},
            {**original, "seed": str(stable_seed(ARTICLE))},
        ]
        for key, value in (("strength", 0.5), ("pipeline", "StableDiffusionPipeline"),
                           ("init_image_source", "unreviewed_upload"),
                           ("guidance_scale", 0.0)):
            profile = copy.deepcopy(original["inference"])
            profile[key] = value
            invalid.append({**original, "inference": profile})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for record in invalid:
                with self.subTest(record=record):
                    save_bundle(root, record=record)
                    self.assertFalse(usable_generated_scene(ARTICLE, root=root))
                    with self.assertRaises(ValueError):
                        validate_bundle(root)
            # Equality with 0 must never admit False or 0.0 as integer seeds.
            with patch("scripts.local_scene_photos.stable_seed", return_value=0):
                for seed in (False, 0.0):
                    save_bundle(root, record={**original, "seed": seed})
                    self.assertIsNone(generated_scene_entry(ARTICLE, root=root))

    def test_reference_bundle_is_one_preview_and_never_automatically_published(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = save_bundle(root)
            with patch("scripts.publish_cpu_scenes.trusted_ci", return_value=True), \
                    patch("scripts.publish_cpu_scenes.sync.safe_to_publish") as publish:
                with self.assertRaisesRegex(ValueError, "preview-only"):
                    publish_bundle(root)
                publish.assert_not_called()
            extra = root / "unexpected.svg"
            extra.write_text("<svg/>")
            with self.assertRaisesRegex(ValueError, "Unexpected"):
                validate_bundle(root)
            extra.unlink()
            second = {**ARTICLE, "slug": "second-reference-photo-fixture"}
            (root / "content/articles.json").write_text(json.dumps([ARTICLE, second]))
            (root / "assets/generated" / (second["slug"] + ".jpg")).write_bytes(JPEG)
            manifest["images"][second["slug"]] = reference_record(second)
            (root / "assets/generated/manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "exactly one"):
                validate_bundle(root)

    def test_history_guard_checks_new_reference_bytes_and_preserves_old_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = save_bundle(root)
            before = {"version": 1, "images": {}}
            self.assertEqual(verify_new_images(before, current, root=root), [])
            self.assertEqual(incompatible_rewrites(current, current), [])
            self.assertEqual(verify_new_images(current, current, root=root), [])
            for key, value in (("seed", False), ("init_rgb_sha256", "invalid")):
                changed = copy.deepcopy(current)
                changed["images"][ARTICLE["slug"]][key] = value
                (root / "assets/generated/manifest.json").write_text(json.dumps(changed))
                self.assertTrue(verify_new_images(before, changed, root=root))
                self.assertTrue(incompatible_rewrites(current, changed))

    def test_legacy_generations_keep_their_exact_records_and_review_gates(self):
        profiles = (("local-comfyui", "sd_xl_base_1.0.safetensors"),
                    ("github-cpu-lcm", LEGACY_MODEL),
                    ("github-cpu-lcm", MODEL), (ORIGIN, MODEL))
        for origin, model in profiles:
            with self.subTest(origin=origin, model=model), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "content").mkdir()
                (root / "content/articles.json").write_text(json.dumps([ARTICLE]))
                write_generated(ARTICLE, JPEG, model, origin=origin, root=root)
                manifest = root / "assets/generated/manifest.json"
                before = manifest.read_bytes()
                self.assertTrue(usable_generated_scene(ARTICLE, root=root))
                self.assertFalse(reviewed_generated_scene(ARTICLE, root=root))
                payload = json.loads(before)
                self.assertEqual(verify_new_images({"version": 1, "images": {}}, payload, root=root), [])
                self.assertEqual(manifest.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
