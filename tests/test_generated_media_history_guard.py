"""Editorial rewrites must not silently relabel old AI photos as newly generated."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

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

    def test_invalid_manifest_fails_closed(self):
        with self.assertRaises(ValueError):
            images({"version": 1, "images": []})
        with self.assertRaises(ValueError):
            images({"version": 2, "images": {}})


if __name__ == "__main__":
    unittest.main()
