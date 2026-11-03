"""Reader-first narrative links; market readers go to current analysis."""
import copy
import json
import unittest
from pathlib import Path

from scripts.serials import render_serials_feature, render_series_page, render_episode_page
from scripts.build import privacy

ROOT = Path(__file__).resolve().parents[1]


def identity_layout(_title, _desc, _path, main, **_kwargs):
    return main


class SerialReadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ROOT / "content/serials.json").read_text(encoding="utf-8"))

    def test_fiction_starts_at_episode_one_even_after_new_publication(self):
        data = copy.deepcopy(self.data)
        fiction = next(s for s in data if s["kind"] == "fiction")
        market = next(s for s in data if s["kind"] == "analysis")
        second = copy.deepcopy(fiction["episodes"][-1])
        second["number"] = 2
        second["slug"] = "victim-not-yet-dead-02"
        fiction["episodes"].append(second)
        home = render_serials_feature(data)
        self.assertIn('href="/serials/case-files/victim-not-yet-dead-01/">第1話から読む', home)
        self.assertIn('href="/serials/case-files/victim-not-yet-dead-02/">最新の第2話へ', home)
        self.assertIn('href="/serials/market-lab/power-grid-after-ai-01/">最新の第1回を読む', home)
        series = render_series_page(fiction, identity_layout)
        self.assertIn('class="serial-start-link" href="/serials/case-files/victim-not-yet-dead-01/"', series)
        self.assertIn("公開済み 2話", series)

    def test_article_keeps_prose_and_clues_without_a_note_form(self):
        fiction = next(s for s in self.data if s["kind"] == "fiction")
        story = render_episode_page(fiction, fiction["episodes"][0], identity_layout)
        self.assertIn('class="serial-prose-section"', story)
        self.assertIn('<details class="serial-clues">', story)
        self.assertIn("読了後に伏線を振り返る", story)
        self.assertNotIn("data-mystery-notebook", story)
        self.assertNotIn("mystery-notes.js", story)
        self.assertNotIn("<textarea", story)
        self.assertIn("次回は未公開", story)

    def test_old_local_notes_are_never_silently_deleted(self):
        notice = privacy()
        self.assertIn("以前の推理メモ", notice)
        self.assertIn("localStorage", notice)
        self.assertIn("自動で消去・送信しません", notice)


if __name__ == "__main__":
    unittest.main()
