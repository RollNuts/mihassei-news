"""Offline, source-bound live verification of one latest market bulletin.

Production checks reuse these contracts with real GETs; no engagement writes,
paid services or extra GitHub workflows are needed.
"""
import hashlib
import unittest
from pathlib import Path

from scripts import build
from scripts.check_live import verify_latest_market_bulletin_delivery
from scripts.check_share_cards import candidate_paths, inspect_markup
from scripts.market_bulletins import (
    load_market_bulletins, bulletin_url, chart_html, figure_url,
    market_bulletin_feed_xml, render_market_bulletin, FEED_URL,
)
from scripts.market_figures import render_market_svg

ROOT = Path(__file__).resolve().parents[1]


class LatestMarketLiveDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reports = load_market_bulletins(ROOT)
        cls.latest = cls.reports[-1]
        cls.url = bulletin_url(cls.latest)

    def fixture(self):
        """Same static page bytes as the generator; SVGs include real cache keys."""
        document = render_market_bulletin(self.latest, build.layout)
        files = {
            self.url: (document.encode("utf-8"), "text/html; charset=utf-8"),
            FEED_URL: (market_bulletin_feed_xml(self.reports).encode("utf-8"),
                       "application/rss+xml"),
        }
        for figure in self.latest["figures"]:
            self.assertEqual(document.count(chart_html(self.latest, figure)), 1)
            for mobile in (False, True):
                svg = render_market_svg(figure, mobile=mobile).encode("utf-8")
                asset = "/" + figure_url(self.latest, figure, mobile=mobile)
                path = asset + "?v=" + hashlib.sha256(svg).hexdigest()[:12]
                files[path] = (svg, "image/svg+xml")
        return files

    def test_real_report_and_its_two_formats_of_every_svg_pass(self):
        files = self.fixture()
        paths = []
        def read(path):
            paths.append(path)
            return files[path]
        self.assertEqual(verify_latest_market_bulletin_delivery(self.reports, fetch=read),
                         len(self.latest["figures"]))
        self.assertEqual(set(paths), set(files))
        self.assertEqual(len(paths), 2 + len(self.latest["figures"]) * 2)

    def test_stale_mobile_svg_and_modified_rss_fail_closed(self):
        files = self.fixture()
        mobile = next(path for path in files if path.endswith(".svg") is False
                      and "-mobile.svg?v=" in path)
        files[mobile] = (b"<svg>stale bytes</svg>", "image/svg+xml")
        with self.assertRaisesRegex(RuntimeError, "published market SVG bytes differ"):
            verify_latest_market_bulletin_delivery(self.reports, fetch=files.__getitem__)
        files = self.fixture()
        files[FEED_URL] = (b"<rss><channel/></rss>", "application/rss+xml")
        with self.assertRaisesRegex(RuntimeError, "RSS differs"):
            verify_latest_market_bulletin_delivery(self.reports, fetch=files.__getitem__)

    def test_missing_mobile_picture_or_changed_share_metadata_is_detected(self):
        files = self.fixture()
        page, mime = files[self.url]
        files[self.url] = (page.replace(b'<source media="(max-width:600px)"',
                                        b'<source media="(max-width:599px)"', 1), mime)
        with self.assertRaisesRegex(RuntimeError, "market figure or its source/time/phone"):
            verify_latest_market_bulletin_delivery(self.reports, fetch=files.__getitem__)
        files[self.url] = (page.replace(b'<meta name="twitter:image" content="https://veripsa.com/',
                                        b'<meta name="twitter:image" content="https://other.example/', 1), mime)
        with self.assertRaisesRegex(RuntimeError, "HTML/OGP mismatch"):
            verify_latest_market_bulletin_delivery(self.reports, fetch=files.__getitem__)

    def test_latest_bulletin_is_in_real_twitterbot_candidates(self):
        self.assertIn(self.url, candidate_paths(ROOT))
        page = render_market_bulletin(self.latest, build.layout)
        image, mime = inspect_markup(page, self.url)
        self.assertEqual(image, "https://veripsa.com/og/serials/market-lab.png")
        self.assertEqual(mime, "image/png")


if __name__ == "__main__":
    unittest.main()
