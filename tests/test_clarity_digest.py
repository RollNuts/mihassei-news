"""Tests for blind first-read clarity digest."""
import copy
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.clarity_digest import (
    blind_rows, load_baseline, main, render_review_digest, render_text,
    review_digest, select_articles,
)
from scripts.generated_photos import article_fingerprint

JST = timezone(timedelta(hours=9))


def article(slug: str, when: datetime, *, title: str = "店主、袋を三つ持ってレジ袋購入") -> dict:
    return {
        "slug": slug,
        "published_at": when.isoformat(),
        "title": title,
        "summary": "袋を持っているのに汚したくなくて使わず、別の袋を買った。",
        "source": {"topic": "hidden source detail"},
        "editorial_signature": {"target": "hidden"},
        "paragraphs": ["hidden body"],
    }


class ClarityDigestTests(unittest.TestCase):
    def test_day_selects_only_that_jst_date(self):
        items = [
            article("a", datetime(2026, 11, 5, 23, 30, tzinfo=JST)),
            article("b", datetime(2026, 11, 6, 7, 30, tzinfo=JST)),
            article("c", datetime(2026, 11, 7, 7, 30, tzinfo=JST)),
        ]
        selected = select_articles(items, day="2026-11-06")
        self.assertEqual([item["slug"] for item in selected], ["b"])

    def test_days_uses_latest_publication_day_as_offline_anchor(self):
        items = [
            article("old", datetime(2026, 11, 1, 7, 30, tzinfo=JST)),
            article("a", datetime(2026, 11, 5, 7, 30, tzinfo=JST)),
            article("b", datetime(2026, 11, 6, 7, 30, tzinfo=JST)),
            article("c", datetime(2026, 11, 7, 7, 30, tzinfo=JST)),
        ]
        selected = select_articles(items, days=3)
        self.assertEqual([item["slug"] for item in selected], ["a", "b", "c"])

    def test_limit_keeps_latest_matches(self):
        items = [
            article(f"a-{i}", datetime(2026, 11, 6, 7 + i, 30, tzinfo=JST))
            for i in range(4)
        ]
        selected = select_articles(items, day="2026-11-06", limit=2)
        self.assertEqual([item["slug"] for item in selected], ["a-2", "a-3"])

    def test_blind_output_excludes_source_metadata_and_body(self):
        item = article("blind", datetime(2026, 11, 6, 19, 30, tzinfo=JST))
        rows = blind_rows([item])
        self.assertEqual(set(rows[0]), {"slug", "published_at", "title", "summary"})
        rendered = render_text(rows)
        self.assertIn("TITLE:", rendered)
        self.assertIn("SUMMARY:", rendered)
        self.assertNotIn("hidden source detail", rendered)
        self.assertNotIn("hidden body", rendered)
        self.assertNotIn("editorial_signature", rendered)

    def test_second_pass_exposes_the_story_but_never_the_authors_rationale(self):
        item = article("blind", datetime(2026, 11, 6, 19, 30, tzinfo=JST))
        item.update(reader_clarity={"oddity": "author's defense"},
                    trend_bridge={"unique_hook": "source-based defense"},
                    x_share_lead="promotional defense")
        item["paragraphs"] = ["A reasonable purchase.", "Nothing was wasted."]
        rows = blind_rows([item], full_text=True)
        rendered = render_text(rows)
        self.assertIn("BODY 1: A reasonable purchase.", rendered)
        self.assertIn("BODY 2: Nothing was wasted.", rendered)
        self.assertEqual(set(rows[0]), {"slug", "published_at", "title", "summary", "paragraphs"})
        for hidden in ("hidden source detail", "author's defense", "source-based defense", "promotional defense"):
            self.assertNotIn(hidden, rendered)
        rows[0]["paragraphs"].append("review note")
        self.assertEqual(len(item["paragraphs"]), 2)


