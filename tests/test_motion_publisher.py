"""Fail-closed control for the free automatic MP4 workflow (no network)."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import motion_clips
from scripts import publish_motion_clips as publisher


GOOD = {"version": 1, "enabled": True, "max_per_run": 1, "min_free_gib": 2}
TRUSTED = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_REPOSITORY": "RollNuts/mihassei-news",
    "GITHUB_REF": "refs/heads/main",
}


class MotionPublisherTests(unittest.TestCase):
    def _config(self, path: Path, value: dict):
        folder = path / ".github"
        folder.mkdir(exist_ok=True)
        (folder / "motion-clips.json").write_text(json.dumps(value),
                                                   encoding="utf-8")

    def test_real_configuration_limits_one_video_and_has_explicit_off_switch(self):
        current = publisher.load_settings(publisher.ROOT)
        self.assertEqual(current["version"], 1)
        self.assertEqual(current["max_per_run"], 1)
        self.assertGreaterEqual(current["min_free_gib"], 2)
        self.assertLessEqual(current["min_free_gib"], 12)
        self.assertIs(type(current["enabled"]), bool)

    def test_disabled_mode_never_calls_video_encoder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._config(root, {**GOOD, "enabled": False})
            with patch.object(publisher, "generate") as mocked:
                self.assertEqual(publisher.run(root, env={}), [])
                mocked.assert_not_called()

    def test_pending_count_off_does_not_read_articles_or_encode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._config(root, {**GOOD, "enabled": False})
            # An OFF pipeline must remain idle even without an article file.
            with patch.object(publisher, "candidates") as scan, \
                 patch.object(publisher, "generate") as encode:
                self.assertEqual(publisher.pending_count(root), 0)
                scan.assert_not_called()
                encode.assert_not_called()

    def test_pending_count_reports_empty_or_full_queue_without_encoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._config(root, GOOD)
            with patch.object(publisher, "candidates") as scan, \
                 patch.object(publisher, "generate") as encode:
                for queued in ([], [{"slug": "first-story"}, {"slug": "second-story"}]):
                    with self.subTest(queued=len(queued)):
                        scan.return_value = queued
                        self.assertEqual(publisher.pending_count(root), len(queued))
                # Generation stays limited to one; planning must expose all
                # remaining work so another run can drain the bounded backlog.
                encode.assert_not_called()

    def test_pending_cli_prints_only_count_without_running_publisher(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(publisher, "pending_count", return_value=2), \
             patch.object(publisher, "run") as publish, \
             patch.object(publisher, "generate") as encode, \
             redirect_stdout(stdout), redirect_stderr(stderr):
            result = publisher.main(["--pending"])
        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), "2\n")
        self.assertEqual(stderr.getvalue(), "")
        publish.assert_not_called()
        encode.assert_not_called()

    def test_pending_cli_invalid_config_fails_closed_without_scanning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._config(root, {**GOOD, "enabled": "true"})
            inspect_pending = publisher.pending_count
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(publisher, "pending_count",
                              side_effect=lambda: inspect_pending(root)), \
                 patch.object(publisher, "candidates") as scan, \
                 patch.object(publisher, "generate") as encode, \
                 redirect_stdout(stdout), redirect_stderr(stderr):
                result = publisher.main(["--pending"])
            self.assertEqual(result, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("Invalid motion clip", stderr.getvalue())
            scan.assert_not_called()
            encode.assert_not_called()

    def test_pending_plan_counts_only_selected_missing_verified_clips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._config(root, GOOD)
            (root / "content").mkdir()
            article_path = root / "content/articles.json"
            stories = [
                {"slug": "ordinary-story", "broadcast_feature": False},
                {"slug": "first-selected-story", "broadcast_feature": True},
                {"slug": "second-selected-story", "broadcast_feature": True},
                {"slug": "already-published-story", "broadcast_feature": True},
                {"slug": "held-image-story", "broadcast_feature": True},
            ]
            for article in stories:
                article["published_at"] = "2026-11-04T00:00:00+00:00"
            article_path.write_text(json.dumps(stories), encoding="utf-8")
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

            def current_clip(article, *, root):
                if article["slug"] == "already-published-story":
                    return {"motion_style_version": motion_clips.MOTION_STYLE_VERSION}
                return None

            with patch.object(motion_clips, "usable_clip", side_effect=current_clip), \
                 patch.object(motion_clips, "usable_generated_scene",
                              side_effect=lambda article, **_: article["slug"] != "held-image-story"), \
                 patch.object(publisher, "generate") as encode:
                # Exercise the real candidate filter, not a mock queue. The
                # article selection and image approval both gate publication.
                self.assertEqual(publisher.pending_count(root), 2)
                encode.assert_not_called()

            after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            self.assertEqual(after, before)

    def test_enabled_mode_refuses_untrusted_context_before_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._config(root, GOOD)
            with patch.object(publisher, "generate") as mocked:
                for env in ({},
                            {**TRUSTED, "GITHUB_REPOSITORY": "someone/other"},
                            {**TRUSTED, "GITHUB_REF": "refs/heads/topic"},
                            {**TRUSTED, "GITHUB_ACTIONS": "false"}):
                    with self.assertRaisesRegex(RuntimeError, "only runs"):
                        publisher.run(root, env=env)
                mocked.assert_not_called()
                mocked.return_value = ["example-story"]
                self.assertEqual(publisher.run(root, env=TRUSTED), ["example-story"])
                mocked.assert_called_once_with(root, limit=1, min_free_gib=2)

    def test_invalid_config_fails_closed_and_does_not_overwrite(self):
        bad = [
            {"version": 1, "enabled": "true", "max_per_run": 1, "min_free_gib": 2},
            {**GOOD, "max_per_run": 100},
            {**GOOD, "max_per_run": 0},
            {**GOOD, "min_free_gib": 0},
            {**GOOD, "min_free_gib": 13},
            {**GOOD, "version": 3},
        ]
        for config in bad:
            with self.subTest(config=config):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    self._config(root, config)
                    with self.assertRaisesRegex(RuntimeError, "Invalid motion clip"):
                        publisher.load_settings(root)
                    self.assertEqual(json.loads((root / ".github/motion-clips.json").read_text()),
                                     config)

    def test_symlinked_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / ".github"
            folder.mkdir()
            original = root / "settings.json"
            original.write_text(json.dumps(GOOD), encoding="utf-8")
            (folder / "motion-clips.json").symlink_to(original)
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                publisher.load_settings(root)


if __name__ == "__main__":
    unittest.main()
