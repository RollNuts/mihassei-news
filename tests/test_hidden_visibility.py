"""The public newsroom must not expose placeholder stats before API verification.

No browser tracking or additional requests are needed: the HTML hidden
attribute and a CSS guard provide the safe initial state.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HiddenVisibilityTests(unittest.TestCase):
    def test_hidden_components_remain_invisible_before_real_data_arrives(self):
        build = (ROOT / "scripts" / "build.py").read_text(encoding="utf-8")
        css = (ROOT / "assets" / "editorial-polish.css").read_text(encoding="utf-8")
        js = (ROOT / "assets" / "engagement.js").read_text(encoding="utf-8")
        for marker in (
            'data-observer-widget hidden',
            'data-popular-section',
        ):
            self.assertIn(marker, build)
        self.assertIn('aria-labelledby="readers-choice-title" hidden', build)
        self.assertRegex(css, r"body\s+\[hidden\]\s*\{\s*display:\s*none\s*;")
        self.assertIn('observerShell.hidden=false', js)
        self.assertIn('popularSection.hidden=false', js)
        # No fake traffic is generated just to fill an empty widget.
        self.assertIn('tracking.unit!=="page-view"', js)
        self.assertIn('showPopular(data,pvHealthy)', js)

    def test_visibility_fix_does_not_need_new_api_calls_or_paid_assets(self):
        css = (ROOT / "assets" / "editorial-polish.css").read_text(encoding="utf-8")
        self.assertEqual(css.count("{"), css.count("}"))
        self.assertNotIn("@import", css)
        self.assertNotIn("!important", css.split("Hidden means hidden, even when", 1)[-1])


if __name__ == "__main__":
    unittest.main()

