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
    def test_reconcile_requires_saved_id_and_canonical_article_url(self):
        e = entry("story")
        remote = {
            "id": e["buffer_id"],
            "status": "scheduled",
            "text": "【未発生新聞】\n見出し\n\nhttps://veripsa.com/articles/story/",
            "dueAt": "2026-11-05T05:00:00Z",
        }
        row = m.reconcile("story", e, remote)
        self.assertEqual(row["buffer_status"], "scheduled")
        self.assertEqual(row["buffer_due_at_utc"], "2026-11-05T05:00:00+00:00")

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
        self.assertEqual(report["scope"]["automatic_lane"], "article-picks-only")

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
                 patch.object(m, "choose_x_channel", side_effect=AssertionError("no Buffer read")):
                self.assertEqual(m.main(), 0)
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)

    def test_save_is_atomic_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "delivery.json"
            report = m.build({"schema_version": 1, "queued": {}}, [], NOW)
            m.save(copy.deepcopy(report), path)
            self.assertEqual(json.loads(path.read_text()), report)


if __name__ == "__main__":
    unittest.main()
