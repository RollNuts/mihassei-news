"""Explicit native-photo reuse fixtures; no real image generation or approval."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import guard_generated_media_history as history
from scripts.generated_photos import (
    article_fingerprint, generation_record_fingerprint, generated_url,
    usable_generated_scene,
)
from scripts.visual_review import reviewed_generated_scene


# Header/hash fixture, NOT a real photograph or provider generation receipt.
JPEG = (b"\xff\xd8\xff\xc0\x00\x11\x08"
        + (675).to_bytes(2, "big") + (1200).to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        + b"\x00" * 17000 + b"\xff\xd9")


class NativePhotoTextReuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "content").mkdir()
        (self.root / "assets/generated").mkdir(parents=True)
        self.slug = "native-reuse-fixture"
        self.source_article = {
            "slug": self.slug, "title": "港の倉庫が動いた",
            "summary": "台船の倉庫が隣の岸壁へ動いた。",
            "category": "社会", "paragraphs": ["作業員が倉庫の移動を見送った。"],
            "scene_prompt": "A tugboat towing a roofed barge beside a dock worker.",
            "published_at": "2026-11-09T10:00:00+00:00",
            "fiction_at": "2031-11-09T10:00:00+00:00",
            "visual_strategy": "cpu-photo",
        }
        self.image_sha = hashlib.sha256(JPEG).hexdigest()
        self.generation = {
            "origin": "chatgpt-imagegen", "model": "ChatGPT ImageGen",
            "generation_id": "4c0beb9e-915d-47bc-8905-da3cc704457a",
            "prompt_version": 3, "article_sha256": article_fingerprint(self.source_article),
            "sha256": self.image_sha, "bytes": len(JPEG),
            "previous_generation": {"origin": "github-cpu-sd15",
                                    "seed": 9085058054470321079},
        }
        self.original_review = {
            "decision": "accepted",
            "article_sha256": article_fingerprint(self.source_article),
            "image_sha256": self.image_sha,
            "reviewed_at": "2026-11-09T12:00:00+00:00",
            "note": "Synthetic original approval fixture; no image was visually approved.",
        }
        self.baseline = {"version": 1, "images": {self.slug: copy.deepcopy(self.generation)},
                         "rejected": {}}
        self.base_articles = [copy.deepcopy(self.source_article)]
        self.base_reviews = {"version": 1, "reviews": {self.slug: copy.deepcopy(self.original_review)}}
        self.baseline_images = {self.slug: self.image_sha}
        self.reset_proposal()

    def reset_proposal(self):
        self.manifest = copy.deepcopy(self.baseline)
        self.article = copy.deepcopy(self.source_article)
        self.article.update({
            "title": "水に浮く倉庫、荷物を載せたまま隣の岸壁へ",
            "paragraphs": ["水に浮く平らな台船に屋根を載せた倉庫を、小型の船が引いて移した。"],
            "modified_at": "2026-11-10T10:00:00+00:00",
        })
        self.review = self.make_review()
        self.jpeg = JPEG
        self.flush()

    def make_review(self, reviewed_at="2026-11-10T10:01:00+00:00"):
        record = self.manifest["images"][self.slug]
        return {
            "decision": "accepted", "article_sha256": article_fingerprint(self.article),
            "image_sha256": record["sha256"], "reviewed_at": reviewed_at,
            "note": "Synthetic new full-text/image review fixture; no actual visual approval.",
            "reuse": {
                "kind": "same_scene_text_revision",
                "generation_record_sha256": generation_record_fingerprint(record),
                "scene_prompt_sha256": hashlib.sha256(self.article["scene_prompt"].encode()).hexdigest(),
                "original_review": copy.deepcopy(self.original_review),
                "reason": "Synthetic clarity-only revision; the tug and barge scene stays the same.",
            },
        }

    def write_json(self, path, value):
        (self.root / path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def flush(self):
        self.write_json("content/articles.json", [self.article])
        self.write_json("assets/generated/manifest.json", self.manifest)
        self.write_json("content/visual_reviews.json", {"version": 1, "reviews": {self.slug: self.review}})
        (self.root / "assets/generated" / (self.slug + ".jpg")).write_bytes(self.jpeg)

    def guard(self):
        return history.verify_text_revision_reviews(
            self.baseline, self.manifest, self.base_articles, self.base_reviews,
            self.baseline_images, root=self.root,
        )

    def test_strict_path_stays_strict_without_explicit_reuse(self):
        self.review.pop("reuse")
        self.flush()
        self.assertFalse(usable_generated_scene(self.article, root=self.root))
        self.assertFalse(reviewed_generated_scene(self.article, root=self.root))
        # The original ordinary accepted path is unchanged.
        self.article = copy.deepcopy(self.source_article)
        self.review = copy.deepcopy(self.original_review)
        self.flush()
        self.assertTrue(reviewed_generated_scene(self.article, root=self.root))

    def test_valid_native_reuse_preserves_every_generation_byte_and_image_url(self):
        original_manifest = json.dumps(self.baseline, ensure_ascii=False, indent=2) + "\n"
        self.assertTrue(usable_generated_scene(self.article, root=self.root))
        self.assertTrue(reviewed_generated_scene(self.article, root=self.root))
        self.assertEqual(self.guard(), [])
        self.assertEqual(history.incompatible_rewrites(self.baseline, self.manifest), [])
        self.assertEqual((self.root / "assets/generated/manifest.json").read_text(), original_manifest)
        self.assertEqual(self.manifest["images"][self.slug]["previous_generation"]["seed"],
                         9085058054470321079)
        self.assertEqual(generated_url(self.article, root=self.root),
                         "/generated/" + self.slug + ".jpg?v=" + self.image_sha[:12])

    def test_missing_or_rejected_baseline_cannot_become_first_approval(self):
        for prior in (None, {**self.original_review, "decision": "rejected"}):
            with self.subTest(prior=prior):
                self.base_reviews["reviews"] = {} if prior is None else {self.slug: prior}
                self.assertTrue(self.guard())

    def test_copied_original_approval_is_verified_against_git_baseline(self):
        for key, value in (("note", "Invented original observation"),
                           ("reviewed_at", "2026-11-09T12:01:00+00:00")):
            with self.subTest(key=key):
                self.reset_proposal()
                self.review["reuse"]["original_review"][key] = value
                self.flush()
                # A structural claim cannot prove history: CI is deliberately independent.
                self.assertTrue(self.guard())

    def test_same_image_generation_record_and_exact_integer_seed_remain_immutable(self):
        for kind in ("generation_id", "seed"):
            with self.subTest(kind=kind):
                self.reset_proposal()
                record = self.manifest["images"][self.slug]
                if kind == "seed":
                    record["previous_generation"]["seed"] = 9085058054470321000
                else:
                    record["generation_id"] = "4c0beb9e-915d-47bc-8905-da3cc704457b"
                self.review["reuse"]["generation_record_sha256"] = generation_record_fingerprint(record)
                self.flush()
                self.assertTrue(history.incompatible_rewrites(self.baseline, self.manifest))
                self.assertTrue(self.guard())

    def test_actual_jpeg_and_baseline_bytes_must_match_the_unchanged_record(self):
        self.jpeg = JPEG + b"changed"
        self.flush()
        self.assertFalse(usable_generated_scene(self.article, root=self.root))
        self.assertTrue(self.guard())
        self.reset_proposal()
        self.baseline_images[self.slug] = "0" * 64
        self.assertTrue(self.guard())

    def test_scene_change_is_rejected_even_if_claimed_scene_hash_is_rewritten(self):
        self.article["scene_prompt"] = "A different scene inside an office."
        self.review = self.make_review()
        self.flush()
        self.assertTrue(self.guard())

    def test_category_and_original_dates_cannot_be_changed_by_text_reuse(self):
        for key, value in (("category", "経済"), ("published_at", "2026-11-10T10:00:00+00:00"),
                           ("fiction_at", "2031-11-10T10:00:00+00:00")):
            with self.subTest(key=key):
                self.reset_proposal()
                self.article[key] = value
                self.review = self.make_review()
                self.flush()
                self.assertTrue(self.guard())

    def test_an_old_accepted_record_does_not_restore_an_illustration_as_photo(self):
        self.base_articles[0]["visual_strategy"] = "illustration"
        self.assertTrue(self.guard())

    def test_stale_naive_or_before_modification_review_times_fail_closed(self):
        for value in ("2026-11-09T12:00:00+00:00", "2026-11-10T09:59:59+00:00",
                      "2026-11-10T10:01:00", None):
            with self.subTest(value=value):
                self.reset_proposal()
                self.review["reviewed_at"] = value
                self.flush()
                self.assertFalse(reviewed_generated_scene(self.article, root=self.root))
                self.assertTrue(self.guard())
        self.reset_proposal()
        self.article["modified_at"] = self.source_article["published_at"]
        self.flush()
        self.assertTrue(self.guard())

    def test_malformed_missing_or_wrong_reuse_fields_fail_closed(self):
        variants = [
            None, [], {},
            {**self.review["reuse"], "kind": "automatic_reapproval"},
            {**self.review["reuse"], "reason": ""},
            {**self.review["reuse"], "generation_record_sha256": "0" * 64},
            {**self.review["reuse"], "scene_prompt_sha256": "0" * 64},
            {**self.review["reuse"], "original_review": None},
            {**self.review["reuse"], "original_review": {**self.original_review, "decision": "rejected"}},
            {**self.review["reuse"], "original_review": {**self.original_review, "reuse": {}}},
        ]
        for reuse in variants:
            with self.subTest(reuse=reuse):
                self.reset_proposal()
                self.review["reuse"] = reuse
                self.flush()
                self.assertFalse(reviewed_generated_scene(self.article, root=self.root))
                self.assertTrue(self.guard())

    def test_review_must_bind_current_article_and_exact_image_with_a_note(self):
        for key, value in (("article_sha256", self.original_review["article_sha256"]),
                           ("image_sha256", "0" * 64), ("note", "")):
            with self.subTest(key=key):
                self.reset_proposal()
                self.review[key] = value
                self.flush()
                self.assertFalse(reviewed_generated_scene(self.article, root=self.root))
                self.assertTrue(self.guard())

    def test_cpu_or_mac_records_are_not_eligible(self):
        for origin in ("local-comfyui", "github-cpu-sd15"):
            with self.subTest(origin=origin):
                self.baseline["images"][self.slug]["origin"] = origin
                self.baseline["images"][self.slug]["model"] = "fixture.safetensors"
                self.reset_proposal()
                self.assertFalse(reviewed_generated_scene(self.article, root=self.root))
                self.assertTrue(self.guard())

    def test_current_original_and_historical_same_image_rejections_are_not_revived(self):
        for article_sha in (article_fingerprint(self.article), self.generation["article_sha256"]):
            with self.subTest(article_sha=article_sha):
                self.reset_proposal()
                self.manifest["rejected"][self.slug] = {"article_sha256": article_sha, "reason": "Fixture hold"}
                self.flush()
                self.assertFalse(reviewed_generated_scene(self.article, root=self.root))
                self.assertTrue(self.guard())
        self.reset_proposal()
        self.manifest["images"][self.slug]["previous_generation"]["previous_rejection"] = {
            "image_sha256": self.image_sha, "reason": "These exact image bytes were rejected."
        }
        self.review = self.make_review()
        self.flush()
        self.assertFalse(reviewed_generated_scene(self.article, root=self.root))
        self.assertTrue(self.guard())

        self.reset_proposal()
        self.manifest["images"][self.slug]["previous_visual_review"] = {
            "decision": "rejected", "image_sha256": self.image_sha,
            "note": "Same image was previously rejected; not a differently named candidate."
        }
        self.review = self.make_review()
        self.flush()
        self.assertFalse(reviewed_generated_scene(self.article, root=self.root))
        self.assertTrue(self.guard())

    def test_distinct_old_rejected_candidate_is_preserved_without_rejecting_valid_replacement(self):
        self.baseline["images"][self.slug]["previous_rejection"] = {
            "image_sha256": "e" * 64, "article_sha256": self.generation["article_sha256"],
            "reason": "Different, older CPU image rejected."
        }
        self.reset_proposal()
        self.assertTrue(reviewed_generated_scene(self.article, root=self.root))
        self.assertEqual(self.guard(), [])

    def test_unknown_hold_or_rejected_candidate_sha_is_not_treated_as_no_hold(self):
        for hold in (None, {}, [], {"article_sha256": "f" * 64},
                     {"article_sha256": "f" * 64, "reason": "Previous rejection",
                      "rejected_candidate_sha256": self.image_sha}):
            with self.subTest(hold=hold):
                self.reset_proposal()
                self.manifest["rejected"][self.slug] = hold
                self.flush()
                self.assertFalse(reviewed_generated_scene(self.article, root=self.root))
                self.assertTrue(self.guard())

    def test_rejection_history_cannot_be_removed_or_type_coerced(self):
        self.baseline["rejected"][self.slug] = {
            "article_sha256": "f" * 64, "image_sha256": "e" * 64,
            "reason": "Older different candidate", "attempt": 0,
        }
        self.reset_proposal()
        self.manifest["rejected"][self.slug]["attempt"] = False
        self.flush()
        self.assertTrue(self.guard())
        self.manifest["rejected"].pop(self.slug)
        self.flush()
        self.assertTrue(self.guard())

    def test_successive_revisions_keep_one_original_review_and_require_a_new_review(self):
        self.assertEqual(self.guard(), [])
        self.base_articles = [copy.deepcopy(self.article)]
        self.base_reviews["reviews"][self.slug] = copy.deepcopy(self.review)
        self.article["summary"] = "荷物を下ろさず、水に浮く倉庫ごと隣へ移した。"
        self.article["modified_at"] = "2026-11-11T10:00:00+00:00"
        self.review = self.make_review("2026-11-11T10:01:00+00:00")
        self.flush()
        self.assertEqual(self.guard(), [])
        self.assertEqual(self.review["reuse"]["original_review"], self.original_review)
        self.assertNotIn("reuse", self.review["reuse"]["original_review"])
        # A later unrelated PR does not have to fabricate another confirmation.
        self.base_articles = [copy.deepcopy(self.article)]
        self.base_reviews["reviews"][self.slug] = copy.deepcopy(self.review)
        self.assertEqual(self.guard(), [])
        self.article["paragraphs"].append("作業員は隣の岸壁へ歩いた。")
        self.article["modified_at"] = "2026-11-11T10:01:00+00:00"
        self.review["article_sha256"] = article_fingerprint(self.article)
        self.flush()
        self.assertTrue(self.guard())  # Same previous review timestamp is not new evidence.

    def test_cli_reads_the_actual_git_baseline_and_rejects_forged_original_approval(self):
        self.write_json("content/articles.json", self.base_articles)
        self.write_json("content/visual_reviews.json", self.base_reviews)
        for command in (["git", "init", "-q"], ["git", "add", "."],
                        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                         "commit", "-qm", "Synthetic accepted baseline"]):
            subprocess.run(command, cwd=self.root, check=True, capture_output=True)
        base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()
        self.flush()
        with patch.object(history, "ROOT", self.root), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(history.main([base]), 0)
            self.review["reuse"]["original_review"]["note"] = "Forged historical observation."
            self.flush()
            self.assertEqual(history.main([base]), 1)
        with patch.object(history, "ROOT", self.root), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(history.main(["0" * 40]), 1)


if __name__ == "__main__":
    unittest.main()
