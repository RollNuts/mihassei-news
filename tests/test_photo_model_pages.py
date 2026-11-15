"""Offline reader contracts for fictional model profiles and weekly discovery."""
from __future__ import annotations

import copy
import json
import re
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree as ET

from scripts import build, photo_issue_pages as pages, photo_issues
from tests.test_photo_issue_pages import PageParser, issue


class PhotoModelPageTests(unittest.TestCase):
    def test_all_live_biographies_reach_profiles_without_changing_issue_snapshots(self):
        catalog = photo_issues.load_photo_issues(build.BASE)
        before = copy.deepcopy(catalog["issues"])
        for model in catalog["models"]:
            markup = pages.render_model_profile(model, [], self.main_only)
            biography = model["biography"]
            self.assertIn("2031年11月時点の架空の経歴", markup)
            for value in (biography["hometown"], biography["strength"], biography["difficulty"],
                          biography["habit"], *(row["text"] for row in biography["career"])):
                self.assertIn(value, markup)
        self.assertEqual(catalog["issues"], before)

    def setUp(self):
        self.models = copy.deepcopy(photo_issues.load_photo_issues(build.BASE)["models"])
        for model in self.models:
            model.pop("profile_portrait", None)
        self.release = issue(model_id=self.models[0]["id"])
        self.release["images"]["01"]["sha256"] = "a" * 64
        self.release["model_snapshot"] = copy.deepcopy(self.models[0])
        self.draft = issue(2, self.models[1]["id"])
        self.draft.update(state="draft", title="PRIVATE DRAFT TITLE", summary="PRIVATE DRAFT SUMMARY")
        self.draft["images"]["01"]["sha256"] = "b" * 64

    @staticmethod
    def parse(markup):
        parsed = PageParser()
        parsed.feed(markup)
        return parsed

    @staticmethod
    def main_only(_title, _description, _path, main, **_kwargs):
        return main

    def test_directory_and_all_seven_profiles_are_built_without_draft_routes(self):
        written = {}
        before = copy.deepcopy((self.models, self.release, self.draft))
        pages.build_photo_models(self.models, [self.draft, self.release], build.layout, written.__setitem__)
        self.assertEqual(len(self.models), 7)
        self.assertEqual(set(written), {"photo/models/index.html"} | {
            pages.model_url(model).lstrip("/") + "index.html" for model in self.models})
        directory = written["photo/models/index.html"]
        links = {link["href"] for link in self.parse(directory).links}
        for model in self.models:
            self.assertIn(pages.model_url(model), links)
            profile = written[pages.model_url(model).lstrip("/") + "index.html"]
            for value in (model["name"], str(model["age"]) + "歳", model["character"]["occupation"],
                          model["character"]["personality"], model["character"]["weakness"],
                          *model["character"]["likes"]):
                self.assertIn(value, profile)
            self.assertIn("架空の成人モデル", profile)
            self.assertIn('href="https://veripsa.com' + pages.model_url(model) + '"', profile)
            self.assertNotIn("PRIVATE DRAFT", profile)
            self.assertNotIn(photo_issues.issue_url(self.draft), profile)
        self.assertEqual((self.models, self.release, self.draft), before)

    def test_profile_images_use_only_own_published_cover_with_native_dimensions(self):
        self.release["cover_cut_id"] = "03"
        self.release["images"]["03"].update(sha256="c" * 64, width=1077, height=1460,
                                            provenance={"kind": "user_supplied_photo"})
        page = pages.render_model_profile(self.models[0], [self.release, self.draft], build.layout)
        cover = "https://veripsa.com/photo/weekly/issue-001/03.jpg?v=" + "c" * 12
        profile_image = self.parse(page).images[0]
        self.assertEqual(profile_image["src"], cover.removeprefix("https://veripsa.com"))
        self.assertEqual((profile_image["width"], profile_image["height"]), ("1077", "1460"))
        self.assertEqual(profile_image["fetchpriority"], "high")
        for property_name in ("og:image", "og:image:secure_url"):
            self.assertIn(f'property="{property_name}" content="{cover}"', page)
        self.assertIn(f'name="twitter:image" content="{cover}"', page)
        self.assertIn('property="og:image:width" content="1077"', page)
        self.assertIn('property="og:image:height" content="1460"', page)
        self.assertNotIn('content="noindex,follow"', page)
        schemas = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page).group(1))
        self.assertEqual(schemas["@type"], "ProfilePage")
        self.assertEqual(schemas["about"]["description"], "架空の成人モデル")
        copy_link = next(button["data-copy-url"] for button in self.parse(page).buttons
                         if "data-copy-url" in button)
        self.assertRegex(copy_link, r"^https://veripsa.com/photo/models/nagino-saya/\?v=[0-9a-f]{12}$")
        share_link = next(link["href"] for link in self.parse(page).links
                          if link.get("href", "").startswith("https://x.com/intent/tweet?"))
        self.assertEqual(parse_qs(urlsplit(share_link).query)["url"], [copy_link])

    def test_no_photo_profile_is_useful_noindex_and_has_no_unapproved_or_draft_image(self):
        model = self.models[1]
        page = pages.render_model_profile(model, [self.release, self.draft], build.layout)
        self.assertEqual(self.parse(page).images, [])
        self.assertIn("掲載号はまだありません。", page)
        self.assertIn('content="noindex,follow"', page)
        self.assertIn(model["character"]["personality"], page)
        self.assertNotIn('content="max-image-preview:large"', page)
        self.assertNotIn("準備中", page)
        self.assertNotIn("PRIVATE DRAFT", page)
        self.assertNotIn("newsreader-preview", page)
        self.assertIn('property="og:image" content="' + build.SITE + build.social_image(None) + '"', page)

    def test_profile_photo_grid_and_counts_cover_only_own_released_shots(self):
        older = copy.deepcopy(self.release)
        older.update(slug="older-issue", number=3, published_at="2026-11-13T13:30:00+09:00")
        foreign = issue(4, self.models[1]["id"])
        markup = pages.render_model_profile(self.models[0], [older, self.draft, foreign, self.release], self.main_only)
        parsed = self.parse(markup)
        expected = [photo_issues.issue_url(value) + "#photo-" + shot["id"]
                    for value in (self.release, older) for shot in value["shots"]]
        grid_links = [link["href"] for link in parsed.links if link.get("class") == "photo-model-grid-link"]
        self.assertEqual(grid_links, expected)
        self.assertEqual(len(parsed.images), 21)  # Own cover avatar plus twenty released grid entries.
        self.assertIn('<strong>20</strong><span>掲載カット</span>', markup)
        self.assertIn('<strong>2</strong><span>掲載号</span>', markup)
        self.assertIn('class="photo-model-avatar"', markup)
        self.assertNotIn(photo_issues.issue_url(foreign), markup)
        for unsupported in ("フォロワー", "フォローする", "instagram.com", "Instagram"):
            self.assertNotIn(unsupported, markup)

    def test_optional_stories_can_be_inserted_near_profile_and_weekly_headers(self):
        story = '<section data-model-stories="nagino-saya">公開済みのひとこと</section>'
        written = {}
        pages.build_photo_models(self.models, [self.release], self.main_only, written.__setitem__,
                                 stories_renderer=lambda model_id: story if model_id == self.models[0]["id"] else "")
        profile = written[pages.model_url(self.models[0]).lstrip("/") + "index.html"]
        self.assertLess(profile.index("data-model-stories"), profile.index('class="photo-model-photos"'))
        weekly = pages.render_photo_index([self.release], self.models, self.main_only, stories_markup=story)
        self.assertLess(weekly.index("data-model-stories"), weekly.index("data-photo-rankings"))

    def test_private_production_fields_never_reach_directory_or_profile(self):
        model = self.models[0]
        for key in ("appearance", "master", "preferred_face_reference", "reference_state", "body_profile",
                    "body_reference", "wardrobe_references", "creative_reference", "generation_prompt"):
            model[key] = {"secret": "PRIVATE " + key}
        model["character"].update(voice="PRIVATE voice", first_person="PRIVATE first_person",
                                  casting_specialty="PRIVATE casting_specialty")
        for markup in (pages.render_model_index(self.models, [self.release], build.layout),
                       pages.render_model_profile(model, [self.release], build.layout)):
            self.assertNotIn("PRIVATE", markup)
            for value in ("齋藤飛鳥", "藤嶌果歩", "reference_state", "body_profile", "creative_reference",
                          "photo-master-", "photo-face-", "assets/generated/", "bust_cm", "waist_cm"):
                self.assertNotIn(value, markup)

    def test_sitemap_matches_indexability_and_published_cover_versions(self):
        entries = pages.model_sitemap_entries(self.models, [self.draft, self.release])
        self.assertEqual(entries, [
            (pages.MODEL_INDEX_URL, self.release["published_at"], photo_issues.cover_url(self.release)),
            (pages.model_url(self.models[0]), self.release["published_at"], photo_issues.cover_url(self.release)),
        ])
        written = {}
        with patch.object(build, "write", side_effect=written.__setitem__):
            build.other_files([], issues={}, serials=[], photo_issues=[self.release], photo_models=self.models)
        tree = ET.fromstring(written["sitemap.xml"])
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9",
              "image": "http://www.google.com/schemas/sitemap-image/1.1"}
        routes = {item.findtext("s:loc", namespaces=ns): item for item in tree}
        for path, stamp, image in entries:
            node = routes[build.SITE + path]
            self.assertEqual(node.findtext("s:lastmod", namespaces=ns), stamp)
            self.assertEqual(node.findtext("image:image/image:loc", namespaces=ns), build.SITE + image)
        for model in self.models[1:]:
            self.assertNotIn(build.SITE + pages.model_url(model), routes)
        self.assertNotIn(build.SITE + photo_issues.issue_url(self.draft), routes)

    def test_empty_release_directory_has_names_and_no_sitemap_image(self):
        page = pages.render_model_index(self.models, [], build.layout)
        self.assertEqual(self.parse(page).images, [])
        self.assertNotIn('content="noindex,follow"', page)
        self.assertEqual(pages.model_sitemap_entries(self.models, []), [(pages.MODEL_INDEX_URL, None, None)])

    def test_issue_byline_and_weekly_navigation_find_profiles_and_rankings_exclude_drafts(self):
        issue_page = pages.render_photo_issue(self.release, self.release["model_snapshot"], self.main_only)
        self.assertIn('<p class="photo-issue-model"><a href="' + pages.model_url(self.models[0]) + '">', issue_page)
        weekly = pages.render_photo_index([self.release, self.draft], self.models, build.layout)
        self.assertIn('href="/photo/models/"', weekly)
        self.assertIn('data-photo-rankings', weekly)
        self.assertIn('<ol data-ranking-list hidden>', weekly)
        self.assertIn('data-ranking-status role="status"', weekly)
        self.assertIn('data-ranking-key="photo-issue:issue-001"', weekly)
        self.assertNotIn('data-ranking-key="photo-issue:issue-002"', weekly)
        self.assertNotIn("PRIVATE DRAFT", weekly)
        self.assertEqual(weekly.count('data-ranking-pv'), 1)
        self.assertNotIn("0 PV", weekly)

    def test_rankings_assets_load_on_weekly_index_only(self):
        for path in ("/", "/archive/", "/photo/weekly/issue-001/", pages.MODEL_INDEX_URL,
                     pages.model_url(self.models[0])):
            page = build.layout("テスト", "説明", path, "本文")
            self.assertNotIn("/assets/photo-rankings.js", page)
            self.assertNotIn("/assets/photo-rankings.css", page)
        weekly = build.layout("週刊グラビア", "説明", "/photo/weekly/", "本文", kind="photo_collection")
        for name in ("photo-rankings.js", "photo-rankings.css"):
            self.assertIn(build.asset_url(name), weekly)
            self.assertRegex(build.asset_url(name), r"\?v=[0-9a-f]{12}$")

    def test_story_assets_load_only_on_home_weekly_index_and_individual_profiles(self):
        for path in ("/archive/", "/photo/weekly/issue-001/", pages.MODEL_INDEX_URL):
            page = build.layout("テスト", "説明", path, "本文")
            self.assertNotIn("/assets/photo-stories.js", page)
            self.assertNotIn("/assets/photo-stories.css", page)
        for path in ("/", "/photo/weekly/", pages.model_url(self.models[0]), pages.model_url(self.models[1])):
            page = build.layout("テスト", "説明", path, "本文")
            for name in ("photo-stories.js", "photo-stories.css"):
                self.assertIn(build.asset_url(name), page)
                self.assertRegex(build.asset_url(name), r"\?v=[0-9a-f]{12}$")

    def test_public_profile_text_is_escaped(self):
        model = copy.deepcopy(self.models[1])
        model["name"] = '<script>alert("name")</script>'
        model["character"]["personality"] = '<img src="x" onerror="bad">'
        for page in (pages.render_model_profile(model, [], self.main_only),
                     pages.render_model_index([model], [], self.main_only)):
            self.assertNotIn("<script>", page)
            self.assertNotIn('<img src="x"', page)
            self.assertIn("&lt;script&gt;", page)
            self.assertIn("&lt;img", page)

    def test_supported_personality_trait_list_uses_reader_text(self):
        model = copy.deepcopy(self.models[1])
        model["character"]["personality"] = ["落ち着いている", "休日はマイペース"]
        for page in (pages.render_model_profile(model, [], self.main_only),
                     pages.render_model_index([model], [], self.main_only)):
            self.assertIn("落ち着いている、休日はマイペース", page)
            self.assertNotIn("[&#x27;", page)


if __name__ == "__main__":
    unittest.main()
