"""Culture-specific editorial illustrations are real, topical, deterministic and free."""
from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from scripts.editorial_covers import ICONS, render_cover, story_subject
from scripts.social_cards import render_social_card, SUBJECT_LABELS

ROOT = Path(__file__).resolve().parents[1]

EXAMPLES = {
    "frozen": "推しコラボのアイス、保存用だけ完売",
    "livestock": "牛とロバの友情、飼育AIが異常と判定",
    "romance": "恋活アプリ、返信の遅さで相性判定",
    "gaming": "ゲームのコントローラーを交換したら",
    "school": "小学校の授業が映画化",
    "music": "レコードの新しいアルバムを発売",
}


class TopicSpecificArtTests(unittest.TestCase):
    def article(self, title, slug="culture-theme", art="generic"):
        return {
            "title": title,
            "slug": slug,
            "fiction_at": "2031-11-03T09:00:00+09:00",
            "category": "エンタメ",
            "art": art,
        }

    def test_specific_subject_beats_generic_template(self):
        for subject, title in EXAMPLES.items():
            with self.subTest(subject=subject):
                article = self.article(title)
                self.assertEqual(story_subject(article), subject)
                self.assertEqual(story_subject({**article, "art": "cards"}), subject)
                self.assertIn(subject, ICONS)
                self.assertIn(subject, SUBJECT_LABELS)
        # Other subject categories retain their original topic mapping.
        for title, expected in (
            ("迷惑電話はもうかけない", "telephone"),
            ("低気圧で上司が休む", "weather"),
            ("プロ野球の新制度", "baseball"),
            ("ドラマの脚本が追いつかない", "movie"),
            ("情報漏洩より先に謝罪", "security"),
            ("推しコラボのアイスが保存される", "fandom"),
            ("会見の例え話がバズる", "meeting"),
        ):
            self.assertEqual(story_subject(self.article(title)), expected, title)

    def test_existing_culture_articles_use_subject_relevant_art(self):
        articles = json.loads((ROOT / "content/articles.json").read_text(encoding="utf-8"))
        by_slug = {a["slug"]: a for a in articles}
        expected = {
            "idol-ice-kept-in-freezer": "frozen",
            "farm-friendship-across-species": "livestock",
            "quiet-reply-dating-matches": "romance",
        }
        for slug, subject in expected.items():
            if slug not in by_slug:  # Future archive edits need not break publishing.
                continue
            self.assertEqual(story_subject(by_slug[slug]), subject, slug)

    def test_svg_covers_have_real_pictograms_without_external_assets(self):
        motifs = set()
        for subject, title in EXAMPLES.items():
            svg = render_cover(self.article(title))
            root = ET.fromstring(svg)
            self.assertEqual(root.attrib["viewBox"], "0 0 1200 675")
            self.assertNotIn("<image", svg)
            self.assertNotIn("<script", svg)
            self.assertNotIn("http://fonts", svg)
            self.assertIn("EDITORIAL ILLUSTRATION", svg)
            self.assertIn("創作", svg)
            self.assertTrue(root.findall("{http://www.w3.org/2000/svg}text"))
            motif = ICONS[subject]
            self.assertIn(motif, svg)
            self.assertNotIn(motif, motifs)
            motifs.add(motif)

    def test_png_social_cards_are_deterministic_and_different_by_topic(self):
        with tempfile.TemporaryDirectory() as folder:
            outputs = {}
            for subject, title in EXAMPLES.items():
                article = self.article(title, slug="newsroom-shared-slug")
                filename = Path(folder) / (subject + ".png")
                render_social_card(article, filename)
                payload = filename.read_bytes()
                self.assertEqual(payload[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(struct.unpack(">II", payload[16:24]), (1200, 630))
                self.assertGreater(len(payload), 1500)
                self.assertLess(len(payload), 200000, "Raster covers must fit free hosting")
                outputs[subject] = payload
                again = Path(folder) / (subject + "-again.png")
                render_social_card(article, again)
                self.assertEqual(again.read_bytes(), payload)
            self.assertEqual(len(set(outputs.values())), len(outputs))


if __name__ == "__main__":
    unittest.main()
