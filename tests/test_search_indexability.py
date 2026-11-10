"""Offline SEO regression checks for crawlable URLs and image discovery.

Runs under the existing GitHub Actions tests, with no Google API or paid tools.
"""
import json
import re
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET
from tests.site_build_fixture import ensure_site_build

ROOT = Path(__file__).resolve().parents[1]
SITE = "https://veripsa.com"
NS = {
    "s": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "image": "http://www.google.com/schemas/sitemap-image/1.1",
}


class SearchIndexabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_site_build()
        cls.urls = ET.parse(ROOT / "dist/sitemap.xml").getroot().findall("s:url", NS)
        cls.articles = json.loads((ROOT / "content/articles.json").read_text(encoding="utf-8"))

    def test_sitemap_urls_are_unique_canonical_indexable_pages(self):
        """No missing, duplicate or noindex pages may enter the submitted sitemap."""
        self.assertGreaterEqual(len(self.urls), len(self.articles) + 1)
        seen = set()
        for entry in self.urls:
            url = entry.findtext("s:loc", namespaces=NS)
            with self.subTest(url=url):
                parsed = urlsplit(url or "")
                self.assertEqual((parsed.scheme, parsed.netloc), ("https", "veripsa.com"))
                self.assertTrue(parsed.path.endswith("/"), url)
                self.assertFalse(parsed.query or parsed.fragment, url)
                self.assertNotIn(url, seen)
                seen.add(url)
                html_path = ROOT / "dist" / unquote(parsed.path).lstrip("/") / "index.html"
                self.assertTrue(html_path.is_file(), str(html_path))
                page = html_path.read_text(encoding="utf-8")
                self.assertIn(f'<link rel="canonical" href="{url}">', page)
                self.assertNotRegex(page, r'<meta\s+name="robots"\s+content="[^"]*noindex')
                self.assertIn('<meta name="robots" content="max-image-preview:large">', page)
                self.assertIn('<meta name="description" content="', page)
                self.assertIn("<title>", page)
                match = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
                self.assertIsNotNone(match)
                schema = json.loads(match.group(1))
                primary = schema[0] if isinstance(schema, list) else schema
                self.assertEqual(primary["url"], url)
        for article in self.articles:
            self.assertIn(f'{SITE}/articles/{article["slug"]}/', seen)
        for excluded in ("/saved/", "/404.html", "/image-review/", "/share-desk/"):
            self.assertNotIn(SITE + excluded, seen)

    def test_image_sitemap_uses_existing_first_party_raster_cards(self):
        """Each article, edition and serial exposes its own public raster preview."""
        from scripts.build import daily_issues, issue_url, article_url, social_image
        from scripts.serials import load_serials, series_url, episode_url

        published = sorted(self.articles, key=lambda a: (
            a.get("published_at") or a["source"]["published_at"],
            a["source"]["published_at"]), reverse=True)
        expected = {SITE + article_url(article): SITE + social_image(article)
                    for article in published}
        expected.update({SITE + issue_url(day): SITE + f"/og/issues/{day}.png"
                         for day in daily_issues(published)})
        for series in load_serials(ROOT):
            expected[SITE + series_url(series)] = SITE + f'/og/serials/{series["slug"]}.png'
            for episode in series["episodes"]:
                expected[SITE + episode_url(series, episode)] = (
                    SITE + f'/og/serials/{series["slug"]}-{episode["slug"]}.png')

        actual = {}
        for entry in self.urls:
            page_url = entry.findtext("s:loc", namespaces=NS)
            pictures = entry.findall("image:image", NS)
            if page_url not in expected:
                self.assertEqual(pictures, [], page_url)
                continue
            with self.subTest(url=page_url):
                self.assertEqual(len(pictures), 1)
                image_url = pictures[0].findtext("image:loc", namespaces=NS)
                self.assertEqual(image_url, expected[page_url])
                parts = urlsplit(image_url)
                self.assertEqual((parts.scheme, parts.netloc), ("https", "veripsa.com"))
                self.assertTrue(parts.path.endswith((".png", ".jpg")))
                image_file = ROOT / "dist" / parts.path.lstrip("/")
                self.assertTrue(image_file.is_file(), str(image_file))
                self.assertGreater(image_file.stat().st_size, 1024)
                actual[page_url] = image_url
        self.assertEqual(actual, expected)

    def test_robots_advertises_sitemap_and_allows_search(self):
        robots = (ROOT / "dist/robots.txt").read_text(encoding="utf-8")
        self.assertIn("User-agent: *", robots)
        self.assertIn("Sitemap: https://veripsa.com/sitemap.xml", robots)
        self.assertNotIn("Disallow: /", robots.splitlines())


if __name__ == "__main__":
    unittest.main()
