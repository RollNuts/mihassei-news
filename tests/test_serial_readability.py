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
        if len(fiction["episodes"]) == 1:
            second = copy.deepcopy(fiction["episodes"][-1])
            second["number"] = 2
            second["slug"] = "victim-not-yet-dead-02"
            fiction["episodes"].append(second)
        home = render_serials_feature(data)
        self.assertIn('href="/serials/case-files/victim-not-yet-dead-01/">第1話から読む', home)
        latest = fiction["episodes"][-1]
        self.assertIn('href="/serials/case-files/'+latest["slug"]+'/">' +
                      f'最新の第{latest["number"]}話へ', home)
        self.assertIn('href="/serials/market-lab/power-grid-after-ai-01/">最新の第1回を読む', home)
        series = render_series_page(fiction, identity_layout)
        self.assertIn('class="serial-start-link" href="/serials/case-files/victim-not-yet-dead-01/"', series)
        self.assertIn(f'公開済み {len(fiction["episodes"])}話', series)

    def test_article_keeps_prose_and_clues_without_a_note_form(self):
        fiction = next(s for s in self.data if s["kind"] == "fiction")
        story = render_episode_page(fiction, fiction["episodes"][0], identity_layout)
        self.assertIn('class="serial-prose-section"', story)
        self.assertIn('<details class="serial-clues">', story)
        self.assertIn("読了後に伏線を振り返る", story)
        self.assertNotIn("data-mystery-notebook", story)
        self.assertNotIn("mystery-notes.js", story)
        self.assertNotIn("<textarea", story)
        self.assertIn("次の話を読む" if len(fiction["episodes"]) > 1 else "次回は未公開", story)
        if len(fiction["episodes"]) > 1:
            self.assertNotIn("第2話は準備中です", story)
            self.assertIn("第2話が公開されています", story)

    def test_old_local_notes_are_never_silently_deleted(self):
        notice = privacy()
        self.assertIn("提供を終了した推理メモの文章が残っている場合", notice)
        self.assertIn("ブラウザのサイトデータを削除すると記録も消えます", notice)
        self.assertIn("同じ方法で削除できます", notice)


if __name__ == "__main__":
    unittest.main()
