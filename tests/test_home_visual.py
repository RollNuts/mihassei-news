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

    def test_first_screen_prioritizes_headlines_without_a_how_to_panel(self):
        markup = home(load())
        opening = markup.split('<section class="hero-grid">', 1)[0]
        lead = markup.split('<article class="lead">', 1)[1].split('</article>', 1)[0]
        self.assertNotIn("この新聞の読み方", opening)
        self.assertNotIn('class="concept-primer"', opening)
        self.assertIn('class="fiction-mark">創作報道', opening)
        self.assertIn('TOP STORY / 一面', lead)
        self.assertEqual(lead.count('class="hero-actions"'), 1)
        self.assertIn('>記事を読む</a>', lead)
        css = (ROOT / "assets/reader-first.css").read_text(encoding="utf-8")
        self.assertIn(".news-briefing", css)
        self.assertIn(".hero-grid", css)
        self.assertIn(".article-body", css)
        self.assertEqual(css.count("{"), css.count("}"))

    def test_stories_take_priority_over_optional_issue_picker(self):
        markup = home(load())
        content = markup.split('<div class="container index-main">', 1)[1]
        positions = [
            content.index('class="hero-grid"'),
            content.index('<section class="section-block">'),
            content.index('data-popular-section'),
            content.index('class="press-timeline"'),
            content.index('class="genre-explore"'),
            content.index('<details class="extras-shelf">'),
            content.index('class="special-edition"'),
            content.index('class="reader-discussions"'),
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('<summary><strong>発行号・号外・コメント</strong>', content)
        self.assertNotIn('class="special-edition"', content[:positions[5]])
        self.assertEqual(content.count('data-popular-section'), 1)
        self.assertEqual(content.count('data-issue-picker'), 1)

    def test_picture_never_has_old_graphic_overlay_or_fixed_height(self):
        css=(ROOT/"assets/editorial-polish.css").read_text(encoding="utf-8")
        self.assertIn('.lead-media::before,.lead-media::after{content:none}',css)
        self.assertRegex(css,r'\.lead-media\s*\{[^}]*height:auto;[^}]*aspect-ratio:16/9;')
        self.assertIn('height:auto;aspect-ratio:16/9;object-fit:cover',css)
        self.assertEqual(css.count("{"),css.count("}"))


if __name__=="__main__":
    unittest.main()
