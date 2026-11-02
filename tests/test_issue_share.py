"""Regression tests for free, share-ready daily editions."""
from __future__ import annotations

import html
import re
import struct
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from scripts.build import daily_issues, home, issue_page, load
from scripts.social_cards import render_issue_card


class IssueShareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles = load()
        cls.issues = daily_issues(cls.articles)
        if not cls.issues:
            raise AssertionError("Expected at least one publishable daily issue")
        cls.day, cls.items = next(iter(cls.issues.items()))

    def test_png_is_original_stable_and_social_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "og.png"
            render_issue_card(self.day, output)
            first = output.read_bytes()
            self.assertTrue(first.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(struct.unpack(">II", first[16:24]), (1200, 630))
            render_issue_card(self.day, output)
            self.assertEqual(first, output.read_bytes())
            self.assertGreater(len(first), 2000)

    def test_bad_or_noncanonical_issue_dates_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for day in ("2026-02-30", "2026-2-3", "unknown"):
                with self.subTest(day=day), self.assertRaises(ValueError):
                    render_issue_card(day, Path(tmp) / "bad.png")

    def test_edition_og_and_canonical_use_real_date(self):
        markup = issue_page(self.day, self.items, self.issues)
        public = "https://veripsa.com/issues/" + self.day + "/"
        cover = "https://veripsa.com/og/issues/" + self.day + ".png"
        self.assertIn('<link rel="canonical" href="' + public + '">', markup)
        self.assertIn('<meta property="og:image" content="' + cover + '">', markup)
        self.assertIn('<meta name="twitter:image" content="' + cover + '">', markup)
        self.assertIn('<meta name="twitter:card" content="summary_large_image">', markup)
        self.assertIn('"@type": "CollectionPage"', markup)
        self.assertIn('"datePublished": "' + self.day + '"', markup)
        self.assertNotIn('"datePublished": "' + self.items[0]["fiction_at"] + '"', markup)

    def test_share_link_is_manual_and_discloses_fiction(self):
        markup = issue_page(self.day, self.items, self.issues)
        self.assertIn('class="issue-share-panel"', markup)
        match = re.search(r'<a class="issue-share-cta" href="([^"]+)"', markup)
        self.assertIsNotNone(match)
        link = html.unescape(match.group(1))
        self.assertEqual(urlparse(link).netloc, "x.com")
        values = parse_qs(urlparse(link).query)
        share_text = values["text"][0]
        target = values["url"][0]
        self.assertIn("フィクション", share_text)
        self.assertIn("未発生新聞", share_text)
        self.assertIn(self.items[0]["title"][:20], share_text)
        self.assertLess(len(share_text), 160)
        self.assertTrue(target.startswith("https://veripsa.com/issues/" + self.day + "/?"))
        self.assertEqual(parse_qs(urlparse(target).query)["utm_campaign"], ["daily_edition"])
        # No silent publishing: this is an X compose link activated by the reader.
        self.assertIn('target="_blank" rel="noopener noreferrer external"', markup)

    def test_home_promotes_latest_dated_share_not_fake_today(self):
        markup = home(self.articles)
        self.assertIn('class="daily-issue-share"', markup)
        self.assertIn('utm_campaign%3Ddaily_edition', markup)
        self.assertIn(self.day, markup)
        self.assertIn("号の見出しをXで紹介", markup)

    def test_mobile_share_actions_are_styled(self):
        root = Path(__file__).resolve().parents[1]
        css = (root / "assets/newsroom.css").read_text(encoding="utf-8")
        for token in (".daily-issue-share", ".issue-share-panel",
                      ".issue-share-cta", "@media(max-width:660px)"):
            self.assertIn(token, css)
        self.assertEqual(css.count("{"), css.count("}"))


if __name__ == "__main__":
    unittest.main()
