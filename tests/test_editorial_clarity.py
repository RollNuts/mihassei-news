"""Reader-first satire fixes: concrete causes, transparent but quiet imagery labels.

These assertions protect known regressions and content integrity; passing them
does not amount to a machine judgement of whether a joke is genuinely funny.
"""
from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path

from scripts import build
from scripts.generated_photos import usable_generated_scene

ROOT = Path(__file__).resolve().parents[1]
REVISED = {
    "volleyball-team-lunch-plate-merch": ("皿の型番", "広報", "弁当箱", "2026-11-03T17:37:14+09:00"),
    "mortgage-bank-clerk-invited-to-live-in": ("家計会議", "食費", "利息", "2026-11-03T13:28:44+09:00"),
    "research-completed-opening-day": ("開所式", "あいさつ", "必要ありません", "2026-11-02T17:36:39+09:00"),
    "robot-job-hopping": ("充電", "17台", "省人化", "2026-11-02T17:36:39+09:00"),
}


class EditorialClarityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles = {a["slug"]: a for a in build.load()}

    def test_revised_stories_have_readable_one_step_premises_and_keep_sources(self):
        for slug, (title_hook, premise, ending_hook, original_date) in REVISED.items():
            with self.subTest(slug=slug):
                article = self.articles[slug]
                self.assertIn(title_hook, article["title"])
                self.assertIn(premise, " ".join(article["paragraphs"]))
                self.assertIn(ending_hook, " ".join(article["paragraphs"]))
                self.assertLessEqual(len(article["title"]), 70)
                self.assertLessEqual(len(article["summary"]), 100)
                self.assertTrue(2 <= len(article["paragraphs"]) <= 4)
                self.assertTrue(all(65 <= len(p) <= 210 for p in article["paragraphs"]))
                self.assertEqual(article["published_at"], original_date)
                self.assertEqual(datetime.fromisoformat(article["fiction_at"]).year,
                                 datetime.fromisoformat(article["source"]["published_at"]).year + 5)
                self.assertTrue(datetime.fromisoformat(article["modified_at"]).tzinfo)
                self.assertGreater(datetime.fromisoformat(article["modified_at"]),
                                   datetime.fromisoformat(article["published_at"]))
                self.assertTrue(article["source"]["url"].startswith("https://"))

    def test_no_return_to_unexplained_multi_step_magic(self):
        self.assertNotIn("皿だけ公式グッズ", self.articles["volleyball-team-lunch-plate-merch"]["title"])
        self.assertNotIn("同居", self.articles["mortgage-bank-clerk-invited-to-live-in"]["title"])
        self.assertNotIn("全テーマ解決", self.articles["research-completed-opening-day"]["title"])
        self.assertNotIn("転職希望", self.articles["robot-job-hopping"]["title"])
        self.assertIn("実在の選手・団体の出来事ではない",
                      self.articles["volleyball-team-lunch-plate-merch"]["source"]["topic"])

    def test_image_notes_are_discreet_but_non_deceptive(self):
        # The old mortgage photo belongs to the old copy and cannot be
        # "reapproved" by rewriting its article fingerprint.
        article = self.articles["mortgage-bank-clerk-invited-to-live-in"]
        self.assertFalse(usable_generated_scene(article, root=ROOT))
        markup = build.story(article, list(self.articles.values()))
        self.assertIn("編集部制作の創作イラスト", markup)
        self.assertIn("実際の出来事の写真ではありません", markup)
        self.assertNotIn("再現イメージ（合成）", markup)
        self.assertIn("<strong>この記事は創作です。</strong>", markup)
        # A genuinely unchanged image still receives its concise synthetic
        # photography disclosure when the original image record is authentic.
        intact = self.articles["music-ai-friends-monopolize-the-song"]
        self.assertTrue(usable_generated_scene(intact, root=ROOT))
        photo_page = build.story(intact, list(self.articles.values()))
        self.assertIn("再現イメージ（合成）", photo_page)
        self.assertIn("実際の出来事を撮影した報道写真ではありません", photo_page)
        self.assertNotIn("AI生成の架空ニュース再現画像", photo_page)
        css = (ROOT/"assets/editorial-polish.css").read_text(encoding="utf-8")
        basic_css = (ROOT/"assets/newsroom.css").read_text(encoding="utf-8")
        self.assertNotIn('content:"AI生成・創作"', css)
        self.assertIn('content:"イメージ"', basic_css)
        policy = build.editorial_policy()
        self.assertIn("実写風画像にもAIによる合成画像", policy)
        # The photo/fiction disclosure stays; unrelated visible AI badges do not.
        self.assertIn("AIによる合成画像", policy)

    def test_revised_stories_fall_back_until_new_real_photos_exist(self):
        # The editorial rewrites did not regenerate any of these JPEGs.
        # Photo records remain honest; the static build supplies illustrations.
        for slug in ("volleyball-team-lunch-plate-merch",
                     "mortgage-bank-clerk-invited-to-live-in",
                     "research-completed-opening-day", "robot-job-hopping"):
            with self.subTest(slug=slug):
                article = self.articles[slug]
                self.assertFalse(usable_generated_scene(article, root=ROOT))
                page = build.story(article, list(self.articles.values()))
                self.assertIn("<h1>"+article["title"]+"</h1>", page)
                self.assertIn('class="article-visual"', page)
                self.assertIn('src="/images/'+slug+'.svg"', page)
                self.assertIn("創作イラスト", page)


if __name__ == "__main__":
    unittest.main()
