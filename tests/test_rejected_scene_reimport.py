"""Rejected-byte imports never restore an old visual approval or erase a hold."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.generated_photos import article_fingerprint, usable_generated_scene
from scripts.local_scene_photos import held_scene, pending_articles, write_generated
from scripts.visual_review import reviewed_generated_scene


def jpeg_fixture(marker: int = 0) -> bytes:
    """Dimension/hash fixture only; these bytes are not an actual reviewed photo."""
    header = (b"\xff\xd8\xff\xc0\x00\x11\x08"
              + (675).to_bytes(2, "big") + (1200).to_bytes(2, "big")
              + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00")
    return header + bytes([marker]) * 17000 + b"\xff\xd9"


class RejectedSceneReimportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.article = {
            "slug": "reimport-regression-fixture",
            "title": "架空の画像再取込テスト",
            "summary": "生成と公開採用を区別する一時テスト用記事。",
            "category": "社会",
            "paragraphs": ["実在の記事や目視承認ではない。"],
            "scene_prompt": "Synthetic integrity regression fixture only.",
        }
        self.original = jpeg_fixture()
        self.model = "sd_xl_base_1.0.safetensors"
        self.image = write_generated(
            self.article, self.original, self.model, root=self.root
        )
        self.manifest = self.root / "assets/generated/manifest.json"
        self.review = self.root / "content/visual_reviews.json"
        self.review.parent.mkdir()
        self.review.write_text(json.dumps({
            "version": 1,
            "reviews": {self.article["slug"]: {
                "decision": "accepted",
                "article_sha256": article_fingerprint(self.article),
                "image_sha256": hashlib.sha256(self.original).hexdigest(),
                "reviewed_at": "2026-11-04T01:30:05+00:00",
                "note": "Synthetic temporary test fixture; not an actual visual review.",
            }},
        }), encoding="utf-8")
        self.assertTrue(reviewed_generated_scene(self.article, root=self.root))

    def hold(self, **extra):
        payload = json.loads(self.manifest.read_text(encoding="utf-8"))
        record = {
            "article_sha256": article_fingerprint(self.article),
            "reason": "Synthetic subsequent visual rejection for this test only.",
            **extra,
        }
        payload.setdefault("rejected", {})[self.article["slug"]] = record
        self.manifest.write_text(json.dumps(payload), encoding="utf-8")
        return record

    def snapshot(self):
        return tuple(path.read_bytes() for path in
                     (self.image, self.manifest, self.review))

    def assert_reimport_rejected(self, data, model=None, origin="local-comfyui"):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "Rejected JPEG.*new bytes"):
            write_generated(self.article, data, model or self.model,
                            root=self.root, origin=origin)
        self.assertEqual(self.snapshot(), before,
                         "Rejected import must not alter the JPEG, hold or old review")
        self.assertEqual(list(self.image.parent.glob("*.tmp")), [])
        self.assertTrue(held_scene(self.article, root=self.root))
        self.assertFalse(reviewed_generated_scene(self.article, root=self.root))
        self.assertEqual(pending_articles([self.article], root=self.root), [])

    def test_existing_record_hash_blocks_same_bytes_and_preserves_old_accepted(self):
        # Older holds need no explicit candidate digest: the existing image
        # record is enough to identify a forbidden same-byte reimport.
        self.hold()
        for origin, model in (
            ("local-comfyui", self.model),
            ("github-cpu-lcm", "SimianLuo/LCM_Dreamshaper_v7"),
        ):
            with self.subTest(origin=origin):
                self.assert_reimport_rejected(self.original, model, origin)

    def test_rejected_candidate_hash_blocks_bytes_different_from_current_file(self):
        rejected_candidate = jpeg_fixture(1)
        self.hold(rejected_candidate_sha256=hashlib.sha256(rejected_candidate).hexdigest())
        self.assertNotEqual(rejected_candidate, self.image.read_bytes())
        self.assert_reimport_rejected(rejected_candidate)

    def test_rejected_candidate_hash_remains_effective_without_image_record(self):
        self.hold(rejected_candidate_sha256=hashlib.sha256(self.original).hexdigest())
        payload = json.loads(self.manifest.read_text(encoding="utf-8"))
        payload["images"].pop(self.article["slug"])
        self.manifest.write_text(json.dumps(payload), encoding="utf-8")
        self.assert_reimport_rejected(self.original)

    def test_new_bytes_can_be_saved_but_do_not_inherit_old_visual_review(self):
        self.hold(rejected_candidate_sha256=hashlib.sha256(self.original).hexdigest())
        before_review = self.review.read_bytes()
        replacement = jpeg_fixture(2)
        write_generated(self.article, replacement, self.model, root=self.root)
        self.assertEqual(self.image.read_bytes(), replacement)
        self.assertTrue(usable_generated_scene(self.article, root=self.root))
        self.assertFalse(held_scene(self.article, root=self.root))
        self.assertFalse(reviewed_generated_scene(self.article, root=self.root))
        self.assertEqual(self.review.read_bytes(), before_review)
        self.assertEqual(pending_articles([self.article], root=self.root), [],
                         "Unreviewed but saved new bytes must not regenerate endlessly")

    def test_old_article_fingerprint_hold_does_not_block_current_article_write(self):
        self.hold(rejected_candidate_sha256=hashlib.sha256(self.original).hexdigest())
        updated = {**self.article, "paragraphs": ["更新後の一時テスト用記事。"]}
        before_review = self.review.read_bytes()
        self.assertFalse(held_scene(updated, root=self.root))
        # Preserve the existing writer contract for a stale article hold. The
        # old article's accepted record still cannot approve the changed story.
        write_generated(updated, self.original, self.model, root=self.root)
        self.assertTrue(usable_generated_scene(updated, root=self.root))
        self.assertFalse(held_scene(updated, root=self.root))
        self.assertFalse(reviewed_generated_scene(updated, root=self.root))
        self.assertEqual(self.review.read_bytes(), before_review)

    def test_another_article_write_preserves_unrelated_hold_and_old_review(self):
        held = self.hold(rejected_candidate_sha256=hashlib.sha256(self.original).hexdigest())
        before_review = self.review.read_bytes()
        other = {**self.article, "slug": "unrelated-reimport-fixture"}
        write_generated(other, jpeg_fixture(3), self.model, root=self.root)
        payload = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(payload["rejected"][self.article["slug"]], held)
        self.assertEqual(self.image.read_bytes(), self.original)
        self.assertEqual(self.review.read_bytes(), before_review)
        self.assertTrue(held_scene(self.article, root=self.root))
        self.assertFalse(reviewed_generated_scene(self.article, root=self.root))
        self.assertTrue(usable_generated_scene(other, root=self.root))


if __name__ == "__main__":
    unittest.main()
