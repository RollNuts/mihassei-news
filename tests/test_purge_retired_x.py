"""Offline safety checks for manual-only retired Buffer X cleanup."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "social-bot"))
import purge_retired_x as purge


class PurgeRetiredIdentityTest(unittest.TestCase):
    def setUp(self):
        self.target = {"post_id": "xexp-test", "buffer_id": "buffer-id",
                       "text_sha256": purge.digest("historical exact text")}
        self.post = {"id": "buffer-id", "channelId": purge.CHANNEL,
                     "text": "historical exact text", "status": "draft",
                     "sentAt": None, "allowedActions": ["deletePost"]}

    def test_list_omission_recovers_only_via_matching_id(self):
        seen = []
        item, problem = purge.resolve_live_target(
            self.target, {}, lambda bid: (seen.append(bid), self.post)[1])
        self.assertEqual(seen, ["buffer-id"])
        self.assertIsNone(problem)
        self.assertIsNone(purge.validate_live(self.target, item))

    def test_missing_id_is_not_treated_as_deleted(self):
        item, problem = purge.resolve_live_target(
            self.target, {}, lambda _bid: None)
        self.assertIsNone(item)
        self.assertEqual(problem, "not_in_listing_or_direct_lookup_unverified")
        self.assertNotEqual(problem, "already_absent")

    def test_failed_direct_read_is_not_treated_as_deleted(self):
        def fail(_bid):
            raise RuntimeError("provider timeout")
        item, problem = purge.resolve_live_target(self.target, {}, fail)
        self.assertIsNone(item)
        self.assertEqual(problem, "direct_id_lookup_failed")

    def test_listing_presence_avoids_extra_api_read(self):
        def fail(_bid):
            self.fail("known listing entry must not be re-fetched")
        item, problem = purge.resolve_live_target(
            self.target, {"buffer-id": self.post}, fail)
        self.assertIsNone(problem)
        self.assertIs(item, self.post)

    def test_missing_or_mismatched_identity_cannot_delete(self):
        for patch, expected in [
            ({"id": "different"}, "identity_mismatch"),
            ({"channelId": "different"}, "channel_mismatch"),
            ({"text": "changed"}, "text_changed"),
            ({"status": "sent"}, "already_published"),
            ({"sentAt": "2026-11-04T00:00:00Z"}, "already_published"),
            ({"allowedActions": []}, "delete_action_not_allowed"),
        ]:
            with self.subTest(expected=expected):
                self.assertEqual(
                    purge.validate_live(self.target, {**self.post, **patch}),
                    expected,
                )

    def test_unpublished_matching_status_is_allowed_only_in_manual_purge(self):
        for status in ("draft", "scheduled", "pending", "error", "failed"):
            with self.subTest(status=status):
                self.assertIsNone(purge.validate_live(
                    self.target, {**self.post, "status": status}))


if __name__ == "__main__":
    unittest.main()
