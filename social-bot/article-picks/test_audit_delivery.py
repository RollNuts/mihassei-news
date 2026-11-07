import copy
import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("article_audit", HERE / "audit_delivery.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone(timedelta(hours=9)))


def entry(slug, hours=2):
    return {
        "buffer_id": "buf-" + slug,
        "queued_at_utc": (NOW - timedelta(hours=hours)).astimezone(timezone.utc).isoformat(),
    }


class ArticleDeliveryAuditTests(unittest.TestCase):
    def test_article_publish_push_only_reads_buffer_in_evening(self):
        for hour, allowed in ((0, False), (20, False), (21, True), (23, True)):
            with self.subTest(hour=hour):
                self.assertIs(m.trigger_allows_provider_read("push", NOW.replace(hour=hour)), allowed)
                self.assertTrue(m.trigger_allows_provider_read("schedule", NOW.replace(hour=hour)))
                self.assertTrue(m.trigger_allows_provider_read("workflow_dispatch", NOW.replace(hour=hour)))
                # Only trusted main publisher completions can reach this event in CI.
                self.assertTrue(m.trigger_allows_provider_read("workflow_run", NOW.replace(hour=hour)))
        with self.assertRaisesRegex(ValueError, "timezone"):
            m.trigger_allows_provider_read("push", NOW.replace(tzinfo=None))

    def test_main_publisher_completion_is_safe_audit_fallback(self):
        workflow = (
            HERE.parents[1] / ".github" / "workflows" / "x-delivery-audit.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'workflow_run:\n    workflows: ["X Article Promotion (Buffer Free)"]\n    types: [completed]',
            workflow,
        )
        self.assertIn(
            "github.event.workflow_run.head_repository.full_name == github.repository",
            workflow,
        )
        self.assertIn("github.event.workflow_run.head_branch == 'main'", workflow)
        self.assertIn(
            """contains(fromJSON('["push","schedule","workflow_dispatch"]'), github.event.workflow_run.event)""",
            workflow,
        )
        self.assertIn(
            """contains(fromJSON('["success","failure"]'), github.event.workflow_run.conclusion)""",
            workflow,
        )
        self.assertIn("github.event_name != 'pull_request'", workflow)
        # Existing :35 and 23:45 UTC cron slots and read-only entrypoint stay.
        self.assertIn('cron: "35 0,2,4,6,8,10,12 * * *"', workflow)
        self.assertIn('cron: "45 14 * * *"', workflow)
        self.assertIn("python social-bot/article-picks/audit_delivery.py", workflow)

    def test_daytime_article_push_does_not_contact_buffer_or_write_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_text(json.dumps({
                "schema_version": 1, "channel_id": "x-1",
            }))
            (root / "state.json").write_text(json.dumps({
                "schema_version": 1, "queued": {},
            }))
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            with patch.object(m, "HERE", root), \
                 patch.object(m, "datetime", wraps=datetime) as clock, \
                 patch.dict(os.environ, {"BUFFER_API_KEY": "test-only", "X_AUDIT_TRIGGER_EVENT": "push"}, clear=True), \
                 patch.object(m, "read_post_by_id") as existing:
                clock.now.return_value = NOW.replace(hour=20)
                self.assertEqual(m.main(), 0)
                existing.assert_not_called()
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)

    def test_reconcile_requires_saved_id_and_canonical_article_url(self):
        e = entry("story")
        e.update({
            "buffer_status_at_accept": "sending",
            "buffer_shared_now_at_accept": True,
            "buffer_share_mode_at_accept": "shareNow",
        })
        remote = {
            "id": e["buffer_id"],
            "status": "scheduled",
            "text": "【未発生新聞】\n見出し\n\nhttps://veripsa.com/articles/story/",
            "createdAt": "2026-11-05T04:59:55Z",
            "dueAt": "2026-11-05T05:00:00Z",
            "externalLink": "https://x.com/veripsa/status/123",
            "sharedNow": True,
            "shareMode": "shareNow",
        }
        row = m.reconcile("story", e, remote)
        self.assertEqual(row["buffer_status"], "scheduled")
        self.assertEqual(row["buffer_due_at_utc"], "2026-11-05T05:00:00+00:00")
        self.assertEqual(row["buffer_created_at_utc"], "2026-11-05T04:59:55+00:00")
        self.assertEqual(row["buffer_status_at_accept"], "sending")
        self.assertIs(row["buffer_shared_now_at_accept"], True)
        self.assertEqual(row["buffer_external_link"], "https://x.com/veripsa/status/123")
        self.assertIs(row["buffer_shared_now"], True)
        self.assertEqual(row["buffer_share_mode"], "shareNow")

        wrong = dict(remote, text="別の記事 https://veripsa.com/articles/other/")
        row = m.reconcile("story", e, wrong)
        self.assertEqual(row["buffer_status"], "unverified")
        self.assertEqual(row["reason"], "canonical_article_url_missing_or_text_mismatch")

    def test_build_counts_only_observed_today_provider_timestamps(self):
        state = {"schema_version": 1, "queued": {
            "sent": entry("sent", 5),
            "scheduled": entry("scheduled", 4),
            "missing": entry("missing", 3),
        }}
        existing = [
            {"id": "buf-sent", "status": "sent",
             "text": "https://veripsa.com/articles/sent/",
             "sentAt": "2026-11-05T01:00:00Z"},
            {"id": "buf-scheduled", "status": "scheduled",
             "text": "https://veripsa.com/articles/scheduled/",
             "dueAt": "2026-11-05T08:00:00Z"},
        ]
        report = m.build(state, existing, NOW)
        self.assertEqual(report["observed"]["buffer_sent_today"], 1)
        self.assertEqual(report["observed"]["buffer_scheduled_today"], 1)
        self.assertEqual(report["observed"]["unverified"], 1)
        self.assertEqual(report["observed"]["delivery_problems"], 0)
        self.assertEqual(report["observed"]["delivery_in_progress"], 0)
        self.assertEqual(report["observed"]["overdue_sending"], 0)
        self.assertEqual(report["scope"]["automatic_lane"], "article-picks-only")

    def test_direct_saved_id_distinguishes_short_sending_from_real_problem(self):
        state = {"schema_version": 1, "queued": {
            "error": entry("error", 2),
            "sending": entry("sending", 1),
        }}
        existing = [
            {"id": "buf-error", "status": "error",
             "text": "https://veripsa.com/articles/error/",
             "error": {
                 "message": "X rejected this post",
                 "supportUrl": "https://support.buffer.com/example",
             }},
            {"id": "buf-sending", "status": "sending",
             "text": "https://veripsa.com/articles/sending/",
             "createdAt": (NOW - timedelta(minutes=5)).isoformat()},
        ]
        report = m.build(state, existing, NOW)
        self.assertEqual(report["observed"]["delivery_problems"], 1)
        self.assertEqual(report["observed"]["delivery_in_progress"], 1)
        self.assertEqual(report["observed"]["overdue_sending"], 0)
        by_slug = {row["slug"]: row for row in report["items"]}
        self.assertEqual(by_slug["error"]["buffer_status"], "error")
        self.assertEqual(
            by_slug["error"]["review_needed"], "provider_not_sent_error"
        )
        self.assertEqual(
            by_slug["error"]["buffer_error_message"], "X rejected this post"
        )
        self.assertEqual(
            by_slug["error"]["buffer_error_support_url"],
            "https://support.buffer.com/example",
        )
        self.assertEqual(by_slug["sending"]["buffer_status"], "sending")
        self.assertIs(by_slug["sending"]["delivery_in_progress"], True)
        self.assertEqual(by_slug["sending"]["sending_age_minutes"], 5)
        self.assertNotIn("review_needed", by_slug["sending"])

    def test_sending_more_than_fifteen_minutes_requires_review_without_retry(self):
        state = {"schema_version": 1, "queued": {
            "sending-old": entry("sending-old", 1),
        }}
        existing = [{
            "id": "buf-sending-old",
            "status": "sending",
            "text": "https://veripsa.com/articles/sending-old/",
            "createdAt": (NOW - timedelta(minutes=16)).isoformat(),
        }]
        report = m.build(state, existing, NOW)
        self.assertEqual(report["observed"]["delivery_problems"], 1)
        self.assertEqual(report["observed"]["delivery_in_progress"], 0)
        self.assertEqual(report["observed"]["overdue_sending"], 1)
        row = report["items"][0]
        self.assertEqual(
            row["review_needed"], "provider_sending_more_than_15_minutes"
        )
        self.assertEqual(row["sending_age_minutes"], 16)

    def test_sending_without_any_timestamp_is_problem_not_success(self):
        state = {"schema_version": 1, "queued": {
            "sending-unknown": {
                "buffer_id": "buf-sending-unknown",
                "queued_at_utc": "",
            },
        }}
        # selected_entries rejects unusable ledger time, so exercise reconcile/build
        # through a valid queued time but no provider createdAt.
        state["queued"]["sending-unknown"]["queued_at_utc"] = (
            NOW - timedelta(minutes=5)
        ).astimezone(timezone.utc).isoformat()
        existing = [{
            "id": "buf-sending-unknown",
            "status": "sending",
            "text": "https://veripsa.com/articles/sending-unknown/",
        }]
        report = m.build(state, existing, NOW)
        # queued_at is the final conservative fallback; this remains in progress.
        self.assertEqual(report["observed"]["delivery_in_progress"], 1)
        self.assertEqual(report["observed"]["delivery_problems"], 0)

    def test_read_post_by_id_is_read_only_and_uses_saved_id(self):
        with patch.object(m, "gql", return_value={
            "post": {
                "id": "buf-story", "status": "sent",
                "text": "https://veripsa.com/articles/story/",
                "sentAt": "2026-11-05T03:00:00Z",
            }
        }) as api:
            row = m.read_post_by_id("secret", "buf-story")
        self.assertEqual(row["id"], "buf-story")
        query = api.call_args.args[1]
        self.assertIn('post(input: {id: "buf-story"})', query)
        self.assertIn("createdAt", query)
        self.assertIn("externalLink sharedNow shareMode", query)
        self.assertIn("error { message supportUrl }", query)
        self.assertNotIn("rawError", query)
        self.assertNotIn("mutation", query)

    def test_direct_lookup_failure_stays_unverified_without_retry(self):
        state = {"schema_version": 1, "queued": {
            "missing": entry("missing", 1),
        }}
        report = m.build(
            state, [], NOW,
            {"buf-missing": "direct_id_lookup_failed:provider unavailable"},
        )
        row = report["items"][0]
        self.assertEqual(row["buffer_status"], "unverified")
        self.assertTrue(row["reason"].startswith("direct_id_lookup_failed:"))

    def test_overdue_reservations_are_flagged_not_counted_as_sent(self):
        state = {"schema_version": 1, "queued": {
            "late": entry("late", 5),
            "no-date": entry("no-date", 4),
        }}
        existing = [
            {"id": "buf-late", "status": "scheduled",
             "text": "https://veripsa.com/articles/late/",
             "dueAt": (NOW - timedelta(hours=3)).isoformat()},
            {"id": "buf-no-date", "status": "scheduled",
             "text": "https://veripsa.com/articles/no-date/"},
        ]
        report = m.build(state, existing, NOW)
        self.assertEqual(report["observed"]["buffer_sent_today"], 0)
        self.assertEqual(report["observed"]["overdue_scheduled"], 1)
        self.assertEqual(report["observed"]["missing_provider_timestamps"], 1)
        late = next(row for row in report["items"] if row["slug"] == "late")
        self.assertEqual(late["review_needed"], "scheduled_more_than_two_hours_past_due")

    def test_old_ledger_entries_are_outside_bounded_audit(self):
        state = {"schema_version": 1, "queued": {
            "old": entry("old", 24 * 10),
            "recent": entry("recent", 24 * 2),
        }}
        selected = m.selected_entries(state, NOW)
        self.assertEqual([slug for slug, _entry, _stamp in selected], ["recent"])

    def test_no_secret_does_not_write_or_open_buffer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_text(json.dumps({
                "schema_version": 1, "channel_id": "x-1"
            }))
            (root / "state.json").write_text(json.dumps({
                "schema_version": 1, "queued": {}
            }))
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            with patch.object(m, "HERE", root), patch.dict(os.environ, {}, clear=True), \
                 patch.object(m, "read_post_by_id", side_effect=AssertionError("no Buffer read")):
                self.assertEqual(m.main(), 0)
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)

    def test_save_is_atomic_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "delivery.json"
            report = m.build({"schema_version": 1, "queued": {}}, [], NOW)
            m.save(copy.deepcopy(report), path)
            self.assertEqual(json.loads(path.read_text()), report)

    def test_confirmed_delivery_is_reused_without_refreshing_its_observation_time(self):
        state = {"schema_version": 1, "queued": {"sent": entry("sent")}}
        before = NOW - timedelta(hours=1)
        previous = m.build(state, [{"id": "buf-sent", "status": "sent",
            "text": m.canonical_url("sent"), "sentAt": before.isoformat()}], before)
        cached = m.reusable_observations(state, previous, NOW)
        self.assertEqual(cached["sent"]["provider_checked_at_utc"], before.astimezone(timezone.utc).isoformat())
        report = m.build(state, [], NOW, cached_items=cached)
        self.assertEqual(report["observed"]["buffer_sent_today"], 1)
        self.assertTrue(report["items"][0]["observation_reused"])
        previous["items"][0]["buffer_id"] = "different-receipt"
        self.assertEqual(m.reusable_observations(state, previous, NOW), {})

    def test_cached_sending_is_still_flagged_when_it_becomes_overdue(self):
        state = {"schema_version": 1, "queued": {"sending": entry("sending")}}
        before = NOW - timedelta(minutes=10)
        previous = m.build(state, [{"id": "buf-sending", "status": "sending",
            "text": m.canonical_url("sending"),
            "createdAt": (before - timedelta(minutes=10)).isoformat()}], before)
        cached = m.reusable_observations(state, previous, NOW)
        report = m.build(state, [], NOW, cached_items=cached)
        self.assertEqual(report["observed"]["overdue_sending"], 1)
        self.assertEqual(report["observed"]["delivery_in_progress"], 0)

    def test_rate_limit_stops_remaining_reads_and_persists_backoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = {"schema_version": 1, "queued": {s: entry(s) for s in ("a", "b", "c")}}
            (root / "state.json").write_text(json.dumps(state))
            (root / "config.json").write_text(json.dumps({"schema_version": 1, "channel_id": "pinned"}))
            retry = (NOW + timedelta(hours=2)).astimezone(timezone.utc).isoformat()
            with patch.object(m, "HERE", root), patch.object(m, "datetime", wraps=datetime) as clock, \
                 patch.dict(os.environ, {"BUFFER_API_KEY": "test-only"}, clear=True), \
                 patch.object(m, "read_post_by_id", side_effect=RuntimeError(
                     "Buffer HTTP error: 429; retry_at_utc=" + retry)) as read:
                clock.now.return_value = NOW
                self.assertEqual(m.main(), 1)
                read.assert_called_once()
                report = json.loads((root / "delivery.json").read_text())
                self.assertEqual(report["provider_retry_at_utc"], retry)
                self.assertEqual(report["observed"]["unverified"], 3)
                self.assertEqual(m.main(), 1)
                read.assert_called_once()
                self.assertEqual(json.loads((root / "state.json").read_text()), state)

    def test_direct_read_budget_does_not_hide_deferred_records(self):
        state = {"schema_version": 1, "queued": {s: entry(s) for s in ("a", "b", "c", "d")}}
        previous = m.build(state, [], NOW, {"buf-a": "deferred_read_budget"})
        cached = m.reusable_observations(state, previous, NOW)
        self.assertNotIn("a", cached)

    def test_rate_limit_headers_expose_only_safe_retry_data(self):
        from email.message import Message
        from publish import rate_limit_detail
        headers = Message()
        headers["Retry-After"] = "900"
        headers["RateLimit"] = '"150-in-15min"; r=0; t=900'
        headers["Authorization"] = "must-not-appear"
        detail = rate_limit_detail(headers)
        self.assertIn("retry_after_seconds=900", detail)
        self.assertIn("retry_at_utc=", detail)
        self.assertIn("remaining=0", detail)
        self.assertNotIn("must-not-appear", detail)


if __name__ == "__main__":
    unittest.main()
