"""Delivery success must not hide unfinished photographs or independent diagrams."""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.generated_photos import article_fingerprint
from scripts import content_operation_health
from scripts.photo_editions import freeze_daily_schedule
from scripts.photo_issues import MODEL_CHAT_URLS
from scripts.report_photo_health import collect, main, operation_lines, summary


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


class ContentOperationObservationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "content").mkdir()
        self.now = datetime(2026, 11, 22, 1, tzinfo=timezone.utc)  # Sunday 10:00 JST.
        self.ids = list(MODEL_CHAT_URLS)
        self.catalog = {"schema_version": 1, "models": [
            {"id": model, "name": "本人" + str(i)} for i, model in enumerate(self.ids)], "issues": []}
        self.schedule = freeze_daily_schedule(self.catalog, "2026-11-15", seed="a" * 32,
                                               now=datetime(2026, 11, 15, tzinfo=timezone.utc))
        for i, model in enumerate(self.schedule["order"][:2]):
            self.catalog["issues"].append({"slug": "feature-" + str(i), "model_id": model,
                "state": "published", "published_at": f"2026-11-{15+i:02d}T14:00:00+09:00",
                "images": {str(cut): {} for cut in range(10)}})
        self.edition = {"number": 2, "slug": "issue-002", "state": "published", "legacy": False,
                        "release_mode": "daily", "title": "写真", "summary": "写真とコラム。",
                        "published_at": self.catalog["issues"][0]["published_at"],
                        "schedule": self.schedule, "features": [row["slug"] for row in self.catalog["issues"]]}
        self.write("photo_issues.json", self.catalog)
        self.write("photo_editions.json", {"schema_version": 1, "editions": [self.edition]})
        self.write("articles.json", [
            {"slug": "today-utc", "published_at": "2026-11-21T15:30:00Z", "fiction_at": "2031-11-22T00:30:00+09:00"},
            {"slug": "yesterday-jst", "published_at": "2026-11-21T23:30:00+09:00"},
            {"slug": "not-yet", "published_at": "2026-11-22T03:00:00Z"},
        ])
        self.write("market_bulletins.json", [{"slug": "friday-close", "published_at": "2026-11-20T16:24:53+09:00"}])
        self.stories = [{"id": "post-" + str(i), "model_id": model,
                         "published_at": "2026-11-20T12:25:00+09:00" if model == "hanase-mio"
                         else "2026-11-21T21:00:00+09:00"} for i, model in enumerate(self.ids)]
        self.write("photo_stories.json", {"schema_version": 1, "stories": self.stories})

    def write(self, name, data):
        (self.root / "content" / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def observe(self):
        return content_operation_health.collect(self.root, now=self.now)

    def test_jst_boundaries_actual_instants_and_future_records_are_distinct(self):
        report = self.observe()
        news = report["news"]
        self.assertEqual(news["status"], "observed")
        self.assertEqual((news["published_by_observation_count"], news["today_count"],
                          news["previous_day_count"], news["future_dated_count"]), (2, 1, 1, 1))
        self.assertEqual(news["latest"]["slug"], "today-utc")
        self.assertEqual(news["latest"]["date_jst"], "2026-11-22")
        self.assertEqual(report["date_jst"], "2026-11-22")
        self.assertFalse(report["live_delivery_verified"])
        self.assertEqual(report["schedule_evaluation"], "not_performed")

    def test_completed_dates_reveal_missing_yesterday_without_guessing_todays_deadline(self):
        report = self.observe()
        people = {row["model_id"]: row for row in report["cast_stories"]["models"]}
        self.assertEqual(len(people), 7)
        self.assertEqual(people["hanase-mio"]["completed_days_since_latest_post"], 1)
        self.assertEqual(people["hanase-mio"]["previous_day_count"], 0)
        self.assertEqual(people["tsukishiro-rena"]["completed_days_since_latest_post"], 0)
        self.assertEqual(people["tsukishiro-rena"]["today_count"], 0)
        self.assertEqual(report["market"]["status"], "observed")
        self.assertEqual(report["market"]["today_count"], 0)  # Sunday is not called an outage.
        self.assertNotIn("overdue", json.dumps(report))
        rendered = "\n".join(operation_lines(report))
        self.assertIn("no scheduled deadline is inferred", rendered)
        self.assertIn("not additional live verification", rendered)

    def test_cross_midnight_news_does_not_fill_another_date_and_market_keeps_delay(self):
        self.write("articles.json", [
            {"slug": "fri-pm", "publication_slot": "2026-11-20T19:30:00+09:00",
             "published_at": "2026-11-21T00:24:19+09:00"},
            {"slug": "sat-am", "publication_slot": "2026-11-20T22:30:00Z",
             "published_at": "2026-11-21T07:34:51+09:00"},
            {"slug": "without-slot", "published_at": "2026-11-21T18:12:00+09:00"},
            {"slug": "future", "publication_slot": "2026-11-21T19:30:00+09:00",
             "published_at": "2026-11-22T20:00:00+09:00"}])
        self.write("market_bulletins.json", [{"slug": "2026-11-20-am", "day": "2026-11-20",
                   "slot": "am", "published_at": "2026-11-20T11:59:27+09:00"}])
        report = self.observe()
        slots = report["news"]["previous_day_slots"]
        self.assertEqual([r["slug"] for r in slots["original"]], ["sat-am"])
        self.assertEqual([r["slug"] for r in slots["published_from_other_dates"]], ["fri-pm"])
        self.assertEqual(slots["published_without_slot"], ["without-slot"])
        summary = "\n".join(operation_lines(report))
        self.assertIn("2026-11-21 JST: 07:30.", summary)
        self.assertIn("08:30 → 11:59", summary)

    def test_oldest_incomplete_week_and_next_frozen_model_survive_week_rollover(self):
        photo = self.observe()["photo_editions"]
        self.assertEqual(photo["status"], "observed")
        oldest = photo["oldest_incomplete_daily_edition"]
        self.assertEqual((oldest["slug"], oldest["published_feature_count"], oldest["recorded_photo_count"]),
                         ("issue-002", 2, 20))
        self.assertEqual(oldest["next_model_id"], self.schedule["order"][2])
        self.assertEqual(oldest["next_scheduled_date_jst"], "2026-11-17")
        self.assertEqual(photo["stock_status"], "not_observed")
        self.assertNotIn("stock_count", photo)

    def test_missing_or_corrupt_channel_stays_unknown_while_other_channels_remain_visible(self):
        (self.root / "content/market_bulletins.json").unlink()
        (self.root / "content/photo_stories.json").write_text("broken")
        report = self.observe()
        for key in ("market", "cast_stories"):
            self.assertEqual(report[key]["status"], "unavailable")
            self.assertNotIn("today_count", report[key])
        self.assertEqual(report["news"]["status"], "observed")
        self.assertEqual(report["photo_editions"]["status"], "observed")
        self.assertIn("unavailable", "\n".join(operation_lines(report)))

    def test_missing_creator_file_is_not_silently_replaced_by_legacy_posts(self):
        directory = self.root / "content/photo_stories"
        directory.mkdir()
        for model in self.ids[:-1]:
            (directory / (model + ".json")).write_text(json.dumps({"schema_version": 1, "stories": []}))
        self.assertEqual(self.observe()["cast_stories"]["status"], "unavailable")

    def test_future_creator_post_does_not_hide_gap_or_count_as_publication(self):
        self.stories.append({"id": "future-mio", "model_id": "hanase-mio", "published_at": "2026-11-23T12:00:00+09:00"})
        self.write("photo_stories.json", {"schema_version": 1, "stories": self.stories})
        mio = next(row for row in self.observe()["cast_stories"]["models"] if row["model_id"] == "hanase-mio")
        self.assertEqual((mio["future_dated_count"], mio["completed_days_since_latest_post"]), (1, 1))
        self.assertEqual(mio["latest"]["date_jst"], "2026-11-20")

    def test_naive_time_or_duplicate_identity_cannot_be_reported_as_healthy_zero(self):
        for rows in ([{"slug": "bad-time", "published_at": "2026-11-22T10:00:00"}],
                     [{"slug": "duplicate", "published_at": "2026-11-21T10:00:00Z"}] * 2):
            self.write("articles.json", rows)
            with self.subTest(rows=rows):
                self.assertEqual(self.observe()["news"]["status"], "unavailable")
                self.assertNotIn("today_count", self.observe()["news"])


if __name__ == "__main__":
    unittest.main()
