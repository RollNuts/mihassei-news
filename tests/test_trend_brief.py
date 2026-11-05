"""Offline tests for the free Google Trends RSS parser."""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.trend_brief import (
    MAX_RSS_BYTES,
    build_snapshot,
    fetch_bytes,
    main,
    parse_google_trends,
)


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0">
  <channel>
    <item>
      <title>流行語大賞</title>
      <link>https://trends.google.com/trending?geo=JP</link>
      <pubDate>Thu, 05 Nov 2026 08:00:00 +0000</pubDate>
      <ht:approx_traffic>20,000+</ht:approx_traffic>
    </item>
    <item>
      <title>無印良品</title>
      <link>http://invalid.example/</link>
      <pubDate>bad date</pubDate>
      <ht:approx_traffic>50,000+</ht:approx_traffic>
    </item>
  </channel>
</rss>
""".encode("utf-8")


class TrendBriefTests(unittest.TestCase):
    def test_parses_terms_without_external_dependencies(self):
        items = parse_google_trends(RSS)
        self.assertEqual([item["term"] for item in items], ["流行語大賞", "無印良品"])
        self.assertEqual(items[0]["approx_traffic"], "20,000+")
        self.assertTrue(items[0]["published_at_utc"].endswith("+00:00"))

    def test_non_https_item_link_falls_back_to_google_source(self):
        items = parse_google_trends(RSS)
        self.assertTrue(items[1]["url"].startswith("https://trends.google.com/"))
        self.assertIsNone(items[1]["published_at_utc"])

    def test_limit_and_snapshot_source_metadata(self):
        snapshot = build_snapshot(RSS, limit=1)
        self.assertEqual(len(snapshot["items"]), 1)
        self.assertEqual(snapshot["source"]["source_kind"], "google_trends")
        self.assertEqual(snapshot["source"]["cost"], "free")
        self.assertIn("Verify the underlying event", snapshot["source"]["note"])

    def test_empty_feed_is_not_reported_as_a_successful_snapshot(self):
        with self.assertRaisesRegex(ValueError, "no usable trend items"):
            build_snapshot(b"<rss><channel></channel></rss>")

    def test_fetch_refuses_an_unbounded_rss_payload(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size):
                self.assert_size = size
                return b"x" * (MAX_RSS_BYTES + 1)

        response = Response()
        with patch("scripts.trend_brief.urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "2 MiB"):
                fetch_bytes("https://trends.google.com/trending/rss?geo=JP")
        self.assertEqual(response.assert_size, MAX_RSS_BYTES + 1)

    def test_cli_returns_clean_nonzero_for_bad_xml(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.xml"
            path.write_text("<rss><broken>", encoding="utf-8")
            error = io.StringIO()
            with patch.object(sys, "argv", ["trend_brief.py", "--input", str(path)]), \
                 contextlib.redirect_stderr(error):
                self.assertEqual(main(), 1)
        self.assertIn("Trend snapshot unavailable", error.getvalue())



if __name__ == "__main__":
    unittest.main()
