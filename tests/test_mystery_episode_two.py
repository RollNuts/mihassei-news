"""Maintain story continuity and honest navigation when episode two is published."""
import json
import unittest
from pathlib import Path

from scripts.serials import render_episode_page, render_series_page

ROOT = Path(__file__).resolve().parents[1]


def plain_layout(_title, _desc, _path, main, **_kwargs):
    return main


class MysterySecondChapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = json.loads((ROOT / "content/serials.json").read_text(encoding="utf-8"))
        cls.fiction = next(s for s in data if s["slug"] == "case-files")

    def test_new_chapter_builds_from_existing_first_chapter(self):
        chapters = self.fiction["episodes"]
        self.assertGreaterEqual(len(chapters), 2)
        self.assertEqual([c["number"] for c in chapters], list(range(1, len(chapters) + 1)))
        self.assertEqual(chapters[0]["slug"], "victim-not-yet-dead-01")
        second = chapters[1]
        self.assertEqual(second["slug"], "victim-not-yet-dead-02")
        self.assertEqual(second["published_at"], "2026-11-03")
        body = "\n".join(p for section in second["sections"] for p in section["paragraphs"])
        # Concrete resolution, physical document clues, voluntary character choice.
        for fact in ("九時十六分になるのを待って", "山縣美津子", "六〇八三", "三階",
                     "山縣綾", "姉を疑う理由をちゃんと調べてください"):
            self.assertIn(fact, body)
        self.assertNotIn("二十四時間も不気味", body)

    def test_first_chapter_points_to_actual_sequel_not_coming_soon(self):
        first = render_episode_page(self.fiction, self.fiction["episodes"][0], plain_layout)
        self.assertIn('/serials/case-files/victim-not-yet-dead-02/', first)
        self.assertIn('第2話が公開されています', first)
        self.assertNotIn("第2話は準備中です", first)
        second = render_episode_page(self.fiction, self.fiction["episodes"][1], plain_layout)
        self.assertIn('/serials/case-files/victim-not-yet-dead-01/', second)
        self.assertIn('第2話・消された四桁', second)
        self.assertNotIn('data-mystery-notebook', second)
        overview = render_series_page(self.fiction, plain_layout)
        self.assertIn('公開済み '+str(len(self.fiction["episodes"]))+'話', overview)
        self.assertIn('第1話から読む', overview)

    def test_episode_two_does_not_present_unverified_alibi_as_a_fact(self):
        clue = self.fiction["episodes"][1]["clues"][0]
        self.assertIn("紗月本人", clue)
        self.assertIn("第三者による裏付けはまだない", clue)


if __name__ == "__main__":
    unittest.main()
