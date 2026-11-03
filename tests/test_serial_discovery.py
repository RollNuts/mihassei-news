"""Cross-serial reader discovery with no fictional/real-market confusion."""
from __future__ import annotations

import html
import json
import re
import unittest
from copy import deepcopy
from pathlib import Path

from scripts.build import SITE, layout, load, story
from scripts.serial_discovery import choose_serial, render_serial_bridge, primary_episode
from scripts.serials import (
    load_serials, render_episode_page, render_serials_index, render_series_page,
)

ROOT = Path(__file__).resolve().parents[1]


def structured(markup):
    match = re.search(r'<script type="application/ld\+json">(.+?)</script>', markup, re.S)
    if match is None:
        raise AssertionError("JSON-LD script not found")
    result = json.loads(match.group(1))
    return result if isinstance(result, list) else [result]


class SerialReaderDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles = load()
        cls.serials = load_serials(ROOT)

    def test_category_matched_promo_points_to_a_published_episode(self):
        count = 0
        for article in self.articles:
            selected = choose_serial(article, self.serials)
            promo = render_serial_bridge(article, self.serials)
            if selected is None:
                self.assertEqual(promo, "", article["slug"])
                continue
            count += 1
            episode = selected["episodes"][-1] if selected["kind"] == "analysis" else selected["episodes"][0]
            url = f'/serials/{selected["slug"]}/{episode["slug"]}/'
            self.assertIn('class="article-serial-bridge"', promo)
            self.assertIn(f'href="{url}"', promo)
            self.assertIn(f'href="/serials/{selected["slug"]}/"', promo)
            self.assertIn(f'src="/serials/{selected["slug"]}/cover.svg"', promo)
            self.assertIn('width="1200" height="675"', promo)
            self.assertIn("未発生新聞の連載", promo)
            if selected["kind"] == "analysis":
                self.assertIn("実際の公開資料", promo)
                self.assertIn("投資助言・売買推奨でもありません", promo)
                self.assertIn("架空の出来事", promo)
                self.assertNotIn("創作推理小説", promo)
            else:
                self.assertIn("創作推理小説", promo)
                self.assertIn("この記事の続報ではなく", promo)
                self.assertNotIn("現実の市場分析", promo)
        self.assertGreaterEqual(count, 5)

    def test_fiction_articles_do_not_masquerade_as_market_evidence(self):
        finance = {"title": "AI工場への投資", "summary": "研究所", "category": "経済"}
        mystery = {"title": "市役所の謎", "summary": "事件", "category": "社会"}
        sports = {"title": "野球の引退", "summary": "野球", "category": "スポーツ"}
        generic_tech = {"title": "道具の展示", "summary": "絵の展示", "category": "技術"}
        self.assertEqual(choose_serial(finance, self.serials)["kind"], "analysis")
        self.assertEqual(choose_serial(mystery, self.serials)["kind"], "fiction")
        self.assertIsNone(choose_serial(sports, self.serials))
        self.assertIsNone(choose_serial(generic_tech, self.serials))
        self.assertEqual(render_serial_bridge(sports, self.serials), "")

    def test_bridge_is_real_html_not_a_script_or_fake_popularity(self):
        article = next(a for a in self.articles if a["category"] == "社会")
        page = story(article, self.articles, serials=self.serials)
        self.assertEqual(page.count('class="article-serial-bridge"'), 1)
        self.assertLess(page.index('class="reader-next"'), page.index('class="article-serial-bridge"'))
        self.assertLess(page.index('class="article-serial-bridge"'), page.index('class="reader-comments"'))
        self.assertNotIn("あなたへのおすすめ", page)
        self.assertNotIn("閲覧数ランキング", page)

    def test_missing_series_and_hostile_input_fail_safely(self):
        article = {"category": "社会", "title": "職場", "summary": ""}
        self.assertIsNone(choose_serial(article, []))
        self.assertEqual(render_serial_bridge(article, []), "")
        altered = deepcopy(self.serials)
        mystery = next(s for s in altered if s["kind"] == "fiction")
        mystery["title"] = '<img src=x onerror=alert(1)>'
        mystery["episodes"][-1]["title"] = '<script>alert(1)</script>'
        promo = render_serial_bridge(article, altered)
        self.assertIn("&lt;img src=x", promo)
        self.assertIn("&lt;script&gt;", promo)
        self.assertNotIn("<script>", promo)
        self.assertNotIn("<img src=x", promo)

    def test_serial_index_has_correct_editorial_title_and_itemlist(self):
        markup = render_serials_index(self.serials, layout)
        self.assertIn("<title>連載一覧 | 未発生新聞（創作と市場分析の連載）</title>", markup)
        schemas = structured(markup)
        page = next(x for x in schemas if x["@type"] == "CollectionPage")
        self.assertEqual(page["genre"], "創作小説・市場分析")
        items = next(x for x in schemas if x["@type"] == "ItemList")
        self.assertEqual(items["numberOfItems"], len(self.serials))
        self.assertEqual(
            [item["url"] for item in items["itemListElement"]],
            [SITE + "/serials/" + s["slug"] + "/" for s in self.serials],
        )
        crumbs = next(x for x in schemas if x["@type"] == "BreadcrumbList")
        self.assertEqual(crumbs["itemListElement"][-1]["item"], SITE + "/serials/")

    def test_chapter_is_a_part_of_correct_series_with_a_four_step_breadcrumb(self):
        for serial in self.serials:
            series_markup = render_series_page(serial, layout)
            series_schema = structured(series_markup)
            overview = next(x for x in series_schema if x["@type"] == "CollectionPage")
            self.assertEqual(overview["url"], SITE + "/serials/" + serial["slug"] + "/")
            chapters = next(x for x in series_schema if x["@type"] == "ItemList")
            self.assertEqual(chapters["numberOfItems"], len(serial["episodes"]))
            for index, ep in enumerate(serial["episodes"]):
                self.assertEqual(
                    chapters["itemListElement"][index]["url"],
                    SITE + "/serials/" + serial["slug"] + "/" + ep["slug"] + "/",
                )
                page = render_episode_page(serial, ep, layout)
                self.assertIn("市場分析" if serial["kind"] == "analysis" else "創作小説",
                              re.search(r"<title>(.*?)</title>", page, re.S).group(1))
                ld = structured(page)
                item = next(x for x in ld if x["@type"] == "Article")
                self.assertEqual(item["datePublished"], ep["published_at"])
                self.assertEqual(item["isPartOf"]["name"], serial["title"])
                self.assertEqual(
                    item["isPartOf"]["url"], SITE + "/serials/" + serial["slug"] + "/")
                crumbs = next(x for x in ld if x["@type"] == "BreadcrumbList")
                self.assertEqual(
                    [x["position"] for x in crumbs["itemListElement"]], [1, 2, 3, 4])
                self.assertEqual(crumbs["itemListElement"][-1]["item"],
                                 SITE + "/serials/" + serial["slug"] + "/" + ep["slug"] + "/")
                self.assertIn('rel="canonical"', page)

    def test_shared_reader_entry_policy_is_stable(self):
        fiction = next(s for s in self.serials if s["kind"]=="fiction")
        market = next(s for s in self.serials if s["kind"]=="analysis")
        self.assertEqual(primary_episode(fiction)["slug"], fiction["episodes"][0]["slug"])
        self.assertEqual(primary_episode(market)["slug"], market["episodes"][-1]["slug"])
        with self.assertRaisesRegex(ValueError, "published episodes"):
            primary_episode({"kind": "fiction", "episodes": []})

    def test_bridge_uses_current_episode_as_serial_grows(self):
        article = {"category": "社会", "title": "推理事件", "summary": ""}
        expanded = deepcopy(self.serials)
        mystery = next(s for s in expanded if s["kind"] == "fiction")
        e = deepcopy(mystery["episodes"][0])
        e["number"] = 2
        e["slug"] = "second-real-episode"
        e["title"] = "新たな事件の続編"
        mystery["episodes"].append(e)
        promo = render_serial_bridge(article, expanded)
        self.assertIn("/victim-not-yet-dead-01/", promo)
        self.assertNotIn("/second-real-episode/", promo)
        self.assertIn("最新の第2話", promo)
        self.assertIn("第1話から読む", promo)
        market = next(s for s in expanded if s["kind"] == "analysis")
        forecast = deepcopy(market["episodes"][0])
        forecast["number"] = 2
        forecast["slug"] = "follow-up-power-grid-02"
        market["episodes"].append(forecast)
        markets = render_serial_bridge({"category": "経済", "title": "電力市場", "summary": ""}, expanded)
        self.assertIn("/follow-up-power-grid-02/", markets)
        self.assertIn("最新の第2回を読む", markets)


if __name__ == "__main__":
    unittest.main()
