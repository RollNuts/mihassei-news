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
        self.assertNotIn(ep["forecast"]["method"],markup)
        self.assertIn('href="/serials/market-lab/'+ep["slug"]+'/"',markup)
        self.assertIn("2027-02-04",markup)
        self.assertIn("結果待ち",markup)
        self.assertNotIn("100%",markup)
        self.assertNotIn("判定済み",markup)
        self.assertIn('class="market-reader-horizon">中期</span>',markup)
        self.assertIn('class="market-reader-horizon">長期</span>',markup)

    def test_due_date_does_not_misrepresent_hypothesis_as_correct(self):
        just_before=prediction_entries(self.series,today=date(2027,2,3))
        on_review=prediction_entries(self.series,today=date(2027,2,4))
        after=prediction_entries(self.series,today=date(2027,2,5))
        original_slug=self.series["episodes"][0]["slug"]
        before=next(row for row in just_before if row["episode"]["slug"] == original_slug)
        due=next(row for row in on_review if row["episode"]["slug"] == original_slug)
        overdue=next(row for row in after if row["episode"]["slug"] == original_slug)
        self.assertEqual(before["state"],"upcoming")
        self.assertEqual(due["state"],"due")
        self.assertEqual(overdue["state"],"due")
        self.assertIn("判定待ち",overdue["state_label"])
        self.assertNotIn("的中",overdue["state_label"])
        for row in after:
            if row["episode"]["slug"] != original_slug:
                self.assertEqual(row["state"],"upcoming", "Later forecasts cannot be due early")

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
        result=next(row for row in prediction_entries(series,today=date(2027,2,6))
                    if row["episode"]["slug"] == series["episodes"][0]["slug"])
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
        self.assertIn("連載の予想と結果",hub)
        self.assertIn('href="/serials/market-lab/#market-proof"',article)
        self.assertNotIn("判定済み：的中",hub)

    def test_fiction_never_has_market_ledger(self):
        mystery={"slug":"case-files","kind":"fiction","episodes":[]}
        self.assertEqual(prediction_entries(mystery,today=date(2026,11,3)),[])
        self.assertEqual(render_forecast_ledger(mystery,today=date(2026,11,3)),"")

    def test_company_methods_remain_private_even_inside_past_forecasts(self):
        series=copy.deepcopy(self.series)
        for n in (4,5):
            ep=copy.deepcopy(series['episodes'][0]);ep.update(number=n,slug='extra-forecast-'+str(n),published_at='2026-11-'+str(12+n))
            series['episodes'].append(ep)
        before=copy.deepcopy(series)
        markup=render_forecast_ledger(series,today=date(2026,11,20))
        recent,past=markup.split('<details class="market-reader-past">',1)
        self.assertEqual(recent.count('class="forecast-ledger-card market-reader-card"'),3)
        self.assertEqual(past.count('class="forecast-ledger-card market-reader-card"'),2)
        self.assertEqual(markup.count('<summary>過去の予想</summary>'),1)
        self.assertNotIn('当初の検証方法',markup)
        for ep in series['episodes']:
            self.assertNotIn(ep['forecast']['method'],markup)
        self.assertEqual(series,before)

    def test_company_future_verdict_is_not_shown_in_historical_view(self):
        series=copy.deepcopy(self.series);fc=series['episodes'][0]['forecast']
        fc.update(status='検証済み',result={
            'outcome':'外れ','verified_on':'2027-02-05',
            'explanation':'比較期間中の対象3銘柄と指数の公式値を比較し、当初の条件に届かなかったと確認した。',
            'evidence':[{'title':'対象日の公開資料','url':'https://example.org/result'}],
        })
        initial=render_forecast_ledger(series,today=date(2026,11,16))
        self.assertIn('結果待ち',initial)
        self.assertNotIn('判定済み：外れ',initial)
        self.assertNotIn('https://example.org/result',initial)
        self.assertIn('判定済み：外れ',render_forecast_ledger(series,today=date(2027,2,6)))

    def test_company_reason_uses_source_facts_not_summary_validation_notes(self):
        series=copy.deepcopy(self.series)
        ep=series['episodes'][-1]
        page=render_forecast_ledger(series,today=date(2026,11,16))
        self.assertIn('営業利益率は約11.2％。',page)
        self.assertNotIn('来年5月の判定条件を固定する。',page)
        ep.pop('sections')
        ep['summary']='会社資料では売上が1.2％伸びた。来年5月の判定条件を固定する。'
        page=render_forecast_ledger(series,today=date(2026,11,16))
        self.assertIn('会社資料では売上が1.2％伸びた。',page)
        self.assertNotIn('来年5月の判定条件を固定する。',page)


if __name__=="__main__":
    unittest.main()
