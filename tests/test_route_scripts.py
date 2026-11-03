"""Keep page-specific reader scripts lean without disabling their features."""
import unittest

from scripts.build import article_url, layout, load


class RouteScriptsTests(unittest.TestCase):
    @staticmethod
    def scripts(html):
        import re
        return set(re.findall(r'<script defer src="([^"]+)"', html))

    def test_only_relevant_scripts_are_loaded(self):
        a = load()[0]
        cases = {
            "/": {
                "/assets/engagement.js", "/assets/comment-activity.js",
                "/assets/serial-progress.js", "/assets/returning-shelf.js",
            },
            "/archive/": {"/assets/engagement.js", "/assets/site.js"},
            "/saved/": {"/assets/engagement.js", "/assets/bookmarks.js"},
            "/about/": {"/assets/engagement.js"},
            "/404.html": {"/assets/engagement.js"},
        }
        for route, expected in cases.items():
            with self.subTest(route=route):
                html = layout("テスト", "テスト", route, "<p>本文</p>")
                self.assertEqual(self.scripts(html), expected)
                self.assertIn('<link rel="canonical"', html)

        article = layout(a["title"], a["summary"], article_url(a), "<p>記事</p>",
                         kind="story", article=a)
        self.assertEqual(self.scripts(article), {
            "/assets/engagement.js", "/assets/comments.js",
            "/assets/bookmarks.js"
        })

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
