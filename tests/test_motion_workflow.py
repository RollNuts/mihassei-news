"""Regression checks for the event and shell boundaries of video automation."""
from __future__ import annotations

import ast
import fnmatch
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"


def event_paths(document: str, event: str) -> list[str]:
    block = re.search(r"(?ms)^  " + re.escape(event) + r":\n(.*?)(?=^  \w|^permissions:)", document).group(1)
    inline = re.search(r"^    paths: (\[.*\])$", block, re.M)
    if inline:
        return ast.literal_eval(inline.group(1))
    return re.findall(r"^      - '([^']+)'$", block, re.M)


def push_paths(document: str) -> list[str]:
    return event_paths(document, "push")


def job(document: str, name: str) -> str:
    return re.search(r"(?ms)^  " + re.escape(name) +
                     r":\n(.*?)(?=^  [\w-]+:\n|\Z)", document).group(1)


def applies(condition: str, values: dict) -> bool:
    """Evaluate only the boolean/comparison subset used by job event gates."""
    expression = re.sub(r"\b(?:github|inputs|needs)(?:\.[A-Za-z_][A-Za-z_0-9]*)+\b",
                        lambda match: repr(values[match.group(0)]), condition)
    expression = expression.replace("&&", " and ").replace("||", " or ")
    expression = re.sub(r"!(?!=)", "not ", expression)
    expression = re.sub(r"\bfalse\b", "False", expression)
    expression = re.sub(r"\btrue\b", "True", expression)
    tree = ast.parse(expression.strip(), mode="eval")
    allowed = (ast.Expression, ast.BoolOp, ast.UnaryOp, ast.Compare, ast.Constant,
               ast.And, ast.Or, ast.Not, ast.Eq, ast.NotEq)
    if not all(isinstance(node, allowed) for node in ast.walk(tree)):
        raise ValueError("Unexpected workflow condition syntax")
    return bool(eval(compile(tree, "<workflow condition>", "eval"), {"__builtins__": {}}))


def step(document: str, name: str) -> str:
    return re.search(r"(?ms)^      - name: " + re.escape(name) +
                     r"\n(.*?)(?=^      - |\Z)", document).group(1)


def shell(block: str) -> str:
    return textwrap.dedent(block.split("        run: |\n", 1)[1])


class MotionWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.video = (WORKFLOWS / "auto-motion-clips.yml").read_text()
        cls.images = (WORKFLOWS / "free-cpu-scenes.yml").read_text()

    def test_selection_review_and_captions_wake_video_but_output_cannot_recurse(self):
        filters = push_paths(self.video)
        for path in ("content/articles.json", "assets/generated/chosen-story.jpg",
                     "assets/generated/manifest.json", "content/visual_reviews.json",
                     "content/broadcasts.json"):
            with self.subTest(path=path):
                self.assertTrue(any(fnmatch.fnmatchcase(path, rule) for rule in filters))
        for path in ("assets/clips/chosen-story.mp4", "assets/clips/manifest.json"):
            with self.subTest(path=path):
                self.assertFalse(any(fnmatch.fnmatchcase(path, rule) for rule in filters))
                self.assertFalse(any(fnmatch.fnmatchcase(path, rule)
                                     for rule in push_paths(self.images)))

    def test_image_pass_only_dispatches_ready_videos_even_with_no_images_to_generate(self):
        block = step(self.images, "Continue selected videos after a successful public image pass")
        condition = re.search(r"^        if: (.*)$", block, re.M).group(1)
        self.assertNotIn("pending", condition)
        self.assertIn("github.ref == 'refs/heads/main'", condition)
        self.assertIn("(github.event_name != 'workflow_dispatch' || inputs.publish)", condition)
        self.assertIn("&& !inputs.retry_held", condition)
        self.assertNotIn("always()", condition)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = root / "gh"
            command.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n')
            command.chmod(0o755)
            python = root / "python3"
            python.write_text(
                '#!/bin/sh\n'
                'if [ "$1" = scripts/publish_motion_clips.py ]; then\n'
                '  printf "%s\\n" "$MOTION_PLAN"\n'
                '  exit "$PLAN_EXIT"\n'
                'fi\n'
                'exec "$REAL_PYTHON" "$@"\n')
            python.chmod(0o755)
            log = root / "calls"
            cases = (
                ({"enabled": True, "pending": 1, "stories": [{"state": "ready"}]}, 0, True),
                ({"enabled": True, "pending": 0, "stories": [{"state": "current"}]}, 0, False),
                ({"enabled": True, "pending": 0, "stories": [{"state": "image_unreviewed"}]}, 0, False),
                ({"enabled": False, "pending": 0, "stories": []}, 0, False),
                ({"enabled": False, "pending": 1, "stories": []}, 0, False),
                ({"enabled": True, "pending": "1", "stories": []}, 0, False),
                ({"enabled": True, "pending": 1, "stories": []}, 1, False),
            )
            for plan, status, dispatch in cases:
                with self.subTest(plan=plan, status=status):
                    log.write_text("")
                    env = {**os.environ, "PATH": str(root) + os.pathsep + os.environ.get("PATH", ""),
                           "CALL_LOG": str(log), "IMAGE_PENDING": "0", "REAL_PYTHON": sys.executable,
                           "MOTION_PLAN": json.dumps(plan), "PLAN_EXIT": str(status)}
                    result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", shell(block)],
                                            env=env, capture_output=True, text=True)
                    invalid = status != 0 or type(plan["pending"]) is not int or (not plan["enabled"] and plan["pending"] > 0)
                    self.assertEqual(result.returncode != 0, invalid, result.stderr)
                    self.assertEqual(log.read_text().splitlines(),
                                     ["workflow run auto-motion-clips.yml --ref main"] if dispatch else [])

    def test_preflight_failure_cannot_be_hidden_as_successful_empty_work(self):
        block = step(self.video, "Check selected video backlog before installing any encoder")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = root / "python3"
            command.write_text('#!/bin/sh\nprintf "%s\\n" "$PLAN_COUNT"\nexit "$PLAN_EXIT"\n')
            command.chmod(0o755)
            output = root / "output"
            for count, status in (("0", "0"), ("2", "0"), ("", "1")):
                with self.subTest(count=count, status=status):
                    output.write_text("")
                    env = {**os.environ,
                           "PATH": str(root) + os.pathsep + os.environ.get("PATH", ""),
                           "GITHUB_OUTPUT": str(output), "PLAN_COUNT": count, "PLAN_EXIT": status}
                    result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", shell(block)],
                                            env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, int(status), result.stderr)
                    self.assertEqual(output.read_text(), "" if status == "1" else f"pending={count}\n")

    def test_pr_updates_cancel_obsolete_motion_validation_but_main_stays_serialized(self):
        block = re.search(r"(?ms)^concurrency:\n(.*?)(?=^jobs:)", self.video).group(1)
        self.assertIn("group: free-motion-clips-v2-${{ github.ref }}", block)
        self.assertIn("cancel-in-progress: ${{ github.event_name == 'pull_request' }}", block)

    def test_no_work_skips_encoders_and_publishing_with_recovery_schedule(self):
        for name in (
            "Install only free system FFmpeg on the disposable runner",
            "Test real multi-shot encoding, legacy clips, source hashes, and OFF switch",
        ):
            condition = re.search(r"(?m)^\s+if: (.+)$", step(self.video, name))
            self.assertIsNotNone(condition, name)
            self.assertEqual(condition.group(1), "github.event_name == 'pull_request'", name)
        publish = self.video.split("\n  publish:\n", 1)[1]
        self.assertIn("github.ref == 'refs/heads/main'", publish)
        self.assertIn("needs.validate.outputs.pending != '0'", publish)
        freshness = step(self.video, "Report newsroom freshness even when the encoder has no work")
        self.assertIn("python3 scripts/publish_motion_clips.py --health", freshness)
        self.assertIn("selection_needed", freshness)
        self.assertIn("awaiting_reviewed_source", freshness)
        self.assertIn("github.event.repository.private == false", self.video)
        self.assertIn("    - cron: '17 0,6,12,18 * * *'", self.video)
        self.assertIn("cron: '17 * * * *'", self.images)

    def test_caster_preview_requires_explicit_manual_public_repository_and_skips_publication(self):
        conditions = {name: re.search(r"^    if: (.+)$", job(self.video, name), re.M).group(1)
                      for name in ("validate", "publish", "news-caster-preview")}
        dispatch = re.search(r"(?ms)^  workflow_dispatch:\n(.*?)(?=^permissions:)", self.video).group(1)
        self.assertEqual(dispatch.count("      caster_preview:"), 1)
        self.assertIn("        type: boolean", dispatch)
        self.assertIn("        default: false", dispatch)
        for event, selected, ref in (
            ("workflow_dispatch", True, "refs/heads/main"),
            ("workflow_dispatch", False, "refs/heads/main"),
            ("workflow_dispatch", True, "refs/heads/preview"),
            ("push", False, "refs/heads/main"),
            ("schedule", False, "refs/heads/main"),
            ("pull_request", False, "refs/pull/1/merge"),
            ("push", True, "refs/heads/main"),
        ):
            with self.subTest(event=event, selected=selected, ref=ref):
                values = {"github.repository": "RollNuts/mihassei-news",
                          "github.event.repository.private": False, "github.event_name": event,
                          "github.ref": ref, "inputs.caster_preview": selected,
                          "needs.validate.outputs.pending": "1"}
                preview = event == "workflow_dispatch" and selected
                self.assertEqual(applies(conditions["news-caster-preview"], values), preview)
                self.assertEqual(applies(conditions["validate"], values), not preview)
                self.assertEqual(applies(conditions["publish"], values),
                                 not preview and event != "pull_request" and ref == "refs/heads/main")
        for repository, private in (("other/repo", False), ("RollNuts/mihassei-news", True)):
            values = {"github.repository": repository, "github.event.repository.private": private,
                      "github.event_name": "workflow_dispatch", "inputs.caster_preview": True}
            with self.subTest(repository=repository, private=private):
                self.assertFalse(applies(conditions["news-caster-preview"], values))
                self.assertFalse(applies(conditions["validate"], values))

    def test_caster_stop_or_unknown_auth_state_blocks_inference_before_any_download(self):
        block = step(self.video, "Generate one manual caster preview only while image automation is active")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gh = root / "gh"
            gh.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$GH_CALL_LOG"\nprintf "%s\\n" "$CPU_STATE"\nexit "$GH_EXIT"\n')
            gh.chmod(0o755)
            python = root / "python"
            python.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$INFERENCE_CALL_LOG"\n')
            python.chmod(0o755)
            gh_log, inference_log = root / "gh-calls", root / "inference-calls"
            for state, status in (("active", 0), ("disabled_manually", 0), ("", 0), ("null", 0), ("active", 17)):
                with self.subTest(state=state, status=status):
                    gh_log.write_text("")
                    inference_log.write_text("")
                    env = {**os.environ, "PATH": str(root) + os.pathsep + os.environ.get("PATH", ""),
                           "GITHUB_REPOSITORY": "RollNuts/mihassei-news", "RUNNER_TEMP": str(root),
                           "GH_CALL_LOG": str(gh_log), "INFERENCE_CALL_LOG": str(inference_log),
                           "CPU_STATE": state, "GH_EXIT": str(status)}
                    result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", shell(block)],
                                            env=env, capture_output=True, text=True)
                    allowed = state == "active" and status == 0
                    self.assertEqual(result.returncode == 0, allowed, result.stderr)
                    self.assertEqual(gh_log.read_text().splitlines(),
                                     ["api repos/RollNuts/mihassei-news/actions/workflows/free-cpu-scenes.yml --jq .state"])
                    expected = ("scripts/news_caster_preview.py --run --portrait assets/casters/newsreader-preview.jpg "
                                "--portrait-receipt assets/casters/newsreader-preview.json "
                                "--output " + str(root / "news-caster-preview"))
                    self.assertEqual(inference_log.read_text().splitlines(), [expected] if allowed else [])

    def test_caster_preview_is_cpu_read_only_and_artifact_paths_cannot_include_models(self):
        preview = job(self.video, "news-caster-preview")
        permissions = re.search(r"(?ms)^    permissions:\n(.*?)(?=^    \w)", preview).group(1)
        self.assertEqual(dict(re.findall(r"^      (\w+): (\w+)$", permissions, re.M)),
                         {"contents": "read", "actions": "read"})
        self.assertIn("    runs-on: ubuntu-22.04", preview)
        self.assertIn("    timeout-minutes: 15", preview)
        self.assertIn("          python-version: '3.10'", preview)
        self.assertIn("      GH_TOKEN: ${{ github.token }}", preview)
        self.assertIn("NEWS_CASTER_PUBLIC_REPOSITORY: ${{ github.event.repository.private == false", preview)
        self.assertIn("          persist-credentials: false", preview)
        for forbidden in ("git push", "--generate", "assets/clips", "actions: write", "contents: write"):
            self.assertNotIn(forbidden, preview)
        artifact = step(self.video, "Keep only bounded preview media and its receipt for one day")
        self.assertIn("        uses: actions/upload-artifact@v4", artifact)
        self.assertIn("          retention-days: 1", artifact)
        paths = re.findall(r"^            (.+)$", artifact, re.M)
        self.assertEqual(len(paths), 4)
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue(path.startswith("${{ runner.temp }}/news-caster-preview/"))
                self.assertIn(path.rsplit("/", 1)[1], {"presenter-preview.mp4", "narration-full.wav",
                                                      "narration-three-seconds.wav", "receipt.json"})
                self.assertNotIn("*", path)

    def test_caster_changes_receive_offline_pr_validation_without_running_inference(self):
        filters = event_paths(self.video, "pull_request")
        for path in ("scripts/news_caster_preview.py", "tests/test_news_caster_preview.py",
                     "assets/casters/newsreader-preview.jpg", "assets/casters/newsreader-preview.json"):
            with self.subTest(path=path):
                self.assertTrue(any(fnmatch.fnmatchcase(path, rule) for rule in filters))
        block = step(self.video, "Test caster preview guards offline without downloading models")
        condition = re.search(r"^        if: (.+)$", block, re.M).group(1)
        for event in ("pull_request", "push", "schedule", "workflow_dispatch"):
            self.assertEqual(applies(condition, {"github.event_name": event}), event == "pull_request")
        self.assertIn("run: python3 -m unittest -v tests.test_news_caster_preview", block)
        self.assertNotIn("--run", block)


if __name__ == "__main__":
    unittest.main()
