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

    def test_withdrawn_fiction_is_not_a_public_feature_or_reader_page(self):
        data = copy.deepcopy(self.data)
        fiction = next(s for s in data if s["slug"] == "case-files")
        market = next(s for s in data if s["kind"] == "analysis")
        feature = render_serials_feature(data)
        self.assertNotIn("case-files", feature)
        self.assertNotIn(fiction["title"], feature)
        self.assertIn(market["title"], feature)
        with self.assertRaisesRegex(ValueError, "withdrawn"):
            render_series_page(fiction, identity_layout)
        with self.assertRaisesRegex(ValueError, "withdrawn"):
            render_episode_page(fiction, fiction["episodes"][0], identity_layout)
        self.assertEqual(data, self.data)

    def test_old_local_notes_are_never_silently_deleted(self):
        notice = privacy()
        self.assertIn("提供を終了した推理メモの文章が残っている場合", notice)
        self.assertIn("ブラウザのサイトデータを削除すると記録も消えます", notice)
        self.assertIn("同じ方法で削除できます", notice)


if __name__ == "__main__":
    unittest.main()
