"""Sourced JP/US stock examples keep currency, session date and legacy rules."""
import copy
from datetime import date, timedelta
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest

from scripts.market_bulletins import (
    load_market_bulletins, render_market_bulletin,
    render_market_bulletins_archive, render_market_bulletins_index, market_bulletin_feed_xml,
)

SOURCE = 'https://example.org/checked-filing'


def stock(**changes):
    return {
        'code': 'ABCDE', 'name': 'Test US company', 'exchange': 'NASDAQ',
        'currency': 'USD', 'market_date': '2026-11-16',
        'previous_market_date': '2026-11-13', 'price_kind': 'close',
        'observed_at': '2026-11-17T06:00:00+09:00',
        'previous_close': 100.0, 'close': 101.0, 'direction': '上昇',
        'source_url': SOURCE, 'corporate_action_checked': True,
        'adjustment_note': 'Test fixture: no split or reverse split in this comparison.',
        'context': 'Test fixture: a checked filing provides the reported change and the next company event.',
        **changes,
    }


def report(picks=(), *, day='2026-11-17', slot='am'):
    return {
        'coverage': 'investor', 'slug': f'{day}-{slot}', 'day': day, 'slot': slot,
        'published_at': day + 'T08:30:00+09:00',
        'title': '日米の株価と企業材料を確認するテスト記事',
        'summary': '公開資料に記録された株価と企業材料の表示を検証する架空のテストデータです。',
        'lead': 'これはテスト専用の入力であり、実際の市場価格や企業の動向を示す公開記事ではありません。',
        'sources': [{'title': 'Synthetic checked source', 'url': SOURCE,
                     'published_on': day, 'checked_on': day}],
        'sections': [{'heading': 'テスト資料の比較', 'paragraphs': [
            '値動きと資料に記載された材料を区別し、観測日と実公開日時を取り違えず表示できることを検証する。']}],
        'figures': [
            {'id': f'comparison-{i}', 'kind': 'bars', 'title': 'テスト数値の比較',
             'note': 'Synthetic values for rendering tests only.', 'unit': 'ポイント',
             'source_url': SOURCE, 'observed_at': day + 'T08:00:00+09:00',
             'values': [{'label': '比較元', 'value': 100.0, 'state': '実績'},
                        {'label': '比較先', 'value': 101.0, 'state': '実績'}]}
            for i in range(2)],
        'picks': list(picks),
    }


def load(item):
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / 'content'
        folder.mkdir()
        (folder / 'market_bulletins.json').write_text(
            json.dumps([item], ensure_ascii=False), encoding='utf-8')
        return load_market_bulletins(tmp, now=date.max)[0]


def content_layout(_title, _description, _url, content, **_kwargs):
    return content


class UnsafeMarkup(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.unsafe = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'iframe') or any(k.startswith('on') for k, _ in attrs):
            self.unsafe.append(tag)


