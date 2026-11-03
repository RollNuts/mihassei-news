"""Immutability rules for original mystery and market predictions."""
import copy
import json
import unittest
from datetime import date
from pathlib import Path

from scripts.guard_serial_history import guard_serial_history

ROOT = Path(__file__).resolve().parents[1]


class SerialHistoryGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = json.loads((ROOT / "content/serials.json").read_text(encoding="utf-8"))

    def test_no_change_or_appending_new_chapter_is_safe(self):
        previous = copy.deepcopy(self.before)
        current = copy.deepcopy(previous)
        self.assertEqual(guard_serial_history(previous, current), 2)
        extra = copy.deepcopy(current[0]["episodes"][-1])
        extra["number"] = 2
        extra["slug"] = "victim-not-yet-dead-02"
        extra["title"] = "新しい第2話"
        current[0]["episodes"].append(extra)
        self.assertEqual(guard_serial_history(previous, current), 2)

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
        self.assertEqual(guard_serial_history(self.before, verified, today=date(2027, 2, 5)), 2)
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


if __name__ == "__main__":
    unittest.main()
