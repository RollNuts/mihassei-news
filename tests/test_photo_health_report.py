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

    def test_unreadable_source_is_not_reported_as_no_pending_photos(self):
        (self.root / "content/articles.json").write_text("broken JSON", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "do not treat it as zero pending"):
            collect(self.root, {"articles": "success"})


if __name__ == "__main__":
    unittest.main()
