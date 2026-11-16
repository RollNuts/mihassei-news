"""Forecast comparison remains distinct from provisional and confirmed results."""
import copy
from datetime import datetime
import html
import json
from pathlib import Path
import re
import unittest

from scripts.market_outlooks import render_market_outlook_board
from tests.test_market_outlooks import original, review, SOURCE


def summary(markup):
    match = re.search(r'<div class="market-forecast-summary">(.*?)</table></div>', markup, re.S)
    return match[1] if match else ''


def final_review():
    return review(
        id="2026-11-17-close-01", at="2026-11-17T18:30:00+09:00",
        stance="強気", trend="暫定的に整合",
        result={"close": 3430.0, "observed_on": "2026-11-17", "source_url": SOURCE,
                "outcome": "的中", "explanation": "テスト用の固定条件で判定した結果。"},
    )


class MarketForecastSummaryTests(unittest.TestCase):
    def test_summary_uses_four_explicit_columns_and_links_to_original_forecast_details(self):
        fc = original()
        output = render_market_outlook_board([fc])
        table = summary(output)
        self.assertEqual(table.count('scope="col"'), 4)
        for label in ("予測日", "対象と方向", "固定基準", "判定日・結果"):
            self.assertIn('data-label="' + label + '"', table)
        self.assertIn('role="table"', table)
        self.assertIn('href="#forecast-2026-11-16-topix"', table)
        self.assertIn('id="forecast-2026-11-16-topix"', output)
        self.assertIn('datetime="2026-11-16T08:30:00+09:00"', table)
        self.assertIn('datetime="2026-11-13"', table)
        self.assertIn('datetime="2026-11-17"', table)
        self.assertIn("3,400.00", table)
        self.assertIn("TOPIX", table)
        self.assertIn("上昇予測", table)
        self.assertIn("結果待ち", table)
        self.assertNotIn("確認済み終値", table)

    def test_provisional_observation_or_positive_stance_never_becomes_a_result(self):
        interim = review(stance="強気", trend="暫定的に整合", observed_value=99999.0,
                         observed_on="2026-11-16")
        table = summary(render_market_outlook_board([original(), interim]))
        self.assertIn("結果待ち", table)
        self.assertNotIn("的中", table)
        self.assertNotIn("99,999", table)
        self.assertNotIn("確認済み終値", table)

    def test_only_explicit_confirmed_result_is_shown_and_historical_snapshot_excludes_it(self):
        events = [original(), review(), final_review()]
        full = summary(render_market_outlook_board(events))
        self.assertIn("判定済み：的中", full)
        self.assertIn("確認済み終値", full)
        self.assertIn("3,430.00", full)
        historical = summary(render_market_outlook_board(
            events, before=datetime.fromisoformat("2026-11-16T09:00:00+09:00")))
        self.assertIn("結果待ち", historical)
        self.assertNotIn("3,430.00", historical)
        self.assertNotIn("判定済み", historical)

    def test_long_fixed_method_and_invalidation_stay_in_existing_details_without_rewriting(self):
        fc = original()
        events = [fc, review()]
        before = copy.deepcopy(events)
        markup = render_market_outlook_board(events)
        card = markup.split('class="daily-forecast-card market-forecast-detail"', 1)[1]
        visible, expanded = card.split('<details>', 1)
        for text in (fc["method"], fc["invalidated_if"], fc["baseline"]["method"]):
            self.assertNotIn(html.escape(text), visible)
            self.assertIn(html.escape(text), expanded)
        for text in ("上昇", "3,400.00", "2026-11-17", "結果待ち"):
            self.assertIn(text, visible)
        self.assertIn("判断の変化", expanded)
        self.assertIn(html.escape(review()["change_reason"]), expanded)
        self.assertEqual(events, before)

    def test_limit_and_cutoff_apply_to_table_and_cards_together(self):
        older = original()
        older["id"] = "2026-11-15-topix"
        older["at"] = "2026-11-15T08:30:00+09:00"
        output = render_market_outlook_board([older, original()], limit=1)
        self.assertEqual(output.count('class="daily-forecast-card market-forecast-detail"'), 1)
        self.assertEqual(summary(output).count('scope="row"'), 1)
        self.assertNotIn('forecast-2026-11-15-topix', output)
        cutoff = render_market_outlook_board([older, original()],
                    before=datetime.fromisoformat("2026-11-15T10:00:00+09:00"))
        self.assertIn('forecast-2026-11-15-topix', cutoff)
        self.assertNotIn('forecast-2026-11-16-topix', cutoff)

    def test_unsafe_forecast_ids_cannot_create_fragment_targets(self):
        for identity in ('2026-11-16-topix" onclick="evil', '2026-99-16-topix', '../other'):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                render_market_outlook_board([{**original(), "id": identity}])

    def test_real_pending_forecasts_do_not_acquire_prices_or_hit_rates(self):
        root = Path(__file__).resolve().parents[1]
        events = json.loads((root / "content/market_outlooks.json").read_text())
        forecasts = [event for event in events if event["type"] == "forecast"]
        pending_only = render_market_outlook_board(forecasts)
        table = summary(pending_only)
        self.assertEqual(table.count('scope="row"'), len(forecasts))
        self.assertEqual(table.count('class="market-forecast-state">結果待ち'), len(forecasts))
        self.assertNotIn("的中率", pending_only)
        self.assertNotIn("確認済み終値", table)
        self.assertNotIn("最新価格", table)

    def test_empty_board_has_no_empty_comparison_or_synthetic_result(self):
        output = render_market_outlook_board([])
        self.assertNotIn('market-forecast-table', output)
        self.assertIn("新しい市場見通しの記事はまだありません", output)


if __name__ == "__main__":
    unittest.main()
