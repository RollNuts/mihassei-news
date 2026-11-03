"""Fail-closed control for the free automatic MP4 workflow (no network)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
