"""Offline checks for first-click reader paths and share-card crawler assets."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from scripts.check_share_cards import candidate_paths, image_dimensions, inspect_markup

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
                self.assertEqual((width, height), (1200, 630))

    def test_social_preview_validation_rejects_missing_or_external_image(self):
        doc = (ROOT / "dist/index.html").read_text(encoding="utf-8")
        inspect_markup(doc, "/")
        altered = doc.replace('property="og:image" content="https://veripsa.com/',
                              'property="og:image" content="https://example.com/', 1)
        with self.assertRaisesRegex(RuntimeError, "images disagree"):
            inspect_markup(altered, "/")
        with self.assertRaisesRegex(RuntimeError, "missing/wrong canonical"):
            inspect_markup(doc, "/articles/unpublished/")

    def test_first_reader_has_three_true_distinct_paths(self):
        doc = (ROOT / "dist/index.html").read_text(encoding="utf-8")
        self.assertIn('class="reading-ways"', doc)
        self.assertLess(doc.index('<section class="hero-grid">'),
                        doc.index('class="reading-ways"'))
        self.assertLess(doc.index('class="reading-ways"'),
                        doc.index('<section class="section-block">'))
        self.assertEqual(doc.count('class="reading-way reading-way--'), 3)
        self.assertIn('href="/archive/"', doc)
        self.assertIn('href="/serials/case-files/"', doc)
        self.assertIn('href="/serials/market-lab/"', doc)
        self.assertIn('FICTION / 架空ニュース', doc)
        self.assertIn('MYSTERY / 創作推理小説', doc)
        self.assertIn('MARKET / 現実の市場分析', doc)
        css = (ROOT / "dist/assets/editorial-polish.css").read_text(encoding="utf-8")
        for token in (".reading-ways-grid", ".reading-way--mystery",
                      ".reading-way--market", "@media(max-width:660px)"):
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
                self.assertIn("公開資料から考える未来", page)
                self.assertNotIn('class="fiction-mark">創作報道</span>', page)
                self.assertNotIn('class="fiction-mark">創作小説</span>', page)
            else:
                self.assertIn('class="fiction-mark">創作小説</span>', page)
        hub = (ROOT / "dist/serials/index.html").read_text(encoding="utf-8")
        self.assertIn('class="fiction-mark">創作と市場分析</span>', hub)


if __name__ == "__main__":
    unittest.main()
