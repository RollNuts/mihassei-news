#!/usr/bin/env python3
"""No-network tests for the optional second daily X post."""
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from midday_dispatch import (
    JST, date_guard, midday_target, parse_time, plan_midday, main,
)
from dispatch import HERE, hash_text, validate
import publish


class MiddaySchedulingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 4, 1, 15, tzinfo=timezone.utc)  # 10:15 JST
        self.state = {"schema_version": 1, "queued": {}}
        self.posts = [
            {"id": "xexp-a", "format": "surreal_text", "sequence": 1,
             "text": "初めて見た奇妙な物語だけど面白くて笑ってしまった。"},
            {"id": "xexp-b", "format": "character_moment", "sequence": 2,
             "text": "二人がしばらく黙ってから笑顔を見せる奇妙な話。"},
        ]
        self.evening = {"id": "buffer-e", "status": "scheduled",
                        "text": "unrelated existing", "dueAt": "2026-11-04T10:30:00Z"}

    def test_target_correct_jst_and_utc(self):
        target = midday_target(self.now)
        self.assertEqual(target.hour, 12)
        self.assertEqual(target.minute, 30)
        self.assertEqual(target.astimezone(timezone.utc).isoformat(),
                         "2026-11-04T03:30:00+00:00")
        self.assertIsNone(midday_target(self.now + timedelta(hours=2)))
        self.assertIsNone(midday_target(self.now - timedelta(hours=4)))
        with self.assertRaises(ValueError):
            midday_target(datetime(2026, 11, 4, 11))

    def test_one_new_original_when_evening_exists(self):
        post, recovered, target, reason = plan_midday(
            self.posts, self.state, [self.evening], self.now)
        self.assertEqual(post["id"], "xexp-a")
        self.assertIsNone(reason)
        self.assertEqual(recovered, [])
        self.assertEqual(target.astimezone(JST).hour, 12)
        self.assertNotIn("xexp-a", self.state["queued"])

    def test_four_existing_reservations_prevent_more(self):
        remote = [dict(self.evening, id=str(i)) for i in range(4)]
        self.assertEqual(plan_midday(
            self.posts, self.state, remote, self.now)[3], "free_queue_safety_cap")

    def test_two_posts_today_prevent_third(self):
        remote = [self.evening,
                  {"id": "m", "status": "sent", "text": "other",
                   "dueAt": "2026-11-04T00:30:00Z"}]
        self.assertEqual(plan_midday(
            self.posts, self.state, remote, self.now)[3], "daily_cap")

    def test_nearby_scheduled_post_prevents_spam(self):
        remote = [{"id": "other", "status": "scheduled", "text": "other",
                   "dueAt": "2026-11-04T04:15:00Z"}]
        self.assertEqual(plan_midday(
            self.posts, self.state, remote, self.now)[3], "four_hour_spacing")

    def test_missing_due_at_fails_closed(self):
        remote = [{"id": "other", "status": "scheduled", "text": "other"}]
        self.assertEqual(plan_midday(
            self.posts, self.state, remote, self.now)[3], "unknown_scheduled_time")

    def test_ledger_prevents_rebook_when_buffer_list_is_missing(self):
        self.state["queued"]["xexp-a"] = {
            "submission_mode": "customScheduled",
            "buffer_id": "past-accepted",
            "due_at_utc": "2026-11-04T03:30:00.000Z",
            "text_sha256": hash_text(self.posts[0]["text"])}
        self.assertEqual(plan_midday(
            self.posts, self.state, [self.evening], self.now)[3],
            "midday_already_submitted")

    def test_unsubmitted_text_recovered_from_buffer(self):
        existing = [self.evening,
                    {"id": "accepted-before-git-push",
                     "text": self.posts[0]["text"],
                     "status": "sent",
                     "dueAt": "2026-11-03T03:30:00Z"}]
        post, recovered, _, reason = plan_midday(
            self.posts, self.state, existing, self.now)
        self.assertIsNone(reason)
        self.assertEqual(recovered, ["xexp-a"])
        self.assertEqual(self.state["queued"]["xexp-a"]["buffer_id"],
                         "accepted-before-git-push")
        self.assertEqual(post["id"], "xexp-b")

    def test_bad_time_unparseable(self):
        self.assertIsNone(parse_time(None))
        self.assertIsNone(parse_time("yesterday"))
        self.assertIsNone(parse_time("2026-11-04T11:30:00"))

    def test_off_switch_and_late_run_never_contact_buffer(self):
        inventory = json.loads((HERE / "posts.json").read_text(encoding="utf-8"))
        cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
        state = json.loads((HERE / "state.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(validate(inventory, cfg, state)), 20)
        with patch("midday_dispatch.choose_x_channel") as remote, \
             patch("midday_dispatch.read_json", side_effect=[dict(cfg, two_daily_midday_enabled=False), inventory, state]):
            self.assertEqual(main(self.now), 0)
            remote.assert_not_called()
        with patch("midday_dispatch.choose_x_channel") as remote:
            self.assertEqual(main(self.now + timedelta(hours=5)), 0)
            remote.assert_not_called()


class BufferCustomSchedulingTests(unittest.TestCase):
    def test_custom_due_at_query_only_for_x_experiments(self):
        due = "2031-11-04T03:30:00.000Z"
        with patch("publish.gql", return_value={
            "createPost": {"post": {"id": "buffer-ok"}}}) as mocked:
            res = publish.queue_one("key", "my-channel", {
                "kind": "text", "id": "xexp-good", "text": "original"
            }, mode="customScheduled", due_at_utc=due)
        self.assertEqual(res, "buffer-ok")
        q = mocked.call_args.args[1]
        self.assertIn("mode: customScheduled", q)
        self.assertIn("dueAt: \"2031-11-04T03:30:00.000Z\"", q)
        self.assertIn("channelId: \"my-channel\"", q)
        with self.assertRaisesRegex(ValueError, "reserved"):
            publish.queue_one("key", "my-channel", {
                "kind": "comic", "id": "comic-old", "text": "not approved"
            }, mode="customScheduled", due_at_utc=due)
        with self.assertRaisesRegex(ValueError, "future UTC"):
            publish.queue_one("key", "my-channel", {
                "kind": "text", "id": "xexp-good", "text": "original"
            }, mode="customScheduled", due_at_utc="2021-01-01T03:30:00Z")
        with self.assertRaisesRegex(ValueError, "only accepted"):
            publish.queue_one("key", "my-channel", {
                "kind": "text", "id": "xexp-good", "text": "original"
            }, due_at_utc=due)

    def test_due_at_provider_lookup_is_opt_in(self):
        with patch("publish.gql", return_value={
            "posts": {"edges": [{"node": {
                "id": "a", "text": "original", "status": "scheduled",
                "dueAt": "2026-11-04T10:30:00Z"}}],
                "pageInfo": {"hasNextPage": False}}}) as mocked:
            published = publish.list_existing("t", "org", "channel", include_due_at=True)
        self.assertEqual(published[0]["dueAt"], "2026-11-04T10:30:00Z")
        self.assertIn("dueAt sentAt", mocked.call_args.args[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
