#!/usr/bin/env python3
"""Zero-cost tests for real-impressions reporting, no X/Buffer access."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from report import format_report, summary, validate

def row(post,impressions,when="2026-11-03T21:30:00+09:00",
        fmt="surreal_text",window=24):
    return {
        "post_id":post,"captured_at_jst":when,"format":fmt,"window_hours":window,
        "impressions":impressions,"metric_source":"x_post_insights"
    }


class ImpressionTests(unittest.TestCase):
    def test_absent_metrics_are_not_zero(self):
        text=format_report({"schema_version":1,"measurements":[]})
        self.assertIn("未取得（0ではない）",text)
        self.assertNotIn("中央値=0",text)

    def test_actual_zero_is_allowed(self):
        report=summary({"measurements":[row("a",0)]},24)
        self.assertEqual(report["surreal_text"]["median_impressions"],0)
        self.assertEqual(report["surreal_text"]["samples"],1)

    def test_latest_snapshot_no_double_count(self):
        rows=[row("a",100),row("a",140,"2026-11-04T09:30:00+09:00"),
              row("b",400),row("c",900)]
        report=summary({"measurements":rows},24)
        self.assertEqual(report["surreal_text"]["samples"],3)
        self.assertEqual(report["surreal_text"]["median_impressions"],400)
        self.assertEqual(report["surreal_text"]["total_impressions"],1440)
        self.assertIn("比較可能",report["surreal_text"]["evidence_level"])

    def test_formats_and_windows_separate(self):
        data={"measurements":[row("a",200),row("b",500,fmt="nejimachi_comic"),
                              row("b",1000,fmt="nejimachi_comic",window=72)]}
        self.assertEqual(summary(data,24)["nejimachi_comic"]["median_impressions"],500)
        self.assertEqual(summary(data,72)["nejimachi_comic"]["median_impressions"],1000)

    def test_reject_fabricated_or_unattributed(self):
        sample=row("a",100)
        sample["metric_source"]="estimated_from_virality"
        with self.assertRaisesRegex(ValueError,"source"):
            validate(sample)
        sample=row("a",100)
        sample["captured_at_jst"]="2026-11-03T10:30:00"
        with self.assertRaisesRegex(ValueError,"timezone"):
            validate(sample)

    def test_few_samples_not_declared_winner(self):
        d={"measurements":[row("a",123,fmt="surreal_text"),
                           row("b",400,fmt="nejimachi_comic")]}
        self.assertTrue(all("参考値" in g["evidence_level"]
                            for g in summary(d,24).values()))
        self.assertIn("勝敗を断定しない",format_report(d))


if __name__=="__main__":
    unittest.main(verbosity=2)
