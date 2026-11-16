"""Publication time never substitutes for the time of a market observation."""
import copy
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET

from scripts.market_bulletins import (
    KEY, SLOTS, load_market_bulletins, market_bulletin_feed_xml,
    observation_window, render_market_bulletin, render_market_bulletins_index, render_market_freshness,
)
from scripts.market_outlooks import load_market_outlooks
from scripts.serials import render_series_page
from tests.test_market_outlooks import original, review

ROOT = Path(__file__).resolve().parents[1]


def passthrough(_title, _description, _path, body, **_kwargs):
    return body


def store(root, name, rows):
    folder = Path(root) / "content"
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


class MarketFreshnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixed_slugs = {"2026-11-15-noon", "2026-11-15-close", "2026-11-16-am"}
        cls.reports = [report for report in load_market_bulletins(ROOT, now=date.max)
                       if report["slug"] in fixed_slugs]
        cls.market = next(s for s in json.loads((ROOT / "content/serials.json").read_text())
                          if s["slug"] == "market-lab")

    def test_observation_range_uses_all_three_collections_and_not_publication(self):
        report = {
            "published_at": "2026-11-16T08:31:07+09:00",
            "figures": [{"observed_at": "2026-11-13T16:59:00+09:00"}],
            "conditions": [{"observed_at": "2026-11-13T17:00:00+09:00"}],
            "picks": [{"observed_at": "2026-11-13T15:30:00+09:00"}],
        }
        before = copy.deepcopy(report)
        start, end = observation_window(report)
        self.assertEqual(start.isoformat(), "2026-11-13T15:30:00+09:00")
        self.assertEqual(end.isoformat(), "2026-11-13T17:00:00+09:00")
        rendered = render_market_freshness(report)
        self.assertIn('datetime="2026-11-16T08:31:07+09:00"', rendered)
        self.assertIn("2026-11-16 08:31", rendered)
        self.assertIn("2026-11-13 15:30", rendered)
        self.assertIn("2026-11-13 17:00", rendered)
        self.assertIn("最終公開", rendered)
        self.assertIn("観測値", rendered)
        self.assertIn('href="https://www.jpx.co.jp/markets/indices/realvalues/"', rendered)
        self.assertEqual(report, before)

    def test_single_or_missing_observation_is_not_expanded_into_a_fake_range(self):
        report = {"published_at": "2026-11-16T08:31:07+09:00",
                  "figures": [{"observed_at": "2026-11-13T16:59:00+09:00"}]}
        one = render_market_freshness(report, include_publication=False)
        self.assertEqual(one.count("<time "), 1)
        self.assertNotIn("〜", one)
        self.assertNotIn("08:31", one)
        missing = render_market_freshness({"published_at": report["published_at"]})
        self.assertNotIn("観測値", missing)
        self.assertEqual(missing.count("<time "), 1)
        self.assertEqual(render_market_freshness({}), "")

    def test_future_or_malformed_observation_is_rejected_in_every_collection(self):
        for group in ("figures", "conditions", "picks"):
            with self.subTest(group=group):
                data = {"published_at": "2026-11-16T08:31:07+09:00",
                        group: [{"observed_at": "2026-11-16T09:00:00+09:00"}]}
                with self.assertRaisesRegex(ValueError, "cannot follow publication"):
                    render_market_freshness(data)
                data[group][0]["observed_at"] = '2026-11-13T16:59:00+09:00" onmouseover="evil'
                with self.assertRaises(ValueError):
                    render_market_freshness(data)

    def test_untrusted_labels_are_not_inserted_into_the_time_annotation(self):
        data = {"published_at": "2026-11-16T08:31:07+09:00",
                "conditions": [{"name": '<script>alert("bad")</script>',
                                "observed_at": "2026-11-13T16:59:00+09:00"}]}
        output = render_market_freshness(data)
        self.assertNotIn("<script", output)
        self.assertNotIn("onmouseover", output)
        with self.assertRaises(ValueError):
            render_market_freshness({"published_at": '<script>alert("bad")</script>'})

    def test_market_entry_displays_actual_publication_and_old_data_separately(self):
        report = self.reports[-1]
        before = copy.deepcopy(report)
        page = render_series_page(self.market, passthrough, bulletins=[report])
        self.assertIn("最終公開", page)
        self.assertIn('datetime="' + report["published_at"] + '"', page)
        self.assertIn("2026-11-16 08:31", page)
        self.assertIn("2026-11-13 16:59", page)
        self.assertIn("2026-11-13 17:00", page)
        self.assertLess(page.index("観測値"), page.index('id="market-focus"'))
        self.assertEqual(report, before)

    def test_four_slots_preserve_old_keys_and_add_open_without_fake_publish_time(self):
        self.assertEqual({k: v[0] for k, v in SLOTS.items()},
                         {"am": "08:30", "open": "09:40", "noon": "12:40", "close": "15:50"})
        for slot in SLOTS:
            self.assertIsNotNone(KEY.fullmatch("2026-11-16-" + slot))
        self.assertIsNone(KEY.fullmatch("2026-11-16-late"))
        legacy = next(x for x in self.reports if x["slot"] == "close")
        markup = render_market_bulletin(legacy, passthrough)
        self.assertIn("実データの市況解説 / 18:38", markup)
        self.assertNotIn("実データの市況解説 / 15:50", markup)

    def test_new_open_report_loads_as_market_overview_and_rejects_stock_picks(self):
        report = copy.deepcopy(self.reports[-1])
        report.update(slot="open", slug="2026-11-16-open",
                      published_at="2026-11-16T09:41:00+09:00")
        with tempfile.TemporaryDirectory() as tmp:
            store(tmp, "market_bulletins.json", [report])
            self.assertEqual(load_market_bulletins(tmp, now=date(2026, 11, 16)), [report])
            feed = ET.fromstring(market_bulletin_feed_xml([report]))
            self.assertEqual(feed.findtext("channel/item/link"),
                             "https://veripsa.com/serials/market-lab/briefings/2026-11-16-open/")
            report["picks"] = copy.deepcopy(next(x for x in self.reports if x["slot"] == "close")["picks"])
            store(tmp, "market_bulletins.json", [report])
            with self.assertRaisesRegex(ValueError, "Broad market overview"):
                load_market_bulletins(tmp, now=date(2026, 11, 16))

    def test_open_review_identity_is_valid_without_rewriting_original_forecast(self):
        first = original()
        update = review(id="2026-11-16-open-01", at="2026-11-16T09:40:00+09:00")
        before = copy.deepcopy(first)
        with tempfile.TemporaryDirectory() as tmp:
            store(tmp, "market_outlooks.json", [first, update])
            loaded = load_market_outlooks(tmp, today=date(2026, 11, 16))
        self.assertEqual(loaded, [first, update])
        self.assertEqual(first, before)

    def test_intraday_cards_show_actual_publish_minutes_not_nominal_slot_times(self):
        first = copy.deepcopy(self.reports[-1])
        first.update(slot="open", slug="2026-11-16-open",
                     published_at="2026-11-16T09:41:00+09:00")
        second = dict(first, slot="noon", slug="2026-11-16-noon",
                      published_at="2026-11-16T12:44:39+09:00")
        markup = render_market_bulletins_index([first, second])
        early = '<time datetime="2026-11-16T09:41:00+09:00">2026-11-16 09:41</time>'
        latest = '<time datetime="2026-11-16T12:44:39+09:00">2026-11-16 12:44</time>'
        self.assertIn(early, markup)
        self.assertIn(latest, markup)
        self.assertLess(markup.index(latest), markup.index(early))
        self.assertNotIn('>2026-11-16 09:40</time>', markup)
        self.assertNotIn('>2026-11-16 12:40</time>', markup)


if __name__ == "__main__":
    unittest.main()
