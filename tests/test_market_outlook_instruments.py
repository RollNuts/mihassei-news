"""Synthetic multi-market forecasts use their own instrument, session and prices."""
from datetime import date, datetime, timedelta
from html import unescape
import json
from pathlib import Path
import tempfile
import unittest

from scripts.market_outlooks import (load_market_outlooks, pending_market_forecasts, render_market_accuracy,
                                     render_market_outlook_archive)
from tests.test_market_outlooks import original, review


INSTRUMENTS = {
    "nikkei225": ("日経平均株価", "JPY"),
    "sp500": ("S&P 500", "ポイント"),
    "tse-5016": ("テスト金属（5016・東証）", "JPY"),
    "nasdaq-aapl": ("Apple（AAPL・NASDAQ）", "USD"),
    "nyse-brk-b": ("Berkshire（BRK.B・NYSE）", "USD"),
}
JPX_CALENDAR = "https://www.jpx.co.jp/corporate/about-jpx/calendar/"
US_CALENDAR = "https://www.nyse.com/markets/hours-calendars"


def instrument_forecast(key):
    subject, unit = INSTRUMENTS[key]
    japan = key == "nikkei225" or key.startswith("tse-")
    source = f"https://example.org/market-test/{key}"
    calendar = JPX_CALENDAR if japan else US_CALENDAR
    baseline_time = "2026-11-13T15:30:00+09:00" if japan else "2026-11-14T06:00:00+09:00"
    close_time = "2026-11-17T15:30:00+09:00" if japan else "2026-11-18T06:00:00+09:00"
    fc = original()
    fc.update(id="2026-11-16-" + key, subject=subject, statement=fc["statement"].replace("TOPIX", subject),
              invalidated_if=fc["invalidated_if"].replace("TOPIX", subject),
              target_session={"close_at": close_time, "source_url": calendar})
    fc["baseline"].update(value=100.0, unit=unit, price_kind="close", observed_at=baseline_time,
                          instrument_id=key, source_url=source)
    price = {**fc["sources"][0], "url": source, "instrument_id": key, "title": subject + " fixture"}
    fc["sources"] = [price, {**fc["sources"][0], "url": calendar, "title": "Fixture trading calendar"}]
    return fc


def instrument_result(fc, close):
    key = fc["baseline"]["instrument_id"]
    observed = fc["target_session"]["close_at"]
    when = (datetime.fromisoformat(observed) + timedelta(seconds=1)).isoformat()
    source = {**fc["sources"][0], "published_on": fc["reviewed_on"], "checked_on": when[:10]}
    result = {"instrument_id": key, "close": close, "unit": fc["baseline"]["unit"],
              "observed_at": observed, "observed_on": fc["reviewed_on"], "price_kind": "close",
              "outcome": "的中" if close > 100.5 else "外れ", "source_url": source["url"],
              "explanation": "合成したテスト用の対象銘柄の公式終値を、予想公表時に決めた基準値と判定日で比較した結果。"}
    if key.startswith(("tse-", "nasdaq-", "nyse-")):
        result.update(corporate_action_checked=True, adjustment_note="Fixture baseline and close are on the same split basis.")
    return review(id=when[:10] + "-close-" + fc["id"], at=when, forecast_id=fc["id"], source=source,
                  result=result)


def load_events(events):
    with tempfile.TemporaryDirectory() as root:
        folder = Path(root) / "content"
        folder.mkdir()
        (folder / "market_outlooks.json").write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
        return load_market_outlooks(root, today=date(2026, 11, 30))


