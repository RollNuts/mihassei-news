"""Static page, noindex, privacy and markup checks for the optional saved list."""
from __future__ import annotations

import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from scripts import build


ROOT = Path(__file__).resolve().parents[1]


class SavedStoriesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles = build.load()

    def test_private_list_is_not_an_indexable_archive(self):
        markup = build.saved_page()
        self.assertIn('name="robots" content="noindex,follow"', markup)
        self.assertIn('<link rel="canonical" href="https://veripsa.com/saved/">', markup)
        self.assertIn('id="main"', markup)
        self.assertIn('data-saved-list aria-label="保存した記事一覧"', markup)
        self.assertIn('data-clear-bookmarks hidden', markup)
        self.assertIn("ブラウザのデータを消去すると", markup)
        self.assertIn('href="/archive/"', markup)
        self.assertNotIn("data-bookmark-slug=", markup)
        self.assertIn('src="/assets/bookmarks.js"', markup)
        self.assertIn('aria-live="polite"', markup)

    def test_save_button_appears_only_on_article_not_auto_saving_cards(self):
        source = self.articles[0]
        page = build.story(source, self.articles)
        self.assertEqual(page.count('data-bookmark-slug="' + source["slug"] + '"'), 1,
                         "A single save button after the article is enough")
        self.assertIn('data-bookmark-title="' + build.escape(source["title"]) + '"', page)
        self.assertIn('data-bookmark-category="' + source["category"] + '"', page)
        self.assertIn('disabled aria-pressed="false"', page)
        self.assertIn('href="/saved/"', page)
        card = build.card(source)
        self.assertNotIn('data-bookmark-slug=', card)
        self.assertNotIn('localStorage', card)

    def test_saved_button_never_injects_unescaped_title_or_attributes(self):
        article = copy.deepcopy(self.articles[0])
        article["title"] = '" ><script>alert(1)</script> & hello'
        fragment = build.bookmark_button(article)
        self.assertNotIn('<script>', fragment)
        self.assertNotIn('data-bookmark-title=""', fragment)
        self.assertIn('&lt;script&gt;', fragment)
        self.assertIn('&quot;', fragment)
        self.assertIn('&amp; hello', fragment)

    def test_saved_list_hidden_from_sitemap_and_free_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(build, "OUT", Path(temp)):
                build.other_files(self.articles)
            urlset = ET.parse(Path(temp) / "sitemap.xml")
            locs = [
                element.text or ""
                for element in urlset.findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
            ]
            self.assertFalse(any("/saved/" in path for path in locs))
            headers = (Path(temp) / "_headers").read_text(encoding="utf-8")
            self.assertIn("/saved/*", headers)
            self.assertIn("X-Robots-Tag: noindex", headers)
        self.assertIn('<a href="/saved/">あとで読む</a>', build.layout("test", "test", "/", "<p>hello</p>"))
        js = (ROOT / "assets/bookmarks.js").read_text(encoding="utf-8")
        self.assertNotIn("fetch(", js)
        self.assertNotIn("document.cookie", js)

    def test_privacy_explains_opt_in_local_storage_and_delete(self):
        markup = build.privacy()
        self.assertIn("localStorage", markup)
        self.assertIn("運営者のサーバーへ送信せず", markup)
        self.assertIn("個別に解除・すべて解除", markup)
        css = (ROOT / "assets/editorial-polish.css").read_text(encoding="utf-8")
        for selector in (".bookmark-button", ".saved-entry", ".saved-empty[hidden]",
                         "@media(max-width:660px)"):
            self.assertIn(selector, css)
        self.assertEqual(css.count("{"), css.count("}"))


if __name__ == "__main__":
    unittest.main()
