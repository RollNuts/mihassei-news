"""The lead illustration is not hidden behind legacy decoration or a fixed crop."""
import re
import unittest
from pathlib import Path
from scripts.build import home, load

ROOT = Path(__file__).resolve().parents[1]


class HomepageVisualTests(unittest.TestCase):
    def test_headline_then_picture_then_teaser_and_action(self):
        markup=home(load())
        lead=markup.split('<article class="lead">',1)[1].split('</article>',1)[0]
        heading=lead.index('<h1>')
        picture=lead.index('<a class="lead-media"')
        teaser=lead.index('<p class="dek">')
        read_more=lead.index('class="read-more"')
        self.assertTrue(heading < picture < teaser < read_more)
        self.assertIn('fetchpriority="high"',lead)
        self.assertIn('class="hero-image"',lead)

    def test_first_screen_prioritizes_article_not_three_line_explanation(self):
        markup = home(load())
        primer = markup.split('<section class="edition-time-key"', 1)[1].split('</section>', 1)[0]
        lead = markup.split('<article class="lead">', 1)[1].split('</article>', 1)[0]
        self.assertIn('<details class="concept-primer-details">', primer)
        self.assertIn('<summary>この新聞の読み方</summary>', primer)
        self.assertIn('class="concept-primer-foot"', primer)
        self.assertTrue(markup.index('<section class="edition-time-key"') <
                        markup.index('<section class="hero-grid">'))
        self.assertEqual(lead.count('class="hero-actions"'), 1)
        self.assertIn('>記事を読む</a>', lead)
        css = (ROOT / "assets/editorial-polish.css").read_text(encoding="utf-8")
        self.assertIn('border-bottom:1px solid var(--line);', css)
        self.assertIn('.concept-primer-details>summary', css)
        self.assertIn('.concept-primer-lead strong{display:inline;', css)

    def test_picture_never_has_old_graphic_overlay_or_fixed_height(self):
        css=(ROOT/"assets/editorial-polish.css").read_text(encoding="utf-8")
        self.assertIn('.lead-media::before,.lead-media::after{content:none}',css)
        self.assertRegex(css,r'\.lead-media\s*\{[^}]*height:auto;[^}]*aspect-ratio:16/9;')
        self.assertIn('height:auto;aspect-ratio:16/9;object-fit:cover',css)
        self.assertEqual(css.count("{"),css.count("}"))


if __name__=="__main__":
    unittest.main()
