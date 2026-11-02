"""The production workflow must verify actual published bytes, not an optional app check."""
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class DeploymentWorkflowTests(unittest.TestCase):
    def test_live_release_waits_for_real_public_commit(self):
        markup=(ROOT/".github/workflows/verify-public-photos.yml").read_text(encoding="utf-8")
        self.assertIn("concurrency:",markup)
        self.assertIn("cancel-in-progress: true",markup)
        self.assertIn('EXPECTED_SHA: ${{ github.sha }}',markup)
        self.assertIn('https://veripsa.com/build-revision.txt?release=',markup)
        self.assertIn('if latest == expected:',markup)
        self.assertIn("python3 scripts/check_live.py",markup)
        self.assertIn("python3 scripts/check_share_cards.py",markup)
        self.assertNotIn("Workers Builds: mihassei-news",markup)
        self.assertNotIn("GH_TOKEN:",markup)
        self.assertIn("read-only",markup.lower())

if __name__=="__main__":
    unittest.main()
