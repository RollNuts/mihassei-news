"""Offline guards for the current X article-only publishing policy."""
from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def step(document: str, name: str) -> str:
    match = re.search(
        r"(?ms)^      - name: " + re.escape(name) + r"\n(.*?)(?=^      - |\Z)",
        document,
    )
    if not match:
        raise AssertionError(f"workflow step not found: {name}")
    return match.group(1)


class XWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.article = (WORKFLOWS / "x-original-experiments.yml").read_text()
        self.useful = (WORKFLOWS / "x-useful-originals.yml").read_text()
        self.midday = (WORKFLOWS / "x-midday-original.yml").read_text()
        self.weekly = (WORKFLOWS / "x-weekly-experiments.yml").read_text()
        self.audit = (WORKFLOWS / "x-delivery-audit.yml").read_text()
        self.surreal = (WORKFLOWS / "free-surreal-x.yml").read_text()

    def test_article_promotion_is_the_only_scheduled_x_sender(self):
        self.assertIn('cron: "5 0,2,4,6,8,10,12 * * *"', self.article)
        self.assertIn('- "content/articles.json"', self.article)
        self.assertIn("run: python social-bot/article-picks/dispatch.py", self.article)
        self.assertIn("BUFFER_API_KEY:", self.article)
        self.assertIn("X_TRIGGER_EVENT: ${{ github.event_name }}", self.article)
        queue = step(self.article, "Queue at most one eligible Veripsa article")
        self.assertIn("github.ref == 'refs/heads/main'", queue)
        self.assertIn("github.event_name != 'pull_request'", queue)

        # Retired creative lanes may retain offline tests/history, but never a schedule
        # or Buffer credential that could post an original automatically.
        for name, document in (
            ("useful", self.useful),
            ("midday", self.midday),
            ("weekly", self.weekly),
        ):
            with self.subTest(name=name):
                self.assertNotRegex(document, r"(?m)^\s*schedule:\s*$")
                self.assertNotIn("BUFFER_API_KEY:", document)
                self.assertNotIn("addToQueue", document)

        # The old manga workflow is hard-paused at job level and has no cron.
        self.assertNotRegex(self.surreal, r"(?m)^\s*schedule:\s*$")
        self.assertIn("if: ${{ false }}", self.surreal)

    def test_article_publication_push_is_only_a_fallback_trigger(self):
        # The workflow may wake on article publication, but dispatch.py owns the
        # JST 09:00-21:59 network gate so 07:30/23:30 publication pushes cannot
        # touch Buffer. Cron remains independently scheduled as backup.
        push_block = re.search(
            r"(?ms)^  push:\n(.*?)(?=^  pull_request:)",
            self.article,
        ).group(1)
        self.assertIn('"content/articles.json"', push_block)
        queue = step(self.article, "Queue at most one eligible Veripsa article")
        self.assertIn("X_TRIGGER_EVENT: ${{ github.event_name }}", queue)

    def test_read_only_delivery_audit_cannot_publish(self):
        self.assertIn('cron: "45 14 * * *"', self.audit)
        self.assertIn('- "content/articles.json"', self.audit)
        self.assertIn("X_AUDIT_TRIGGER_EVENT: ${{ github.event_name }}", self.audit)
        self.assertIn("article-picks/audit_delivery.py", self.audit)
        self.assertNotIn("article-picks/dispatch.py", self.audit)
        self.assertNotIn("experiments/dispatch.py", self.audit)
        self.assertNotIn("publish.py", self.audit)

    def test_retired_lane_switches_are_off(self):
        useful = json.loads((ROOT / "social-bot" / "useful-posts.json").read_text())
        experiments = json.loads((ROOT / "social-bot" / "experiments" / "config.json").read_text())
        picks = json.loads((ROOT / "social-bot" / "article-picks" / "config.json").read_text())
        self.assertIs(useful["enabled"], False)
        self.assertIs(experiments["experiment_publishing_enabled"], False)
        self.assertIs(experiments["two_daily_midday_enabled"], False)
        self.assertIs(picks["enabled"], True)
        self.assertEqual(picks["max_per_jst_day"], 7)
        self.assertEqual(picks["minimum_interval_hours"], 2)

    def test_pr_article_validation_never_calls_buffer(self):
        queue = step(self.article, "Queue at most one eligible Veripsa article")
        persist = step(self.article, "Persist exact Buffer article receipt")
        for block in (queue, persist):
            self.assertIn("github.ref == 'refs/heads/main'", block)
            self.assertIn("github.event_name != 'pull_request'", block)

    def test_completed_legacy_migrations_remain_manual_only(self):
        mutations = {
            "hold-nejimachi-quality.yml": (
                "Move only the two exact unreviewed manga previews to Buffer drafts",
                "Preserve hold audit in Git history",
            ),
            "migrate-legacy-buffer.yml": (
                "Preserve only two old setup posts as Buffer drafts (not deleted)",
                "Fill queue with up to two canonical Nejimachi episodes",
                "Save queue ledger and reversible migration audit",
            ),
        }
        for filename, steps in mutations.items():
            document = (WORKFLOWS / filename).read_text()
            self.assertNotRegex(document, r"(?m)^\s*schedule:\s*$")
            for name in steps:
                block = step(document, name)
                condition = re.search(r"^        if: (.*)$", block, re.M)
                self.assertIsNotNone(condition, (filename, name))
                expression = condition.group(1)
                self.assertIn("workflow_dispatch", expression)
                self.assertNotIn("schedule", expression)


if __name__ == "__main__":
    unittest.main()
