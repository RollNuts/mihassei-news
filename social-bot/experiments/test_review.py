#!/usr/bin/env python3
"""Offline, deterministic safeguards for the experiment learning loop."""
import sys
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
sys.path.insert(0,str(Path(__file__).resolve().parent))
from review import actual_summary, compose, hashtag_summary


def fake_impression(post_id,fmt,count,when="2026-11-03T20:30:00+09:00",hours=24):
    return {
        "post_id":post_id,
        "format":fmt,
        "impressions":count,
        "window_hours":hours,
        "captured_at_jst":when,
        "metric_source":"x_post_insights"
    }


class WeeklyReviewTests(unittest.TestCase):
    def setUp(self):
        self.posts={"experiments":[
            {"id":"a","sequence":1,"format":"surreal_text","text":"オリジナルA"},
            {"id":"b","sequence":2,"format":"character_moment","text":"オリジナルB"},
            {"id":"c","sequence":3,"format":"surreal_text","text":"オリジナルC"},
        ]}
        self.state={"queued":{"a":{"buffer_id":"buffer-a"}}}
        self.delivery={"checked_at_utc":"2026-11-03T11:45:00+00:00",
                       "items":[{"post_id":"a","buffer_status":"scheduled"}]}
        self.now=datetime(2026,11,3,22,0,tzinfo=timezone(timedelta(hours=9)))

    def run_review(self,rows):
        return compose(self.posts,self.state,self.delivery,{"measurements":rows},self.now)

    def test_missing_data_is_not_zero_or_a_winner(self):
        result=self.run_review([])
        self.assertIn("未取得（0回ではない）",result)
        self.assertIn("勝ち形式はまだ判断しない",result)
        self.assertNotIn("観測上の先行",result)

    def test_two_buffer_statuses_are_not_fake_x_views(self):
        result=self.run_review([])
        self.assertIn("scheduled",result)
        self.assertNotIn("合計 0（実測",result)

    def test_underfive_samples_no_winner(self):
        rows=[fake_impression("a","surreal_text",100),
              fake_impression("b","character_moment",1000)]
        result=self.run_review(rows)
        self.assertIn("勝ち形式はまだ判断しない",result)

    def test_five_samples_each_allows_observational_comparison(self):
        rows=[fake_impression(f"s{i}","surreal_text",40+i) for i in range(5)]
        rows += [fake_impression(f"c{i}","character_moment",150+i) for i in range(5)]
        result=self.run_review(rows)
        self.assertIn("観測上の先行",result)
        self.assertIn("character_moment",result)
        self.assertIn("因果関係を示さない",result)

    def test_one_post_multiple_observations_not_double_counted(self):
        rows=[fake_impression("a","surreal_text",100),
              fake_impression("a","surreal_text",150,"2026-11-04T20:30:00+09:00")]
        stats=actual_summary(rows,"surreal_text",24)
        self.assertEqual(stats,{"n":1,"median":150,"sum":150})

    def test_hashtag_report_is_unmeasured_without_linked_x_values(self):
        self.posts["experiments"][2]["hashtag"]="#シュールネタ"
        data=self.run_review([])
        self.assertIn("ハッシュタグ実験",data)
        self.assertIn("効果は判定保留",data)
        self.assertIn("未取得（0回ではない）",data)

    def test_linked_hashtag_controls_are_compared_separately(self):
        self.posts["experiments"][2]["hashtag"]="#シュールネタ"
        tagged=fake_impression("x-actual-3","surreal_text",250)
        control=fake_impression("x-actual-1","surreal_text",100)
        tagged["experiment_id"]="c"
        control["experiment_id"]="a"
        stats=hashtag_summary(self.posts["experiments"],[tagged,control],"surreal_text",24)
        self.assertEqual(stats["one_relevant_tag"],{"n":1,"median":250})
        self.assertEqual(stats["no_tag"],{"n":1,"median":100})
        self.assertIn("判定保留",self.run_review([tagged,control]))

    def test_unlinked_x_metrics_do_not_inflate_hashtag_results(self):
        self.posts["experiments"][2]["hashtag"]="#シュールネタ"
        unknown=fake_impression("x-screenshot-without-experiment-id","surreal_text",10000)
        stats=hashtag_summary(self.posts["experiments"],[unknown],"surreal_text")
        self.assertEqual(stats["one_relevant_tag"]["n"],0)
        self.assertEqual(stats["no_tag"]["n"],0)

    def test_format_mismatch_in_linked_metric_is_rejected(self):
        self.posts["experiments"][2]["hashtag"]="#シュールネタ"
        wrong=fake_impression("x-post","character_moment",100)
        wrong["experiment_id"]="c"
        with self.assertRaisesRegex(ValueError,"format differs"):
            hashtag_summary(self.posts["experiments"],[wrong],"surreal_text")

    def test_observations_of_different_time_window_not_mixed(self):
        rows=[fake_impression("a","surreal_text",100),
              fake_impression("a","surreal_text",600,hours=72)]
        self.assertEqual(actual_summary(rows,"surreal_text",24)["sum"],100)
        self.assertEqual(actual_summary(rows,"surreal_text",72)["sum"],600)


if __name__=="__main__":
    unittest.main(verbosity=2)
