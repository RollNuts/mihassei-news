"""Immutability rules for original mystery and market predictions."""
import copy
import json
import unittest
from datetime import date
from pathlib import Path

from scripts.guard_serial_history import guard_serial_history, guard_photo_edition_history

ROOT = Path(__file__).resolve().parents[1]


class SerialHistoryGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = json.loads((ROOT / "content/serials.json").read_text(encoding="utf-8"))

    def test_no_change_or_appending_new_chapter_is_safe(self):
        previous = copy.deepcopy(self.before)
        current = copy.deepcopy(previous)
        published = sum(len(s["episodes"]) for s in previous)
        self.assertEqual(guard_serial_history(previous, current), published)
        extra = copy.deepcopy(current[0]["episodes"][-1])
        next_number = len(current[0]["episodes"]) + 1
        extra["number"] = next_number
        extra["slug"] = f"victim-not-yet-dead-{next_number:02d}"
        extra["title"] = f"新しい第{next_number}話"
        current[0]["episodes"].append(extra)
        self.assertEqual(guard_serial_history(previous, current), published)

    def test_fiction_retcon_and_removal_are_blocked(self):
        changed = copy.deepcopy(self.before)
        changed[0]["episodes"][0]["sections"][0]["paragraphs"][0] = "別の内容"
        with self.assertRaisesRegex(ValueError, "mystery rewritten"):
            guard_serial_history(self.before, changed)
        missing = copy.deepcopy(self.before)
        missing.pop(0)
        with self.assertRaisesRegex(ValueError, "removed"):
            guard_serial_history(self.before, missing)

    def test_market_forecast_thesis_method_and_old_article_frozen(self):
        altered = copy.deepcopy(self.before)
        altered[1]["episodes"][0]["forecast"]["statement"] = "都合の良い予測に変更"
        with self.assertRaisesRegex(ValueError, "original forecast"):
            guard_serial_history(self.before, altered)
        changed = copy.deepcopy(self.before)
        changed[1]["episodes"][0]["summary"] = "予測後に改稿"
        with self.assertRaisesRegex(ValueError, "market article changed"):
            guard_serial_history(self.before, changed)

    def test_validly_evidenced_review_only_after_due_date(self):
        verified = copy.deepcopy(self.before)
        forecast = verified[1]["episodes"][0]["forecast"]
        forecast["status"] = "検証済み"
        forecast["result"] = {
            "outcome": "外れ",
            "verified_on": "2027-02-05",
            "explanation": "当初指定した三社の終値を指数と比較し、事前に固定した条件を満たさなかったことを確認した。",
            "evidence": [{"title": "実績に関する証拠", "url": "https://example.org/market-source"}],
        }
        with self.assertRaisesRegex(ValueError, "verification date"):
            guard_serial_history(self.before, verified, today=date(2027, 2, 4))
        self.assertEqual(guard_serial_history(self.before, verified, today=date(2027, 2, 5)), sum(len(s["episodes"]) for s in self.before))
        with self.assertRaisesRegex(ValueError, "immutable"):
            modified = copy.deepcopy(verified)
            modified[1]["episodes"][0]["forecast"]["result"]["outcome"] = "的中"
            guard_serial_history(verified, modified, today=date(2027, 2, 6))

    def test_full_history_baseline_is_used_by_ci(self):
        workflow = (ROOT / ".github/workflows/check.yml").read_text(encoding="utf-8")
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("github.event.pull_request.base.sha", workflow)
        self.assertIn("github.event.before", workflow)
        self.assertIn('scripts/guard_serial_history.py "$SERIAL_BASE_SHA"', workflow)
        self.assertNotIn("scripts/guard_serial_history.py 'HEAD^1'", workflow)

    def test_no_result_without_citable_evidence(self):
        changed = copy.deepcopy(self.before)
        fc = changed[1]["episodes"][0]["forecast"]
        fc["status"] = "検証済み"
        fc["result"] = {
            "outcome": "的中",
            "verified_on": "2027-02-05",
            "explanation": "十分な長さの文章はあるが、根拠資料がまだ確認できていないままの主張。",
            "evidence": [],
        }
        with self.assertRaisesRegex(ValueError, "evidence"):
            guard_serial_history(self.before, changed, today=date(2027, 2, 5))


class PhotoEditionHistoryGuardTests(unittest.TestCase):
    def setUp(self):
        self.before = {"schema_version": 1, "editions": [{
            "number": 1, "slug": "issue-001", "state": "published", "legacy": False,
            "published_at": "2026-11-15T11:28:23+09:00", "title": "七人の写真", "summary": "七人のコラム。",
            "features": ["feature-" + str(i) for i in range(7)],
            "ranking": {"window": {"start_date_jst": "2026-11-08", "end_date_jst": "2026-11-14"},
                        "seed": "0123456789abcdef0123456789abcdef", "order": list(range(7)),
                        "models": [{"model_id": "saya", "page_views": 0}],
                        "source": {"daily": [{"date_jst": "2026-11-14", "page_views": 0}]}}
        }]}

    def test_initial_file_append_and_unpublished_draft_edits_are_allowed(self):
        self.assertEqual(guard_photo_edition_history(None, self.before), 0)
        current = copy.deepcopy(self.before)
        current["editions"].append({"number": 2, "slug": "issue-002", "state": "draft"})
        self.assertEqual(guard_photo_edition_history(self.before, current), 1)
        previous = copy.deepcopy(current)
        current["editions"][1]["features"] = ["a-new-draft-choice"]
        self.assertEqual(guard_photo_edition_history(previous, current), 1)

    def test_title_summary_typos_and_json_object_key_order_do_not_change_contents(self):
        current = copy.deepcopy(self.before)
        edition = current["editions"][0]
        edition.update(title="七人の写真。", summary="七人の未来コラム。")
        edition["ranking"] = dict(reversed(tuple(edition["ranking"].items())))
        self.assertEqual(guard_photo_edition_history(self.before, current), 1)

    def test_member_order_window_counts_seed_and_evidence_are_immutable_after_publication(self):
        changes = (
            lambda edition: edition["features"].reverse(),
            lambda edition: edition["features"].pop(),
            lambda edition: edition["ranking"]["window"].update(end_date_jst="2026-11-15"),
            lambda edition: edition["ranking"].update(seed="f" * 32),
            lambda edition: edition["ranking"]["order"].reverse(),
            lambda edition: edition["ranking"]["models"][0].update(page_views=1),
            lambda edition: edition["ranking"]["models"][0].update(page_views=False),
            lambda edition: edition["ranking"]["source"]["daily"][0].update(page_views=1),
        )
        for change in changes:
            current = copy.deepcopy(self.before)
            change(current["editions"][0])
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "contents or ranking evidence changed"):
                guard_photo_edition_history(self.before, current)

    def test_deletion_renaming_draft_reset_and_publication_identity_changes_are_blocked(self):
        for current in (None, {"schema_version": 1, "editions": []}):
            with self.assertRaisesRegex(ValueError, "removed or renamed"):
                guard_photo_edition_history(self.before, current)
        for field, value in (("slug", "issue-002"), ("state", "draft"), ("number", 2),
                             ("legacy", True), ("published_at", "2026-11-16T11:28:23+09:00")):
            current = copy.deepcopy(self.before)
            current["editions"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                guard_photo_edition_history(self.before, current)


if __name__ == "__main__":
    unittest.main()
