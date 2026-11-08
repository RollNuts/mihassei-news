"""Stored visual approval is separate from real JPEG integrity validation."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.editorial_covers import story_subject
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

    def write_editorial_scene(self, article: dict) -> Path:
        """A synthetic SVG fixture, not an actual visual approval."""
        path = self.root / "assets/editorial-scenes" / (article["slug"] + ".svg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 675" '
            'role="img" aria-label="テスト用の創作イラスト" '
            f'data-visual-topic="{story_subject(article)}" '
            f'data-article-sha256="{article_fingerprint(article)}">'
            '<title>Synthetic fixture, not an actual reviewed image</title>'
            '<rect width="1200" height="675" fill="#ffffff"/></svg>',
            encoding="utf-8",
        )
        return path

    def write_stock_photo(self, article: dict):
        """Supply all stock-photo gate inputs locally, without any request."""
        article["photo"] = {
            "source": "https://unsplash.com/photos/fixture-photo",
            "url": "https://images.unsplash.com/photo-fixture",
            "license": "Unsplash", "credit": "Synthetic fixture",
            "alt": "Synthetic fixture; not an actual photograph.",
        }
        approvals = self.root / "content/approved_photos.json"
        data = json.loads(approvals.read_text()) if approvals.exists() else {
            "version": 1, "approved": {},
        }
        data["approved"][article["slug"]] = {
            "source": article["photo"]["source"],
            "reason": "Synthetic fixture; not an actual visual review.",
        }
        approvals.write_text(json.dumps(data), encoding="utf-8")
        path = self.root / "assets/photos" / (article["slug"] + ".jpg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fake_jpeg())

    def report(self, articles: list[dict]) -> dict:
        (self.root / "content/articles.json").write_text(
            json.dumps(articles, ensure_ascii=False), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["--root", str(self.root)]), 0)
        return json.loads(output.getvalue())

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
        from scripts import build

        self.accept()
        self.write_editorial_scene(self.article)
        self.manifest["rejected"][self.article["slug"]] = {
            "article_sha256": article_fingerprint(self.article),
            "reason": "Fixture hold; no real image was reviewed.",
        }
        self.write_manifest()
        self.assertFalse(reviewed_generated_scene(self.article, root=self.root))
        with patch.object(build, "BASE", self.root):
            self.assertEqual(build.editorial_image(self.article),
                             "/images/review-fixture.svg")
            self.assertEqual(build.photo_credit(self.article), "創作イラスト")
        self.manifest["rejected"][self.article["slug"]]["article_sha256"] = "0" * 64
        self.write_manifest()
        self.assertTrue(reviewed_generated_scene(self.article, root=self.root))
        with patch.object(build, "BASE", self.root):
            self.assertEqual(build.editorial_image(self.article),
                             "/generated/review-fixture.jpg?v=" + self.manifest["images"][self.article["slug"]]["sha256"][:12])

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
        self.assertEqual(result["publication_counts"], {
            "generated_jpeg": 1, "editorial_svg": 0, "stock_photo": 0, "generic_fallback": 3,
        })
        self.assertEqual(result["strategy_counts"], {"cpu-photo": 4, "illustration": 0})
        self.assertIn("本番配信を確認した結果ではありません", result["publication_basis"])
        after = {path.relative_to(self.root): path.read_bytes()
                 for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_report_matches_build_selection_for_all_four_image_kinds(self):
        from scripts import build

        generated = self.article
        editorial = {**self.article, "slug": "editorial-fixture"}
        stock = {**self.article, "slug": "stock-fixture"}
        generic = {**self.article, "slug": "generic-fixture"}
        self.accept(generated)
        for article in (generated, editorial):
            self.write_editorial_scene(article)
        for article in (generated, editorial, stock):
            self.write_stock_photo(article)
        articles = [generated, editorial, stock, generic]
        result = self.report(articles)
        rows = {row["slug"]: row for row in result["images"]}
        expected = {
            generated["slug"]: "generated_jpeg", editorial["slug"]: "editorial_svg",
            stock["slug"]: "stock_photo", generic["slug"]: "generic_fallback",
        }
        with patch.object(build, "BASE", self.root):
            for article in articles:
                row = rows[article["slug"]]
                self.assertEqual(row["publication"]["kind"], expected[article["slug"]])
                self.assertEqual(row["publication"]["url"], build.editorial_image(article))
        self.assertEqual(result["publication_counts"], dict.fromkeys(expected.values(), 1))
        self.assertEqual(rows[editorial["slug"]]["editorial_scene"]["status"], "current")
        # Missing/rejected JPEGs do not mandate generation when an approved
        # illustration or stock photo is already selected.
        self.assertEqual([item["slug"] for item in result["attention"]], [generic["slug"]])

    def test_illustration_strategy_is_reported_and_never_treated_as_cpu_backlog(self):
        illustration = {**self.article, "slug": "illustration-fixture",
                        "visual_strategy": "illustration"}
        result = self.report([illustration])
        row = result["images"][0]
        self.assertEqual(row["visual_strategy"], "illustration")
        self.assertEqual(result["strategy_counts"], {"cpu-photo": 0, "illustration": 1})
        self.assertEqual(row["publication"]["kind"], "generic_fallback")
        self.assertEqual(result["attention"][0]["reason"],
                         "illustration_strategy_without_reviewed_scene")
        self.assertIn("CPU生成へ戻さず", result["attention"][0]["next_action"])

    def test_invalid_visual_strategy_fails_closed(self):
        invalid = {**self.article, "visual_strategy": "remote-api"}
        (self.root / "content/articles.json").write_text(
            json.dumps([invalid], ensure_ascii=False), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--root", str(self.root)])
        self.assertEqual(code, 2)
        self.assertIn("visual_strategy", output.getvalue())

    def test_illustration_strategy_overrides_an_accepted_jpeg_in_report_and_build(self):
        from scripts import build

        self.accept()
        illustration = {**self.article, "visual_strategy": "illustration"}
        self.write_editorial_scene(illustration)
        result = self.report([illustration])
        row = result["images"][0]
        self.assertEqual(row["status"], "accepted")
        self.assertEqual(row["visual_strategy"], "illustration")
        self.assertEqual(row["publication"]["kind"], "editorial_svg")
        self.assertEqual(result["strategy_counts"], {"cpu-photo": 0, "illustration": 1})
        with patch.object(build, "BASE", self.root):
            self.assertFalse(build.uses_generated_scene(illustration))
            self.assertEqual(build.editorial_image(illustration),
                             "/images/" + illustration["slug"] + ".svg")
            self.assertEqual(build.photo_credit(illustration), "創作イラスト")

    def test_article_edit_reports_lost_editorial_scene_without_reapproving_it(self):
        path = self.write_editorial_scene(self.article)
        original_svg = path.read_bytes()
        before = self.report([self.article])
        self.assertEqual(before["images"][0]["publication"]["kind"], "editorial_svg")

        changed = {**self.article, "paragraphs": ["店主が別の呼び鈴を取り付けた。"]}
        result = self.report([changed])
        row = result["images"][0]
        self.assertEqual(row["status"], "missing")
        self.assertEqual(row["publication"], {
            "kind": "generic_fallback", "url": "/images/review-fixture.svg",
        })
        self.assertEqual(row["editorial_scene"]["status"], "stale")
        self.assertEqual(row["editorial_scene"]["reason"], "article_fingerprint_changed")
        self.assertEqual(result["attention"][0]["reason"], "stale_editorial_scene")
        self.assertIn("実物で再確認", result["attention"][0]["next_action"])
        self.assertEqual(path.read_bytes(), original_svg)
        self.assertFalse(reviewed_generated_scene(changed, root=self.root))

    def test_report_flags_invalid_scene_instead_of_treating_it_as_reviewed(self):
        path = self.write_editorial_scene(self.article)
        path.write_text(path.read_text(encoding="utf-8").replace(
            '<rect width="1200" height="675" fill="#ffffff"/>',
            '<image href="https://example.invalid/unreviewed.png"/>',
        ), encoding="utf-8")
        original_svg = path.read_bytes()
        result = self.report([self.article])
        row = result["images"][0]
        self.assertEqual(row["editorial_scene"]["status"], "invalid")
        self.assertEqual(row["publication"]["kind"], "generic_fallback")
        self.assertEqual({item["reason"] for item in result["attention"]}, {
            "invalid_editorial_scene", "pending_jpeg",
        })
        self.assertEqual(path.read_bytes(), original_svg)


if __name__ == "__main__":
    unittest.main()
