"""Regression tests for the mobile navigation and article reading hierarchy.

Free, static checks: no browser, API keys, external assets or network.
"""
from pathlib import Path
import re
import unittest

from scripts.build import layout, load, story

ROOT = Path(__file__).resolve().parents[1]


class MobileReadingFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles = load()

    def test_native_menu_shows_primary_routes_and_keeps_every_destination(self):
        html = layout("テスト", "テスト", "/", "<p>テスト</p>", active="home")
        desktop = html.split('<div class="container nav-inner">', 1)[1].split('</div>', 1)[0]
        mobile = html.split('<div class="container mobile-nav">', 1)[1].split('</div></details></div>', 1)[0]
        self.assertIn('<details class="mobile-nav-more">', mobile)
        self.assertIn('<summary>メニュー</summary>', mobile)
        self.assertIn('<div class="mobile-nav-menu">', mobile)
        primary = mobile.split('<details', 1)[0]
        secondary = mobile.split('<div class="mobile-nav-menu">', 1)[1]
        self.assertEqual(re.findall(r'href="([^"]+)"', primary),
                         ['/', '/archive/', '/serials/'])
        self.assertEqual(re.findall(r'href="([^"]+)"', secondary),
                         ['/issues/'])
        self.assertEqual(set(re.findall(r'href="([^"]+)"', mobile)),
                         set(re.findall(r'href="([^"]+)"', desktop)))
        self.assertIn('<a href="/" aria-current=page>一面</a>', primary)
        self.assertIn('href="/saved/"', html)
        self.assertIn('href="/about/"', html)

    def test_current_issue_page_still_marked_in_secondary_menu(self):
        html = layout("テスト", "テスト", "/issues/", "<p>テスト</p>", active="issues")
        self.assertIn('<a href="/issues/" aria-current=page>発行号</a>', html)

    def test_headline_and_hero_come_before_secondary_bookmark_tools(self):
        article = self.articles[0]
        html = story(article, self.articles)
        header = html.split('<header class="article-header">', 1)[1].split('</header>', 1)[0]
        self.assertNotIn('class="article-edition"', header)
        self.assertLess(header.index('class="article-dateline"'), header.index('<h1>'))
        self.assertLess(header.index('<h1>'), header.index('class="dek"'))
        self.assertIn('<strong>この記事は創作です。</strong>', header)
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
        self.assertIn('.mobile-nav{display:none}', css)
        self.assertIn('.mobile-nav-more>summary:focus-visible', css)
        self.assertIn('.mobile-nav-menu>a{', css)
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
