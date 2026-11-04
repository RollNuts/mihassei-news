"""Offline workflow boundaries: paused rendering, independent feeds and X locks."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
from types import SimpleNamespace
import unittest


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"


def step(document, name):
    return re.search(r"(?ms)^      - name: " + re.escape(name) +
                     r"\n(.*?)(?=^      - |\Z)", document).group(1)


def expression(value, event="schedule", ref="refs/heads/main", workflow="X", flags=None):
    """Evaluate only the boolean/string subset used by these checked-in gates."""
    value = value.removeprefix("${{").removesuffix("}}").strip()
    value = value.replace("&&", " and ").replace("||", " or ")
    context = {
        "github": SimpleNamespace(event_name=event, ref=ref, workflow=workflow),
        "steps": SimpleNamespace(lanes=SimpleNamespace(outputs=SimpleNamespace(**(flags or {})))),
        "format": lambda template, *args: template.format(*args),
        "always": lambda: True,
    }
    return eval(value, {"__builtins__": {}}, context)


class XWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.original = (WORKFLOWS / "x-original-experiments.yml").read_text()

    def gate(self, name, **context):
        block = step(self.original, name)
        condition = re.search(r"^        if: (.*)$", block, re.M).group(1)
        return expression(condition, **context)

    def plan(self, experiments, picks):
        block = step(self.original, "Read X lane switches before preparing any renderer")
        shell = textwrap.dedent(block.split("        run: |\n", 1)[1])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder, key, value in (("experiments", "experiment_publishing_enabled", experiments),
                                       ("article-picks", "enabled", picks)):
                path = root / "social-bot" / folder / "config.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({key: value}))
            output = root / "output"
            result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", shell],
                                    cwd=root, env={**os.environ, "GITHUB_OUTPUT": str(output)},
                                    capture_output=True, text=True)
            flags = dict(line.split("=", 1) for line in output.read_text().splitlines()) if output.exists() else {}
            return result, flags

    def test_paused_experiments_skip_renderer_and_submission_but_keep_article_picks(self):
        result, flags = self.plan(False, True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for event in ("schedule", "push", "workflow_dispatch"):
            with self.subTest(event=event):
                for name in ("Install free local illustration renderer (no AI API)",
                             "Render original X illustrations without revising old assets",
                             "Check visual content, dimensions and image mutation safety",
                             "Host newly generated experiment PNGs on main before queueing",
                             "Refill seven daily original X Buffer slots safely"):
                    self.assertFalse(self.gate(name, event=event, flags=flags), name)
                self.assertTrue(self.gate("Fill any remaining free Buffer slot with a measured-interest article",
                                          event=event, flags=flags))
        self.assertIn('cron: "5 3,15 * * *"', self.original)

    def test_pr_validation_remains_offline_even_with_enabled_lanes(self):
        for enabled in (False, True):
            result, flags = self.plan(enabled, enabled)
            self.assertEqual(result.returncode, 0, result.stderr)
            context = dict(event="pull_request", ref="refs/pull/99/merge", flags=flags)
            self.assertTrue(self.gate("Install free local illustration renderer (no AI API)", **context))
            self.assertTrue(self.gate("Check visual content, dimensions and image mutation safety", **context))
            for name in ("Host newly generated experiment PNGs on main before queueing",
                         "Refill seven daily original X Buffer slots safely",
                         "Fill any remaining free Buffer slot with a measured-interest article",
                         "Record exact Buffer submission IDs (also after partial API failure)"):
                self.assertFalse(self.gate(name, **context), name)

    def test_invalid_switches_fail_before_emitting_any_enabled_lane(self):
        for experiments, picks in (("false", True), (False, 1), (None, False)):
            with self.subTest(experiments=experiments, picks=picks):
                result, flags = self.plan(experiments, picks)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(flags, {})
        result, flags = self.plan(False, False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.gate("Fill any remaining free Buffer slot with a measured-interest article", flags=flags))

    def test_prs_cannot_displace_other_workflows_or_production_submissions(self):
        names = ("x-original-experiments.yml", "x-useful-originals.yml", "x-midday-original.yml",
                 "free-surreal-x.yml", "hold-nejimachi-quality.yml", "migrate-legacy-buffer.yml")
        validation_groups = set()
        for name in names:
            document = (WORKFLOWS / name).read_text()
            block = re.search(r"(?ms)^concurrency:\n(.*?)(?=^\S)", document).group(1)
            group = re.search(r"^  group: (.*)$", block, re.M).group(1)
            self.assertIn("queue: max", block, name)
            self.assertIn("cancel-in-progress: false", block, name)
            self.assertEqual(expression(group, event="workflow_dispatch", workflow=name), "free-surreal-x")
            for ref in ("refs/pull/98/merge", "refs/pull/99/merge"):
                pr_group = expression(group, event="pull_request", ref=ref, workflow=name)
                self.assertNotEqual(pr_group, "free-surreal-x", name)
                self.assertNotIn(pr_group, validation_groups, name)
                validation_groups.add(pr_group)
        remaster = (WORKFLOWS / "remaster-queued-nejimachi.yml").read_text()
        self.assertIn("group: free-surreal-x\n  queue: max\n  cancel-in-progress: false", remaster)

    def test_completed_migrations_cannot_replay_buffer_operations_on_push(self):
        mutations = {
            "hold-nejimachi-quality.yml": ("Move only the two exact unreviewed manga previews to Buffer drafts",
                                          "Preserve hold audit in Git history"),
            "migrate-legacy-buffer.yml": ("Preserve only two old setup posts as Buffer drafts (not deleted)",
                                         "Fill queue with up to two canonical Nejimachi episodes",
                                         "Save queue ledger and reversible migration audit"),
        }
        for filename, steps in mutations.items():
            document = (WORKFLOWS / filename).read_text()
            for name in steps:
                condition = re.search(r"^        if: (.*)$", step(document, name), re.M).group(1)
                for event in ("push", "pull_request", "schedule"):
                    self.assertFalse(expression(condition, event=event), (filename, name, event))
                self.assertTrue(expression(condition, event="workflow_dispatch"))
                self.assertFalse(expression(condition, event="workflow_dispatch", ref="refs/heads/preview"))


if __name__ == "__main__":
    unittest.main()
