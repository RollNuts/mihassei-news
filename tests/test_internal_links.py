"""Prove the free SEO audit catches internal 404s and stale anchors.

External source URLs and filtered archive query strings are never misclassified.
"""
import tempfile
import unittest
from pathlib import Path

from scripts.audit_search_index import Page, check_internal_links


class InternalLinkTests(unittest.TestCase):
    def test_local_pages_files_and_fragments_are_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            dist = Path(temp)
            (dist / "archive").mkdir()
            (dist / "assets").mkdir()
            (dist / "index.html").write_text('<main id="main">新聞</main>', encoding="utf-8")
            (dist / "archive/index.html").write_text(
                '<section id="category-feeds"><h1>記事一覧</h1></section>', encoding="utf-8"
            )
            (dist / "assets/demo.svg").write_text("<svg></svg>", encoding="utf-8")
            home = Page()
            home.feed(
                '<main id="main">'
                '<a href="/archive/?category=%E7%A4%BE%E4%BC%9A#category-feeds">社会</a>'
                '<a href="/assets/demo.svg">イラスト</a>'
                '<a href="#main">本文</a>'
                '<a href="https://example.org/absent/">元ニュース</a>'
                '<a href="mailto:reader@example.org">連絡</a>'
                '</main>'
            )
            archive = Page()
            archive.feed((dist / "archive/index.html").read_text(encoding="utf-8"))
            errors, checked = check_internal_links(
                dist, {"/": home, "/archive/": archive}
            )
            self.assertEqual(errors, [])
            self.assertEqual(checked, 3)

            home.links += [
                "/articles/missing-story/",
                "/archive/#typo",
                "/archive/%2e%2e/%2e%2e/secret",
            ]
            errors, checked = check_internal_links(
                dist, {"/": home, "/archive/": archive}
            )
            self.assertEqual(checked, 6)
            self.assertTrue(any("Broken internal link" in err and "missing-story" in err
                                for err in errors))
            self.assertTrue(any("Broken internal fragment" in err and "#typo" in err
                                for err in errors))
            self.assertTrue(any("Unsafe internal link" in err and "secret" in err
                                for err in errors))

    def test_error_messages_mention_origin_for_debugging(self):
        with tempfile.TemporaryDirectory() as temp:
            dist = Path(temp)
            (dist / "articles/example").mkdir(parents=True)
            (dist / "articles/example/index.html").write_text("<h1>Example</h1>", encoding="utf-8")
            article = Page()
            article.feed('<a href="/articles/old-name/">Read next</a>')
            errors, checked = check_internal_links(dist, {"/articles/example/": article})
            self.assertEqual(checked, 1)
            self.assertIn("/articles/example/ -> /articles/old-name/", errors[0])


if __name__ == "__main__":
    unittest.main()
