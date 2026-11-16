"""Readers see forecasts, reasons and evidenced results without internal rules."""
import copy
from datetime import date, datetime, timedelta
import html
import json
from pathlib import Path
import unittest

from scripts.market_outlooks import (render_market_outlook_board, render_market_accuracy,
                                     forecast_reader_statement, forecast_reader_reason, forecast_horizon)
from tests.test_market_outlooks import original, review, SOURCE


def final_review(fc=None, *, hit=True):
    fc = fc or original()
    target = fc['reviewed_on']
    return review(
        id=target+'-close-01', at=target+'T18:30:00+09:00', forecast_id=fc['id'],
        stance='強気', trend='暫定的に整合',
        result={'close':3430.0 if hit else 3300.0,'observed_on':target,'source_url':SOURCE,
                'outcome':'的中' if hit else '外れ',
                'explanation':'テスト用の固定基準値と対象日の公式終値を比較し、事前に記録した方向の条件を満たすか確認した結果。'},
    )


class MarketForecastSummaryTests(unittest.TestCase):
    def test_horizon_boundaries_use_original_calendar_dates_and_jst(self):
        start=date(2026,11,16)
        for days,label in ((0,'短期'),(7,'短期'),(8,'中期'),(179,'中期'),(180,'長期')):
            with self.subTest(days=days):
                self.assertEqual(forecast_horizon(start.isoformat(),(start+timedelta(days=days)).isoformat()),label)
        self.assertEqual(forecast_horizon('2026-11-15T15:30:00+00:00','2026-11-23'),'短期')
        with self.assertRaises(ValueError):forecast_horizon('2026-11-16','2026-11-15')
        with self.assertRaises(ValueError):forecast_horizon('2026-11-16T08:30:00','2026-11-17')

    def test_other_horizons_are_visible_but_excluded_from_short_term_accuracy(self):
        short=original()
        medium={**copy.deepcopy(original()),'id':'2026-11-17-topix',
                'at':'2026-11-17T08:30:00+09:00','reviewed_on':'2026-11-25'}
        long={**copy.deepcopy(original()),'id':'2026-11-18-topix',
              'at':'2026-11-18T08:30:00+09:00','reviewed_on':'2027-05-17'}
        events=[short,final_review(short),medium,final_review(medium,hit=False),
                long,final_review(long,hit=False)]
        before=copy.deepcopy(events)
        markup=render_market_outlook_board(events)
        for label in ('短期','中期','長期'):
            self.assertIn('class="market-reader-horizon">'+label+'</span>',markup)
        self.assertIn('100%',markup)
        self.assertIn('的中 1 / 確定 1件',markup)
        self.assertNotIn('33.3%',markup)
        self.assertIn('2027年5月17日のTOPIX',markup)
        no_short=render_market_accuracy([medium,final_review(medium,hit=False),long,final_review(long,hit=False)])
        self.assertIn('確定0件',no_short)
        self.assertNotIn('0%',no_short)
        self.assertEqual(events,before)

    def test_short_statement_uses_original_target_and_direction_without_exposing_method(self):
        fc=original()
        self.assertEqual(forecast_reader_statement(fc),'11月17日のTOPIXは上昇を予想。')
        markup=render_market_outlook_board([fc])
        self.assertIn('datetime="2026-11-16T08:30:00+09:00"',markup)
        self.assertIn('11月17日のTOPIXは上昇を予想。',markup)
        self.assertIn('id="forecast-2026-11-16-topix"',markup)
        self.assertNotIn('market-forecast-table',markup)
        self.assertNotIn(fc['statement'],markup)
        self.assertNotIn('3,400.00',markup)
        self.assertIn('結果待ち',markup)
        self.assertIn('href="'+SOURCE+'"',markup)

    def test_reason_uses_a_complete_existing_rationale_sentence(self):
        text='TOPIXは0.11%上昇したが始値を下回って引けた。グロース指数は0.21%下落した。一次資料が足りず横ばいを基本線とした。'
        expected='TOPIXは0.11%上昇したが始値を下回って引けた。グロース指数は0.21%下落した。'
        fc={**original(),'reason':text}
        self.assertEqual(forecast_reader_reason(fc),expected)
        self.assertIn(expected,render_market_outlook_board([fc]))

    def test_reason_does_not_drop_economic_documents_or_clip_decimal_points(self):
        text='資料を取得できないため未確認。日銀の展望資料が物価の0.5%変化を示した。決算資料では利益が1.2%増えた。これは確実な予告ではない。'
        self.assertEqual(forecast_reader_reason({**original(),'reason':text}),
                         '日銀の展望資料が物価の0.5%変化を示した。決算資料では利益が1.2%増えた。')

    def test_fixed_methods_scenarios_and_policy_history_are_not_in_any_public_html(self):
        fc=original();events=[fc,review()];before=copy.deepcopy(events)
        markup=render_market_outlook_board(events)
        for text in (fc['method'],fc['invalidated_if'],fc['baseline']['method'],
                     *fc['scenarios'].values(),review()['change_reason']):
            self.assertNotIn(html.escape(text),markup)
        self.assertNotIn('3シナリオ',markup)
        self.assertNotIn('判断の変化',markup)
        self.assertEqual(events,before)

    def test_interim_positive_observation_is_neither_a_result_nor_a_zero_percent(self):
        interim=review(stance='強気',trend='暫定的に整合',observed_value=99999.0,observed_on='2026-11-16')
        markup=render_market_outlook_board([original(),interim])
        self.assertIn('結果待ち',markup)
        self.assertIn('確定0件',markup)
        self.assertNotIn('99,999',markup)
        self.assertNotIn('0%',markup)
        self.assertNotIn('判定済み：的中',markup)

    def test_latest_assessment_keeps_the_original_prediction_and_reason_separate(self):
        fc={**original(),'reason':'受注が増加した。利益率も改善した。'}
        latest=review(stance='弱気',reason='新たな費用増で利益率の改善が鈍化した。市場の売りも広がった。')
        events=[fc,latest];before=copy.deepcopy(events)
        markup=render_market_outlook_board(events)
        self.assertIn('11月17日のTOPIXは上昇を予想。',markup)
        self.assertIn('の当初の予想',markup)
        self.assertIn('<strong>当初の理由</strong>'+fc['reason'],markup)
        self.assertIn('最新の評価：',markup)
        self.assertIn('datetime="'+latest['at']+'"',markup)
        self.assertIn('弱気（暫定的に逆行）',markup)
        self.assertIn('<strong>評価の理由</strong>'+latest['reason'],markup)
        self.assertIn('評価の出典：'+latest['source']['title'],markup)
        self.assertLess(markup.index(fc['reason']),markup.index(latest['reason']))
        self.assertIn('確定0件',markup)
        self.assertNotIn('0%',markup)
        self.assertEqual(events,before)

    def test_late_source_review_is_due_but_does_not_make_a_reported_close_final(self):
        late=review(id='2026-11-18-close-01',at='2026-11-18T18:30:00+09:00',
                    stance='弱気',observed_value=3300.0,observed_on='2026-11-17',
                    reason='報道された終値は当初の上昇予想を下回った。公式終値の確認ができていないため、正式な結果は保留する。',
                    source={'title':'終値の報道','url':'https://example.com/reported-close',
                            'published_on':'2026-11-17','checked_on':'2026-11-18'})
        events=[original(),late]
        markup=render_market_outlook_board(events)
        self.assertIn('結果日到来・確認待ち',markup)
        self.assertIn(late['reason'],markup)
        self.assertIn('datetime="2026-11-17"',markup)
        self.assertIn('集計時点：<time datetime="2026-11-18T18:30:00+09:00"',markup)
        self.assertIn('確定0件',markup)
        self.assertNotIn('確認済み終値',markup)
        self.assertNotIn('判定済み',markup)
        self.assertNotIn('3,300.00',markup)
        self.assertNotIn('0%',markup)
        past=render_market_outlook_board(events,before=datetime.fromisoformat('2026-11-16T09:00:00+09:00'))
        self.assertIn('結果待ち',past)
        for hidden in (late['reason'],late['source']['url'],'最新の評価：','結果日到来'):
            self.assertNotIn(hidden,past)

    def test_no_review_status_is_qualified_by_recorded_time_without_a_wall_clock(self):
        events=[original()]
        markup=render_market_outlook_board(events)
        self.assertIn('結果待ち',markup)
        self.assertIn('結果日：<time datetime="2026-11-17">2026-11-17</time>',markup)
        self.assertIn('集計時点：<time datetime="2026-11-16T08:30:00+09:00"',markup)
        self.assertIn('この予想の更新評価はまだありません。',markup)
        self.assertNotIn('最新の評価：',markup)
        later=render_market_outlook_board(events,before=datetime.fromisoformat('2026-11-19T09:00:00+09:00'))
        self.assertIn('結果日到来・確認待ち',later)
        self.assertIn('集計時点：<time datetime="2026-11-19T09:00:00+09:00"',later)
        self.assertNotIn('最新の評価：',later)

    def test_review_reason_and_source_are_escaped_and_unsafe_source_is_rejected(self):
        latest=review(reason='売りが広がった。<script>alert("reason")</script>という文字列もデータとして扱う。',
                      source={'title':'終値 <img src=x onerror="bad"> & 資料',
                              'url':'https://example.com/?q=" onclick="bad',
                              'published_on':'2026-11-16','checked_on':'2026-11-16'})
        markup=render_market_outlook_board([original(),latest])
        self.assertIn(html.escape(latest['reason']),markup)
        self.assertIn(html.escape(latest['source']['title']),markup)
        self.assertIn('href="'+html.escape(latest['source']['url'],quote=True)+'"',markup)
        self.assertNotIn('<script>',markup)
        self.assertNotIn('<img src=x',markup)
        self.assertNotIn(' onclick="bad',markup)
        latest['source']['url']='javascript:alert(1)'
        with self.assertRaises(ValueError):
            render_market_outlook_board([original(),latest])

    def test_explicit_final_result_and_rate_do_not_leak_into_earlier_snapshot(self):
        events=[original(),review(),final_review()]
        markup=render_market_outlook_board(events)
        self.assertIn('判定済み：的中',markup)
        self.assertIn('3,430.00',markup)
        self.assertIn('100%',markup)
        self.assertIn('的中 1 / 確定 1件',markup)
        past=render_market_outlook_board(events,before=datetime.fromisoformat('2026-11-16T09:00:00+09:00'))
        self.assertIn('結果待ち',past)
        self.assertIn('確定0件',past)
        self.assertNotIn('3,430.00',past)
        self.assertNotIn('判定済み',past)
        self.assertNotIn('最新の評価：',past)

    def test_rate_uses_all_confirmed_records_not_only_three_visible_cards(self):
        events=[]
        for n in range(6):
            day=date(2026,11,16)+timedelta(days=n)
            fc={**copy.deepcopy(original()),'id':day.isoformat()+'-topix',
                'at':day.isoformat()+'T08:30:00+09:00','reviewed_on':(day+timedelta(days=1)).isoformat()}
            events.append(fc)
            if n<4:events.append(final_review(fc,hit=n<3))
            if n==4:
                held=final_review(fc);held['result']['outcome']='判定不能';events.append(held)
        markup=render_market_outlook_board(events)
        recent,archived=markup.split('<details class="market-reader-past">',1)
        self.assertEqual(recent.count('class="market-reader-card"'),3)
        self.assertEqual(archived.count('class="market-reader-card"'),3)
        self.assertEqual(markup.count('<summary>過去の予想</summary>'),1)
        self.assertIn('75%',markup)
        self.assertIn('的中 3 / 確定 4件',markup)
        self.assertIn('判定保留',markup)
        self.assertIn('短期相場予想の的中率',markup)

    def test_tampered_or_uncited_outcome_cannot_improve_accuracy(self):
        for change in ({'close':3300.0},{'source_url':'https://elsewhere.example/unverified'},
                       {'observed_on':'2026-11-16'},{'explanation':'未確認'}):
            final=final_review();final['result'].update(change)
            with self.subTest(change=change):
                markup=render_market_accuracy([original(),final])
                self.assertIn('確定0件',markup)
                self.assertNotIn('100%',markup)

    def test_safe_anchors_and_limit_keep_old_records_in_one_collapsed_archive(self):
        older={**copy.deepcopy(original()),'id':'2026-11-15-topix','at':'2026-11-15T08:30:00+09:00'}
        markup=render_market_outlook_board([older,original()],limit=1)
        self.assertIn('forecast-2026-11-15-topix',markup.split('過去の予想',1)[1])
        for identity in ('2026-11-16-topix" onclick="bad','2026-99-16-topix','../other'):
            with self.subTest(identity=identity),self.assertRaises(ValueError):
                render_market_outlook_board([{**original(),'id':identity}])

    def test_real_pending_forecasts_keep_zero_confirmations_and_original_records(self):
        root=Path(__file__).resolve().parents[1]
        events=json.loads((root/'content/market_outlooks.json').read_text())
        forecasts=[event for event in events if event['type']=='forecast']
        before=copy.deepcopy(forecasts)
        markup=render_market_outlook_board(forecasts)
        self.assertEqual(markup.count('class="market-reader-card"'),len(forecasts))
        self.assertIn('確定0件',markup)
        self.assertNotIn('確認済み終値',markup)
        self.assertEqual(forecasts,before)

    def test_empty_board_has_no_synthetic_percentage_or_results(self):
        output=render_market_outlook_board([])
        self.assertIn('新しい相場予想はまだありません',output)
        self.assertIn('確定0件',output)
        self.assertNotIn('0%',output)


if __name__=='__main__':
    unittest.main()
