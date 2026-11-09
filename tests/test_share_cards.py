"""Offline checks for first-click reader paths and share-card crawler assets."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from scripts.check_share_cards import candidate_paths, image_dimensions, inspect_markup, png_card_dimensions

ROOT = Path(__file__).resolve().parents[1]


class ShareCardsAndReadingPaths(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([sys.executable, str(ROOT / "scripts/build.py")],
                       cwd=ROOT, check=True)

    def test_twitter_cards_reference_real_first_party_rasters(self):
        candidates = candidate_paths(ROOT)
        self.assertIn("/", candidates)
        self.assertIn("/serials/", candidates)
        self.assertTrue(any(path.startswith("/articles/") for path in candidates))
        self.assertTrue(any(path.startswith("/serials/market-lab/") for path in candidates))
        for path in candidates:
            html_file = ROOT / "dist" / path.lstrip("/") / "index.html"
            self.assertTrue(html_file.is_file(), path)
            markup = html_file.read_text(encoding="utf-8")
            image_url, mime = inspect_markup(markup, path)
            image_file = ROOT / "dist" / urlsplit(image_url).path.lstrip("/")
            self.assertTrue(image_file.is_file(), image_url)
            width, height = image_dimensions(image_file.read_bytes(), mime)
            self.assertGreaterEqual(width, 600)
            self.assertGreaterEqual(height, 315)
            if mime == "image/png":
                self.assertEqual((width, height), png_card_dimensions(image_url))

    def test_social_preview_validation_rejects_missing_or_external_image(self):
        doc = (ROOT / "dist/index.html").read_text(encoding="utf-8")
        inspect_markup(doc, "/")
        altered = doc.replace('property="og:image" content="https://veripsa.com/',
                              'property="og:image" content="https://example.com/', 1)
        with self.assertRaisesRegex(RuntimeError, "images disagree"):
            inspect_markup(altered, "/")
        with self.assertRaisesRegex(RuntimeError, "missing/wrong canonical"):
            inspect_markup(doc, "/articles/unpublished/")

    def test_first_reader_gets_one_measured_recommendation_area(self):
        """Keep real story navigation before a PV panel, without hand-picked ranks."""
        doc = (ROOT / "dist/index.html").read_text(encoding="utf-8")
        self.assertNotIn('class="starter-editorial"', doc)
        self.assertEqual(doc.count('data-popular-section'), 1)
        self.assertLess(doc.index('<section class="hero-grid">'),
                        doc.index('data-popular-section'))
        self.assertLess(doc.index('<section class="section-block">'),
                        doc.index('data-popular-section'))
        self.assertIn('aria-labelledby="readers-choice-title" hidden', doc)
        self.assertIn('data-popular-period>過去7日間のPV順', doc)
        self.assertNotIn('class="reading-ways"', doc)
        # Ordinary serials, genre navigation and RSS must remain available.
        for link in ('href="/archive/"',
                     'href="/serials/case-files/"',
                     'href="/serials/market-lab/"',
                     'href="/feed.xml"'):
            self.assertIn(link, doc)
        css = (ROOT / "dist/assets/editorial-polish.css").read_text(encoding="utf-8")
        for token in (".readers-choice-list", ".readers-choice-item",
                      ".readers-choice-count", "@media(max-width:660px)"):
            self.assertIn(token, css)
        self.assertEqual(css.count("{"), css.count("}"))

    def test_real_market_analysis_does_not_claim_it_is_fictional_news(self):
        data = json.loads((ROOT / "content/serials.json").read_text(encoding="utf-8"))
        for serial in data:
            episode = serial["episodes"][-1]
            page = (ROOT / "dist/serials" / serial["slug"] / episode["slug"] /
                    "index.html").read_text(encoding="utf-8")
            if serial["kind"] == "analysis":
                self.assertIn('class="fiction-mark">市場分析</span>', page)
                self.assertIn("公開資料から、相場の先を考える。", page)
                self.assertNotIn('class="fiction-mark">創作報道</span>', page)
                self.assertNotIn('class="fiction-mark">創作小説</span>', page)
            else:
                self.assertIn('class="fiction-mark">創作小説</span>', page)
        hub = (ROOT / "dist/serials/index.html").read_text(encoding="utf-8")
        self.assertIn('class="fiction-mark">創作と市場分析</span>', hub)


if __name__ == "__main__":
    unittest.main()
