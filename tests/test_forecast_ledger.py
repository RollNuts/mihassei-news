"""Keep a market forecast auditable without claiming performance before evidence."""
from __future__ import annotations

import copy
import json
import unittest
from datetime import date
from pathlib import Path

from scripts.forecast_ledger import prediction_entries, render_forecast_ledger, _verified_outcome

ROOT = Path(__file__).resolve().parents[1]


class MarketForecastLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data=json.loads((ROOT/"content/serials.json").read_text(encoding="utf-8"))
        cls.series=next(s for s in data if s["kind"]=="analysis")

    def test_forecast_is_discoverable_without_invented_performance(self):
        markup=render_forecast_ledger(self.series,today=date(2026,11,3))
        ep=self.series["episodes"][0]
        self.assertIn('id="forecast-ledger"',markup)
        self.assertIn(ep["title"],markup)
        self.assertIn(ep["forecast"]["statement"],markup)
        self.assertIn(ep["forecast"]["method"],markup)
        self.assertIn('href="/serials/market-lab/'+ep["slug"]+'/"',markup)
        self.assertIn("2027-02-04",markup)
        self.assertIn("検証予定",markup)
        self.assertNotIn("100%",markup)
        self.assertNotIn("判定済み",markup)

    def test_due_date_does_not_misrepresent_hypothesis_as_correct(self):
        just_before=prediction_entries(self.series,today=date(2027,2,3))
        on_review=prediction_entries(self.series,today=date(2027,2,4))
        after=prediction_entries(self.series,today=date(2027,2,5))
        self.assertEqual(just_before[0]["state"],"upcoming")
        self.assertEqual(on_review[0]["state"],"due")
        self.assertEqual(after[0]["state"],"due")
        self.assertIn("判定待ち",after[0]["state_label"])
        self.assertNotIn("的中",after[0]["state_label"])

    def test_claiming_verified_without_cited_outcome_fails_closed(self):
        fc=copy.deepcopy(self.series["episodes"][0]["forecast"])
        fc["status"]="検証済み"
        with self.assertRaisesRegex(ValueError,"cited result"):
            _verified_outcome(fc)
        fc["result"]={"outcome":"的中","verified_on":"2027-02-04",
                      "explanation":"2026年11月と2027年2月の終値を比較する計算について確認している途中です。"}
        with self.assertRaisesRegex(ValueError,"evidence"):
            _verified_outcome(fc)

    def test_verified_outcome_needs_real_methodology_and_https_sources(self):
        series=copy.deepcopy(self.series)
        fc=series["episodes"][0]["forecast"]
        fc["status"]="検証済み"
        fc["result"]={
            "outcome":"外れ",
            "verified_on":"2027-02-05",
            "explanation":"比較期間中の対象3銘柄の騰落率を指数と比較し、条件に届かなかったと確認した。",
            "evidence":[{"title":"検証に使った価格資料","url":"https://example.org/markets/review"}],
        }
        result=prediction_entries(series,today=date(2027,2,6))[0]
        self.assertEqual(result["state"],"verified")
        self.assertEqual(result["result"]["outcome"],"外れ")
        markup=render_forecast_ledger(series,today=date(2027,2,6))
        self.assertIn("判定済み：外れ",markup)
        self.assertIn('href="https://example.org/markets/review"',markup)
        fc["result"]["evidence"][0]["url"]="javascript:alert(1)"
        with self.assertRaisesRegex(ValueError,"HTTPS"):
            render_forecast_ledger(series,today=date(2027,2,6))

    def test_escape_all_editorial_prose(self):
        series=copy.deepcopy(self.series)
        fc=series["episodes"][0]["forecast"]
        fc["statement"]='<img src=x onerror=alert(1)>'
        markup=render_forecast_ledger(series,today=date(2026,11,3))
        self.assertNotIn('<img src=x',markup)
        self.assertIn('&lt;img src=x',markup)

    def test_market_serial_hub_and_episode_link_to_public_ledger(self):
        from scripts.serials import render_series_page, render_episode_page
        html_only=lambda title,desc,path,main,**kwargs: main
        hub=render_series_page(self.series,html_only)
        article=render_episode_page(self.series,self.series["episodes"][0],html_only)
        self.assertIn('id="forecast-ledger"',hub)
        self.assertIn("予測は、答え合わせまで残す。",hub)
        self.assertIn('href="/serials/market-lab/#forecast-ledger"',article)
        self.assertNotIn("判定済み：的中",hub)

    def test_fiction_never_has_market_ledger(self):
        mystery={"slug":"case-files","kind":"fiction","episodes":[]}
        self.assertEqual(prediction_entries(mystery,today=date(2026,11,3)),[])
        self.assertEqual(render_forecast_ledger(mystery,today=date(2026,11,3)),"")


if __name__=="__main__":
    unittest.main()