class InvestorCoverageTests(unittest.TestCase):
    def test_mixed_small_moves_render_each_market_currency_and_price_kind(self):
        item = report([
            stock(code='123A', name='Test JP company', exchange='東証', currency='JPY',
                  observed_at='2026-11-16T15:30:00+09:00'),
            stock(close=99.25, direction='下落', price_kind='intraday'),
            stock(code='BRK.B', exchange='NYSE', close=100.0, direction='横ばい'),
        ])
        original = copy.deepcopy(item)
        self.assertEqual(load(item), original)
        output = render_market_bulletin(item, content_layout)
        for text in ('注目銘柄の材料と値動き', '123A・東証', 'ABCDE・NASDAQ', 'BRK.B・NYSE',
                     '101.00 JPY', '99.25 USD', '途中値：', '終値：',
                     '2026-11-13', '2026-11-16（米国東部時間）', '2026-11-16（日本時間）'):
            self.assertIn(text, output)
        self.assertNotIn('USD円', output)
        self.assertNotIn('ABCDE・東証', output)
        self.assertNotIn('日本株の急騰・急落', output)
        self.assertIn('日本の指数（JPX）', output)
        self.assertNotIn('現在の指数（JPX）', output)
        self.assertIn('<time datetime="2026-11-17T06:00:00+09:00">2026-11-17 06:00</time> JST', output)
        self.assertNotIn('2026-11-17T06:00:00+09:00 JST', output)
        self.assertNotIn('market-stock-pick--down"><span class="market-stock-direction">横ばい', output)
        self.assertIn('market-stock-pick--flat"><span class="market-stock-direction">横ばい', output)
        self.assertIn('日米株の材料と値動き', render_market_bulletins_index([item]))

    def test_existing_four_slots_allow_zero_or_one_example_without_a_mover_quota(self):
        for slot in ('am', 'open', 'noon', 'close'):
            for picks in ([], [stock()]):
                with self.subTest(slot=slot, count=len(picks)):
                    item = report(picks, slot=slot)
                    self.assertEqual(load(item), item)
                    if not picks:
                        self.assertNotIn('class="market-stock-picks"', render_market_bulletin(item, content_layout))
        for picks in ([stock(code=code) for code in ('A', 'B', 'C', 'D')], None, {}):
            item = report()
            item['picks'] = picks
            with self.subTest(picks=picks), self.assertRaisesRegex(ValueError, 'zero to three'):
                load(item)

    def test_uppercase_us_tickers_allow_share_classes_but_not_markup_or_foreign_codes(self):
        for code in ('A', 'ABCDE', 'BRK.B', 'BF-B'):
            self.assertEqual(load(report([stock(code=code)]))['picks'][0]['code'], code)
        for code in ('abc', 'ABCDEF', '$AAPL', 'BRK..B', '1234', 'A/B', 'A<script>', ''):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, 'code does not match'):
                load(report([stock(code=code)]))

    def test_exchange_local_date_handles_new_york_dst_and_jst_day_boundary(self):
        cases = [
            ('2026-11-17T06:00:00+09:00', '2026-11-16'),
            ('2026-01-15T13:30:00+09:00', '2026-01-14'),
            ('2026-07-15T13:30:00+09:00', '2026-07-15'),
        ]
        for observed, market_day in cases:
            previous = (date.fromisoformat(market_day) - timedelta(days=1)).isoformat()
            quote = stock(observed_at=observed, market_date=market_day,
                          previous_market_date=previous, price_kind='intraday')
            item = report([quote], day=observed[:10])
            item['published_at'] = observed[:10] + 'T23:00:00+09:00'
            with self.subTest(observed=observed):
                self.assertEqual(load(item)['picks'][0]['market_date'], market_day)
                quote['market_date'] = (date.fromisoformat(market_day) + timedelta(days=1)).isoformat()
                with self.assertRaisesRegex(ValueError, 'exchange-local'):
                    load(item)
        with self.assertRaisesRegex(ValueError, 'exchange-local'):
            load(report([stock(exchange='東証', code='1234', currency='JPY')]))

    def test_currency_exchange_quote_kind_and_previous_date_are_required(self):
        for changes in (
            {'currency': 'JPY'}, {'currency': None}, {'exchange': 'TSE'},
            {'exchange': 'LSE'}, {'price_kind': None}, {'price_kind': 'latest'},
            {'previous_market_date': '2026-11-16'}, {'previous_market_date': '2026-11-17'},
            {'market_date': '2026-11-6'},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                load(report([stock(**changes)]))
        with self.assertRaisesRegex(ValueError, 'postdate'):
            load(report([stock(observed_at='2026-11-17T09:00:00+09:00')]))

    def test_closing_quotes_wait_for_normal_exchange_close_including_dst(self):
        for quote in (
            stock(observed_at='2026-11-17T00:00:00+09:00'),  # New York 10:00.
            stock(code='1234', exchange='東証', currency='JPY',
                  observed_at='2026-11-16T15:29:59+09:00'),
        ):
            with self.subTest(quote=quote), self.assertRaisesRegex(ValueError, 'session close'):
                load(report([quote]))
        summer = stock(market_date='2026-07-14', previous_market_date='2026-07-13',
                       observed_at='2026-07-15T05:00:00+09:00')
        self.assertEqual(load(report([summer], day='2026-07-15'))['picks'][0]['price_kind'], 'close')
        summer['observed_at'] = '2026-07-15T04:59:59+09:00'
        with self.assertRaisesRegex(ValueError, 'session close'):
            load(report([summer], day='2026-07-15'))

    def test_source_cited_short_session_accepts_its_actual_close_and_renders_evidence(self):
        calendar_url = 'https://example.org/official-exchange-session-calendar'
        quote = stock(market_date='2026-11-27', previous_market_date='2026-11-25',
                      observed_at='2026-11-28T03:00:00+09:00',
                      session_close_at='2026-11-28T03:00:00+09:00',
                      session_source_url=calendar_url)
        item = report([quote], day='2026-11-28')
        item['sources'].append({'title': 'Synthetic official session evidence', 'url': calendar_url,
                                'published_on': '2026-11-27', 'checked_on': '2026-11-28'})
        output = render_market_bulletin(load(item), content_layout)
        self.assertIn('2026-11-27 13:00</time>（米国東部時間）', output)
        self.assertIn('取引時間の出典', output)
        self.assertIn('href="' + calendar_url + '"', output)
        for changes in (
            {'session_close_at': '2026-11-29T03:00:00+09:00'},  # Wrong NY day.
            {'session_close_at': '2026-11-28T06:01:00+09:00'},  # After normal close.
            {'session_close_at': '2026-11-27T23:30:00+09:00'},  # At normal open.
            {'session_source_url': 'https://example.org/uncited-calendar'},
            {'observed_at': '2026-11-28T02:59:59+09:00'},
        ):
            bad = copy.deepcopy(item)
            bad['picks'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                load(bad)
        for missing in ('session_close_at', 'session_source_url'):
            bad = copy.deepcopy(item)
            del bad['picks'][0][missing]
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, 'both time and source'):
                load(bad)
        bad = copy.deepcopy(item)
        bad['published_at'] = '2026-11-28T02:30:00+09:00'
        # Keep the chart contract valid while isolating a future session override.
        bad['figures'] = copy.deepcopy(item['figures'])
        for figure in bad['figures']:
            figure['observed_at'] = '2026-11-28T02:00:00+09:00'
        bad['picks'][0].update(price_kind='intraday', observed_at='2026-11-28T02:00:00+09:00')
        with self.assertRaisesRegex(ValueError, 'Session close cannot postdate'):
            load(bad)

    def test_direction_uses_actual_sign_and_large_mover_labels_still_require_five_percent(self):
        for value, direction in ((101, '上昇'), (99, '下落'), (100, '横ばい'),
                                 (110, '急騰'), (90, '急落')):
            self.assertEqual(load(report([stock(close=value, direction=direction)]))['picks'][0]['direction'], direction)
        for value, direction in ((101, '下落'), (99, '横ばい'), (100, '上昇'),
                                 (101, '急騰'), (99, '急落'), (110, '急落'), (90, '急騰')):
            with self.subTest(value=value, direction=direction), self.assertRaises(ValueError):
                load(report([stock(close=value, direction=direction)]))

    def test_duplicates_use_exchange_and_code_and_provenance_is_not_optional(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate exchange'):
            load(report([stock(), stock()]))
        with self.assertRaisesRegex(ValueError, 'Duplicate exchange'):
            load(report([stock(exchange='NYSE', code='BRK.B'), stock(exchange='NYSE', code='BRK-B')]))
        self.assertEqual(len(load(report([stock(), stock(exchange='NYSE')]))['picks']), 2)
        for changes in ({'source_url': 'https://example.org/uncited'},
                        {'corporate_action_checked': False}, {'adjustment_note': ''}, {'context': 'short'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                load(report([stock(**changes)]))
        for field in ('close', 'previous_close'):
            for value in (0, -1, True, float('nan'), float('inf')):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    load(report([stock(**{field: value})]))

    def test_card_labels_and_source_attributes_escape_untrusted_text(self):
        url = 'https://example.org/filing?a=1&label="quote"'
        item = report([stock(name='<script>company</script>',
                             context='<img src=x onerror=bad()> is text from a synthetic context for this test.',
                             adjustment_note='<iframe>split note</iframe>', source_url=url)])
        item['sources'][0]['url'] = url
        for figure in item['figures']:
            figure['source_url'] = url
        output = render_market_bulletin(load(item), content_layout)
        self.assertEqual(UnsafeMarkup(output).unsafe, [])
        self.assertIn('&lt;script&gt;company&lt;/script&gt;', output)
        self.assertIn('&lt;img src=x onerror=bad()&gt;', output)
        self.assertIn('&amp;label=&quot;quote&quot;', output)

    def test_unknown_coverage_is_rejected_and_missing_coverage_keeps_legacy_gates(self):
        for mode in ('unknown', 'japan', None, True):
            item = report()
            item['coverage'] = mode
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'coverage mode'):
                load(item)
        for slot in ('am', 'open', 'noon'):
            item = report([stock()], slot=slot)
            del item['coverage']
            with self.subTest(slot=slot), self.assertRaisesRegex(ValueError, 'Broad market overview'):
                load(item)
        item = report(slot='close')
        del item['coverage']
        with self.assertRaisesRegex(ValueError, 'both Japanese risers and fallers'):
            load(item)
        up = stock(code='1234', exchange='東証', close=110, direction='急騰')
        down = stock(code='2345', exchange='東証', close=90, direction='急落')
        item['picks'] = [up, down]
        loaded = load(item)
        output = render_market_bulletin(loaded, content_layout)
        self.assertIn('急騰と急落：日本株の注目例', output)
        self.assertIn('前日終値：100.00円 ／ 観測値：110.00円', output)
        item['picks'][0] = stock(direction='急騰', close=110)
        with self.assertRaisesRegex(ValueError, 'Japanese-listed'):
            load(item)

    def test_archive_describes_japan_and_us_coverage_without_a_us_stock_ban(self):
        output = render_market_bulletins_archive([], content_layout)
        self.assertIn('日本株・米国株の値動きと材料', output)
        self.assertNotIn('米国の個別株は掲載しません', output)
        self.assertIn('日米の指数・個別株の材料と値動き', market_bulletin_feed_xml([]))


if __name__ == '__main__':
    unittest.main()
