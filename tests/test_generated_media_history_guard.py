"""Editorial rewrites must not silently relabel old AI photos as newly generated."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.cpu_photo_model import MODEL as CPU_MODEL, LEGACY_MODEL, ORIGIN as CPU_ORIGIN
from scripts.guard_generated_media_history import (
    incompatible_rewrites, verify_new_images, images,
)
from scripts.generated_photos import article_fingerprint
from scripts.local_scene_photos import stable_seed, write_generated


ARTICLE = {
    "slug": "hypothetical-satire",
    "title": "かつての写真から新しい冗談",
    "summary": "昔の写真と今の物語は違う",
    "paragraphs": ["新しい出来事は別の場所で起きた。"],
    "scene_prompt": "A new live action scene of an ordinary object.",
}
# Minimal bytes are an existing test-only header fixture, not a publishable photo.
JPEG = (b"\xff\xd8\xff\xc0\x00\x11\x08"
        + (675).to_bytes(2, "big") + (1200).to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        + b"\x00" * 17000 + b"\xff\xd9")


def document(*, seed=9007199254740999):
    return {"version": 1, "images": {
        "hypothetical-satire": {
            "sha256": "a" * 64,
            "article_sha256": "b" * 64,
            "model": "sd_xl_base_1.0.safetensors",
            "seed": seed,
        }
    }, "rejected": {}}


class MediaHistoryGuardTests(unittest.TestCase):
    def test_protects_existing_photo_fingerprint_from_manual_reapproval(self):
        before = document()
        after = copy.deepcopy(before)
        after["images"]["hypothetical-satire"]["article_sha256"] = "c" * 64
        errors = incompatible_rewrites(before, after)
        self.assertEqual(len(errors), 1)
        self.assertIn("article_sha256", errors[0])

    def test_protects_unmodified_photo_seed_from_javascript_precision_loss(self):
        before = document(seed=9007199254740999)
        after = json.loads(json.dumps(before))
        after["images"]["hypothetical-satire"]["seed"] = 9007199254741000
        self.assertIn("seed", incompatible_rewrites(before, after)[0])

    def test_genuine_new_image_is_allowed_to_change_generation_record(self):
        before = document()
        after = copy.deepcopy(before)
        after["images"]["hypothetical-satire"]["sha256"] = "c" * 64
        after["images"]["hypothetical-satire"]["article_sha256"] = "d" * 64
        self.assertEqual(incompatible_rewrites(before, after), [])
        self.assertEqual(incompatible_rewrites(before, before), [])

    def test_new_image_must_have_actual_verified_bytes_and_current_story(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "content").mkdir()
            (root / "content/articles.json").write_text(
                json.dumps([ARTICLE], ensure_ascii=False), encoding="utf-8")
            new = {"version": 1, "images": {}, "rejected": {}}
            write_generated(ARTICLE, JPEG, "sd_xl_base_1.0.safetensors", root=root)
            new = json.loads((root / "assets/generated/manifest.json").read_text())
            self.assertEqual(verify_new_images({"version":1,"images":{}}, new, root=root), [])
            wrong_seed = copy.deepcopy(new)
            wrong_seed["images"][ARTICLE["slug"]]["seed"] += 1
            self.assertTrue(any("seed" in err for err in verify_new_images(
                {"version":1,"images":{}}, wrong_seed, root=root)))
            wrong_text = copy.deepcopy(new)
            wrong_text["images"][ARTICLE["slug"]]["article_sha256"] = "0" * 64
            self.assertTrue(any("record" in err for err in verify_new_images(
                {"version":1,"images":{}}, wrong_text, root=root)))
            self.assertEqual(new["images"][ARTICLE["slug"]]["article_sha256"],
                             article_fingerprint(ARTICLE))
            self.assertEqual(new["images"][ARTICLE["slug"]]["seed"],
                             stable_seed(ARTICLE))

    def test_native_chatgpt_image_uses_generation_receipt_not_local_seed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "content").mkdir()
            (root / "content/articles.json").write_text(
                json.dumps([ARTICLE], ensure_ascii=False), encoding="utf-8")
            generated = root / "assets/generated"
            generated.mkdir(parents=True)
            photo = JPEG
            (generated / (ARTICLE["slug"] + ".jpg")).write_bytes(photo)
            import hashlib
            record = {
                "origin": "chatgpt-imagegen",
                "model": "ChatGPT ImageGen",
                "generation_id": "4c0beb9e-915d-47bc-8905-da3cc704457a",
                "article_sha256": article_fingerprint(ARTICLE),
                "sha256": hashlib.sha256(photo).hexdigest(),
                "bytes": len(photo),
                "prompt_version": 3,
            }
            current = {"version": 1, "images": {ARTICLE["slug"]: record}, "rejected": {}}
            (generated / "manifest.json").write_text(
                json.dumps(current), encoding="utf-8")
            self.assertEqual(
                verify_new_images({"version": 1, "images": {}, "rejected": {}},
                                  current, root=root),
                [],
            )
            with_seed = copy.deepcopy(current)
            with_seed["images"][ARTICLE["slug"]]["seed"] = stable_seed(ARTICLE)
            (generated / "manifest.json").write_text(
                json.dumps(with_seed), encoding="utf-8")
            self.assertTrue(any(
                "synthetic seed" in err for err in verify_new_images(
                    {"version": 1, "images": {}, "rejected": {}},
                    with_seed, root=root)
            ))

    def test_new_cpu_origin_and_previous_profiles_keep_verified_provenance(self):
        for origin, model in ((CPU_ORIGIN, CPU_MODEL),
                              ("github-cpu-lcm", CPU_MODEL),
                              ("github-cpu-lcm", LEGACY_MODEL),
                              ("local-comfyui", "sd_xl_base_1.0.safetensors")):
            with self.subTest(origin=origin, model=model), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "content").mkdir()
                (root / "content/articles.json").write_text(json.dumps([ARTICLE]))
                write_generated(ARTICLE, JPEG, model, origin=origin, root=root)
                path = root / "assets/generated/manifest.json"
                record = path.read_bytes()
                current = json.loads(record)
                self.assertEqual(verify_new_images({"version": 1, "images": {}}, current, root=root), [])
                self.assertEqual(verify_new_images(current, current, root=root), [])
                self.assertEqual(incompatible_rewrites(current, current), [])
                self.assertEqual(path.read_bytes(), record)

    def test_sd15_new_image_rejects_noninteger_seed_and_wrong_inference_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "content").mkdir()
            (root / "content/articles.json").write_text(json.dumps([ARTICLE]))
            write_generated(ARTICLE, JPEG, CPU_MODEL, origin=CPU_ORIGIN, root=root)
            path = root / "assets/generated/manifest.json"
            current = json.loads(path.read_text())
            before = {"version": 1, "images": {}}
            # Numeric equality alone accepts 0.0 and False as the integer 0.
            # Keep that corner case independent of this article's actual hash.
            with patch("scripts.guard_generated_media_history.stable_seed", return_value=0):
                for seed in (0.0, False):
                    with self.subTest(seed=repr(seed)):
                        invalid = copy.deepcopy(current)
                        invalid["images"][ARTICLE["slug"]]["seed"] = seed
                        path.write_text(json.dumps(invalid))
                        self.assertTrue(any("seed" in error for error in verify_new_images(before, invalid, root=root)))
            invalid = copy.deepcopy(current)
            invalid["images"][ARTICLE["slug"]]["inference"]["guidance_scale"] = 0.0
            path.write_text(json.dumps(invalid))
            self.assertTrue(any("integrity" in error for error in verify_new_images(before, invalid, root=root)))

    def test_invalid_manifest_fails_closed(self):
        with self.assertRaises(ValueError):
            images({"version": 1, "images": []})
        with self.assertRaises(ValueError):
            images({"version": 2, "images": {}})


if __name__ == "__main__":
    unittest.main()
