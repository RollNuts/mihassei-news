"""Regression checks for the seven-day writing-first satire experiment.

The site already supports 2-6 paragraphs. This tests that short-form stories
are actually publishable; it must NOT restrict future writing to two paragraphs.
"""
import json
import unittest
from datetime import datetime
from pathlib import Path

from scripts.build import BASE, load, story

REPO=Path(__file__).resolve().parents[1]


class SatirePilotTests(unittest.TestCase):
    def test_two_public_experiments_have_a_second_turn_without_filler(self):
        items={a["slug"]:a for a in load()}
        subjects={
            "press-conference-metaphor-viral":{
                "headline":"ネタバレ注意",
                "second_turn":"公式が解釈を確定させるな",
                "source":"https://www.itmedia.co.jp/news/article/2611/02/2000002828/",
            },
            "idol-ice-kept-in-freezer":{
                "headline":"無料試食まで保存用",
                "second_turn":"無料配布の限定品",
                "source":"https://prtimes.jp/main/html/rd/p/000000097.000086324.html",
            },
        }
        for slug,expected in subjects.items():
            with self.subTest(slug=slug):
                a=items[slug]
                self.assertIn(expected["headline"],a["title"])
                self.assertIn(expected["second_turn"],a["paragraphs"][-1])
                self.assertEqual(a["source"]["url"],expected["source"])
                self.assertEqual(len(a["paragraphs"]),2)
                self.assertGreaterEqual(sum(map(len,a["paragraphs"])),180)
                self.assertLessEqual(sum(map(len,a["paragraphs"])),350)
                self.assertGreater(datetime.fromisoformat(a["modified_at"]),
                                   datetime.fromisoformat(a["published_at"]))
                real=datetime.fromisoformat(a["source"]["published_at"])
                future=datetime.fromisoformat(a["fiction_at"])
                self.assertEqual(future,real.replace(year=real.year+5))
                # A rewrite must not be falsely promoted as a new publication.
                self.assertEqual(a["published_at"],"2026-11-03T08:08:49+09:00")

    def test_two_more_punchlines_focus_on_human_behavior(self):
        """Old published articles are revised, not falsely relabeled new news."""
        subjects={
            "supporter-guarantor-contract":{
                "title_parts":("ファンにも戦力外通告","FA宣言"),
                "second_turn":"選手にはFAがあるのに",
                "source":"https://kumanichi.com/articles/2053373",
                "published":"2026-11-02T19:16:37+09:00",
            },
            "quiet-reply-dating-matches":{
                "title_parts":("返信3日に1回","運営のブロック"),
                "second_turn":"本人たちにはもう届かなかった",
                "source":"https://www.reuters.com/technology/tinders-us-daily-active-users-return-growth-october-match-ramps-up-ai-efforts-2026-11-02/",
                "published":"2026-11-03T07:40:27+09:00",
            }
        }
        items={a["slug"]:a for a in load()}
        for slug, expected in subjects.items():
            with self.subTest(slug=slug):
                a=items[slug]
                for phrase in expected["title_parts"]:
                    self.assertIn(phrase,a["title"])
                self.assertIn(expected["second_turn"],a["paragraphs"][-1])
                self.assertEqual(a["source"]["url"],expected["source"])
                self.assertEqual(a["published_at"],expected["published"])
                self.assertEqual(len(a["paragraphs"]),2)
                self.assertGreaterEqual(sum(map(len,a["paragraphs"])),180)
                self.assertLessEqual(sum(map(len,a["paragraphs"])),390)
                self.assertGreater(datetime.fromisoformat(a["modified_at"]),
                                   datetime.fromisoformat(a["published_at"]))
                source=datetime.fromisoformat(a["source"]["published_at"])
                self.assertEqual(datetime.fromisoformat(a["fiction_at"]),source.replace(year=source.year+5))
                html=story(a,list(items.values()))
                body=html.split('<div class="article-body">',1)[1].split("</div>",1)[0]
                self.assertEqual(body.count("<p>"),2)
                self.assertIn('data-future-poll',html)
                self.assertIn('aria-label="着想した実際の話題"',html)

    def test_two_paragraph_stories_render_without_changing_site_schema(self):
        articles=load()
        by_slug={a["slug"]:a for a in articles}
        for slug in ("press-conference-metaphor-viral","idol-ice-kept-in-freezer"):
            markup=story(by_slug[slug],articles)
            section=markup.split('<div class="article-body">',1)[1].split("</div>",1)[0]
            self.assertEqual(section.count("<p>"),2)
            self.assertEqual(section.count("</p>"),2)
            self.assertIn("着想元",markup)
            self.assertIn("創作ニュース",markup)
            self.assertIn(by_slug[slug]["title"],markup)
            self.assertIn('content="https://veripsa.com/og/articles/'+slug+'.png',markup)
        # Other stories retain their longer, richer formats.
        longer=[a for a in articles if len(a["paragraphs"])>=3]
        self.assertTrue(longer,"The experiment must not force every article to be short")


if __name__=="__main__":
    unittest.main()
