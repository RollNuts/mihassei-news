"""Reader notebook stays private, accessible and separate from market analysis."""
import json
import unittest
from pathlib import Path

from scripts.serials import render_mystery_notebook, render_episode_page
from scripts.build import privacy

ROOT = Path(__file__).resolve().parents[1]


class MysteryReaderNotebookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.serials = json.loads((ROOT/"content/serials.json").read_text(encoding="utf-8"))

    def test_episode_shows_an_accessible_opt_in_notebook(self):
        fiction = next(series for series in self.serials if series["kind"] == "fiction")
        episode = fiction["episodes"][0]
        markup = render_mystery_notebook(fiction, episode)
        self.assertIn('data-mystery-notebook', markup)
        self.assertIn('data-mystery-serial="'+fiction["slug"]+'"', markup)
        self.assertIn('data-mystery-episode="'+episode["slug"]+'"', markup)
        self.assertIn('label for="mystery-notebook-text"', markup)
        self.assertIn('maxlength="800"', markup)
        self.assertIn('data-mystery-note-save', markup)
        self.assertIn('data-mystery-note-clear', markup)
        self.assertIn('role="status"', markup)
        self.assertIn('aria-live="polite"', markup)
        self.assertIn('src="/assets/mystery-notes.js"', markup)
        self.assertIn('サイトへの送信・読者への公開は行いません', markup)
        self.assertIn('保存を押すまで端末にも記録されません', markup)

    def test_memo_does_not_appear_in_real_market_analysis(self):
        market = next(series for series in self.serials if series["kind"] == "analysis")
        self.assertEqual(render_mystery_notebook(market, market["episodes"][0]), "")
        render = lambda _title, _desc, _path, main, **_kwargs: main
        episode_html = render_episode_page(market, market["episodes"][0], render)
        self.assertNotIn('data-mystery-notebook', episode_html)
        self.assertNotIn('/assets/mystery-notes.js', episode_html)

    def test_memo_is_below_fair_clues_in_actual_episode_markup(self):
        fiction = next(series for series in self.serials if series["kind"] == "fiction")
        render = lambda _title, _desc, _path, main, **_kwargs: main
        page = render_episode_page(fiction, fiction["episodes"][0], render)
        self.assertLess(page.index('読者への手掛かり'), page.index('data-mystery-notebook'))
        self.assertLess(page.index('data-mystery-notebook'), page.index('次回は未公開'))
        self.assertEqual(page.count('/assets/mystery-notes.js'), 1)

    def test_privacy_policy_explains_persistence_and_deletion(self):
        page = privacy()
        for token in ('推理小説の自分用メモ', 'localStorage', '800文字',
                      'ブラウザ間で同期されず', 'サーバーへ送信したり',
                      'メモを消す'):
            self.assertIn(token, page)

    def test_no_paid_script_or_network_action(self):
        source = (ROOT/"assets/mystery-notes.js").read_text(encoding="utf-8")
        for forbidden in ("fetch(", "XMLHttpRequest", "sendBeacon(", "WebSocket(",
                          "axios(", "https://", "http://"):
            self.assertNotIn(forbidden, source)
        css = (ROOT/"assets/serials.css").read_text(encoding="utf-8")
        self.assertIn('@media(max-width:560px)', css)
        self.assertIn('.mystery-notebook', css)


if __name__ == '__main__':
    unittest.main()
