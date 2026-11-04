"""Stored visual approval is separate from real JPEG integrity validation."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts.generated_photos import article_fingerprint, usable_generated_scene
from scripts.visual_review import main, reviewed_generated_scene


def fake_jpeg(marker: bytes = b"\x00") -> bytes:
    """Actual header/hash fixture; never a photograph or a visual approval."""
    sof = (b"\xff\xd8\xff\xc0\x00\x11\x08"
           + (675).to_bytes(2, "big") + (1200).to_bytes(2, "big")
           + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00")
    return sof + marker * 17000 + b"\xff\xd9"


class VisualReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "content").mkdir()
        (self.root / "assets/generated").mkdir(parents=True)
        self.article = {
            "slug": "review-fixture", "title": "呼び鈴が戻った店",
            "summary": "箱に入った呼び鈴が店に戻った。", "category": "事件",
            "paragraphs": ["入口に不在票が残っていた。"],
            "scene_prompt": "A fictional closed shop with a returned doorbell.",
        }
        self.manifest = {"version": 1, "images": {}, "rejected": {}}
        self.reviews = {"version": 1, "reviews": {}}
        self.write_generation(self.article)

    def write_generation(self, article: dict, data: bytes | None = None):
        data = fake_jpeg() if data is None else data
        (self.root / "assets/generated" / (article["slug"] + ".jpg")).write_bytes(data)
        self.manifest["images"][article["slug"]] = {
            "origin": "local-comfyui", "model": "fixture.safetensors",
            "prompt_version": 2, "article_sha256": article_fingerprint(article),
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
        }
        self.write_manifest()

    def write_manifest(self):
        (self.root / "assets/generated/manifest.json").write_text(
            json.dumps(self.manifest), encoding="utf-8")

    def accepted_record(self, article: dict | None = None) -> dict:
        article = self.article if article is None else article
        return {
            "decision": "accepted", "article_sha256": article_fingerprint(article),
            "image_sha256": self.manifest["images"][article["slug"]]["sha256"],
            "reviewed_at": "2026-11-04T01:30:05+00:00",
            "note": "Synthetic test fixture; not an actual visual review.",
        }

    def write_reviews(self):
        (self.root / "content/visual_reviews.json").write_text(
            json.dumps(self.reviews), encoding="utf-8")

    def accept(self, article: dict | None = None):
        article = self.article if article is None else article
        self.reviews["reviews"][article["slug"]] = self.accepted_record(article)
        self.write_reviews()

    def test_intact_jpeg_needs_a_matching_explicit_visual_review(self):
        self.assertTrue(usable_generated_scene(self.article, root=self.root))
        self.assertFalse(reviewed_generated_scene(self.article, root=self.root))
        self.accept()
        self.assertTrue(reviewed_generated_scene(self.article, root=self.root))
        for timestamp in ("2026-11-04T01:30:05Z", "2026-11-04T10:30:05.123+09:00"):
            self.reviews["reviews"][self.article["slug"]]["reviewed_at"] = timestamp
            self.write_reviews()
            self.assertTrue(reviewed_generated_scene(self.article, root=self.root))

    def test_body_change_invalidates_review_even_after_valid_new_generation(self):
        self.accept()
        changed = {**self.article, "paragraphs": ["店主が別の呼び鈴を取り付けた。"]}
        self.assertFalse(reviewed_generated_scene(changed, root=self.root))
        self.write_generation(changed, fake_jpeg(b"\x01"))
        self.assertTrue(usable_generated_scene(changed, root=self.root))
        # Even supplying the new image hash cannot transfer an old story review.
        self.reviews["reviews"][changed["slug"]]["image_sha256"] = self.manifest["images"][changed["slug"]]["sha256"]
        self.write_reviews()
        self.assertFalse(reviewed_generated_scene(changed, root=self.root))

    def test_new_image_bytes_require_a_new_image_specific_review(self):
        self.accept()
        self.write_generation(self.article, fake_jpeg(b"\x02"))
        self.assertTrue(usable_generated_scene(self.article, root=self.root))
        self.assertFalse(reviewed_generated_scene(self.article, root=self.root))

    def test_current_hold_overrides_acceptance_but_old_hold_does_not(self):
        self.accept()
        self.manifest["rejected"][self.article["slug"]] = {
            "article_sha256": article_fingerprint(self.article),
            "reason": "Fixture hold; no real image was reviewed.",
        }
        self.write_manifest()
        self.assertFalse(reviewed_generated_scene(self.article, root=self.root))
        self.manifest["rejected"][self.article["slug"]]["article_sha256"] = "0" * 64
        self.write_manifest()
        self.assertTrue(reviewed_generated_scene(self.article, root=self.root))

    def test_malformed_or_incomplete_review_records_fail_closed(self):
        valid = self.accepted_record()
        cases = (
            {**valid, "decision": "rejected"}, {**valid, "decision": True},
            {**valid, "article_sha256": "0" * 64}, {**valid, "image_sha256": "0" * 64},
            {**valid, "note": " \n\t"}, {**valid, "note": 7},
            {**valid, "reviewed_at": "2026-11-04T01:30:05"},
            {**valid, "reviewed_at": "2026-02-30T01:30:05+00:00"},
            {**valid, "reviewed_at": "not-a-date"}, {**valid, "reviewed_at": None},
            {key: value for key, value in valid.items() if key != "note"}, [], None,
        )
        for record in cases:
            with self.subTest(record=record):
                self.reviews["reviews"][self.article["slug"]] = record
                self.write_reviews()
                self.assertFalse(reviewed_generated_scene(self.article, root=self.root))
        path = self.root / "content/visual_reviews.json"
        for payload in ("{bad-json", "[]", '{"version":2,"reviews":{}}',
                        '{"version":true,"reviews":{}}', '{"version":1,"reviews":[]}'):
            path.write_text(payload, encoding="utf-8")
            self.assertFalse(reviewed_generated_scene(self.article, root=self.root))

    def test_review_cannot_override_missing_corrupt_or_wrong_sized_jpeg(self):
        self.accept()
        path = self.root / "assets/generated" / (self.article["slug"] + ".jpg")
        path.unlink()
        self.assertFalse(reviewed_generated_scene(self.article, root=self.root))
        path.write_bytes(fake_jpeg(b"\x03"))
        self.assertFalse(reviewed_generated_scene(self.article, root=self.root))
        bad_dimensions = fake_jpeg().replace((675).to_bytes(2, "big"), (674).to_bytes(2, "big"), 1)
        self.write_generation(self.article, bad_dimensions)
        self.accept()
        self.assertFalse(usable_generated_scene(self.article, root=self.root))
        self.assertFalse(reviewed_generated_scene(self.article, root=self.root))

    def test_cli_reports_four_states_without_modifying_any_records(self):
        self.accept()
        pending = {**self.article, "slug": "pending-fixture"}
        rejected = {**self.article, "slug": "rejected-fixture"}
        missing = {**self.article, "slug": "missing-fixture"}
        self.write_generation(pending)
        self.write_generation(rejected)
        self.accept(rejected)
        self.manifest["rejected"][rejected["slug"]] = {
            "article_sha256": article_fingerprint(rejected), "reason": "Fixture hold.",
        }
        self.write_manifest()
        (self.root / "content/articles.json").write_text(
            json.dumps([self.article, pending, rejected, missing]), encoding="utf-8")
        before = {path.relative_to(self.root): path.read_bytes()
                  for path in self.root.rglob("*") if path.is_file()}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--root", str(self.root)])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["counts"], {"accepted": 1, "pending": 1, "rejected": 1, "missing": 1})
        by_slug = {row["slug"]: row["status"] for row in result["images"]}
        self.assertEqual(by_slug[self.article["slug"]], "accepted")
        self.assertEqual(by_slug[pending["slug"]], "pending")
        self.assertEqual(by_slug[rejected["slug"]], "rejected")
        self.assertEqual(by_slug[missing["slug"]], "missing")
        self.assertIn("自動判定しません", result["basis"])
        after = {path.relative_to(self.root): path.read_bytes()
                 for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
