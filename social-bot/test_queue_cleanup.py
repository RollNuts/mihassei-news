import copy
import importlib.util
import json
import unittest
from unittest.mock import patch
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
        self.request.pop("recovery_request_id", None)
        self.request.pop("recovery_results", None)
        self.request.pop("continuation_request_id", None)
        self.request.pop("continuation_results", None)
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

    def move(self, bid, original_text):
        self.assertEqual(original_text, self.remote[bid]["text"])
        self.events.append(("edit", bid))
        self.remote[bid]["status"] = "draft"
        self.remote[bid]["dueAt"] = None
        return {"id": bid, "status": "draft"}

    def persist(self, request):
        self.events.append(("persist", None))
        self.saved.append(copy.deepcopy(request))

    def perform(self, fetch=None, move=None, persist=None, recovery=False, continuation=False):
        m.process(self.request, self.inventory, self.ledger,
                  fetch or self.fetch, move or self.move, persist or self.persist, lambda: self.now,
                  recovery=recovery, continuation=continuation)

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
        def timeout(bid, original_text):
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
        def timeout_after_success(bid, original_text):
            self.move(bid, original_text)
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
        def damaged(bid, original_text):
            receipt = self.move(bid, original_text)
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

    def failed_original(self):
        self.single()
        def rejected(bid, original_text):
            self.events.append(("edit", bid))
            raise RuntimeError("Buffer editPost rejected: PostInvalidInputError - Text or media is required")
        self.perform(move=rejected)
        initial = copy.deepcopy(self.request["results"])
        self.request["recovery_request_id"] = m.RECOVERY_ID
        self.request["recovery_results"] = {}
        self.events.clear()
        return initial

    def test_provider_rejection_type_and_message_are_preserved(self):
        self.failed_original()
        error = self.result()["provider_error"]
        self.assertEqual(error["provider_type"], "PostInvalidInputError")
        self.assertIn("Text or media is required", error["message"])

    def test_named_recovery_keeps_original_failed_result_and_exact_text(self):
        initial = self.failed_original()
        self.perform(recovery=True)
        self.assertEqual(self.request["results"], initial)
        row = next(iter(self.request["recovery_results"].values()))
        self.assertEqual(row["state"], "verified_draft")
        self.assertEqual(row["recovery_request_id"], m.RECOVERY_ID)
        self.assertEqual(len(self.edits()), 1)
        first_recovery = self.saved[-2]["recovery_results"]
        self.assertEqual(next(iter(first_recovery.values()))["state"], "intent")

    def test_recovery_requires_its_explicit_request_id(self):
        self.failed_original()
        self.request.pop("recovery_request_id")
        with self.assertRaises(ValueError):
            self.perform(recovery=True)
        self.assertEqual(self.events, [])

    def test_initial_unknown_outcome_without_same_scheduled_proof_is_not_retried(self):
        self.failed_original()
        self.result().pop("after")
        self.perform(recovery=True)
        self.assertFalse(self.edits())
        row = next(iter(self.request["recovery_results"].values()))
        self.assertEqual(row["reason"], "initial_input_failure_not_proven")
        self.assertEqual([event[0] for event in self.events], ["persist"])

    def test_changed_initial_after_snapshot_is_not_retried(self):
        self.failed_original()
        self.result()["after"]["text_sha256"] = "f" * 64
        self.perform(recovery=True)
        self.assertFalse(self.edits())

    def test_successful_original_cannot_be_retried_as_recovery(self):
        self.failed_original()
        self.result()["state"] = "verified_draft"
        self.perform(recovery=True)
        self.assertFalse(self.edits())

    def test_recovery_rereads_current_post_and_will_not_touch_published_post(self):
        self.failed_original()
        target = self.request["targets"][0]
        self.remote[target["buffer_id"]]["status"] = "sent"
        self.perform(recovery=True)
        self.assertFalse(self.edits())
        row = next(iter(self.request["recovery_results"].values()))
        self.assertEqual(row["reason"], "already_published")

    def test_failed_recovery_is_recorded_separately_and_never_repeated(self):
        initial = self.failed_original()
        def unavailable(bid, original_text):
            self.events.append(("edit", bid))
            raise RuntimeError("Buffer editPost rejected: UnauthorizedError - Access denied")
        self.perform(move=unavailable, recovery=True)
        self.assertEqual(self.request["results"], initial)
        row = next(iter(self.request["recovery_results"].values()))
        self.assertEqual(row["state"], "held_unknown")
        self.assertEqual(row["provider_error"]["provider_type"], "UnauthorizedError")
        self.events.clear()
        self.perform(recovery=True)
        self.assertEqual(self.events, [])

    def test_successful_recovery_is_never_repeated(self):
        self.failed_original()
        self.perform(recovery=True)
        self.events.clear()
        self.perform(recovery=True)
        self.assertEqual(self.events, [])

    def test_recovery_cannot_expand_to_other_legacy_reservations(self):
        self.single()
        pid = "xexp-selfcheckout-dialogue"
        receipt = self.ledger["queued"][pid]
        self.request["targets"][0].update(
            post_id=pid, buffer_id=receipt["buffer_id"],
            text_sha256=receipt["published_text_sha256"],
        )
        self.request["recovery_request_id"] = m.RECOVERY_ID
        with self.assertRaisesRegex(ValueError, "original six named"):
            self.perform(recovery=True)
        self.assertEqual(self.events, [])

    def test_error_evidence_redacts_credentials(self):
        with patch.dict("os.environ", {"BUFFER_API_KEY": "test-secret-key"}):
            error = m.provider_error(RuntimeError(
                "Buffer GraphQL error: token=test-secret-key Bearer different-secret api_key=third-secret"
            ))
        serialized = json.dumps(error)
        self.assertNotIn("test-secret-key", serialized)
        self.assertNotIn("different-secret", serialized)
        self.assertNotIn("third-secret", serialized)
        self.assertIn("[redacted]", serialized)

    def compacting_move(self, bid, original_text):
        ordered = sorted((p for p in self.remote.values() if p["status"] == "scheduled"),
                         key=lambda p: m.stamp(p["dueAt"]))
        slots = [p["dueAt"] for p in ordered]
        index = next(i for i, p in enumerate(ordered) if p["id"] == bid)
        result = self.move(bid, original_text)
        for i in range(index + 1, len(ordered)):
            ordered[i]["dueAt"] = slots[i - 1]
        return result

    def compacted_recovery(self):
        def rejected(bid, original_text):
            raise RuntimeError("Buffer editPost rejected: PostInvalidInputError - Text or media is required")
        self.perform(move=rejected)
        self.request["recovery_request_id"] = m.RECOVERY_ID
        self.perform(move=self.compacting_move, recovery=True)
        self.assertEqual(sum(r["state"] == "verified_draft" for r in self.request["recovery_results"].values()), 1)
        self.assertEqual(sum(r.get("reason") == "schedule_changed" for r in self.request["recovery_results"].values()), 5)
        self.request["continuation_request_id"] = m.CONTINUATION_ID
        self.request["continuation_results"] = {}
        self.events.clear()

    def test_reverse_order_completes_five_while_provider_compacts_later_slots(self):
        self.compacted_recovery()
        immutable = {k: copy.deepcopy(self.request[k]) for k in ("targets", "results", "recovery_results")}
        expected = [t["buffer_id"] for t in m.continuation_targets(self.request)]
        self.perform(move=self.compacting_move, continuation=True)
        self.assertEqual([bid for action, bid in self.edits()], expected)
        self.assertEqual(len(self.edits()), 5)
        self.assertTrue(all(r["state"] == "verified_draft" for r in self.request["continuation_results"].values()))
        for key, original in immutable.items():
            self.assertEqual(self.request[key], original)
        self.events.clear()
        self.perform(continuation=True)
        self.assertEqual(self.events, [])

    def test_continuation_excludes_any_recovery_that_reached_intent(self):
        self.compacted_recovery()
        target = m.continuation_targets(self.request)[0]
        self.request["recovery_results"][target["post_id"]]["intent_at"] = self.now.isoformat()
        self.assertNotIn(target["post_id"], {t["post_id"] for t in m.continuation_targets(self.request)})

    def test_continuation_excludes_unknown_recovery_outcome(self):
        self.compacted_recovery()
        target = m.continuation_targets(self.request)[0]
        self.request["recovery_results"][target["post_id"]].update(
            state="held_unknown", reason="draft_outcome_unverified", mutation_receipt_confirmed=False)
        self.assertNotIn(target["post_id"], {t["post_id"] for t in m.continuation_targets(self.request)})

    def test_continuation_keeps_exact_observed_schedule_guard(self):
        self.compacted_recovery()
        target = m.continuation_targets(self.request)[0]
        self.remote[target["buffer_id"]]["dueAt"] = "2026-11-06T12:00:00Z"
        self.perform(continuation=True)
        row = self.request["continuation_results"][target["post_id"]]
        self.assertEqual(row["reason"], "schedule_changed")
        self.assertNotIn(("edit", target["buffer_id"]), self.edits())

    def test_continuation_requires_its_named_request(self):
        self.compacted_recovery()
        self.request.pop("continuation_request_id")
        with self.assertRaises(ValueError):
            self.perform(continuation=True)
        self.assertEqual(self.events, [])

    def test_unknown_continuation_is_never_replayed(self):
        self.compacted_recovery()
        def timeout(bid, original_text):
            self.events.append(("edit", bid))
            raise TimeoutError("Response lost")
        self.perform(move=timeout, continuation=True)
        self.assertTrue(all(r["state"] == "held_unknown" for r in self.request["continuation_results"].values()))
        self.events.clear()
        self.perform(continuation=True)
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
