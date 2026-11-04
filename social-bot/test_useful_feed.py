import copy
import importlib.util
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("useful_feed", HERE / "useful_feed.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class UsefulFeedTests(unittest.TestCase):
    def setUp(self):
        self.feed = json.loads((HERE / "useful-posts.json").read_text())
        self.feed["posts"] = self.feed["posts"][:1]
        p = self.feed["posts"][0]
        p.update(publish_at="2026-11-04T10:00:00Z", checked_at="2026-11-04T00:00:00Z", valid_until="2026-11-04T14:59:00Z")
        self.state = {"schema_version": 1, "attempts": {}}
        self.now = datetime(2026, 11, 4, 1, tzinfo=timezone.utc)

    def pick(self, live=(), prior=()):
        return m.select(self.feed, self.state, self.now, list(live), prior)

    def test_valid_is_eligible(self): self.assertIsNotNone(self.pick())
    def test_disabled_is_noop(self):
        self.feed["enabled"] = False
        self.assertIsNone(self.pick())
    def test_no_catchup(self):
        self.now = datetime(2026, 11, 5, 1, tzinfo=timezone.utc)
        self.assertIsNone(self.pick())
    def test_no_late_submit(self):
        self.now = datetime(2026, 11, 4, 9, 50, tzinfo=timezone.utc)
        self.assertIsNone(self.pick())
    def test_queue_cap(self): self.assertIsNone(self.pick([{"status": "scheduled"}] * 10))
    def test_exact_copy(self): self.assertIsNone(self.pick([{"text": self.feed["posts"][0]["text"], "status": "sent"}]))
    def test_legacy_copy(self): self.assertIsNone(self.pick(prior=[self.feed["posts"][0]["text"]]))
    def test_pending_intent_never_retried(self):
        p = self.feed["posts"][0]
        self.state["attempts"][p["id"]] = {"day_jst": "2026-11-04", "text_sha256": m.fingerprint(p["text"])}
        self.assertIsNone(self.pick())
    def test_second_id_same_day_rejected(self):
        p = copy.deepcopy(self.feed["posts"][0]); p["id"] += "-other"; p["text"] = "これは完全に別の長さが十分なテスト原稿です。"
        self.feed["posts"].append(p)
        with self.assertRaises(ValueError): self.pick()
    def test_trend_rejected(self):
        self.feed["posts"][0]["trend_targeted"] = True
        with self.assertRaises(ValueError): self.pick()
    def test_market_needs_evidence(self):
        self.feed["posts"][0]["kind"] = "market_close"
        with self.assertRaises(ValueError): self.pick()
    def test_manual_never_sent(self):
        self.feed["posts"][0]["delivery"] = "manual_only"
        self.assertIsNone(self.pick())
    def test_crowding(self):
        self.assertIsNone(self.pick([{"text": "other", "status": "scheduled", "dueAt": "2026-11-04T10:15:00Z"}]))
    def test_no_amending_attempt(self):
        p = self.feed["posts"][0]
        self.state["attempts"][p["id"]] = {"text_sha256": "wrong"}
        with self.assertRaises(ValueError): self.pick()
    def test_oversize_rejected(self):
        self.feed["posts"][0]["text"] = "あ" * 139
        with self.assertRaises(ValueError): self.pick()
    def test_wrong_account(self):
        self.feed["channel_id"] = "other"
        with self.assertRaises(ValueError): self.pick()
    def test_near_duplicate(self):
        text = self.feed["posts"][0]["text"]
        self.assertTrue(m.close_copy(text, text + "。"))
    def test_fake_conversation_unmarked(self):
        self.feed["posts"][0]["text"] = "昨日友人に言われた話です。これは事実とは確認できません。"
        with self.assertRaises(ValueError): self.pick()

    def test_last_free_slot_available_for_useful_original(self):
        self.assertIsNotNone(self.pick([{"status": "scheduled"}] * 9))

    def test_past_sent_posts_do_not_consume_slots(self):
        self.assertIsNotNone(self.pick([{"status": "sent"}] * 20))

    def test_full_queue_has_exact_reason(self):
        reasons = []
        m.select(self.feed, self.state, self.now, [{"status": "scheduled"}] * 10, reasons=reasons)
        self.assertEqual(reasons, [{"reason": "shared_free_queue_full"}])

    def test_crowding_has_exact_reason_without_exposing_other_text(self):
        reasons = []
        m.select(self.feed, self.state, self.now,
                 [{"text": "private other post", "status": "scheduled", "dueAt": "2026-11-04T10:15:00Z"}], reasons=reasons)
        self.assertEqual(reasons[0]["reason"], "within_30_minutes_of_existing_reservation")
        self.assertNotIn("private other post", json.dumps(reasons))

    def test_cutoff_has_exact_reason(self):
        reasons = []
        now = datetime(2026, 11, 4, 9, 50, tzinfo=timezone.utc)
        m.select(self.feed, self.state, now, [], reasons=reasons)
        self.assertEqual(reasons[0]["reason"], "past_cutoff_or_expired")

    def test_selection_does_not_mutate_posts_or_ledger(self):
        feed, state = copy.deepcopy(self.feed), copy.deepcopy(self.state)
        self.pick([{"status": "scheduled"}] * 9)
        self.assertEqual(self.feed, feed)
        self.assertEqual(self.state, state)

if __name__ == "__main__": unittest.main()
