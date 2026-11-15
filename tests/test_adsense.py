"""AdSense connection and disclosure checks without a build or ad requests."""
import json
import re
import unittest
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from scripts import build
from scripts.photo_issue_pages import render_photo_issue
from tests.test_photo_issue_pages import MODEL, issue


class HeadParser(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.in_head = False
        self.scripts = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        if tag == "head":
            self.in_head = True
        elif tag == "script":
            self.scripts.append((dict(attrs), self.in_head))

    def handle_endtag(self, tag):
        if tag == "head":
            self.in_head = False


class AdSenseTests(unittest.TestCase):
    def test_public_pages_connect_once_to_the_ads_txt_publisher_in_head(self):
        publisher = (build.BASE / "ads.txt").read_text(encoding="utf-8").split(",")[1].strip()
        for path in ("/", "/archive/", "/articles/reader-story/", "/photo/weekly/",
                     "/serials/market-lab/", "/serials/market-lab/briefings/",
                     "/serials/market-lab/outlooks/",
                     "/serials/market-lab/briefings/2026-11-16-am/"):
            with self.subTest(path=path):
                markup = build.layout("見出し", "概要", path, "<h1>見出し</h1>")
                scripts = [(attrs, in_head) for attrs, in_head in HeadParser(markup).scripts
                           if "adsbygoogle.js" in attrs.get("src", "")]
                self.assertEqual(len(scripts), 1)
                attrs, in_head = scripts[0]
                self.assertTrue(in_head)
                self.assertIn("async", attrs)
                self.assertEqual(attrs["crossorigin"], "anonymous")
                url = urlsplit(attrs["src"])
                self.assertEqual((url.scheme, url.netloc, url.path), (
                    "https", "pagead2.googlesyndication.com", "/pagead/js/adsbygoogle.js"))
                self.assertEqual(parse_qs(url.query), {"client": ["ca-" + publisher]})
                self.assertIn(f'<meta name="google-adsense-account" content="ca-{publisher}">', markup)
                self.assertNotIn("data-ad-slot=", markup)
                self.assertNotIn("adsbygoogle.push", markup)

    def test_noindex_utility_pages_do_not_request_ads(self):
        for path, options in (("/saved/", {}), ("/404.html", {}),
                              ("/local-preview/", {"indexable": False})):
            with self.subTest(path=path):
                markup = build.layout("見出し", "概要", path, "<h1>見出し</h1>", **options)
                self.assertIn('name="robots" content="noindex,follow"', markup)
                self.assertNotIn("adsbygoogle.js", markup)

    def test_privacy_explains_ad_data_and_separate_reader_controls(self):
        markup = build.privacy()
        for text in ("Google AdSense", "Cookie", "他のサイト", "URLやIPアドレス",
                     "パーソナライズド広告", "広告の設定とは別"):
            self.assertIn(text, markup)
        for url in ("https://www.google.com/settings/ads", "https://www.aboutads.info/choices/",
                    "https://policies.google.com/technologies/partner-sites?hl=ja"):
            self.assertIn(f'href="{url}"', markup)
        self.assertNotIn("広告を掲載する際は", markup)

    def test_weekly_issue_keeps_clean_canonical_real_dates_and_jpeg_metadata(self):
        value = issue()
        markup = render_photo_issue(value, MODEL, build.layout)
        canonical = "https://veripsa.com/photo/weekly/issue-001/"
        self.assertIn(f'<link rel="canonical" href="{canonical}">', markup)
        self.assertIn(f'property="og:url" content="{canonical}">', markup)
        schema = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', markup).group(1))
        self.assertEqual((schema["@type"], schema["genre"]), ("Article", "創作写真コラム"))
        self.assertEqual(schema["url"], canonical)
        self.assertEqual(schema["datePublished"], value["published_at"])
        self.assertEqual(schema["dateModified"], value["published_at"])
        self.assertEqual(schema["image"], [canonical + "01.jpg?v=000000000000"])
        self.assertIn('property="og:image:type" content="image/jpeg"', markup)
        self.assertIn('name="robots" content="max-image-preview:large"', markup)
        self.assertNotIn('content="noindex', markup)


if __name__ == "__main__":
    unittest.main()
