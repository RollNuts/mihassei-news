"""Offline tests for advisory-only satire-quality warnings, not humor scores."""
import copy
import json
import unittest
from datetime import datetime
from pathlib import Path

from scripts.audit_satire import analyze, latest, near_duplicate, final_sentence, next_slot_brief

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

    def test_six_adjacent_premises_prompt_topic_rotation(self):
        titles = [
            "AIが本人確認を始める日", "購入申請が必要な店",
            "新しい認証制度に失敗", "登録した人だけ拒否できる方法",
            "猫の休日", "サッカーの遠征",
        ]
        articles = [sample(i, title=title) for i, title in enumerate(titles)]
        for i, article in enumerate(articles):
            article["category"] = "社会" if i < 5 else "スポーツ"
        original = copy.deepcopy(articles)
        flags = analyze(articles)
        labels = {entry[0] for entry in flags}
        self.assertIn("RECENT_CATEGORY_RUN", labels)
        self.assertIn("PROCEDURAL_PREMISE_LOOP", labels)
        self.assertEqual(articles, original, "Advisory quality checks must never rewrite publications")

    def test_varied_recent_topics_do_not_raise_burst_warnings(self):
        titles = [
            "夜の音楽祭", "ゲームの新しい遊び方",
            "旅先で人気のご当地グッズ", "家族の料理",
            "趣味の庭づくり", "子どもの遠足",
        ]
        categories = ["エンタメ", "技術", "暮らし", "社会", "経済", "スポーツ"]
        articles = [sample(i, title=title) for i, title in enumerate(titles)]
        for article, category in zip(articles, categories):
            article["category"] = category
        labels = {entry[0] for entry in analyze(articles)}
        self.assertNotIn("RECENT_CATEGORY_RUN", labels)
        self.assertNotIn("PROCEDURAL_PREMISE_LOOP", labels)

    def test_unrelated_jokes_are_not_automatically_dismissed(self):
        self.assertEqual(near_duplicate("電話の着信を止められない",
                                        "映画の感想が明日になる"),0.0)
        distinct=[
            sample(1,title="電話を待ち続けた猫の休日",ending="猫は庭でしばらく眠っていた。"),
            sample(2,title="山の向こうで始まった星の祭り",ending="夕方には星の色が変わった。"),
            sample(3,title="音楽のない映画を観に行く",ending="観客は拍手の代わりに静かに帰宅した。"),
        ]
        self.assertEqual(analyze(distinct),[])

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


    def test_next_slot_brief_reacts_to_bursts_without_blocking_publication(self):
        titles = [
            "本人確認を追加する街", "購入申請が必要な店",
            "新しい認証制度に失敗", "登録した人だけ拒否できる方法",
            "帰省した家族の食卓", "サッカーの遠征",
        ]
        articles = [sample(i,title=title) for i,title in enumerate(titles)]
        for i,article in enumerate(articles):
            article["category"] = "社会" if i < 5 else "スポーツ"
        original = copy.deepcopy(articles)
        notes = next_slot_brief(articles)
        rendered = "\n".join(notes)
        self.assertIn("直近6本",rendered)
        self.assertIn("確認・申請・認証・拒否",rendered)
        self.assertIn("社会が集中",rendered)
        self.assertIn("異なる仕組みの見出し2案",rendered)
        self.assertIn("警告だけで発行を止めない",rendered)
        self.assertEqual(articles,original)
        self.assertEqual(len(articles),6)

    def test_next_slot_brief_does_not_invent_humor_or_traffic_scores(self):
        self.assertIn("掲載済み記事なし",next_slot_brief([])[0])
        articles = [sample(i,title=title) for i,title in enumerate((
            "夜の音楽祭", "ゲームの新しい遊び方",
            "旅先で人気のご当地グッズ", "家族の料理",
            "趣味の庭づくり", "子どもの遠足",
        ))]
        for article,category in zip(articles,("エンタメ","技術","暮らし","社会","経済","スポーツ")):
            article["category"] = category
        notes = "\n".join(next_slot_brief(articles))
        self.assertIn("目立つ定型警告はなし",notes)
        self.assertIn("面白さの採点ではない",notes)
        self.assertNotIn("PV",notes)



if __name__=="__main__":
    unittest.main()
