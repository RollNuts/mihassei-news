"""Offline magazine/feature contracts; no full news build or network."""
import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from scripts import build, photo_issues, photo_issue_pages as features, photo_edition_pages as pages
from scripts.photo_editions import load_photo_editions, edition_url
from tests.test_photo_issue_pages import PageParser


class PhotoEditionPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = photo_issues.load_photo_issues(build.BASE)
        released = photo_issues.published_issues(cls.catalog,root=build.BASE)
        editions = load_photo_editions(build.BASE,catalog=cls.catalog,published=released)
        # Keep the published first issue as a regression fixture as daily issues grow.
        cls.editions = [edition for edition in editions if edition["slug"] == "issue-001"]
        cls.edition = cls.editions[0]
        cls.released = cls.edition["ordered_features"]
        cls.models = cls.catalog["models"]

    @staticmethod
    def parse(markup):
        parser=PageParser();parser.feed(markup);return parser

    def test_parent_contents_follow_frozen_order_and_keep_independent_feature_urls(self):
        before=copy.deepcopy(self.catalog)
        page=pages.render_photo_edition(self.edition,self.models,build.layout)
        parsed=self.parse(page)
        ordered=self.edition["ordered_features"]
        self.assertEqual([link["href"] for link in parsed.links if link.get("class")=="photo-edition-feature-image"],
                         [photo_issues.issue_url(feature) for feature in ordered])
        self.assertIn("7人・70枚",page)
        self.assertIn('href="https://veripsa.com/photo/weekly/issue-001/"',page)
        image="https://veripsa.com"+photo_issues.cover_url(ordered[0])
        self.assertIn('property="og:image" content="'+image+'"',page)
        self.assertIn('name="twitter:image" content="'+image+'"',page)
        self.assertEqual(self.catalog,before)
        for private in (self.edition["ranking"]["seed"],"feature_keys","source_png","assets/generated/","authored_fictional_design_cm"):
            self.assertNotIn(private,page)

    def test_parent_engagement_uses_only_its_own_key_and_comments(self):
        page=pages.render_photo_edition(self.edition,self.models,build.layout)
        parsed=self.parse(page)
        self.assertEqual([item["data-engagement-key"] for item in parsed.articles if "data-engagement-key" in item],
                         ["photo-issue:issue-001"])
        self.assertIn('data-like-slug="photo-issue:issue-001"',page)
        self.assertIn('data-comments-key="photo-issue:issue-001"',page)
        self.assertIn('src="'+build.asset_url("comments.js")+'"',page)
        self.assertRegex(next(button["data-copy-url"] for button in parsed.buttons if "data-copy-url" in button),
                         r"^https://veripsa.com/photo/weekly/issue-001/\?v=[0-9a-f]{12}$")
        for feature in self.released:
            self.assertNotIn('data-article-pv="photo-issue:'+feature["slug"]+'"',page)

    def test_index_shows_one_magazine_and_seven_real_feature_ranking_candidates(self):
        page=pages.render_edition_index(self.editions,self.models,build.layout)
        parsed=self.parse(page)
        self.assertEqual([link["href"] for link in parsed.links if link.get("class")=="photo-issue-card-link"],
                         [edition_url(self.edition)])
        self.assertEqual(len(parsed.images),1)
        self.assertNotIn('photo-edition-shortcuts', page)
        self.assertEqual(page.count("data-ranking-key="),7)
        for feature in self.released:
            self.assertIn('data-ranking-key="photo-issue:'+feature["slug"]+'"',page)
        for number in range(2,8):
            self.assertNotIn(f"第{number}号",page)

    def test_features_have_parent_and_previous_next_links_in_frozen_order(self):
        ordered=self.edition["ordered_features"]
        for position,feature in enumerate(ordered):
            page=features.render_photo_issue(feature,feature["model_snapshot"],build.layout,edition=self.edition)
            adjacent=page.split('<nav class="photo-feature-adjacent"',1)[1].split('</nav>',1)[0]
            expected=[photo_issues.issue_url(ordered[index]) for index in (position-1,position+1) if 0<=index<len(ordered)]
            self.assertEqual([link["href"] for link in self.parse(adjacent).links],expected)
            self.assertIn('href="/photo/weekly/issue-001/#contents"',page)
            self.assertIn('data-engagement-key="photo-issue:'+feature["slug"]+'"',page)
            self.assertIn('data-pv-label="この特集"',page)
            self.assertIn('href="https://veripsa.com'+photo_issues.issue_url(feature)+'"',page)
            self.assertIn('"url": "https://veripsa.com/photo/weekly/issue-001/"',page)
            for number in range(2,8):
                self.assertNotIn(f"第{number}号",page)

    def test_all_profiles_count_parent_editions_and_keep_ten_feature_grid_links(self):
        for model in self.models:
            page=features.render_model_profile(model,self.released,build.layout,editions=self.editions)
            self.assertIn('<strong>10</strong><span>掲載カット</span>',page)
            self.assertIn('<strong>1</strong><span>掲載号</span>',page)
            self.assertIn('href="/photo/weekly/issue-001/"',page)
            self.assertIn("特集公開日",page)
            feature=next(feature for feature in self.released if feature["model_id"]==model["id"])
            grid=[link["href"] for link in self.parse(page).links if link.get("class")=="photo-model-grid-link"]
            self.assertEqual(grid,[photo_issues.issue_url(feature)+"#photo-"+shot["id"] for shot in feature["shots"]])
            for number in range(2,8):
                self.assertNotIn(f"第{number}号",page)

    def test_rss_has_one_entry_per_parent_with_versioned_existing_cover(self):
        root=ET.fromstring(pages.generate_edition_rss(self.editions))
        items=root.findall("channel/item")
        self.assertEqual(len(items),1)
        self.assertEqual(items[0].findtext("link"),build.SITE+edition_url(self.edition))
        self.assertEqual(items[0].findtext("guid"),build.SITE+edition_url(self.edition))
        self.assertEqual(items[0].find("enclosure").get("url"),build.SITE+photo_issues.cover_url(pages.edition_cover(self.edition)))

    def test_sitemap_and_homepoint_to_parent_without_losing_feature_routes(self):
        written={}
        with patch.object(build,"write",side_effect=written.__setitem__):
            build.other_files([],issues={},serials=[],photo_issues=self.released,photo_models=self.models,photo_editions=self.editions)
        root=ET.fromstring(written["sitemap.xml"])
        ns={"s":"http://www.sitemaps.org/schemas/sitemap/0.9"}
        routes=[node.findtext("s:loc",namespaces=ns) for node in root]
        for path in ["/photo/weekly/",edition_url(self.edition),*(photo_issues.issue_url(feature) for feature in self.released)]:
            self.assertEqual(routes.count(build.SITE+path),1)
        card=build.gravure_feature_card([],self.released,self.editions)
        self.assertIn('href="/photo/weekly/issue-001/"',card)
        self.assertIn("第1号 · 7人・70枚",card)
        for number in range(2,8):
            self.assertNotIn(f"第{number}号",card)

    def test_photo_only_build_copies_seventy_existing_jpegs_and_no_raw_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)
            def write(path,value):
                target=output/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(value)
            photo_issues.build_photo_issues(self.released,self.models,build.layout,write,output,root=build.BASE,editions=self.editions)
            pages.build_photo_editions(self.editions,self.models,build.layout,write)
            features.build_photo_models(self.models,self.released,build.layout,write,root=build.BASE,output=output,editions=self.editions)
            self.assertEqual(len(list(output.rglob("*.jpg"))),70)
            self.assertEqual(len(list(output.rglob("*.png"))),0)
            self.assertFalse((output/"maintenance").exists())
            self.assertFalse((output/"assets/generated").exists())
            self.assertTrue((output/"photo/weekly/issue-001/index.html").is_file())
            self.assertEqual(len(ET.parse(output/"feeds/weekly-photo.xml").findall("channel/item")),1)

    def test_daily_contents_latest_cover_home_rss_and_seo_expose_only_released_features(self):
        daily = copy.deepcopy(self.edition)
        daily.update(number=2, slug="issue-002", release_mode="daily", title="11月の一週間",
                     summary="写真とコラム。", modified_at="2026-11-16T14:00:00+09:00")
        daily["ordered_features"] = daily["ordered_features"][:2]
        daily["features"] = [feature["slug"] for feature in daily["ordered_features"]]
        daily["ordered_features"][1]["published_at"] = daily["modified_at"]
        first, latest = daily["ordered_features"]
        self.assertEqual(pages.edition_cover(daily), latest)
        page = pages.render_photo_edition(daily, self.models, build.layout)
        self.assertIn("2人・20枚", page)
        self.assertIn('"dateModified": "2026-11-16T14:00:00+09:00"', page)
        self.assertEqual([link["href"] for link in self.parse(page).links if link.get("class")=="photo-edition-feature-image"],
                         [photo_issues.issue_url(first), photo_issues.issue_url(latest)])
        card = build.gravure_feature_card([], daily["ordered_features"], [daily])
        self.assertIn('href="'+photo_issues.issue_url(latest)+'"', card)
        self.assertIn(latest["title"], card)
        rss = ET.fromstring(pages.generate_edition_rss([daily, self.edition]))
        self.assertEqual([item.findtext("guid") for item in rss.findall("channel/item")],
                         [build.SITE+photo_issues.issue_url(latest), build.SITE+photo_issues.issue_url(first),
                          build.SITE+edition_url(self.edition)])
        self.assertIn((edition_url(daily), daily["modified_at"], photo_issues.cover_url(latest)),
                      pages.edition_sitemap_entries([daily, self.edition]))

    def test_latest_card_precedes_stories_and_ranking_and_links_to_daily_feature(self):
        daily = copy.deepcopy(self.edition)
        daily.update(number=2, slug="issue-002", release_mode="daily")
        daily["ordered_features"] = daily["ordered_features"][:1]
        daily["features"] = [daily["ordered_features"][0]["slug"]]
        # Use a separate old-issue member so the fixture also enforces no republishing.
        old = copy.deepcopy(self.edition)
        old["ordered_features"] = old["ordered_features"][1:]
        stories = '<section class="photo-stories"><h2>ストーリーズ</h2></section>'
        page = pages.render_edition_index([daily,old],self.models,build.layout,stories_markup=stories)
        main = page.split('<div class="container photo-issues-page photo-edition-index">',1)[1]
        self.assertLess(main.index('class="photo-issue-card-link"'),main.index('class="photo-stories"'))
        self.assertLess(main.index('class="photo-issue-card-link"'),main.index('data-photo-rankings'))
        parsed = self.parse(page)
        self.assertEqual([link["href"] for link in parsed.links if link.get("class")=="photo-issue-card-link"],
                         [photo_issues.issue_url(daily["ordered_features"][0]),edition_url(old)])
        self.assertEqual(parsed.images[0]["loading"],"eager")
        self.assertEqual(parsed.images[0]["fetchpriority"],"high")
        self.assertIn('最新の特集を読む',page)

    def test_daily_index_links_each_released_person_and_parent_contents_before_stories(self):
        daily = copy.deepcopy(self.edition)
        daily.update(number=2, slug="issue-002", release_mode="daily")
        daily["ordered_features"] = daily["ordered_features"][:5]
        daily["features"] = [feature["slug"] for feature in daily["ordered_features"]]
        # All seven people may be scheduled; only five released features are navigable.
        daily["schedule"] = {"order": [feature["model_id"] for feature in self.released]}
        old = copy.deepcopy(self.edition)
        old["ordered_features"] = old["ordered_features"][5:]
        old["features"] = [feature["slug"] for feature in old["ordered_features"]]
        before = copy.deepcopy((daily, old, self.models))
        stories = '<section class="photo-stories"><h2>ストーリーズ</h2></section>'
        page = pages.render_edition_index([daily, old], self.models, build.layout, stories_markup=stories)
        navigation = page.split('<div class="photo-edition-shortcuts">', 1)[1].split('</nav>', 1)[0]
        parsed = self.parse(navigation)
        expected = [photo_issues.issue_url(feature) for feature in daily["ordered_features"]]
        self.assertEqual([link["href"] for link in parsed.links], expected + [edition_url(daily) + "#contents"])
        for feature in daily["ordered_features"]:
            name = feature["model_snapshot"]["name"]
            self.assertEqual(navigation.count(f'>{name}</a>'), 1)
        for feature in old["ordered_features"]:
            self.assertNotIn(photo_issues.issue_url(feature), navigation)
            self.assertNotIn(feature["model_snapshot"]["name"], navigation)
        self.assertIn('第2号の掲載特集</h2>', navigation)
        self.assertIn('aria-labelledby="photo-edition-shortcuts-title"', navigation)
        self.assertIn('第2号の目次へ', navigation)
        self.assertEqual(parsed.images, [])
        self.assertEqual(len(self.parse(page).images), 2)
        self.assertLess(page.index('photo-issue-card-link'), page.index('class="photo-edition-shortcuts"'))
        self.assertLess(page.index('class="photo-edition-shortcuts"'), page.index(stories))
        self.assertEqual((daily, old, self.models), before)


if __name__=="__main__":
    unittest.main()
