"""Keep page-specific reader scripts lean without disabling their features."""
import unittest

from scripts.build import article_url, asset_url, layout, load


class RouteScriptsTests(unittest.TestCase):
    @staticmethod
    def scripts(html):
        import re
        return set(re.findall(r'<script defer src="([^"]+)"', html))

    def test_only_relevant_scripts_are_loaded(self):
        a = load()[0]
        js = asset_url
        cases = {
            "/": {
                js("engagement.js"), js("comment-activity.js"),
                js("serial-progress.js"), js("returning-shelf.js"),
                js("news-broadcast.js"),
            },
            "/archive/": {js("engagement.js"), js("site.js")},
            "/saved/": {js("engagement.js"), js("bookmarks.js")},
            "/about/": {js("engagement.js")},
            "/404.html": {js("engagement.js")},
        }
        for route, expected in cases.items():
            with self.subTest(route=route):
                html = layout("テスト", "テスト", route, "<p>本文</p>")
                self.assertEqual(self.scripts(html), expected)
                self.assertIn('<link rel="canonical"', html)

        article = layout(a["title"], a["summary"], article_url(a), "<p>記事</p>",
                         kind="story", article=a)
        expected = {asset_url("engagement.js"), asset_url("comments.js"), asset_url("bookmarks.js")}
        from scripts import build
        if build.usable_clip(a, root=build.BASE):
            expected.add(asset_url("news-broadcast.js"))
        self.assertEqual(self.scripts(article), expected)

    def test_reader_script_urls_are_content_fingerprinted(self):
        import re
        for name in ("engagement.js", "site.js", "news-broadcast.js", "comments.js",
                     "comment-activity.js", "bookmarks.js", "serial-progress.js",
                     "returning-shelf.js"):
            self.assertRegex(asset_url(name), rf"^/assets/{re.escape(name)}\\?v=[0-9a-f]{{12}}$")
        with self.assertRaises(ValueError):
            asset_url("../engagement.js")

    def test_only_non_content_pages_are_noindexed(self):
        a = load()[0]
        for route in ("/404.html", "/saved/"):
            self.assertIn('<meta name="robots" content="noindex,follow">',
                          layout("テスト", "テスト", route, ""))
        for route in ("/", "/archive/", article_url(a)):
            self.assertNotIn('<meta name="robots" content="noindex,follow">',
                             layout("テスト", "テスト", route, ""))


if __name__ == "__main__":
    unittest.main()
