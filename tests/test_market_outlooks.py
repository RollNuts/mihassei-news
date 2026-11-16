"""Real-date market forecasts keep verdicts separate from interim sentiment."""
import copy
from datetime import date, datetime
import json
from pathlib import Path
import tempfile
import unittest

from scripts.market_outlooks import (
    _forecast_result, load_market_outlooks, render_market_outlook_board,
    view_models, render_market_outlook_archive,
)

SOURCE = "https://www.jpx.co.jp/markets/indices/topix/"
AT = "2026-11-16T08:30:00+09:00"


def original():
    return {
        "type": "forecast", "id": "2026-11-16-topix", "at": AT,
        "subject": "TOPIX", "direction": "上昇", "stance": "強気",
        "reviewed_on": "2026-11-17",
        "statement": "TOPIXは2026年11月17日の終値で基準終値より0.5％超上昇するとの仮説を記録する。",
        "method": "2026年11月13日の公式終値を基準に、2026年11月17日の公式終値で騰落率を計算。プラス0.5％超なら上昇的中とし、以下は外れ。途中値は判定対象外。",
        "reason": "ここに記載した価格はテスト用の架空値。実際の価格として公開されない構造を検証するために使用している。",
        "invalidated_if": "2026年11月17日のTOPIX終値が起点値の0.5％上昇に届かなければ、上昇仮説は外れる。",
        "scenarios": {
            "強気": "需要や円安による押し上げが続き、翌取引日の終値が基準より0.5％超上昇する。",
            "中立": "買い材料と売り材料が相殺し、終値は基準とほぼ同水準にとどまる。",
            "弱気": "売り材料が強まって終値が基準を0.5％より大きく下回る。",
        },
        "baseline": {"date": "2026-11-13", "value": 3400.0,
                     "unit": "ポイント", "source_url": SOURCE,
                     "method": "公式値を使用。法人活動等による指数の連続性は指数公表機関の算定を用いる。"},
        "sources": [{"title": "JPX TOPIX（テスト参照先）",
                     "url": SOURCE, "published_on": "2026-11-13",
                     "checked_on": "2026-11-16"}],
    }


def review(**kwargs):
    return {
        "type": "review", "id": "2026-11-16-noon-01",
        "at": "2026-11-16T12:40:00+09:00",
        "forecast_id": "2026-11-16-topix",
        "stance": "中立", "trend": "暫定的に逆行",
        "reason": "午前中の条件が弱まったという仮想のテスト判断であり、確定した終値ではないことを検証する。",
        "change_reason": "基準時点の強気とは異なる材料を午前中に確認した想定で、判断を中立に変更したことを明示する。",
        "source": {"title": "JPX TOPIX（テスト参照先）", "url": SOURCE,
                   "published_on": "2026-11-13", "checked_on": "2026-11-16"},
        **kwargs,
    }


class DailyOutlookTests(unittest.TestCase):
    def test_empty_archive_never_claims_market_performance(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "content"
            folder.mkdir()
            (folder / "market_outlooks.json").write_text("[]", encoding="utf-8")
            self.assertEqual(load_market_outlooks(tmp), [])
            output = render_market_outlook_board([])
            self.assertIn("新しい相場予想はまだありません", output)
            self.assertNotIn("判定済み：的中", output)

    def test_snapshot_prevents_post_publication_knowledge(self):
        events = [original(), review()]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "content"
            folder.mkdir()
            (folder / "market_outlooks.json").write_text(
                json.dumps(events, ensure_ascii=False), encoding="utf-8")
            parsed = load_market_outlooks(tmp, today=date(2026, 11, 16))
        at_morning = datetime.fromisoformat("2026-11-16T09:00:00+09:00")
        at_noon = datetime.fromisoformat("2026-11-16T13:00:00+09:00")
        initial = view_models(parsed, before=at_morning)
        updated = view_models(parsed, before=at_noon)
        self.assertEqual(len(initial[0]["updates"]), 0)
        self.assertEqual(len(updated[0]["updates"]), 1)
        self.assertIn("結果待ち", render_market_outlook_board(parsed))
        self.assertIn("上昇を予想", render_market_outlook_board(parsed))
        self.assertNotIn("判断の変化", render_market_outlook_board(parsed))
        self.assertNotIn("変更あり", render_market_outlook_board(parsed))
        self.assertNotIn("判定済み：的中", render_market_outlook_board(parsed))

    def test_only_verifies_on_original_fixed_day_with_evidence(self):
        fc = original()
        initial = review()
        final = {
            **review(), "id": "2026-11-17-close-01",
            "at": "2026-11-17T18:30:00+09:00",
            "stance": "強気", "trend": "暫定的に整合",
            "change_reason": "比較対象となる公式終値が公開されたので、正式な判定を行い、当初の方向を再確認した。",
            "source": {"title": "JPX終値（テスト）", "url": SOURCE,
                       "published_on": "2026-11-17", "checked_on": "2026-11-17"},
            "result": {"close": 3430.0, "observed_on": "2026-11-17",
                       "source_url": SOURCE, "outcome": "的中",
                       "explanation": "基準値3400.0から3430.0への変化は約0.88％で、当初の0.5％超という条件を満たしている。"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "content"
            folder.mkdir()
            p = folder / "market_outlooks.json"
            p.write_text(json.dumps([fc, initial, final], ensure_ascii=False), encoding="utf-8")
            parsed = load_market_outlooks(tmp, today=date(2026, 11, 17))
            self.assertEqual(_forecast_result(fc, final["result"]["close"]), "的中")
            self.assertIn("判定済み：的中", render_market_outlook_board(parsed))
            final_bad = copy.deepcopy(final)
            final_bad["at"] = "2026-11-16T18:30:00+09:00"
            final_bad["id"] = "2026-11-16-close-01"
            final_bad["source"]["published_on"] = "2026-11-16"
            final_bad["source"]["checked_on"] = "2026-11-16"
            p.write_text(json.dumps([fc, initial, final_bad], ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "before fixed judgement date"):
                load_market_outlooks(tmp, today=date(2026, 11, 17))

    def test_rejects_uncited_baseline_and_unauthorized_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "content"
            folder.mkdir()
            p = folder / "market_outlooks.json"
            fc = original()
            fc["baseline"]["source_url"] = "https://example.invalid/fake"
            p.write_text(json.dumps([fc], ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cited public source"):
                load_market_outlooks(tmp, today=date(2026, 11, 16))
            fc["baseline"]["source_url"] = SOURCE
            fc["reviewed_on"] = "2026-11-15"
            p.write_text(json.dumps([fc], ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "after publication"):
                load_market_outlooks(tmp, today=date(2026, 11, 16))


if __name__ == "__main__":
    unittest.main()
