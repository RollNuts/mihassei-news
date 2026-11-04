"""Editorial starters are original published content, never a fabricated ranking."""
from __future__ import annotations

import html
import json
import re
import tempfile
import unittest
from pathlib import Path

from scripts.build import BASE, home, load, load_serials
from scripts.editorial_starter import starter_picks, render_starter_picks


class EditorialStarterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles=load()
        cls.serials=load_serials(BASE)

    def test_three_genuine_distinct_editorial_selections(self):
        excluded={x["slug"] for x in self.articles[:4]}
        selected=starter_picks(self.articles, excluded, base=BASE)
        self.assertEqual(len(selected), 3)
        slugs=[x["article"]["slug"] for x in selected]
        self.assertEqual(len(slugs), len(set(slugs)))
        self.assertTrue(excluded.isdisjoint(slugs))
        for item in selected:
            self.assertIn(item["article"],self.articles)
            self.assertTrue(item["hook"])
            self.assertTrue(item["label"])
        self.assertGreaterEqual(len({x["article"]["category"] for x in selected}), 2)

    def test_homepage_replaces_fixed_starters_with_all_article_pv_candidates(self):
        markup=home(self.articles,self.serials)
        self.assertNotIn('class="starter-editorial"',markup)
        self.assertNotIn('class="starter-pick-card"',markup)
        self.assertEqual(markup.count('data-popular-section'),1)
        raw=re.search(r'data-popular-options="([^"]+)"',markup)
        self.assertIsNotNone(raw)
        candidates=json.loads(html.unescape(raw.group(1)))
        self.assertEqual({x['slug'] for x in candidates},{x['slug'] for x in self.articles})
        self.assertIn('aria-labelledby="readers-choice-title" hidden',markup)
        self.assertIn('class="serials-feature"',markup)
        self.assertNotIn('class="reading-ways"',markup)

    def test_legacy_curation_helper_deduplicates_but_no_longer_controls_public_rankings(self):
        from scripts.build import editorial_lead
        from scripts.editorial_starter import homepage_starter_picks
        lead=editorial_lead(self.articles)
        remaining=[item for item in self.articles if item["slug"]!=lead["slug"]]
        occupied={lead["slug"],*(item["slug"] for item in remaining[:3])}
        expected=starter_picks(self.articles,occupied,base=BASE)
        actual=homepage_starter_picks(self.articles,lead,base=BASE)
        self.assertEqual(actual,expected)
        self.assertFalse({x["article"]["slug"] for x in actual}&occupied)
        # When a different lead is passed, deduplicate exactly those occupied
        # slots. The production lead itself always follows publication order.
        fake_lead=self.articles[-1]
        selected=homepage_starter_picks(self.articles,fake_lead,base=BASE)
        excluded={fake_lead["slug"],*(item["slug"] for item in self.articles[:3])}
        self.assertEqual(selected,starter_picks(self.articles,excluded,base=BASE))
        source=(BASE/"scripts/build.py").read_text(encoding="utf-8")
        self.assertNotIn("homepage_starter_picks(articles",source)
        self.assertIn("popular_section = pv_recommendations(articles)",source)

    def test_curation_never_makes_the_publisher_skip_a_daily_build(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/"content").mkdir()
            self.assertEqual(starter_picks(self.articles,base=root),[])
            (root/"content/editorial_picks.json").write_text("{invalid",encoding="utf-8")
            self.assertEqual(starter_picks(self.articles,base=root),[])
            (root/"content/editorial_picks.json").write_text('{"picks":"bad"}',encoding="utf-8")
            self.assertEqual(starter_picks(self.articles,base=root),[])
            # Missing/deleted recommended articles just disappear from
            # presentation, never require deleting or hiding published news.
            (root/"content/editorial_picks.json").write_text(
                json.dumps({"picks":[
                    {"slug":"removed-article","label":"届かない","hook":"掲載されていない記事を表示してはいけません。"}
                ]},ensure_ascii=False),encoding="utf-8")
            self.assertEqual(starter_picks(self.articles,base=root),[])

    def test_selected_when_early_articles_get_replaced_by_new_stories(self):
        published=self.articles
        config=json.loads((BASE/"content/editorial_picks.json").read_text(encoding="utf-8"))
        wanted=[item["slug"] for item in config["picks"]]
        # One curated story became the hero: choose a different published pick.
        excluded={wanted[0]}
        chosen=starter_picks(published,excluded,base=BASE)
        self.assertEqual(len(chosen),3)
        self.assertNotIn(wanted[0],[x["article"]["slug"] for x in chosen])

    def test_html_is_escaped_and_only_known_urls_are_rendered(self):
        malicious={
            "slug":"safe-entry", "title":"<script>alert(1)</script>",
            "summary":"...","category":"暮らし"
        }
        sample=[{"article":malicious,"label":"<x>", "hook":"<img src=x onerror=alert(1)>"}]
        markup=render_starter_picks(
            sample, lambda a,cls,extra="": '<img class="'+cls+'" alt="safe">',
            lambda a:"/articles/"+a["slug"]+"/")
        self.assertNotIn("<script>alert(1)</script>",markup)
        self.assertNotIn("<img src=x onerror=alert(1)>",markup)
        self.assertIn("&lt;script&gt;",markup)
        self.assertIn("&lt;img src=x",markup)
        self.assertIn('href="/articles/safe-entry/"',markup)
        self.assertEqual(markup.count('class="starter-pick-card"'),1)


if __name__=="__main__":
    unittest.main()
