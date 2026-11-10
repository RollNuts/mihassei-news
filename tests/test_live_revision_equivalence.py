"""Prove docs-only deployment equivalence with real Git history, without HTTP."""
from __future__ import annotations

import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts import check_live


class PublicRevisionEquivalenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.write("content/articles.json", "original article\n")
        self.write("docs/OPERATIONS.md", "original instructions\n")
        self.original = self.commit("original")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=5).stdout.decode().strip()

    def write(self, path, body):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def matches(self, expected, actual):
        return check_live.public_revision_matches(expected, actual, root=self.root)

    def test_exact_revision_and_multiple_docs_only_descendants(self):
        self.assertTrue(self.matches(self.original, self.original))
        self.write("docs/OPERATIONS.md", "revised instructions\n")
        self.commit("docs one")
        self.write("docs/nested/review\nnotes.md", "review instructions\n")
        expected = self.commit("docs two")
        self.assertTrue(self.matches(expected, self.original))

    def test_public_input_change_cannot_hide_behind_final_docs_commit(self):
        for path in ("content/articles.json", "assets/generated/new.jpg", "scripts/build.py",
                     "README.md", ".github/workflows/verify-public-photos.yml"):
            with self.subTest(path=path):
                self.git("reset", "--hard", self.original)
                self.write(path, "new public input\n")
                self.commit("public input")
                self.write("docs/OPERATIONS.md", "new instructions\n")
                expected = self.commit("docs after public input")
                self.assertFalse(self.matches(expected, self.original))

    def test_docs_renames_deletions_are_allowed_but_move_outside_docs_is_not(self):
        self.git("mv", "docs/OPERATIONS.md", "docs/ARCHIVE.md")
        docs_rename = self.commit("rename within docs")
        self.assertTrue(self.matches(docs_rename, self.original))
        self.git("mv", "docs/ARCHIVE.md", "reader-guide.md")
        outside = self.commit("move outside docs")
        self.assertFalse(self.matches(outside, docs_rename))
        self.assertFalse(self.matches(outside, self.original))

    def test_newer_public_commit_and_diverged_history_are_not_equivalent(self):
        self.write("docs/OPERATIONS.md", "new instructions\n")
        newer = self.commit("docs descendant")
        self.assertFalse(self.matches(self.original, newer))
        self.git("checkout", "--detach", self.original)
        self.write("docs/OPERATIONS.md", "different instructions\n")
        diverged = self.commit("other branch")
        self.assertFalse(self.matches(newer, diverged))

    def test_empty_diff_unknown_sha_invalid_sha_and_noncommit_are_not_proof(self):
        self.git("commit", "--allow-empty", "-q", "-m", "empty commit")
        self.assertFalse(self.matches(self.git("rev-parse", "HEAD"), self.original))
        for invalid in ("a" * 40, "HEAD", "x" * 40, self.original.upper(), None):
            with self.subTest(invalid=invalid):
                self.assertFalse(self.matches(invalid, self.original))
                self.assertFalse(self.matches(self.original, invalid))
        self.git("tag", "-a", "public-tag", "-m", "not a commit SHA", self.original)
        tag_object = self.git("rev-parse", "public-tag")
        self.write("docs/OPERATIONS.md", "new instructions\n")
        expected = self.commit("docs")
        self.assertFalse(self.matches(expected, tag_object))

    def test_git_failure_or_incomplete_history_fails_closed(self):
        self.write("docs/OPERATIONS.md", "new instructions\n")
        expected = self.commit("docs")
        for failure in (FileNotFoundError("git unavailable"),
                        subprocess.TimeoutExpired("git", 5)):
            with self.subTest(failure=type(failure).__name__), \
                    patch.object(check_live.subprocess, "run", side_effect=failure):
                self.assertFalse(self.matches(expected, self.original))
        with patch.object(check_live.subprocess, "run", return_value=subprocess.CompletedProcess(
                ["git"], 128, stdout=b"", stderr=b"missing history")):
            self.assertFalse(self.matches(expected, self.original))
        real_run = subprocess.run

        def failed_diff(args, **kwargs):
            if args[1] == "diff":
                return subprocess.CompletedProcess(args, 128, stdout=b"docs/OPERATIONS.md\0")
            return real_run(args, **kwargs)

        with patch.object(check_live.subprocess, "run", side_effect=failed_diff):
            self.assertFalse(self.matches(expected, self.original))

    def test_final_check_reports_actual_public_sha(self):
        self.write("docs/OPERATIONS.md", "new instructions\n")
        expected = self.commit("docs")
        output = io.StringIO()
        with patch.object(check_live, "get", return_value=(self.original.encode(), "text/plain")), \
                redirect_stdout(output):
            actual = check_live.verify_public_revision(root=self.root)
        self.assertEqual(actual, self.original)
        self.assertIn("live=" + self.original, output.getvalue())
        self.assertIn("expected=" + expected, output.getvalue())
        self.assertIn("docs-only difference", output.getvalue())

    def test_successful_revision_proof_does_not_skip_the_remaining_live_checks(self):
        class ReachedContentCheck(Exception):
            pass

        with patch.object(check_live, "verify_public_revision", return_value=self.original), \
                patch.object(check_live, "latest_articles", side_effect=ReachedContentCheck):
            with self.assertRaises(ReachedContentCheck):
                check_live.check()

    def test_final_check_fails_for_unavailable_marker_or_public_input_change(self):
        self.write("content/articles.json", "changed article\n")
        self.commit("article")
        with patch.object(check_live, "get", return_value=(self.original.encode(), "text/plain")):
            with self.assertRaisesRegex(RuntimeError, "different revision"):
                check_live.verify_public_revision(root=self.root)
        with patch.object(check_live, "get", side_effect=TimeoutError("unavailable")):
            with self.assertRaises(TimeoutError):
                check_live.verify_public_revision(root=self.root)

    def test_failed_head_lookup_cannot_pass_even_with_sha_on_stdout(self):
        failure = subprocess.CompletedProcess(["git", "rev-parse", "HEAD"], 128,
                                              stdout=self.original + "\n")
        with patch.object(check_live.subprocess, "run", return_value=failure), \
                patch.object(check_live, "get") as fetch:
            with self.assertRaisesRegex(RuntimeError, "Cannot determine"):
                check_live.verify_public_revision(root=self.root)
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
