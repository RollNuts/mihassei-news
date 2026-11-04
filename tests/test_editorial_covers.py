"""Image quality gates: no network, paid API, AI image model or extra package."""
import html
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from scripts.editorial_covers import (
    ICONS, headline_layout, render_cover, render_story_visual,
    reviewed_story_scene, story_subject,
)
from scripts.generated_photos import article_fingerprint
from scripts.media_policy import (
    approved_sources, jpeg_dimensions, photo_is_approved, usable_cached_photo,
)
from scripts.cache_photos import fetch_photo

ROOT = Path(__file__).resolve().parents[1]
ARTICLES = json.loads((ROOT / "content/articles.json").read_text(encoding="utf-8"))


class CoverQualityTests(unittest.TestCase):
    def test_every_article_has_title_specific_accessible_svg(self):
        seen = set()
        for article in ARTICLES:
            svg = render_cover(article)
            self.assertLess(len(svg), 25_000)
            root = ElementTree.fromstring(svg)
            self.assertEqual(root.attrib["viewBox"], "0 0 1200 675")
            self.assertIn("編集部制作表紙", root.attrib["aria-label"])
            self.assertIn(article["title"], root.attrib["aria-label"])
            self.assertIn("EDITORIAL ILLUSTRATION", svg)
            self.assertNotIn("<script", svg)
            self.assertNotIn("<image", svg)
            self.assertNotIn("http://images.", svg)
            ns = "{http://www.w3.org/2000/svg}"
            text_nodes = root.findall(ns + "text")
            lines = [
                t.text or "" for t in text_nodes
                if t.attrib.get("x") == "74"
                and t.attrib.get("font-weight") == "900"
                and t.attrib.get("fill") == "#213039"
            ]
            expected = re.sub(r"\s+", "", article["title"])
            actual = re.sub(r"\s+", "", "".join(lines))
            self.assertEqual(actual, expected)
            self.assertLessEqual(len(lines), 5)
            seen.add(svg)
        self.assertEqual(len(seen), len(ARTICLES),
                         "A real story headline must distinguish each original cover")

    def test_article_artwork_does_not_bake_a_second_headline_into_cards(self):
        from scripts.editorial_covers import story_subject
        seen = set()
        for article in ARTICLES:
            svg = render_story_visual(article)
            root = ElementTree.fromstring(svg)
            self.assertEqual(root.attrib["viewBox"], "0 0 1200 675")
            self.assertEqual(root.attrib["role"], "img")
            self.assertEqual(root.attrib["data-visual-topic"], story_subject(article))
            self.assertIn("創作イラスト", root.attrib["aria-label"])
            self.assertNotIn("<text", svg)
            self.assertNotIn("<image", svg)
            self.assertNotIn("<script", svg)
            self.assertNotIn(article["title"], svg)
            seen.add(svg)
        self.assertEqual(len(seen), len(ARTICLES))

    def test_headlines_fit_bounded_lines_and_safe_xml(self):
        for title in (
            "今まで誰も聞いたことのない未来",
            "AIを選ぶAI、人間の管理職を「不採用」　異議申し立てもAIが却下",
            "未発生" * 40,
            "前提が壊れる & <span> \"おかしい\" ニュース",
            "",
        ):
            lines, size = headline_layout(title)
            self.assertLessEqual(len(lines), 5)
            self.assertGreaterEqual(size, 28)
            image = render_cover({
                "title": title, "category": "技術",
                "art": "generic", "fiction_at": "2031-11-02T12:00:00+09:00",
            })
            ElementTree.fromstring(image)

    def test_topic_motif_is_grounded_in_actual_article(self):
        examples = {
            "unwanted-call-protection-calls-back": "telephone",
            "local-souvenir-cannot-leave-town": "parcel",
            "supporter-guarantor-contract": "baseball",
            "pressure-office-mood-forecast": "weather",
            "fan-theories-write-final": "movie",
            "apology-before-incident": "security",
            "robot-job-hopping": "robot",
            "research-completed-opening-day": "laboratory",
            "ai-chair-removes-executives": "meeting",
        }
        by_slug = {article["slug"]: article for article in ARTICLES}
        for slug, subject in examples.items():
            if slug in by_slug:
                self.assertEqual(story_subject(by_slug[slug]), subject, slug)

    def test_doorbell_subject_takes_priority_over_delivery_and_apology(self):
        for title in (
            "盗まれた店の呼び鈴、宅配便で戻る　配達員は押せず不在票",
            "謝罪の配達員、ドアベルのない店先で立ち止まる",
        ):
            article = {"title": title, "art": "cards"}
            self.assertEqual(story_subject(article), "doorbell")
            cover = render_cover(article)
            ElementTree.fromstring(cover)
            self.assertIn(ICONS["doorbell"], cover)
            self.assertNotIn(ICONS["parcel"], cover)
        self.assertEqual(story_subject({
            "title": "店に届いた小包", "summary": "配達された中身は呼び鈴だった。",
        }), "doorbell")
        self.assertEqual(story_subject({"title": "地元の土産を配達する"}), "parcel")

    def test_reviewed_svg_requires_the_current_article_body(self):
        article = {
            "slug": "doorbell-scene-test", "title": "呼び鈴が戻った店",
            "category": "事件", "summary": "小包の中に呼び鈴が入っていた。",
            "paragraphs": ["閉じた入口の横には呼び鈴の取付跡が残っていた。"],
        }
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 675"'
            ' role="img" aria-label="空の取付跡と小包を描いた創作イラスト"'
            ' data-visual-topic="doorbell"'
            f' data-article-sha256="{article_fingerprint(article)}">'
            '<title>店先へ届いた呼び鈴</title><path d="M10 10H100V100H10Z"/></svg>'
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "assets/editorial-scenes"
            directory.mkdir(parents=True)
            path = directory / (article["slug"] + ".svg")
            self.assertIsNone(reviewed_story_scene(article, root=root))
            path.write_text(svg, encoding="utf-8")
            self.assertEqual(reviewed_story_scene(article, root=root), svg)
            changed = {**article, "paragraphs": ["後日、店主が別の呼び鈴を買った。"]}
            self.assertIsNone(reviewed_story_scene(changed, root=root))
            with patch("scripts.editorial_covers.reviewed_story_scene",
                       side_effect=lambda value: reviewed_story_scene(value, root=root)):
                self.assertEqual(render_story_visual(article), svg)
                fallback = render_story_visual(changed)
                self.assertNotEqual(fallback, svg)
                parsed = ElementTree.fromstring(fallback)
                self.assertEqual(parsed.get("data-visual-topic"), "doorbell")
                self.assertEqual(parsed.get("role"), "img")
                self.assertIn("創作イラスト", parsed.get("aria-label"))
            # Invalid, inaccessible or executable SVGs never become a reviewed scene.
            for invalid in (
                svg[:-6],
                svg.replace('role="img"', 'role="none"'),
                svg.replace('viewBox="0 0 1200 675"', 'viewBox="0 0 1200 630"'),
                svg.replace('data-visual-topic="doorbell"', 'data-visual-topic="parcel"'),
                svg.replace('aria-label="空の取付跡と小包を描いた創作イラスト"', ''),
                svg.replace('<title>店先へ届いた呼び鈴</title>', ''),
                svg.replace('</svg>', '<script>alert(1)</script></svg>'),
                svg.replace('</svg>', '<image href="https://example.com/photo.jpg"/></svg>'),
            ):
                path.write_text(invalid, encoding="utf-8")
                self.assertIsNone(reviewed_story_scene(article, root=root))
            self.assertIsNone(reviewed_story_scene({**article, "slug": "../doorbell-scene-test"}, root=root))

    def test_photo_review_is_explicit_and_matches_exact_source(self):
        approvals = approved_sources()
        self.assertGreaterEqual(len(approvals), 3)
        for article in ARTICLES:
            expected = bool(article.get("photo")
                            and approvals.get(article["slug"]) == article["photo"]["source"])
            self.assertEqual(photo_is_approved(article), expected)
            if expected:
                changed = {**article, "photo": {
                    **article["photo"], "source": article["photo"]["source"] + "-changed"}}
                self.assertFalse(photo_is_approved(changed))
            else:
                # The source being online or local is not proof of relevance.
                self.assertFalse(usable_cached_photo(article))
        self.assertTrue(any(usable_cached_photo(article) for article in ARTICLES))

    def test_unapproved_photo_uses_cover_without_network_or_failing_publication(self):
        unreviewed = next(article for article in ARTICLES
                          if article.get("photo") and not photo_is_approved(article))
        with patch("urllib.request.urlopen", side_effect=AssertionError("network called")):
            self.assertEqual(fetch_photo(unreviewed),
                             "not-approved (original editorial cover)")

    def test_jpeg_header_parser_rejects_missing_or_broken_payload(self):
        self.assertIsNone(jpeg_dimensions(b"not JPEG data"))
        self.assertIsNone(jpeg_dimensions(b"\xff\xd8\xff\xe0broken\xff\xd9"))
        # A SOF0 header with 1200 x 675 dimensions, even without full image
        # data, is suitable for exercising just the geometry parser.
        sof = b"\xff\xd8\xff\xc0\x00\x11\x08" + (675).to_bytes(2,"big") + \
              (1200).to_bytes(2,"big") + b"\x03" + b"\x01\x11\x00" + \
              b"\x02\x11\x00" + b"\x03\x11\x00" + b"\xff\xd9"
        self.assertEqual(jpeg_dimensions(sof), (1200,675))


if __name__ == "__main__":
    unittest.main()
