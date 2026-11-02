"""Offline tests for advisory-only satire-quality warnings, not humor scores."""
import copy
import json
import unittest
from datetime import datetime
from pathlib import Path

from scripts.audit_satire import analyze, latest, near_duplicate, final_sentence

ROOT=Path(__file__).resolve().parents[1]


def sample(index: int, *, title=None, opening=None, ending=None, source=None) -> dict:
    return {
        "slug":f"story-{index}",
        "title":title or f"新しい街の出来事その{index}　人々の予想外な行動",
        "category":"暮らし",
        "published_at":f"2026-11-{index % 26 + 1:02d}T08:40:00+09:00",
        "source":{"published_at":"2026-11-01T07:00:00+09:00",
                  "url":source or f"https://example.org/news/{index}"},
        "paragraphs":[opening or f"街の人たちがそれぞれの趣味を楽しんだ例{index}。",
                      f"具体的な出来事が{index}つ続いた。",
                      ending or f"最後はそれぞれが自分の好きな物を持って帰った{index}。"]
    }


class SatireAuditTests(unittest.TestCase):
    def test_repeated_source_and_punchline_are_advisory(self):
        articles=[
            sample(1,title="手続きが終わらない未来、申請が仕事になる",
                   ending="結局、受付担当者だけが残業をすることになった。",
                   source="https://example.org/story"),
            sample(2,title="手続きが終わらない未来、申請が仕事になる",
                   ending="結局、受付担当者だけが残業をすることになった。",
                   source="https://example.org/story"),
            sample(3,title="恋愛アプリの謎",ending="全員が解散して昼寝を楽しんだ。")
        ]
        original=copy.deepcopy(articles)
        flags=analyze(articles)
        labels={x[0] for x in flags}
        self.assertIn("REUSED_SOURCE",labels)
        self.assertIn("SIMILAR_HEADLINE",labels)
        self.assertIn("SIMILAR_PUNCHLINE",labels)
        self.assertEqual(articles,original,"Warnings must never mutate or remove new articles")

    def test_group_of_formal_press_releases_prompts_better_scene_openings(self):
        articles=[
            sample(i,opening="本社の担当者は3日、新しいサービスを発表した。"
                   if i<8 else "何も知らない人が路上で困っていた。")
            for i in range(10)
        ]
        flags=analyze(articles)
        self.assertIn("PRESS_RELEASE_OPENING_LOOP",{x[0] for x in flags})

    def test_recurring_ai_management_stories_get_a_variety_warning(self):
        titles=[
            "AI議長が会議の司会を奪う", "ロボットが上司に出勤簿を出す",
            "人工知能を試した新システム", "管理職が昇進できない街",
            "役員が会議で話し続ける", "新しい恋愛の形",
            "牧場の小さな友情", "気候が変わる暮らし",
        ]
        # Five strong, literal AI/management motifs out of eight articles.
        flags=analyze([sample(i,title=t) for i,t in enumerate(titles)])
        self.assertIn("WORK_AI_CLUSTER",{x[0] for x in flags})

    def test_unrelated_jokes_are_not_automatically_dismissed(self):
        self.assertEqual(near_duplicate("電話の着信を止められない",
                                        "映画の感想が明日になる"),0.0)
        self.assertEqual(analyze([sample(1),sample(2),sample(3)]),[])

    def test_fictional_year_cannot_change_real_publication_order(self):
        articles=[sample(1),sample(15)]
        articles[0]["fiction_at"]="2099-01-01T00:00:00+09:00"
        self.assertEqual(latest(articles)[0]["slug"],"story-15")

    def test_existing_animal_story_is_now_memetic_not_bureaucratic(self):
        stories=json.loads((ROOT/"content/articles.json").read_text(encoding="utf-8"))
        farm=next(a for a in stories if a["slug"]=="farm-friendship-across-species")
        self.assertIn("SNSで考察合戦",farm["title"])
        self.assertIn("牧草の草です",farm["paragraphs"][-1])
        self.assertNotIn("部屋割り",farm["title"])
        self.assertEqual(len(farm["paragraphs"]),3)
        self.assertEqual(farm["published_at"],"2026-11-03T07:40:27+09:00")
        self.assertEqual(farm["source"]["publisher"],"The Guardian")
        self.assertEqual(farm["fiction_at"],"2031-11-03T01:53:00+09:00")
        self.assertGreater(datetime.fromisoformat(farm["modified_at"]),
                           datetime.fromisoformat(farm["published_at"]))

    def test_many_different_stories_remain_fully_publishable(self):
        articles=[sample(i) for i in range(1,12)]
        before=len(articles)
        warnings=analyze(articles)
        self.assertEqual(len(articles),before)
        self.assertTrue(all(len(item)==3 for item in warnings))
        # No exit, exception or moderation operation: output is advice only.


if __name__=="__main__":
    unittest.main()
