"""Sitemap freshness regression without a site build, media rendering or network."""
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

from scripts import build


class SitemapFreshnessTests(unittest.TestCase):
    def test_home_lastmod_compares_news_photo_and_date_only_serial_instants(self):
        # This UTC photo was released at 22:34 JST, after the 20:01 JST story.
        # Comparing the ISO strings instead incorrectly chooses the story.
        photo_published = "2026-11-15T13:34:21.239318+00:00"
        article = {"slug": "earlier-news", "category": "社会",
                   "published_at": "2026-11-15T20:01:35+09:00"}
        # Existing market-lab episodes use legacy date-only publication values.
        series = {"slug": "market-lab", "episodes": [
            {"slug": "outlook-" + day, "published_at": day}
            for day in ("2026-11-03", "2026-11-08", "2026-11-15")
        ]}
        output = {}
        with patch.multiple(
            build,
            write=Mock(side_effect=lambda path, value: output.__setitem__(path, value)),
            generate_rss=Mock(return_value=""),
            layout=Mock(return_value=""),
            social_image=Mock(return_value="/og/brand.png"),
            load_market_bulletins=Mock(return_value=[]),
            load_market_outlooks=Mock(return_value=[]),
            photo_sitemap_entries=Mock(return_value=[]),
            model_sitemap_entries=Mock(return_value=[]),
        ):
            build.other_files([article], issues={}, serials=[series],
                              photo_issues=[{"published_at": photo_published}])
        namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        entries = ET.fromstring(output["sitemap.xml"]).findall("s:url", namespace)
        dates = {entry.findtext("s:loc", namespaces=namespace):
                 entry.findtext("s:lastmod", namespaces=namespace) for entry in entries}
        self.assertEqual(dates[build.SITE + "/"], photo_published)
        self.assertEqual(dates[build.SITE + "/articles/earlier-news/"], article["published_at"])
        self.assertEqual(dates[build.SITE + "/serials/market-lab/outlook-2026-11-15/"], "2026-11-15")

    def test_date_only_publication_means_jst_midnight(self):
        self.assertEqual(build._sitemap_timestamp("2026-11-15"),
                         datetime(2026, 11, 14, 15, tzinfo=timezone.utc))

    def test_cast_and_market_updates_refresh_only_the_pages_that_show_them(self):
        article = {"slug": "morning-news", "category": "社会",
                   "published_at": "2026-11-20T08:00:00+09:00"}
        post_at = "2026-11-21T11:30:00+00:00"
        market_at = "2026-11-21T16:24:00+09:00"
        issue_at = "2026-11-19T06:00:00+09:00"
        output = {}
        with patch.multiple(
            build,
            write=Mock(side_effect=lambda path, value: output.__setitem__(path, value)),
            generate_rss=Mock(return_value=""),
            layout=Mock(return_value=""),
            social_image=Mock(return_value="/og/brand.png"),
            load_market_bulletins=Mock(return_value=[{"slug": "evening", "published_at": market_at}]),
            market_bulletin_routes=Mock(return_value=[]),
            load_market_outlooks=Mock(return_value=[]),
            photo_sitemap_entries=Mock(return_value=[
                ("/photo/weekly/", issue_at, ""),
                ("/photo/weekly/old-feature/", issue_at, ""),
            ]),
            model_sitemap_entries=Mock(return_value=[]),
        ):
            for posts, expected_home, expected_photo in (
                ([{"modelId": "model-one", "publishedAt": post_at}], post_at, post_at),
                ([], market_at, issue_at),
            ):
                with self.subTest(posts=posts):
                    build.other_files([article], issues={}, serials=[], photo_stories=posts)
                    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
                    dates = {
                        entry.findtext("s:loc", namespaces=ns): entry.findtext("s:lastmod", namespaces=ns)
                        for entry in ET.fromstring(output["sitemap.xml"]).findall("s:url", ns)
                    }
                    self.assertEqual(dates[build.SITE + "/"], expected_home)
                    self.assertEqual(dates[build.SITE + "/photo/weekly/"], expected_photo)
                    self.assertEqual(dates[build.SITE + "/archive/"], article["published_at"])
                    self.assertEqual(dates[build.SITE + "/photo/weekly/old-feature/"], issue_at)


if __name__ == "__main__":
    unittest.main()
