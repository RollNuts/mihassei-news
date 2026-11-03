"""Device-only resume shelf on the homepage: functional even without analytics."""
from pathlib import Path
import unittest

from scripts.build import home, layout, load


ROOT = Path(__file__).resolve().parents[1]


class ReturningShelfTests(unittest.TestCase):
    def test_only_homepage_has_an_optional_saved_entry(self):
        html = home(load())
        assert '<aside class="returning-shelf" data-returning-shelf hidden' in html
        self.assertIn('保存した記事から再開', html)
        self.assertIn('data-returning-shelf-article', html)
        self.assertIn('data-returning-shelf-count', html)
        self.assertIn('href="/saved/"', html)
        self.assertLess(html.index('data-returning-shelf hidden'), html.index('class="hero-grid"'))
        self.assertEqual(html.count('data-returning-shelf hidden'), 1)
        self.assertEqual(html.count('src="/assets/returning-shelf.js"'), 1)

    def test_other_pages_do_not_load_home_only_script(self):
        article = layout('A title', 'desc', '/articles/a-test/', '<p>story</p>', kind='story')
        saved = layout('Saved', 'desc', '/saved/', '<p>saved</p>')
        for html in (article, saved):
            self.assertNotIn('src="/assets/returning-shelf.js"', html)
        self.assertIn('src="/assets/bookmarks.js"', article)
        self.assertIn('src="/assets/bookmarks.js"', saved)

    def test_only_existing_local_bookmarks_and_no_paid_calls(self):
        source = (ROOT / 'assets/returning-shelf.js').read_text(encoding='utf-8')
        css = (ROOT / 'assets/editorial-polish.css').read_text(encoding='utf-8')
        for snippet in ('mihassei-saved-stories-v1', 'localStorage.getItem',
                        'textContent', '/articles/', 'window.addEventListener',
                        'shelf.hidden = true'):
            self.assertIn(snippet, source)
        for banned in ('fetch(', 'XMLHttpRequest', 'sendBeacon(', 'https://', 'http://'):
            self.assertNotIn(banned, source)
        self.assertIn('.returning-shelf{', css)
        self.assertIn('@media(prefers-reduced-motion:no-preference)', css)
        self.assertEqual(css.count('{'), css.count('}'))


if __name__ == '__main__':
    unittest.main()
