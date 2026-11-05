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
        details = {}
        item, problem = purge.resolve_live_target(
            self.target, {}, fail, error_detail=details)
        self.assertIsNone(item)
        self.assertEqual(problem, "direct_id_lookup_failed")
        self.assertEqual(details, {"error_class": "RuntimeError"})

    def test_safe_buffer_error_is_preserved_without_credentials(self):
        def fail(_bid):
            raise RuntimeError("Buffer GraphQL error: post cannot be found")
        detail = {}
        _, reason = purge.resolve_live_target(
            self.target, {}, fail, error_detail=detail)
        self.assertEqual(reason, "direct_id_lookup_failed")
        self.assertEqual(detail, {
            "error_class": "RuntimeError",
            "provider_message": "Buffer GraphQL error: post cannot be found",
        })

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

    def test_readonly_main_inspection_and_manual_only_deletion(self):
        manual = {"GITHUB_REF": "refs/heads/main",
                  "GITHUB_EVENT_NAME": "workflow_dispatch"}
        self.assertEqual(purge.execution_mode([], {}), "validate")
        self.assertEqual(purge.execution_mode(["--inspect"], manual), "inspect")
        self.assertEqual(purge.execution_mode(["--apply"], manual), "apply")
        push = {"GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "push"}
        self.assertEqual(purge.execution_mode(["--inspect"], push), "inspect")
        with self.assertRaises(RuntimeError):
            purge.execution_mode(["--apply"], push)
        for env in (
            {"GITHUB_REF": "refs/heads/feature", "GITHUB_EVENT_NAME": "workflow_dispatch"},
            {"GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "pull_request"},
        ):
            for flag in ("--inspect", "--apply"):
                with self.subTest(env=env, flag=flag):
                    with self.assertRaises(RuntimeError):
                        purge.execution_mode([flag], env)
        with self.assertRaises(ValueError):
            purge.execution_mode(["--inspect", "--apply"], manual)
        with self.assertRaises(ValueError):
            purge.execution_mode(["--delete-all"], manual)

    def test_exact_id_read_overrides_stale_listing(self):
        stale = {**self.post, "status": "sent"}
        checked = []
        remote, problem = purge.resolve_live_target(
            self.target, {"buffer-id": stale},
            lambda bid: (checked.append(bid), self.post)[1],
            always_direct=True,
        )
        self.assertEqual(checked, ["buffer-id"])
        self.assertIsNone(problem)
        self.assertIs(remote, self.post)

    def test_last_minute_change_blocks_deletion(self):
        self.assertIsNone(purge.deletion_recheck_reason(
            self.target, self.post, dict(self.post)))
        for patch, expected in (
            ({"status": "sent", "sentAt": "2026-11-05T09:30:00Z"}, "already_published"),
            ({"status": "pending"}, "changed_before_delete"),
            ({"dueAt": "2026-11-05T09:30:00Z"}, "changed_before_delete"),
            ({"text": "edited"}, "text_changed"),
            ({"channelId": "unrelated"}, "channel_mismatch"),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(purge.deletion_recheck_reason(
                    self.target, self.post, {**self.post, **patch}), expected)
        self.assertEqual(purge.deletion_recheck_reason(
            self.target, self.post, None), "not_in_listing_unverified")

    def test_draft_status_is_explicitly_filtered_without_date_cutoff(self):
        query = purge.posts_list_query("org", purge.CHANNEL, "cursor",
                                       drafts_only=True)
        self.assertIn("status: [draft]", query)
        self.assertIn("channelIds:", query)
        self.assertIn('after: "cursor"', query)
        self.assertNotIn("dueAt: {start", query)
        ordinary = purge.posts_list_query("org", purge.CHANNEL, None)
        self.assertNotIn("status: [draft]", ordinary)

    def test_live_draft_list_recovers_post_missing_from_single_id_query(self):
        self.assertIsNone(purge.validate_live_identity(
            self.target, {**self.post, "allowedActions": []}))
        self.assertEqual(purge.validate_live(
            self.target, {**self.post, "allowedActions": []}),
            "delete_action_not_allowed")

        def cannot_fetch(_bid):
            self.fail("Exact draft-list match needs no single-post API call")

        remote, source, problem = purge.inspect_live_target(
            self.target, {"buffer-id": {**self.post, "allowedActions": []}},
            cannot_fetch)
        self.assertIsNone(problem)
        self.assertEqual(source, "buffer_draft_list")
        self.assertEqual(remote["status"], "draft")

    def test_mismatched_draft_listing_never_establishes_live_identity(self):
        for patch in (
            {"id": "different"}, {"text": "edited"},
            {"channelId": "other"}, {"status": "sent"},
            {"sentAt": "2026-11-05T01:00:00Z"},
        ):
            with self.subTest(patch=patch):
                detail = {}
                post, source, reason = purge.inspect_live_target(
                    self.target, {"buffer-id": {**self.post, **patch}},
                    lambda _id: (_ for _ in ()).throw(
                        RuntimeError("Buffer GraphQL error: Post not found")),
                    error_detail=detail)
                self.assertIsNone(post)
                self.assertEqual(source, "buffer_post_id")
                self.assertEqual(reason, "direct_id_lookup_failed")

    def test_historical_draft_receipt_is_evidence_not_current_state(self):
        archived = {
            "state": "verified_draft", "mutation_receipt_confirmed": True,
            "verified_at": "2026-11-04T03:45:00+00:00",
            "after": {
                "buffer_id": self.target["buffer_id"],
                "channel_id": purge.CHANNEL,
                "text_sha256": self.target["text_sha256"],
                "provider_status": "draft",
                "sent_at": None,
            },
        }
        cleanup = {"recovery_results": {"xexp-test": archived}}
        prior = purge.last_verified_historical_draft(self.target, cleanup)
        self.assertEqual(prior["status"], "draft")
        self.assertEqual(prior["verified_at_utc"], "2026-11-04T03:45:00+00:00")
        self.assertEqual(prior["note"], "historical_receipt_not_current_buffer_state")

        for changed in (
            {"mutation_receipt_confirmed": False},
            {"after": {**archived["after"], "channel_id": "another"}},
            {"after": {**archived["after"], "text_sha256": "different"}},
            {"after": {**archived["after"], "provider_status": "scheduled"}},
            {"after": {**archived["after"], "sent_at": "2026-11-04T03:45:00Z"}},
            {"verified_at": "not-a-time"},
        ):
            with self.subTest(changed=changed):
                forged = {**archived, **changed}
                self.assertIsNone(purge.last_verified_historical_draft(
                    self.target, {"recovery_results": {"xexp-test": forged}}))

    def test_unpublished_matching_status_is_allowed_only_in_manual_purge(self):
        for status in ("draft", "scheduled", "pending", "error", "failed"):
            with self.subTest(status=status):
                self.assertIsNone(purge.validate_live(
                    self.target, {**self.post, "status": status}))


if __name__ == "__main__":
    unittest.main()
