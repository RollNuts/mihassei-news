"""Delivery success must not hide unfinished photographs or independent diagrams."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.generated_photos import article_fingerprint
from scripts.report_photo_health import collect, main, summary


class PhotoHealthReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "content").mkdir()
        articles = [{
            "slug": slug, "title": "写真回収のテスト", "summary": "テスト記事。",
            "paragraphs": ["写真ではないテスト用の本文。"], "category": "社会",
            "scene_prompt": "Synthetic fixture.", "visual_strategy": strategy,
        } for slug, strategy in (("photo-waiting", "cpu-photo"), ("intentional-drawing", "illustration"))]
        (self.root / "content/articles.json").write_text(
            json.dumps(articles, ensure_ascii=False), encoding="utf-8")

    def test_successful_delivery_keeps_missing_photo_visible_without_failing(self):
        artifact, markdown = self.root / "health.json", self.root / "summary.md"
        with patch.dict(os.environ, {
            "GITHUB_STEP_SUMMARY": str(markdown), "PHOTO_REVISION_CHECK": "success",
            "PHOTO_ARTICLE_CHECK": "success", "PHOTO_SHARE_CARD_CHECK": "success",
        }), patch("scripts.report_photo_health.subprocess.check_output", return_value="a" * 40), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--root", str(self.root), "--output", str(artifact)]), 0)
        report = json.loads(artifact.read_text(encoding="utf-8"))
        self.assertEqual(report["checkout_sha"], "a" * 40)
        self.assertEqual(report["live_checks"]["articles"], "success")
        self.assertEqual(report["counts"]["missing"], 2)
        self.assertEqual(report["publication_counts"]["generic_fallback"], 2)
        self.assertEqual(report["photo_followup_counts"], {"pending": 0, "missing": 1, "rejected": 0})
        self.assertEqual([row["slug"] for row in report["photo_followup"]], ["photo-waiting"])
        rendered = markdown.read_text(encoding="utf-8")
        self.assertIn("photo-waiting", rendered)
        self.assertIn("intentional-drawing", rendered)  # Its missing authored SVG still needs attention.
        self.assertIn(report["photo_followup"][0]["next_action"], rendered)

    def test_photo_recovery_does_not_hide_a_diagram_problem_on_same_article(self):
        with patch("scripts.report_photo_health.subprocess.check_output", return_value="b" * 40):
            report = collect(self.root, {"revision": "failure", "articles": "skipped", "share_cards": "skipped"})
        report["attention"].append({
            "slug": "photo-waiting", "reason": "stale_article_diagram", "next_action": "Inspect the changed diagram.",
        })
        rendered = summary(report)
        self.assertIn("revision=failure", rendered)
        self.assertIn("articles=skipped", rendered)
        self.assertIn("stale_article_diagram", rendered)
        self.assertIn("Inspect the changed diagram.", rendered)

    def test_saved_rejection_is_available_in_summary_and_job_log_without_artifact_download(self):
        article = json.loads((self.root / "content/articles.json").read_text())[0]
        reason = "The board must be <empty>.\r\nKeep the pieces outside | the board."
        run = "https://github.com/RollNuts/mihassei-news/actions/runs/12345"
        reviewed_at = "2026-11-16T01:50:19.985066+00:00"
        generated = self.root / "assets/generated"
        generated.mkdir(parents=True)
        (generated / "manifest.json").write_text(json.dumps({
            "version": 1, "images": {}, "rejected": {article["slug"]: {
                "article_sha256": article_fingerprint(article), "reason": reason,
                "rejected_candidate_sha256": "d" * 64, "reviewed_at": reviewed_at,
                "review_run": run, "unrelated": {"private": "MUST_NOT_PRINT"},
            }},
        }))
        artifact, markdown = self.root / "health.json", self.root / "summary.md"
        output = io.StringIO()
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(markdown)}), \
                patch("scripts.report_photo_health.subprocess.check_output", return_value="a" * 40), \
                contextlib.redirect_stdout(output):
            self.assertEqual(main(["--root", str(self.root), "--output", str(artifact)]), 0)
        rendered = markdown.read_text()
        self.assertEqual(output.getvalue(), rendered + "\n")
        self.assertIn("#### Saved rejection: photo-waiting", rendered)
        self.assertIn("The board must be &lt;empty&gt;.  Keep the pieces outside \\| the board.", rendered)
        self.assertIn("- JPEG SHA-256: `" + "d" * 64 + "`", rendered)
        self.assertIn("- Reviewed at: " + reviewed_at, rendered)
        self.assertIn("- Review run: " + run, rendered)
        self.assertNotIn("<empty>", rendered)
        self.assertNotIn("MUST_NOT_PRINT", rendered)
        report = json.loads(artifact.read_text())
        self.assertEqual(report["photo_followup_counts"], {"pending": 0, "missing": 0, "rejected": 1})
        self.assertEqual(report["publication_counts"]["generic_fallback"], 2)

    def test_summary_does_not_print_malformed_or_nonrejected_evidence(self):
        with patch("scripts.report_photo_health.subprocess.check_output", return_value="b" * 40):
            report = collect(self.root, {"articles": "success"})
        item = report["photo_followup"][0]
        item["status"] = "rejected"
        valid = {"source": "content/visual_reviews.json", "reason": "Saved reason."}
        for evidence in (None, [], {**valid, "reason": []}, {**valid, "reason": "  "},
                         {**valid, "source": "unrelated/private.json"}):
            with self.subTest(evidence=evidence):
                item["rejection_evidence"] = evidence
                self.assertNotIn("#### Saved rejection:", summary(report))
        item["rejection_evidence"] = {
            **valid, "image_sha256": {"guess": "MUST_NOT_PRINT"},
            "reviewed_at": "2026-11-16T01:50:19", "review_run": ["MUST_NOT_PRINT"],
            "unrelated": "MUST_NOT_PRINT",
        }
        before = json.dumps(report, sort_keys=True)
        rendered = summary(report)
        self.assertIn("- Reason: Saved reason.", rendered)
        for excluded in ("MUST_NOT_PRINT", "- JPEG SHA-256:", "- Reviewed at:", "- Review run:"):
            self.assertNotIn(excluded, rendered)
        self.assertEqual(json.dumps(report, sort_keys=True), before)
        item["status"] = "missing"
        self.assertNotIn("#### Saved rejection:", summary(report))

    def test_unreadable_source_is_not_reported_as_no_pending_photos(self):
        (self.root / "content/articles.json").write_text("broken JSON", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "do not treat it as zero pending"):
            collect(self.root, {"articles": "success"})


if __name__ == "__main__":
    unittest.main()
