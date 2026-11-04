import copy
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

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

class UsefulFeedPersistenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "useful-state.json"
        self.state = {
            "schema_version": 1,
            "attempts": {"test-post": {
                "provider_status": "scheduled", "buffer_id": "receipt-1",
                "due_at": "2026-11-04T03:00:00Z", "sent_at": None,
                "attempted_at": "2026-11-04T01:00:00Z",
                "checked_at": "2026-11-04T02:00:00Z",
            }},
            "last_selection": {
                "checked_at_utc": "2026-11-04T02:00:00Z", "scheduled_count": 9,
                "shared_queue_limit": 10, "selected_post_id": None,
                "reasons": [{"reason": "day_already_attempted"}],
            },
        }
        self.path.write_text(json.dumps(self.state) + "\n", encoding="utf-8")
        path_patch = patch.object(m, "STATE_PATH", self.path)
        path_patch.start()
        self.addCleanup(path_patch.stop)
        # No test may run Git or contact a provider. Simulate a real staged diff.
        def git_result(args, **kwargs):
            return Mock(returncode=1 if args[1] == "diff" else 0)
        run_patch = patch.object(m.subprocess, "run", side_effect=git_result)
        self.run = run_patch.start()
        self.addCleanup(run_patch.stop)

    def test_recheck_changes_neither_file_nor_git(self):
        original = self.path.read_bytes()
        self.state["attempts"]["test-post"]["checked_at"] = "2026-11-04T02:54:00Z"
        self.state["last_selection"]["checked_at_utc"] = "2026-11-04T02:54:01Z"
        unchanged_input = copy.deepcopy(self.state)
        m.persist(self.state)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.state, unchanged_input)
        self.run.assert_not_called()

    def assert_persisted(self):
        m.persist(self.state)
        self.assertEqual(m.read(self.path), self.state)
        self.assertIn(["git", "push", "origin", "HEAD:main"],
                      [call.args[0] for call in self.run.call_args_list])

    def test_delivery_transition_keeps_provider_evidence(self):
        self.state["attempts"]["test-post"].update(
            provider_status="sent", sent_at="2026-11-04T03:00:01Z",
            checked_at="2026-11-04T03:05:00Z")
        self.assert_persisted()

    def test_new_intent_without_receipt_is_durable(self):
        self.state["attempts"]["new-post"] = {
            "provider_status": "intent", "attempted_at": "2026-11-05T01:00:00Z",
            "day_jst": "2026-11-05", "text_sha256": "immutable-text",
        }
        self.assert_persisted()
        self.assertNotIn("buffer_id", m.read(self.path)["attempts"]["new-post"])

    def test_changed_queue_or_selection_is_durable(self):
        for key, value in (
            ("scheduled_count", 10),
            ("reasons", [{"reason": "shared_free_queue_full"}]),
            ("selected_post_id", "next-post"),
        ):
            with self.subTest(key=key):
                self.run.reset_mock()
                self.state["last_selection"][key] = value
                self.assert_persisted()

    def test_only_observation_times_are_ignored(self):
        for key, value in (
            ("buffer_id", "receipt-2"),
            ("due_at", "2026-11-04T03:05:00Z"),
            ("attempted_at", "2026-11-04T01:01:00Z"),
            ("payload_sha256", "immutable-payload"),
        ):
            with self.subTest(key=key):
                self.run.reset_mock()
                self.state["attempts"]["test-post"][key] = value
                self.assert_persisted()

    def test_first_ledger_is_persisted(self):
        self.path.unlink()
        self.assert_persisted()


if __name__ == "__main__": unittest.main()
