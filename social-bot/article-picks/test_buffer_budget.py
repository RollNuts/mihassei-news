"""Offline enforcement of provider limits; no production credentials or HTTP."""
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import buffer_budget as budget
import publish

NOW = datetime(2026, 11, 7, 1, tzinfo=timezone.utc)


class BufferBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "budget.json"
        budget.write(self.path, {"schema_version": 1, "requests": [], "provider_limits": []})

    def request(self, age, role="publish"):
        return {"at_utc": (NOW - age).isoformat(), "role": role}

    def seed(self, requests, **extra):
        budget.write(self.path, {"schema_version": 1, "requests": requests, "provider_limits": [], **extra})

    def test_rolling_day_limit_blocks_http_before_any_network(self):
        self.seed([self.request(timedelta(minutes=10))] * 30)
        with patch.dict(os.environ, {"BUFFER_BUDGET_PATH": str(self.path), "BUFFER_REQUEST_ROLE": "publish"}), \
             patch.object(budget, "datetime", wraps=datetime) as clock, patch.object(publish, "urlopen") as http:
            clock.now.return_value = NOW
            with self.assertRaisesRegex(RuntimeError, "rolling request"):
                publish.gql("test-only", "query { account { id } }")
            http.assert_not_called()
        self.assertEqual(len(budget.read(self.path)["requests"]), 30)

    def test_month_limit_counts_failed_attempts_and_expires_by_age(self):
        self.seed([self.request(timedelta(days=2))] * 900)
        with self.assertRaisesRegex(RuntimeError, "rolling request"):
            budget.reserve(self.path, "publish", NOW)
        self.seed([self.request(timedelta(days=31))] * 900)
        budget.reserve(self.path, "publish", NOW)
        self.assertEqual(len(budget.read(self.path)["requests"]), 1)

    def test_audit_cannot_spend_reserved_posting_requests(self):
        self.seed([self.request(timedelta(hours=2), "audit")] * 7)
        with self.assertRaisesRegex(RuntimeError, "reserved for publishing"):
            budget.reserve(self.path, "audit", NOW)
        budget.reserve(self.path, "publish", NOW)
        self.assertEqual(budget.read(self.path)["requests"][-1]["role"], "publish")
        self.seed([self.request(timedelta(hours=2))] * 24)
        with self.assertRaisesRegex(RuntimeError, "reserved for publishing"):
            budget.reserve(self.path, "audit", NOW)
        budget.reserve(self.path, "publish", NOW)

    def test_live_remaining_and_reset_headers_override_local_empty_history(self):
        headers = Message()
        headers["RateLimit"] = '"1000-in-30days"; r=0; t=3600'
        headers["RateLimit-Policy"] = '"1000-in-30days"; q=1000; w=2592000; pk=:PRIVATE:'
        budget.observe(self.path, headers, NOW)
        with self.assertRaisesRegex(RuntimeError, "provider allowance"):
            budget.reserve(self.path, "publish", NOW)
        self.assertNotIn("PRIVATE", self.path.read_text())
        budget.reserve(self.path, "publish", NOW + timedelta(hours=2))

    def test_monthly_provider_reserve_is_available_to_posts_only(self):
        self.seed([], provider_limits=[{"remaining": 200, "window_seconds": 2592000,
                                       "reset_at_utc": (NOW + timedelta(days=10)).isoformat()}])
        with self.assertRaisesRegex(RuntimeError, "provider allowance"):
            budget.reserve(self.path, "audit", NOW)
        budget.reserve(self.path, "publish", NOW)
        self.assertEqual(budget.read(self.path)["provider_limits"][0]["remaining"], 199)

    def test_http_429_is_counted_and_persists_shared_cooldown(self):
        headers = Message()
        headers["Retry-After"] = "3600"
        error = HTTPError("https://api.buffer.com", 429, "rate limited", headers, io.BytesIO(b""))
        with patch.dict(os.environ, {"BUFFER_BUDGET_PATH": str(self.path), "BUFFER_REQUEST_ROLE": "publish"}), \
             patch.object(budget, "datetime", wraps=datetime) as clock, \
             patch.object(publish, "urlopen", side_effect=error) as http:
            clock.now.return_value = NOW
            with self.assertRaisesRegex(RuntimeError, "429"):
                publish.gql("test-only", "query { account { id } }")
            with self.assertRaisesRegex(RuntimeError, "hold until"):
                publish.gql("test-only", "query { account { id } }")
            http.assert_called_once()
        data = budget.read(self.path)
        self.assertEqual(len(data["requests"]), 1)
        self.assertEqual(data["provider_retry_at_utc"], (NOW + timedelta(hours=1)).isoformat())

    def test_successful_post_receipt_survives_quota_metadata_write_failure(self):
        response = io.BytesIO(b'{"data":{"createPost":{"id":"receipt"}}}')
        response.headers = Message()
        with patch.dict(os.environ, {"BUFFER_BUDGET_PATH": str(self.path), "BUFFER_REQUEST_ROLE": "publish"}), \
             patch.object(publish, "urlopen", return_value=response), \
             patch.object(budget, "observe", side_effect=OSError("disk unavailable")):
            result = publish.gql("test-only", "mutation { createPost { id } }")
        self.assertEqual(result["createPost"]["id"], "receipt")
        self.assertEqual(len(budget.read(self.path)["requests"]), 1)

    def test_missing_or_corrupt_evidence_does_not_reset_the_allowance(self):
        self.path.unlink()
        with self.assertRaises(OSError):
            budget.reserve(self.path, "publish", NOW)
        self.seed([{"at_utc": "bad-time", "role": "publish"}])
        with self.assertRaisesRegex(RuntimeError, "Invalid Buffer"):
            budget.reserve(self.path, "publish", NOW)

    def test_publishing_and_audit_serialize_and_persist_the_same_budget(self):
        root = Path(__file__).resolve().parents[2]
        for name in ("x-original-experiments.yml", "x-delivery-audit.yml"):
            workflow = (root / ".github/workflows" / name).read_text()
            self.assertIn("|| 'x-article-promotion'", workflow)
            self.assertIn("BUFFER_BUDGET_PATH: social-bot/article-picks/api-budget.json", workflow)
            self.assertIn("git add social-bot/article-picks/", workflow)
            self.assertIn("if: always()", workflow)


if __name__ == "__main__":
    unittest.main()
