"""Offline checks for first-click reader paths and share-card crawler assets."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from scripts.check_share_cards import candidate_paths, image_dimensions, inspect_markup, png_card_dimensions
from tests.site_build_fixture import ensure_site_build

ROOT = Path(__file__).resolve().parents[1]


class WeeklyShareCardContracts(unittest.TestCase):
    @staticmethod
    def markup(path, image, kind="article"):
        metadata = {
            "og:url": "https://veripsa.com" + path, "og:type": kind,
            "og:title": "Weekly photo", "og:description": "Photo issue",
            "og:image": image, "og:image:secure_url": image,
            "og:image:alt": "Photo", "og:image:type": "image/jpeg",
            "twitter:title": "Weekly photo", "twitter:description": "Photo issue",
            "twitter:image": image, "twitter:image:alt": "Photo",
            "twitter:card": "summary_large_image",
        }
        return ('<link rel="canonical" href="https://veripsa.com' + path + '">' +
                "".join(f'<meta property="{key}" content="{value}">' for key, value in metadata.items()))

    def test_weekly_images_and_page_types_are_strict(self):
        path = "/photo/weekly/winter-coat-swimsuit-saya-001/"
        base = "https://veripsa.com" + path
        for cut in ("01", "10"):
            image = base + cut + ".jpg?v=012345abcdef"
            self.assertEqual(inspect_markup(self.markup(path, image), path), (image, "image/jpeg"))
        for image in (base + "00.jpg?v=012345abcdef", base + "11.jpg?v=012345abcdef",
                      base + "1.jpg?v=012345abcdef", base + "01.png?v=012345abcdef",
                      base + "nested/01.jpg?v=012345abcdef", base + "../01.jpg?v=012345abcdef",
                      base + "01.jpg", base + "01.jpg?v=bad",
                      base + "01.jpg?v=012345abcdef&v=012345abcdef",
                      base + "01.jpg?v=012345abcdef#fragment"):
            with self.subTest(image=image), self.assertRaisesRegex(RuntimeError, "first-party raster"):
                inspect_markup(self.markup(path, image), path)
        image = base + "01.jpg?v=012345abcdef"
        with self.assertRaisesRegex(RuntimeError, "must advertise article type"):
            inspect_markup(self.markup(path, image, "website"), path)
        index = "/photo/weekly/"
        inspect_markup(self.markup(index, image, "website"), index)
        with self.assertRaisesRegex(RuntimeError, "collection must not advertise article type"):
            inspect_markup(self.markup(index, image), index)

    def test_candidates_include_only_validated_latest_published_photo_issue(self):
        published = {"slug": "released-photo", "state": "published", "published_at": "2026-11-14T07:00:00+00:00"}
        older = {"slug": "older-photo", "state": "published", "published_at": "2026-11-13T07:00:00+00:00"}
        draft = {"slug": "unfinished-photo", "state": "draft", "published_at": "2031-11-14T07:00:00+00:00"}
        catalog = {"issues": [older, draft, published]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "content").mkdir()
            for filename in ("articles.json", "serials.json"):
                (root / "content" / filename).write_text("[]", encoding="utf-8")
            with patch("scripts.check_share_cards.load_photo_issues", return_value=catalog), \
                    patch("scripts.photo_issues.validate_published_issue", side_effect=lambda issue, **kwargs: issue) as gate:
                self.assertEqual(candidate_paths(root), ["/", "/photo/weekly/", "/photo/weekly/released-photo/"])
                self.assertEqual([call.args[0] for call in gate.call_args_list], [older, published])
            with patch("scripts.check_share_cards.load_photo_issues", return_value={"issues": [draft]}):
                self.assertEqual(candidate_paths(root), ["/"])


class ShareCardsAndReadingPaths(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_site_build()

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
        # The gravure corner, market analysis, genre navigation and RSS remain available.
        for link in ('href="/archive/"',
                     'href="/serials/market-lab/"',
                     'href="/feed.xml"'):
            self.assertIn(link, doc)
        self.assertIn("グラビア誌", doc)
        self.assertNotIn('href="/serials/case-files/"', doc)
        css = (ROOT / "dist/assets/editorial-polish.css").read_text(encoding="utf-8")
        for token in (".readers-choice-list", ".readers-choice-item",
                      ".readers-choice-count", "@media(max-width:660px)"):
            self.assertIn(token, css)
        self.assertEqual(css.count("{"), css.count("}"))

    def test_real_market_analysis_does_not_claim_it_is_fictional_news(self):
        data = json.loads((ROOT / "content/serials.json").read_text(encoding="utf-8"))
        for serial in data:
            if serial["slug"] == "case-files":
                self.assertFalse((ROOT / "dist/serials" / serial["slug"]).exists())
                continue
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
        self.assertIn('class="fiction-mark">市場分析</span>', hub)


if __name__ == "__main__":
    unittest.main()
