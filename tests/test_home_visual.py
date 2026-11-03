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

    def test_picture_never_has_old_graphic_overlay_or_fixed_height(self):
        css=(ROOT/"assets/editorial-polish.css").read_text(encoding="utf-8")
        self.assertIn('.lead-media::before,.lead-media::after{content:none}',css)
        self.assertRegex(css,r'\.lead-media\s*\{[^}]*height:auto;[^}]*aspect-ratio:16/9;')
        self.assertIn('height:auto;aspect-ratio:16/9;object-fit:cover',css)
        self.assertEqual(css.count("{"),css.count("}"))


if __name__=="__main__":
    unittest.main()
