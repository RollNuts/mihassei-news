"""The shared read-only snapshot must be fresh and successfully built."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import site_build_fixture as fixture


class SiteBuildFixtureTests(unittest.TestCase):
    def test_existing_dist_still_builds_once_for_all_readonly_consumers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "dist").mkdir()
            (root / "dist" / "stale.html").write_text("Old output is not proof of a fresh build.")
            with patch.object(fixture, "ROOT", root), \
                 patch.object(fixture, "_built", False), \
                 patch.object(fixture.subprocess, "run") as build:
                for _ in range(6):
                    fixture.ensure_site_build()
                build.assert_called_once_with(
                    [sys.executable, str(root / "scripts/build.py")], cwd=root, check=True)

    def test_failed_build_is_not_reused_as_a_successful_snapshot(self):
        failure = subprocess.CalledProcessError(1, ["test-build"])
        with patch.object(fixture, "_built", False), \
             patch.object(fixture.subprocess, "run", side_effect=[failure, None]) as build:
            with self.assertRaises(subprocess.CalledProcessError):
                fixture.ensure_site_build()
            self.assertFalse(fixture._built)
            fixture.ensure_site_build()
            fixture.ensure_site_build()
            self.assertEqual(build.call_count, 2)
            self.assertTrue(fixture._built)


if __name__ == "__main__":
    unittest.main()
