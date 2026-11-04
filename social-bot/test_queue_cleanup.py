import copy
import importlib.util
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("queue_cleanup", HERE / "queue_cleanup.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class QueueCleanupTests(unittest.TestCase):
    def setUp(self):
        self.request = json.loads((HERE / "queue-cleanup.json").read_text())
        self.request["results"] = {}
        self.inventory = json.loads((HERE / "experiments/posts.json").read_text())
        self.ledger = json.loads((HERE / "experiments/state.json").read_text())
        self.now = datetime(2026, 11, 4, 3, tzinfo=timezone.utc)
        self.events, self.saved = [], []
        self.remote, self.read_count = {}, {}
        manuscripts = {p["id"]: p for p in self.inventory["experiments"]}
        for target in self.request["targets"]:
            post = manuscripts[target["post_id"]]
            text = post["text"]
            if post.get("hashtag"):
                text = text.rstrip() + "\n\n" + post["hashtag"]
            self.remote[target["buffer_id"]] = {
                "id": target["buffer_id"], "channelId": m.CHANNEL, "text": text,
                "status": "scheduled", "dueAt": target["expected_due_at"], "sentAt": None,
                "allowedActions": ["movePostToDraft"],
                "assets": [{"id": "test-image-" + target["post_id"], "mimeType": "image/png"}]
                          if post.get("visual") else [],
            }

    def single(self):
        self.request["targets"] = self.request["targets"][:1]
        return self.request["targets"][0]

    def fetch(self, bid):
        self.events.append(("read", bid))
        self.read_count[bid] = self.read_count.get(bid, 0) + 1
        return copy.deepcopy(self.remote.get(bid))

    def move(self, bid):
        self.events.append(("edit", bid))
        self.remote[bid]["status"] = "draft"
        self.remote[bid]["dueAt"] = None
        return {"id": bid, "status": "draft"}

    def persist(self, request):
        self.events.append(("persist", None))
        self.saved.append(copy.deepcopy(request))

    def perform(self, fetch=None, move=None, persist=None):
        m.process(self.request, self.inventory, self.ledger,
                  fetch or self.fetch, move or self.move, persist or self.persist, lambda: self.now)

    def result(self):
        return self.request["results"][self.request["targets"][0]["post_id"]]

    def edits(self):
        return [event for event in self.events if event[0] == "edit"]

    def assert_skipped(self, reason):
        self.perform()
        self.assertEqual(self.result()["state"], "skipped")
        self.assertEqual(self.result()["reason"], reason)
        self.assertFalse(self.edits())

    def test_exact_six_targets_match_original_submission_evidence(self):
        targets = m.validate(self.request, self.inventory, self.ledger)
        self.assertEqual({t["post_id"] for t in targets}, {
            "xexp-invisible-nails", "xexp-witch-autopilot", "xexp-nejimachi-one-screw",
            "xexp-shadow-home", "xexp-book-review", "xexp-nejimachi-nothing-wrong",
        })

    def test_all_named_targets_drafted_once_without_altering_originals(self):
        inventory, ledger = copy.deepcopy(self.inventory), copy.deepcopy(self.ledger)
        self.perform()
        self.assertEqual(len(self.edits()), 6)
        self.assertEqual(self.inventory, inventory)
        self.assertEqual(self.ledger, ledger)
        self.assertTrue(all(row["state"] == "verified_draft" for row in self.request["results"].values()))
        self.assertTrue(all(row["mutation_receipt_confirmed"] for row in self.request["results"].values()))

    def test_durable_intent_and_fresh_read_precede_mutation(self):
        self.single()
        self.perform()
        self.assertEqual([event[0] for event in self.events], ["read", "persist", "read", "edit", "read", "persist"])
        self.assertEqual(next(iter(self.saved[0]["results"].values()))["state"], "intent")

    def test_completed_or_unknown_result_never_repeated(self):
        self.single()
        self.perform()
        self.events.clear()
        self.perform()
        self.assertEqual(self.events, [])

    def test_disabled_request_makes_no_provider_calls(self):
        self.request["enabled"] = False
        self.perform()
        self.assertEqual(self.events, [])

    def test_expired_request_does_not_even_read_buffer(self):
        self.single()
        self.now = datetime(2026, 11, 5, 0, tzinfo=timezone.utc)
        self.assert_skipped("cleanup_request_expired")
        self.assertEqual([event[0] for event in self.events], ["persist"])

    def test_published_post_is_never_modified(self):
        target = self.single()
        self.remote[target["buffer_id"]].update(status="sent", sentAt="2026-11-04T02:00:00Z")
        self.assert_skipped("already_published")

    def test_any_sent_timestamp_prevents_edit_even_if_status_is_stale(self):
        target = self.single()
        self.remote[target["buffer_id"]]["sentAt"] = "2026-11-04T02:00:00Z"
        self.assert_skipped("already_published")

    def test_already_draft_is_not_touched(self):
        target = self.single()
        self.remote[target["buffer_id"]]["status"] = "draft"
        self.assert_skipped("already_draft")

    def test_other_channel_is_not_touched(self):
        target = self.single()
        self.remote[target["buffer_id"]]["channelId"] = "someone-else"
        self.assert_skipped("identity_mismatch")

    def test_missing_or_changed_post_is_not_touched(self):
        target = self.single()
        del self.remote[target["buffer_id"]]
        self.assert_skipped("provider_post_missing")

    def test_changed_text_is_not_touched(self):
        target = self.single()
        self.remote[target["buffer_id"]]["text"] += " changed"
        self.assert_skipped("text_changed")

    def test_schedule_change_needs_new_review(self):
        target = self.single()
        self.remote[target["buffer_id"]]["dueAt"] = "2026-11-04T09:31:00Z"
        self.assert_skipped("schedule_changed")

    def test_missing_schedule_does_not_use_stale_saved_time(self):
        target = self.single()
        self.remote[target["buffer_id"]]["dueAt"] = None
        self.assert_skipped("due_at_unconfirmed")

    def test_fifteen_minute_publication_buffer(self):
        self.single()
        self.now = datetime(2026, 11, 4, 9, 15, tzinfo=timezone.utc)
        self.assert_skipped("past_or_too_close_to_publication")

    def test_provider_must_allow_moving_to_draft(self):
        target = self.single()
        self.remote[target["buffer_id"]]["allowedActions"] = ["viewPost"]
        self.assert_skipped("draft_action_not_allowed")

    def test_fresh_status_change_after_intent_prevents_mutation(self):
        target = self.single()
        def read(bid):
            if self.read_count.get(bid) == 1:
                self.remote[bid]["status"] = "sent"
            return self.fetch(bid)
        self.perform(fetch=read)
        self.assertEqual(self.result()["reason"], "already_published")
        self.assertFalse(self.edits())

    def test_asset_change_after_intent_prevents_mutation(self):
        self.single()
        def read(bid):
            if self.read_count.get(bid) == 1:
                self.remote[bid]["assets"] = []
            return self.fetch(bid)
        self.perform(fetch=read)
        self.assertEqual(self.result()["reason"], "assets_changed")
        self.assertFalse(self.edits())

    def test_failed_intent_persistence_prevents_mutation(self):
        self.single()
        def fail(_):
            raise RuntimeError("Git push unavailable")
        with self.assertRaises(RuntimeError):
            self.perform(persist=fail)
        self.assertFalse(self.edits())

    def test_ambiguous_provider_edit_is_held_and_never_repeated(self):
        self.single()
        def timeout(bid):
            self.events.append(("edit", bid))
            raise TimeoutError("No response")
        self.perform(move=timeout)
        self.assertEqual(self.result()["state"], "held_unknown")
        self.assertEqual(len(self.edits()), 1)
        self.events.clear()
        self.perform()
        self.assertEqual(self.events, [])

    def test_timeout_can_be_verified_by_reading_draft_without_repeating_edit(self):
        self.single()
        def timeout_after_success(bid):
            self.move(bid)
            raise TimeoutError("Lost response")
        self.perform(move=timeout_after_success)
        self.assertEqual(self.result()["state"], "verified_draft")
        self.assertFalse(self.result()["mutation_receipt_confirmed"])
        self.assertEqual(len(self.edits()), 1)

    def test_success_response_without_verification_is_not_claimed_as_done(self):
        self.single()
        def read(bid):
            if self.read_count.get(bid) == 2:
                raise TimeoutError("Cannot verify")
            return self.fetch(bid)
        self.perform(fetch=read)
        self.assertEqual(self.result()["state"], "held_unknown")
        self.assertTrue(self.result()["mutation_receipt_confirmed"])

    def test_asset_loss_is_not_claimed_as_verified(self):
        self.single()
        def damaged(bid):
            receipt = self.move(bid)
            self.remote[bid]["assets"] = []
            return receipt
        self.perform(move=damaged)
        self.assertEqual(self.result()["state"], "held_unknown")

    def test_modified_target_cannot_reuse_recorded_intent(self):
        self.single()
        self.perform()
        self.request["targets"][0]["expected_due_at"] = "2026-11-05T01:00:00Z"
        with self.assertRaisesRegex(ValueError, "attempted cleanup target"):
            self.perform()

    def test_cannot_substitute_another_receipt_or_account(self):
        self.single()
        self.request["channel_id"] = "other"
        with self.assertRaises(ValueError):
            self.perform()
        self.assertEqual(self.events, [])

    def test_tampered_final_text_fingerprint_rejected_before_network(self):
        self.single()
        self.request["targets"][0]["text_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.perform()
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
