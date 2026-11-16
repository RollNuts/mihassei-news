"""Forecast progression is a sourced timeline, not an early verdict."""
import copy
from datetime import date
import json
from pathlib import Path
import unittest

from scripts.market_outlook import (
    guard_market_outlook_history,
    load_market_outlook,
    render_market_outlook_index,
    render_market_outlook_episode,
    validate_outlook_records,
)
from scripts.serials import render_episode_page, render_series_page

ROOT = Path(__file__).resolve().parents[1]


def identity_layout(_title, _description, _url, body, **_kw):
    return body


class MarketOutlookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        all_series = json.loads((ROOT / "content/serials.json").read_text(encoding="utf-8"))
        cls.market = next(s for s in all_series if s["slug"] == "market-lab")
        cls.all_series = all_series
        cls.records = json.loads((ROOT / "content/market_outlook.json").read_text(encoding="utf-8"))
        cls.outlook = load_market_outlook(ROOT, all_series, today=date(2026, 11, 15))

    def test_interim_progress_is_sourced_but_does_not_announce_a_win(self):
        self.assertEqual(len(self.records), 2)
        for item in self.records:
            self.assertLessEqual(item["source_published_on"], item["checked_on"])
            self.assertEqual(item["observed_on"], "2026-11-15")
            self.assertTrue(item["source_url"].startswith("https://"))
        hub = render_market_outlook_index(self.market, self.outlook)
        self.assertEqual(hub, "")
        self.assertNotIn("判定材料不足", hub)
        self.assertNotIn("支持材料あり", hub)  # Original disclosures are not new proof.
        self.assertNotIn("判断保留", hub)  # Internal state is not front-page news.
        self.assertNotIn("的中率", hub)
        for episode in self.market["episodes"]:
            timeline = render_market_outlook_episode(episode, self.outlook.get(episode["slug"], []))
            page = render_episode_page(
                self.market, episode, identity_layout, outlook=self.outlook.get(episode["slug"], []))
            self.assertNotIn('id="forecast-progress"', page)
            self.assertIn(timeline, page)
            self.assertEqual(timeline, "")
            self.assertNotIn(episode["forecast"]["method"], page)
            self.assertIn(episode["forecast"]["reviewed_on"], page)
            self.assertIn("結果待ち", page)
        series_page = render_series_page(self.market, identity_layout, outlook=self.outlook)
        self.assertNotIn('id="market-outlook"', series_page)

    def test_stance_changes_are_derived_without_overwriting_the_old_snapshot(self):
        series = copy.deepcopy(self.records)
        update = copy.deepcopy(series[-1])
        update["observed_on"] = "2026-11-16"
        update["checked_on"] = "2026-11-16"
        update["source_published_on"] = "2026-11-16"
        update["stance"] = "弱気"
        update["signal"] = "challenging"
        update["summary"] = "追加の一次資料で方向が変わった場合だけ新しい記録を追加する。"
        update["reason"] = "後続の資料による変化であり元の予想には手を触れない。"
        series.append(update)
        validate_outlook_records(series, self.all_series, today=date(2026, 11, 16))
        title = render_market_outlook_episode(None, [series[1], series[2]])
        self.assertNotIn("見方を変更", title)
        self.assertNotIn("2026-11-15", title)
        self.assertIn("2026-11-16", title)
        self.assertIn(update["summary"], title)
        self.assertIn(update["reason"], title)
        self.assertIn(update["source_url"], title)
        self.assertNotIn(update["next_trigger"], title)
        self.assertNotIn('<details', title)
        self.assertNotIn("的中率", title)
        self.assertEqual(guard_market_outlook_history(self.records, series), 2)

    def test_history_protects_earlier_statements(self):
        changed = copy.deepcopy(self.records)
        changed[0]["reason"] = "古い観測を後から書き換える"
        with self.assertRaisesRegex(ValueError, "rewritten"):
            guard_market_outlook_history(self.records, changed)
        with self.assertRaisesRegex(ValueError, "rewritten"):
            guard_market_outlook_history(self.records, self.records[:-1])

    def test_bad_sources_dates_and_spoofed_verdicts_are_rejected(self):
        for bad in (
            {"source_url": "http://untrusted.example"},
            {"source_published_on": "2026-11-17"},
            {"observed_on": "2031-11-15"},
            {"stance": "的中"},
            {"result": "的中"},
        ):
            clone = copy.deepcopy(self.records)
            clone[-1].update(bad)
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_outlook_records(clone, self.all_series, today=date(2026, 11, 15))

    def test_dated_market_hub_excludes_fiction(self):
        fiction = next(s for s in self.all_series if s["kind"] == "fiction")
        self.assertEqual(render_market_outlook_index(fiction, self.outlook), "")
