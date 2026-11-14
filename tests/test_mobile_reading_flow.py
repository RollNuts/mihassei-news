"""Regression tests for the mobile navigation and article reading hierarchy.

Free, static checks: no browser, API keys, external assets or network.
"""
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from scripts import build

from scripts.build import layout, load, story

ROOT = Path(__file__).resolve().parents[1]


class MobileReadingFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles = load()

    def test_one_navigation_keeps_all_five_destinations_directly_accessible(self):
        with patch.object(build, "PHOTO_ISSUES_AVAILABLE", True):
            html = layout("テスト", "テスト", "/", "<p>テスト</p>", active="home")
        navigation = html.split('<nav class="nav-bar" aria-label="メインナビゲーション">', 1)[1].split('</nav>', 1)[0]
        self.assertEqual(re.findall(r'href="([^"]+)"', navigation),
                         ['/', '/archive/', '/photo/weekly/', '/serials/market-lab/', '/issues/'])
        self.assertNotIn('<details', navigation)
        self.assertNotIn('mobile-nav', html)
        self.assertIn('<a href="/" aria-current=page>一面</a>', navigation)
        self.assertIn('href="/saved/"', html)
        self.assertIn('href="/about/"', html)

    def test_current_issue_page_is_marked_in_shared_navigation(self):
        html = layout("テスト", "テスト", "/issues/", "<p>テスト</p>", active="issues")
        self.assertEqual(html.count('<a href="/issues/" aria-current=page>発行号</a>'), 1)

    def test_headline_and_hero_come_before_secondary_bookmark_tools(self):
        article = self.articles[0]
        html = story(article, self.articles)
        header = html.split('<header class="article-header">', 1)[1].split('</header>', 1)[0]
        self.assertNotIn('class="article-edition"', header)
        self.assertLess(header.index('class="article-dateline"'), header.index('<h1>'))
        self.assertLess(header.index('<h1>'), header.index('class="dek"'))
        self.assertNotIn('class="story-fiction-context"', header)
        image = html.index('<figure class="article-hero-figure">')
        bookmark = html.index('<div class="article-save-inline">')
        body = html.index('<div class="article-body">')
        self.assertTrue(image < body < bookmark)
        self.assertIn('<details class="source-context" id="story-source" aria-label="着想した実際の話題">', html)
        self.assertLess(html.index('<div class="article-endmark">'), bookmark)
        self.assertEqual(html.count('data-bookmark-slug="' + article["slug"] + '"'), 1)
        self.assertIn('id="story-source"', html)

    def test_mobile_nav_has_real_touch_targets_and_article_images_keep_fallback(self):
        css = (ROOT / 'assets/editorial-polish.css').read_text(encoding='utf-8')
        responsive_css = (ROOT / 'assets/reader-first.css').read_text(encoding='utf-8')
        self.assertIn('grid-template-columns:repeat(5,minmax(0,1fr))', responsive_css)
        self.assertIn('min-height:46px', responsive_css)
        self.assertIn('.nav-inner a:focus-visible', responsive_css)
        for source in (css, responsive_css):
            self.assertNotIn('mobile-nav', source)
            self.assertEqual(source.count('{'), source.count('}'))
        self.assertIn('min-height:44px;', css)
        self.assertIn('.article-hero-figure .article-visual{', css)
        self.assertIn('aspect-ratio:16/9;', css)
        self.assertEqual(css.count('{'), css.count('}'))
        self.assertNotIn('@import', css)
        # The image fallback mechanism remains responsible for actual content.
        build = (ROOT / 'scripts/build.py').read_text(encoding='utf-8')
        self.assertIn('data-fallback=', build)


if __name__ == '__main__':
    unittest.main()
