"""Shared newspaper shell stays CSS-only, responsive and scoped to reading UI."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SharedReadingShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = (ROOT / 'assets/article-extra.css').read_text(encoding='utf-8')
        cls.shell = cls.css.split('/* Shared reading shell:', 1)[1]

    def test_styles_use_the_existing_bundle_without_new_requests(self):
        source = (ROOT / 'scripts/build.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        sheets = next(ast.literal_eval(node.value) for node in tree.body
                      if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == 'SITE_STYLESHEETS'
                              for target in node.targets))
        self.assertIn('article-extra.css', sheets)
        self.assertLess(sheets.index('article-extra.css'), sheets.index('reader-first.css'))
        self.assertNotIn('@import', self.shell)
        self.assertNotIn('url(', self.shell)
        self.assertNotIn('!important', self.shell)
        self.assertEqual(self.css.count('{'), self.css.count('}'))

    def test_current_section_has_a_cue_and_keyboard_focus(self):
        self.assertIn('.nav-bar .nav-inner a[aria-current="page"]', self.shell)
        self.assertIn('box-shadow:inset 0 -3px 0 var(--accent)', self.shell)
        self.assertIn('.nav-bar .nav-inner a:focus-visible', self.shell)
        self.assertIn('.footer .footer-nav a', self.shell)
        self.assertIn('min-height:44px', self.shell)

    def test_compact_mobile_header_only_targets_inner_pages(self):
        self.assertIn('.page-inner .masthead .brand{font-size:32px', self.shell)
        self.assertIn('.page-inner .mast-kicker{display:none}', self.shell)
        self.assertNotIn('.fiction-mark', self.shell)
        self.assertNotIn('.nav-inner{display:none', self.shell)
        self.assertIn('.page-home .index-main .lead .dek{font-size:15px', self.shell)
        self.assertIn('.archive-page .search-row .search-input{min-width:0;font-size:16px}', self.shell)

    def test_topics_follow_the_summary_without_taking_the_photo_column(self):
        self.assertIn('.archive-list>.story-card>.story-tags', self.shell)
        self.assertIn('grid-column:1 / -1;grid-row:4', self.shell)
        self.assertIn('.index-main .lead>.story-tags{grid-column:1;grid-row:5', self.shell)
        self.assertIn('.index-main .lead>.hero-actions{grid-column:1;grid-row:6}', self.shell)
        self.assertIn('.index-main .lead>.lead-media{grid-row:1 / span 6}', self.shell)
        self.assertNotIn('object-fit:', self.shell)
        self.assertNotIn('line-clamp', self.shell)

    def test_narrow_card_rules_outrank_the_later_reader_stylesheet(self):
        narrow = self.shell.split('@media(max-width:360px)', 1)[1]
        self.assertIn('.page-home .index-main>.section-block>.story-grid>.story-card', narrow)
        self.assertIn('.archive-page .archive-list .story-card', narrow)
        self.assertIn('grid-template-columns:88px minmax(0,1fr)', narrow)
        self.assertIn('flex-wrap:wrap', self.shell)


if __name__ == '__main__':
    unittest.main()
