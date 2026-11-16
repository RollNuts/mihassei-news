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
    @classmethod
    def setUpClass(cls):
        cls.article = build.load()[0]
        cls.analysis = {
            "slug": "market-lab", "kind": "analysis", "episode": "sample-analysis",
            "published_at": "2026-11-16T08:30:00+09:00",
        }

    def body_cases(self):
        return (
            (build.article_url(self.article), {"kind": "story", "article": self.article}),
            ("/serials/market-lab/sample-analysis/",
             {"kind": "serial_episode", "serial": self.analysis}),
            ("/serials/market-lab/briefings/sample-analysis/",
             {"kind": "market_bulletin", "serial": self.analysis}),
        )

    def assert_account_meta(self, markup):
        self.assertEqual(markup.count(
            '<meta name="google-adsense-account" content="ca-pub-6754575901141756">'), 1)

    def test_body_pages_connect_once_to_the_ads_txt_publisher_in_head(self):
        publisher = (build.BASE / "ads.txt").read_text(encoding="utf-8").split(",")[1].strip()
        for path, options in self.body_cases():
            with self.subTest(path=path):
                markup = build.layout("見出し", "概要", path, "<h1>見出し</h1>", **options)
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

    def test_indexable_non_body_pages_keep_verification_without_requesting_ads(self):
        cases = (
            ("/", {}), ("/archive/", {}), ("/topics/", {}), ("/topics/AI/", {}),
            ("/category/technology/", {}), ("/issues/", {}), ("/issues/2026-11-16/", {}),
            ("/about/", {}), ("/editorial/", {}), ("/privacy/", {}), ("/advertise/", {}),
            ("/photo/weekly/", {"kind": "photo_collection"}),
            ("/photo/weekly/feature/", {"kind": "photo_issue"}),
            ("/photo/weekly/issue-001/", {"kind": "photo_edition"}),
            ("/photo/models/", {"kind": "photo_collection"}),
            ("/photo/models/sample/", {"kind": "photo_model"}),
            ("/serials/", {}), ("/serials/market-lab/", {"serial": self.analysis}),
            ("/serials/market-lab/briefings/", {"serial": self.analysis}),
            ("/serials/market-lab/outlooks/", {"serial": self.analysis}),
        )
        for path, options in cases:
            with self.subTest(path=path):
                markup = build.layout("見出し", "概要", path, "<h1>見出し</h1>", **options)
                self.assertNotIn('content="noindex', markup)
                self.assertNotIn("adsbygoogle.js", markup)
                self.assert_account_meta(markup)

    def test_body_type_and_metadata_are_both_required(self):
        cases = (
            {"kind": "story"}, {"article": self.article},
            {"kind": "serial_episode"}, {"kind": "market_bulletin"},
            {"kind": "serial_episode", "serial": {**self.analysis, "episode": ""}},
            {"kind": "market_bulletin", "serial": {**self.analysis, "episode": ""}},
            {"kind": "serial_episode", "serial": {**self.analysis, "kind": "fiction"}},
            {"kind": "market_bulletin", "serial": {**self.analysis, "kind": "fiction"}},
        )
        for options in cases:
            with self.subTest(options=options):
                markup = build.layout("見出し", "概要", "/body-check/", "<p>本文</p>", **options)
                self.assertNotIn("adsbygoogle.js", markup)
                self.assert_account_meta(markup)

    def test_noindex_body_pages_do_not_request_ads(self):
        for path, options in self.body_cases():
            with self.subTest(path=path):
                markup = build.layout("見出し", "概要", path, "<p>本文</p>", indexable=False, **options)
                self.assertIn('name="robots" content="noindex,follow"', markup)
                self.assertNotIn("adsbygoogle.js", markup)
                self.assert_account_meta(markup)

    def test_noindex_utility_pages_do_not_request_ads(self):
        for path, options in (("/saved/", {}), ("/404.html", {}),
                              ("/local-preview/", {"indexable": False})):
            with self.subTest(path=path):
                markup = build.layout("見出し", "概要", path, "<h1>見出し</h1>", **options)
                self.assertIn('name="robots" content="noindex,follow"', markup)
                self.assertNotIn("adsbygoogle.js", markup)
                self.assert_account_meta(markup)

    def test_publisher_file_remains_byte_exact(self):
        self.assertEqual((build.BASE / "ads.txt").read_bytes(),
                         b"google.com, pub-6754575901141756, DIRECT, f08c47fec0942fa0\n")

    def test_live_checker_enforces_loader_selection_and_account_verification(self):
        from scripts.check_live import verify_adsense_markup

        path, options = self.body_cases()[0]
        article_page = build.layout("記事", "概要", path, "<p>本文</p>", **options)
        information_page = build.advertise()
        verify_adsense_markup(article_page, loader_expected=True)
        verify_adsense_markup(information_page, loader_expected=False)
        for markup, expected in ((article_page, False), (information_page, True)):
            with self.subTest(loader_expected=expected), self.assertRaises(RuntimeError):
                verify_adsense_markup(markup, loader_expected=expected)
        without_account = information_page.replace(
            '<meta name="google-adsense-account" content="ca-pub-6754575901141756">', "")
        with self.assertRaisesRegex(RuntimeError, "account metadata"):
            verify_adsense_markup(without_account, loader_expected=False)
        loader = re.search(r'<script async src="[^"]+adsbygoogle.js[^<]+</script>', article_page).group(0)
        for broken in (article_page + loader,
                       article_page.replace("?client=ca-pub-6754575901141756", "?client=ca-pub-0")):
            with self.assertRaisesRegex(RuntimeError, "loader"):
                verify_adsense_markup(broken, loader_expected=True)

    def test_about_introduces_the_reading_experience_with_existing_stories(self):
        markup = build.about()
        self.assertIn("はじめて読むなら", markup)
        main = markup.split('<main', 1)[1].split('</main>', 1)[0]
        slugs = re.findall(r'href="/articles/([^/]+)/"', main)
        self.assertEqual(slugs, [
            "local-council-keeps-last-bus-despite-grant-formula",
            "fermentation-rna-inspection-two-flavors",
            "seven-venues-compose-one-live-song",
        ])
        self.assertTrue(set(slugs) <= {article["slug"] for article in build.load()})
        self.assertIn("ニュース記事はフィクションです", markup)
        self.assertIn("現実の公開資料に基づく市場分析", markup)
        self.assertNotIn("adsbygoogle.js", markup)

    def test_editorial_disclosure_describes_ai_production_and_existing_contact(self):
        markup = build.editorial_policy()
        self.assertIn("企画・執筆・合成画像の制作に生成AIを使用しています", markup)
        self.assertNotIn("文章づくりの補助", markup)
        self.assertIn('href="https://github.com/RollNuts/mihassei-news/issues"', markup)
        self.assertIn("投稿は公開されるため、個人情報は記入しないでください", markup)
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
        self.assertNotIn("adsbygoogle.js", markup)
        self.assert_account_meta(markup)


if __name__ == "__main__":
    unittest.main()