class ClarityChangeDigestTests(unittest.TestCase):
    def test_new_and_old_revised_articles_are_found_without_modified_at(self):
        old = article("old", datetime(2026, 10, 1, 7, 30, tzinfo=JST))
        untouched = article("untouched", datetime(2026, 11, 11, 7, 30, tzinfo=JST))
        revised = copy.deepcopy(old)
        revised["paragraphs"] = ["The revised action is now explicit."]
        new = article("new", datetime(2026, 11, 12, 7, 30, tzinfo=JST))
        digest = review_digest([new, untouched, revised], baseline=[old, untouched])
        self.assertEqual([r["slug"] for r in digest["selected"]], ["old", "new"])
        self.assertEqual(digest["total"], 2)
        self.assertEqual(digest["selected"][0]["article_sha256"], article_fingerprint(revised))

    def test_each_reader_field_triggers_review_but_image_metadata_does_not(self):
        old = article("story", datetime(2026, 11, 12, 7, 30, tzinfo=JST))
        for field, value in (("title", "Different headline"), ("summary", "Different summary"),
                             ("paragraphs", ["Different body"])):
            with self.subTest(field=field):
                revised = copy.deepcopy(old)
                revised[field] = value
                self.assertEqual(review_digest([revised], baseline=[old])["total"], 1)
        revised = copy.deepcopy(old)
        revised.update(
            scene_prompt="A replacement photograph.", category="技術",
            modified_at="2026-11-12T18:00:00+09:00",
            reader_clarity={"who": "author's explanation"},
            source={"topic": "updated source metadata"},
        )
        self.assertNotEqual(article_fingerprint(old), article_fingerprint(revised))
        self.assertEqual(review_digest([revised], baseline=[old])["total"], 0)

    def test_limit_retains_all_deferred_slugs_and_hashes_in_both_formats(self):
        items = [article(f"a-{i}", datetime(2026, 11, 12, 7 + i, 30, tzinfo=JST))
                 for i in range(4)]
        digest = review_digest(list(reversed(items)), baseline=[], base_commit="a" * 40, limit=3)
        self.assertEqual(digest["total"], 4)
        self.assertEqual(digest["selected_count"], 3)
        self.assertEqual(digest["pending_count"], 1)
        self.assertEqual([r["slug"] for r in digest["selected"]], ["a-0", "a-1", "a-2"])
        pending = {"slug": "a-3", "article_sha256": article_fingerprint(items[3])}
        self.assertEqual(json.loads(json.dumps(digest))["pending"], [pending])
        rendered = render_review_digest(digest)
        self.assertIn("total=4 selected=3 pending=1", rendered)
        self.assertIn("BASE COMMIT: " + "a" * 40, rendered)
        self.assertIn("PENDING: a-3 ARTICLE SHA256: " + pending["article_sha256"], rendered)
        self.assertIn("--slug", rendered)

    def test_pending_resumes_after_baseline_advanced_and_is_not_duplicated(self):
        item = article("pending", datetime(2026, 11, 12, 7, 30, tzinfo=JST))
        self.assertEqual(review_digest([item], baseline=[item])["total"], 0)
        digest = review_digest([item], baseline=[item], slugs=["pending", "pending"])
        self.assertEqual([r["slug"] for r in digest["selected"]], ["pending"])
        self.assertEqual(digest["total"], 1)
        self.assertEqual(review_digest([item], slugs=["pending"])["total"], 1)

    def test_explicit_pending_is_selected_before_older_new_changes(self):
        items = [article(f"a-{i}", datetime(2026, 11, 12, 7 + i, 30, tzinfo=JST))
                 for i in range(4)]
        digest = review_digest(items, baseline=[], slugs=["a-3", "a-2"], limit=3)
        self.assertEqual([r["slug"] for r in digest["selected"]], ["a-2", "a-3", "a-0"])
        self.assertEqual([r["slug"] for r in digest["pending"]], ["a-1"])

    def test_missing_pending_slug_and_invalid_limit_are_errors(self):
        with self.assertRaisesRegex(ValueError, "Unknown article slug.*missing"):
            review_digest([], baseline=[], slugs=["missing"])
        with self.assertRaisesRegex(ValueError, "limit must be"):
            review_digest([], baseline=[], limit=0)

    def test_change_digest_still_hides_authors_rationale_and_first_pass_body(self):
        item = article("blind", datetime(2026, 11, 12, 7, 30, tzinfo=JST))
        item["reader_clarity"] = {"who": "author's defense"}
        first = review_digest([item], baseline=[])
        second = review_digest([item], baseline=[], full_text=True)
        self.assertNotIn("paragraphs", first["selected"][0])
        self.assertEqual(second["selected"][0]["paragraphs"], ["hidden body"])
        for digest in (first, second):
            text = json.dumps(digest) + render_review_digest(digest)
            self.assertNotIn("hidden source detail", text)
            self.assertNotIn("author's defense", text)


class ClarityDigestCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "content").mkdir()
        self.item = article("story", datetime(2026, 11, 12, 7, 30, tzinfo=JST))
        self.write_articles([self.item])
        self.git("init", "--quiet")
        self.git("add", "content/articles.json")
        self.git("-c", "user.name=Clarity test", "-c", "user.email=clarity@example.test",
                 "commit", "--quiet", "-m", "Baseline fixture")
        self.commit = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True,
                              capture_output=True, text=True).stdout

    def write_articles(self, items):
        (self.root / "content/articles.json").write_text(
            json.dumps(items, ensure_ascii=False), encoding="utf-8")

    def run_cli(self, args):
        output = io.StringIO()
        with patch("scripts.clarity_digest.ROOT", self.root), redirect_stdout(output):
            main(args)
        return output.getvalue()

    def test_cli_resolves_ref_and_reports_current_text_then_pending_without_changes(self):
        revised = copy.deepcopy(self.item)
        revised["summary"] = "The current public text changed."
        self.write_articles([revised])
        digest = json.loads(self.run_cli(["--since-ref", "HEAD", "--json"]))
        self.assertEqual(digest["base_commit"], self.commit)
        self.assertEqual(digest["selected"][0]["summary"], revised["summary"])
        self.write_articles([self.item])
        digest = json.loads(self.run_cli(["--since-ref", "HEAD", "--slug", "story", "--json"]))
        self.assertEqual(digest["total"], 1)
        self.assertEqual(digest["selected"][0]["slug"], "story")

    def test_cli_date_mode_keeps_the_original_json_array(self):
        rows = json.loads(self.run_cli(["--day", "2026-11-12", "--json"]))
        self.assertIsInstance(rows, list)
        self.assertEqual(set(rows[0]), {"slug", "published_at", "title", "summary"})

    def test_invalid_baseline_or_slug_fails_instead_of_reporting_no_changes(self):
        for args in (["--since-ref", "missing-ref", "--json"],
                     ["--since-ref", "HEAD", "--slug", "missing", "--json"]):
            with self.subTest(args=args):
                output, error = io.StringIO(), io.StringIO()
                with patch("scripts.clarity_digest.ROOT", self.root), redirect_stdout(output), redirect_stderr(error):
                    with self.assertRaises(SystemExit) as caught:
                        main(args)
                self.assertEqual(caught.exception.code, 2)
                self.assertEqual(output.getvalue(), "")
                self.assertIn("error:", error.getvalue())

    def test_baseline_with_unreadable_articles_is_not_an_empty_baseline(self):
        (self.root / "content/articles.json").unlink()
        self.git("add", "-u")
        self.git("-c", "user.name=Clarity test", "-c", "user.email=clarity@example.test",
                 "commit", "--quiet", "-m", "Missing article fixture")
        with self.assertRaisesRegex(ValueError, "review scope is unknown"):
            load_baseline("HEAD", root=self.root)


if __name__ == "__main__":
    unittest.main()
