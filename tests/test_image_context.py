"""Article-specific honest image descriptions, priority loading and fallbacks.

All checks operate on locally rendered HTML; no image API or network calls.
"""
import html
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from scripts import build
from scripts.generated_photos import article_fingerprint


class ImageContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles = build.load()

    def test_image_alt_is_specific_without_pretending_to_know_rendered_pixels(self):
        descriptions = set()
        non_photo_count = 0
        for article in self.articles:
            generated = build.reviewed_generated_scene(article, root=build.BASE)
            photo_ok = not generated and build.uses_stock_photo(article)
            native_scene = build.reviewed_story_scene(article, root=build.BASE)
            expected = build.article_image_alt(
                article, generated=generated, photo_ok=photo_ok
            )
            tag = build.image_tag(article, "story-thumb", extra='loading="lazy" decoding="async"')
            self.assertIn('alt="' + html.escape(expected, quote=True) + '"', tag)
            self.assertIn('width="1200" height="675"', tag)
            self.assertIn('loading="lazy"', tag)
            if generated:
                self.assertIn("再現イメージ", expected)
                self.assertIn(article["title"], expected)
                expected_source = "/generated/" + article["slug"] + ".jpg"
            elif photo_ok:
                self.assertIn(article["photo"]["alt"], expected)
                expected_source = "/photos/" + article["slug"] + ".jpg"
            elif native_scene:
                self.assertEqual(expected, ET.fromstring(native_scene).attrib["aria-label"])
                self.assertIn("創作イラスト", expected)
                self.assertNotIn(article["title"], expected)
                expected_source = "/images/" + article["slug"] + ".svg"
            else:
                self.assertIn("編集部制作イラスト", expected)
                self.assertIn(article["title"], expected)
                expected_source = "/images/" + article["slug"] + ".svg"
            self.assertIn('src="' + expected_source + '"', tag)
            if not photo_ok:
                non_photo_count += 1
                self.assertNotIn("記事の題材を表現した創作イラスト", expected)
                descriptions.add(expected)
            if generated or photo_ok:
                fallback_description = build.article_image_alt(article)
                self.assertIn('data-fallback="/images/' + article["slug"] + '.svg"', tag)
                self.assertIn(
                    'data-fallback-alt="' + html.escape(fallback_description, quote=True) + '"',
                    tag,
                )
            else:
                self.assertNotIn('data-fallback=', tag)
        self.assertGreaterEqual(len(descriptions), non_photo_count-1)

    def test_story_hero_prioritized_and_social_alt_describes_selected_asset(self):
        for article in self.articles:
            story = build.story(article, self.articles)
            figure = story.split('<figure class="article-hero-figure">', 1)[1].split('</figure>', 1)[0]
            self.assertIn('class="article-visual"', figure)
            self.assertIn('loading="eager" fetchpriority="high" decoding="async"', figure)
            self.assertNotIn('loading="lazy"', figure)
            generated = build.reviewed_generated_scene(article, root=build.BASE)
            expected = build.article_image_alt(
                article,
                generated=generated,
                photo_ok=not generated and build.uses_stock_photo(article),
            )
            encoded = html.escape(expected, quote=True)
            social_source = build.social_image(article)
            if social_source.split("?", 1)[0].endswith(".jpg"):
                social_description = expected
            else:
                # The raster fallback contains a topic illustration and brand
                # labels; the Japanese article headline is in HTML metadata.
                social_description = "創作ニュース「" + article["title"] + "」のテーマを表した共有イラスト"
            encoded_social = html.escape(social_description, quote=True)
            for field in ('property="og:image:alt"', 'name="twitter:image:alt"'):
                self.assertIn(field + ' content="' + encoded_social + '"', story)
            self.assertIn('alt="' + encoded + '"', figure)
            self.assertIn('<strong>創作ニュース</strong>', story)
            self.assertIn('<summary>着想元を見る</summary>', story)
            self.assertNotIn('href="#story-source"', story)

    def test_home_first_image_priority_does_not_preload_every_card(self):
        markup = build.home(self.articles)
        lead = markup.split('<article class="lead">', 1)[1].split('</article>', 1)[0]
        self.assertIn('fetchpriority="high"', lead)
        self.assertNotIn('loading="lazy"', lead)
        self.assertIn('loading="lazy"', markup.split('class="story-grid"', 1)[1])


class EditorialSceneCardFramingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.article = {
            "slug": "scene-framing-fixture", "title": "呼び鈴が店に届いた",
            "category": "事件", "summary": "入口と小包を同時に見せる構図。",
            "paragraphs": ["店主は入口で小包に入った呼び鈴を見つけた。"],
            "art": "cards",
        }
        self.scene_source = "/images/scene-framing-fixture.svg"
        self.scene_file = self.root / "assets/editorial-scenes/scene-framing-fixture.svg"
        self.scene_file.parent.mkdir(parents=True)
        self.scene_file.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 675"'
            ' role="img" aria-label="入口と小包を描いた創作イラスト"'
            ' data-visual-topic="doorbell"'
            f' data-article-sha256="{article_fingerprint(self.article)}">'
            '<title>構図保持の一時テスト用イラスト</title>'
            '<path d="M10 10H100V100H10Z"/></svg>', encoding="utf-8",
        )
        base_patch = patch.object(build, "BASE", self.root)
        base_patch.start()
        self.addCleanup(base_patch.stop)

    def image_attributes(self, article=None):
        return ET.fromstring(
            build.image_tag(article or self.article, "story-thumb")[:-1] + " />"
        ).attrib

    def test_current_reviewed_scene_preserves_composition_but_stale_scene_does_not(self):
        attributes = self.image_attributes()
        self.assertEqual(attributes["src"], self.scene_source)
        self.assertEqual(attributes["data-editorial-scene-src"], self.scene_source)
        self.assertNotIn("data-fallback", attributes)
        changed = {**self.article, "paragraphs": ["店主は新しい呼び鈴を買い直した。"]}
        fallback = self.image_attributes(changed)
        self.assertEqual(fallback["src"], self.scene_source)
        self.assertNotIn("data-editorial-scene-src", fallback)

    def test_photo_retains_its_source_and_identifies_only_its_reviewed_svg_fallback(self):
        with patch.object(build, "reviewed_generated_scene", return_value=True):
            attributes = self.image_attributes()
            self.assertEqual(attributes["src"], "/generated/scene-framing-fixture.jpg")
            self.assertEqual(attributes["data-fallback"], self.scene_source)
            self.assertEqual(attributes["data-editorial-scene-src"], self.scene_source)
            self.assertEqual(attributes["data-generated-scene"], "true")
            self.scene_file.unlink()
            ordinary_photo = self.image_attributes()
            self.assertEqual(ordinary_photo["src"], "/generated/scene-framing-fixture.jpg")
            self.assertNotIn("data-editorial-scene-src", ordinary_photo)


if __name__ == "__main__":
    unittest.main()
