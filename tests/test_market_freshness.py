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
    render_market_bulletins_archive,
)
from scripts.market_outlooks import load_market_outlooks, render_market_outlook_archive
from scripts.serials import render_series_page, render_serials_feature, render_serials_index
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
        self.assertIn("最新の市況 ／ 寄り付き前", page)
        self.assertIn('datetime="' + report["published_at"] + '"', page)
        self.assertIn("2026-11-16 08:31", page)
        self.assertIn("2026-11-13 16:59", page)
        self.assertIn("2026-11-13 17:00", page)
        lead = page.split('id="market-focus"', 1)[1].split('</section>', 1)[0]
        self.assertLess(lead.index("2026-11-16 08:31"), lead.index('<h2'))
        self.assertLess(lead.index('class="market-paper-dek"'), lead.index("観測値"))
        masthead = page.split('class="market-paper-masthead"', 1)[1].split('</header>', 1)[0]
        self.assertNotIn("観測値", masthead)
        self.assertNotIn("最終公開", page)
        self.assertEqual(report, before)

    def test_close_update_is_prominent_without_labelling_previous_card_latest(self):
        report = copy.deepcopy(next(x for x in self.reports if x["slot"] == "close"))
        report.update(day="2026-11-17", slug="2026-11-17-close",
                      published_at="2026-11-17T16:17:14+09:00")
        previous = dict(report, slot="noon", slug="2026-11-17-noon",
                        published_at="2026-11-17T13:02:24+09:00")
        before = copy.deepcopy([previous, report])
        page = render_series_page(self.market, passthrough, bulletins=[previous, report])
        lead = page.split('id="market-focus"', 1)[1].split('</section>', 1)[0]
        self.assertIn('class="market-latest-update"', lead)
        self.assertIn("最新の市況 ／ 引け後", lead)
        self.assertIn('<time datetime="2026-11-17T16:17:14+09:00">2026-11-17 16:17</time> JST 公開', lead)
        self.assertEqual(page.count('datetime="2026-11-17T16:17:14+09:00"'), 1)
        self.assertNotIn("2031", lead)
        self.assertNotIn("15:50", lead)
        self.assertIn('href="/serials/market-lab/briefings/2026-11-17-close/"', lead)
        recent = page.split('id="market-desk"', 1)[1].split('</section>', 1)[0]
        self.assertIn("2026-11-17 13:02", recent)
        self.assertNotIn('class="market-latest-label"', recent)
        self.assertEqual([previous, report], before)

    def test_full_bulletin_archive_marks_only_the_actual_latest_phase(self):
        early = copy.deepcopy(self.reports[-1])
        late = dict(early, slot="close", slug="2026-11-16-close",
                    published_at="2026-11-16T16:17:14+09:00")
        page = render_market_bulletins_archive([early, late], passthrough)
        self.assertEqual(page.count('class="market-latest-label"'), 1)
        latest_card = page.split('<li>', 1)[1].split('</li>', 1)[0]
        self.assertIn('2026-11-16-close/', latest_card)
        self.assertIn('>2026-11-16 16:17</time> ／ 引け後', latest_card)
        self.assertIn('class="market-latest-label"', latest_card)

    def test_home_and_series_cards_lead_with_daily_update_and_keep_company_analysis(self):
        report = copy.deepcopy(next(x for x in self.reports if x["slot"] == "close"))
        report.update(day="2026-11-17", slug="2026-11-17-close",
                      published_at="2026-11-17T16:17:14+09:00")
        series = copy.deepcopy(self.market)
        company = series["episodes"][-1]
        before = copy.deepcopy([series, report])
        pages = [render_serials_feature([series], bulletins=[report]),
                 render_serials_index([series], passthrough, bulletins=[report])]
        for page in pages:
            with self.subTest(page=page[:40]):
                self.assertIn("最新の市況 ／ 引け後", page)
                self.assertIn('>2026-11-17 16:17</time> JST 公開', page)
                self.assertIn(report["title"], page)
                self.assertIn('class="serial-link" href="/serials/market-lab/briefings/2026-11-17-close/"', page)
                self.assertIn('href="/serials/market-lab/' + company["slug"] + '/"', page)
                self.assertIn(f'企業分析 第{company["number"]}回を読む', page)
                self.assertNotIn("最新公開：" + company["title"], page)
                self.assertNotIn("最新の第", page)
        self.assertEqual([series, report], before)

    def test_older_or_missing_bulletin_does_not_replace_a_newer_company_article(self):
        report = copy.deepcopy(self.reports[-1])
        report["day"] = "2026-11-01"
        for reports in ([], [report]):
            page = render_serials_feature([self.market], bulletins=reports)
            self.assertIn("最新公開：" + self.market["episodes"][-1]["title"], page)
            self.assertNotIn("最新の市況", page)

    def test_outlook_update_uses_latest_record_even_if_it_reviews_an_older_forecast(self):
        earlier = original()
        newer = {**earlier, "id": "2026-11-17-topix", "at": "2026-11-17T08:41:38+09:00"}
        assessment = review(id="2026-11-17-close-01", at="2026-11-17T15:54:30+09:00")
        events = [earlier, newer, assessment]
        before = copy.deepcopy(events)
        page = render_market_outlook_archive(events, passthrough)
        header = page.split('<header', 1)[1].split('</header>', 1)[0]
        self.assertIn("予想・結果の最終更新", header)
        self.assertIn('<time datetime="2026-11-17T15:54:30+09:00">2026年11月17日 15:54</time> JST', header)
        self.assertIn('href="/serials/market-lab/outlooks/#forecast-2026-11-16-topix"', header)
        self.assertNotIn("08:41", header)
        self.assertNotIn("観測", header)
        self.assertEqual(events, before)
        self.assertNotIn("予想・結果の最終更新", render_market_outlook_archive([], passthrough))

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
