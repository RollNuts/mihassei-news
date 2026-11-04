"""Regression checks for the event and shell boundaries of video automation."""
from __future__ import annotations

import ast
import fnmatch
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"


def push_paths(document: str) -> list[str]:
    block = re.search(r"(?ms)^  push:\n(.*?)(?=^  \w|^permissions:)", document).group(1)
    inline = re.search(r"^    paths: (\[.*\])$", block, re.M)
    if inline:
        return ast.literal_eval(inline.group(1))
    return re.findall(r"^      - '([^']+)'$", block, re.M)


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

    def test_selection_and_image_changes_wake_video_but_video_output_cannot_recurse(self):
        filters = push_paths(self.video)
        for path in ("content/articles.json", "assets/generated/chosen-story.jpg",
                     "assets/generated/manifest.json"):
            with self.subTest(path=path):
                self.assertTrue(any(fnmatch.fnmatchcase(path, rule) for rule in filters))
        for path in ("assets/clips/chosen-story.mp4", "assets/clips/manifest.json",
                     "content/broadcasts.json"):
            with self.subTest(path=path):
                self.assertFalse(any(fnmatch.fnmatchcase(path, rule) for rule in filters))
                self.assertFalse(any(fnmatch.fnmatchcase(path, rule)
                                     for rule in push_paths(self.images)))

    def test_image_pass_dispatches_video_even_with_no_images_to_generate(self):
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
            log = root / "calls"
            env = {**os.environ, "PATH": str(root) + os.pathsep + os.environ.get("PATH", ""),
                   "CALL_LOG": str(log), "IMAGE_PENDING": "0"}
            result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", shell(block)],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(log.read_text().splitlines(),
                             ["workflow run auto-motion-clips.yml --ref main"])

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

    def test_no_work_skips_encoders_and_publishing_without_adding_a_timer(self):
        install = step(self.video, "Install only free system FFmpeg on the disposable runner")
        self.assertIn("github.event_name == 'pull_request' || steps.plan.outputs.pending != '0'", install)
        publish = self.video.split("\n  publish:\n", 1)[1]
        self.assertIn("github.ref == 'refs/heads/main'", publish)
        self.assertIn("needs.validate.outputs.pending != '0'", publish)
        self.assertIn("github.event.repository.private == false", self.video)
        self.assertNotRegex(self.video, r"(?m)^  schedule:")
        self.assertIn("cron: '17 * * * *'", self.images)


if __name__ == "__main__":
    unittest.main()