class MarketOutlookInstrumentTests(unittest.TestCase):
    def test_legacy_topix_and_new_instruments_keep_same_day_ids_units_and_anchors(self):
        forecasts = [instrument_forecast(key) for key in INSTRUMENTS]
        closes = [instrument_result(fc, 102) for fc in forecasts]
        events = [original(), *forecasts, *sorted(closes, key=lambda row: row["at"])]
        self.assertEqual(load_events(events), events)
        archive = render_market_outlook_archive(events, lambda _t, _d, _p, body, **kw: body)
        for fc in forecasts:
            with self.subTest(key=fc["id"]):
                card = archive.split('id="forecast-' + fc["id"] + '"', 1)[1].split("</article>", 1)[0]
                unit = {"JPY": "円", "USD": "米ドル"}.get(fc["baseline"]["unit"], "ポイント")
                self.assertIn("100.00" + unit, card)
                self.assertIn("102.00" + unit, card)
        self.assertIn('id="forecast-2026-11-16-topix"', archive)

    def test_mismatched_currency_instrument_and_nonfinal_prices_cannot_settle_a_forecast(self):
        fc = instrument_forecast("nasdaq-aapl")
        for change in ({"instrument_id": "nyse-brk-b"}, {"unit": "JPY"}, {"price_kind": "intraday"},
                       {"observed_at": "2026-11-18T05:59:59+09:00"}, {"observed_on": "2026-11-18"},
                       {"corporate_action_checked": False}):
            with self.subTest(change=change):
                verdict = instrument_result(fc, 102)
                verdict["result"].update(change)
                with self.assertRaises(ValueError):
                    load_events([fc, verdict])
        for change in ({"instrument_id": "nikkei225"}, {"unit": "ポイント"}):
            with self.subTest(baseline=change):
                invalid = instrument_forecast("tse-5016")
                invalid["baseline"].update(change)
                with self.assertRaises(ValueError):
                    load_events([invalid])
        verdict = instrument_result(fc, 102)
        verdict["source"]["instrument_id"] = "nyse-brk-b"
        with self.assertRaises(ValueError):
            load_events([fc, verdict])

    def test_us_close_uses_new_york_session_date_and_original_close_time(self):
        fc = instrument_forecast("nyse-brk-b")
        # The US Friday after Thanksgiving ends at 13:00 EST, already Saturday in Japan.
        fc["reviewed_on"] = "2026-11-27"
        fc["target_session"]["close_at"] = "2026-11-28T03:00:00+09:00"
        verdict = instrument_result(fc, 102)
        self.assertEqual(load_events([fc, verdict]), [fc, verdict])
        verdict["at"] = "2026-11-28T02:59:59+09:00"
        with self.assertRaises(ValueError):
            load_events([fc, verdict])

    def test_open_us_session_can_have_target_date_yesterday_in_japan(self):
        fc = instrument_forecast("sp500")
        fc.update(id="2026-11-24-sp500", at="2026-11-24T03:30:00+09:00", reviewed_on="2026-11-23")
        fc["target_session"]["close_at"] = "2026-11-24T06:00:00+09:00"
        events = load_events([fc, instrument_result(fc, 102)])
        before = datetime.fromisoformat("2026-11-24T05:59:59+09:00")
        self.assertEqual([row["forecast"] for row in pending_market_forecasts(events, before=before)], [fc])
        self.assertIn("未確定1件は集計外", render_market_accuracy(events, before=before))
        at_close = datetime.fromisoformat(fc["target_session"]["close_at"])
        self.assertEqual(pending_market_forecasts(events, before=at_close), [])
        self.assertIn("未確定1件は集計外", render_market_accuracy(events, before=at_close))
        self.assertIn("判定済み1件のうち1件が的中", render_market_accuracy(events))
        self.assertEqual(pending_market_forecasts(events, before=datetime.fromisoformat(events[-1]["at"])), [])

    def test_each_instruments_score_uses_only_its_verified_closes_at_publication(self):
        japan, america, index = map(instrument_forecast, ("tse-5016", "nasdaq-aapl", "sp500"))
        observed = "2026-11-17T00:30:00+09:00"  # Monday 10:30 in New York.
        interim = review(id="2026-11-17-open-" + index["id"], at=observed, forecast_id=index["id"],
                         observed_value=110, observed_on="2026-11-16", observed_at=observed,
                         price_kind="intraday", instrument_id="sp500", unit="ポイント",
                         trend="暫定的に整合", stance="強気",
                         source={**index["sources"][0], "published_on": "2026-11-16", "checked_on": "2026-11-17"})
        events = load_events([japan, america, index, interim, instrument_result(japan, 102),
                              instrument_result(america, 98)])
        before_us_close = datetime.fromisoformat("2026-11-18T05:59:59+09:00")
        upcoming = pending_market_forecasts(events, before=before_us_close)
        self.assertEqual({row["forecast"]["id"] for row in upcoming}, {america["id"], index["id"]})
        score = render_market_accuracy(events)
        self.assertEqual(score.count('class="market-score-ranges"'), 1)

        def score_cards(before=None):
            # Parse only score blocks, so a label elsewhere on the page cannot satisfy an assertion.
            blocks = unescape(render_market_accuracy(events, before=before)).split('class="market-reader-accuracy"')[1:]
            self.assertEqual(len(blocks), 3)
            return {fc["subject"]: next(block for block in blocks if fc["subject"] + "の短期予想" in block)
                    for fc in (japan, america, index)}

        scores = score_cards()
        for fc, rate, counts in ((japan, "100%", "判定済み1件のうち1件が的中"),
                                 (america, "0%", "判定済み1件のうち0件が的中"),
                                 (index, "—", "未確定1件は集計外")):
            with self.subTest(subject=fc["subject"]):
                self.assertIn("<strong>" + rate + "</strong>", scores[fc["subject"]])
                self.assertIn(counts, scores[fc["subject"]])
        self.assertNotIn("market-score-example", scores[index["subject"]])
        historical_scores = score_cards(before_us_close)
        self.assertIn("判定済み1件のうち1件が的中", historical_scores[japan["subject"]])
        self.assertIn("確定0件", historical_scores[america["subject"]])
        self.assertNotIn("market-score-example", historical_scores[america["subject"]])

    def test_forecast_id_subject_and_calendar_must_belong_to_the_same_market(self):
        for wrong_subject in ("Apple（AAPL・NYSE）", "Microsoft（MSFT・NASDAQ）", "S&P 500"):
            fc = instrument_forecast("nasdaq-aapl")
            fc["subject"] = wrong_subject
            with self.subTest(subject=wrong_subject), self.assertRaises(ValueError):
                load_events([fc])
        fc = instrument_forecast("sp500")
        fc["sources"][1]["url"] = fc["target_session"]["source_url"] = JPX_CALENDAR
        with self.assertRaises(ValueError):
            load_events([fc])

    def test_after_hours_observation_needs_evidence_of_same_sessions_official_close(self):
        fc = instrument_forecast("nasdaq-aapl")
        verdict = instrument_result(fc, 102)
        verdict["at"] = verdict["result"]["observed_at"] = "2026-11-18T06:15:00+09:00"
        for labels in ({}, {"price_kind": "intraday", "market_date": "2026-11-17"},
                       {"price_kind": "close", "market_date": "2026-11-16"}):
            with self.subTest(labels=labels), self.assertRaises(ValueError):
                load_events([fc, {**verdict, "source": {**verdict["source"], **labels}}])
        verdict["source"].update(price_kind="close", market_date="2026-11-17")
        self.assertEqual(load_events([fc, verdict]), [fc, verdict])
