"""Direct display checks; never invoke the full-site build fixture."""
import copy
import html
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import build, serials
from scripts.serial_discovery import render_serial_bridge

ROOT = Path(__file__).resolve().parents[1]


def just_main(_title, _description, _path, main, **_kwargs):
    return main


class GravureCornerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles = build.load()
        cls.serials = serials.load_serials(ROOT)
        cls.beach = next(item for item in cls.articles if item["slug"] == build.GRAVURE_SOURCE_SLUG)

    def test_draft_corner_uses_only_the_existing_reviewed_photo_article(self):
        card = build.gravure_feature_card(self.articles)
        self.assertIn('href="' + build.article_url(self.beach) + '"', card)
        self.assertIn('src="' + html.escape(build.generated_url(self.beach, root=ROOT), quote=True) + '"', card)
        self.assertIn("既刊の写真記事を読む", card)
        self.assertNotIn("/photo/weekly/", card)
        for text in ("第1号", "10枚", "創刊", "準備中", "draft"):
            self.assertNotIn(text, card)
        with patch.object(build, "uses_generated_scene", return_value=False):
            self.assertEqual(build.gravure_feature_card(self.articles), "")
        self.assertEqual(build.gravure_feature_card([]), "")

    def test_home_replaces_mystery_promo_and_keeps_market_analysis(self):
        page = build.home(self.articles, self.serials)
        self.assertIn("グラビア誌", page)
        self.assertIn("未来相場観測室", page)
        self.assertIn('class="serial-card serial-card--analysis"', page)
        self.assertNotIn('data-series-id="case-files"', page)
        self.assertNotIn('href="/serials/case-files/', page)
        with patch.object(build, "PHOTO_ISSUES_AVAILABLE", False):
            nav = build.layout("Page", "Description", "/example/", "body")
        self.assertIn('href="' + build.GRAVURE_SOURCE_URL + '">グラビア誌</a>', nav)
        self.assertIn('href="/serials/market-lab/">未来相場観測室</a>', nav)
        self.assertNotIn('href="/photo/weekly/"', nav)

    def test_published_issue_promotes_latest_and_weekly_index(self):
        def issue(number):
            return {"slug": "issue-" + str(number), "number": number, "title": "完成した写真号",
                    "summary": "写真と本人コラム。", "published_at": f"2026-11-{number:02d}T13:30:00+09:00",
                    "shots": [{"id": "01", "alt": "架空モデルの写真"}],
                    "images": {"01": {"sha256": "a" * 64}}}
        card = build.gravure_feature_card(self.articles, [issue(2), issue(1)])
        self.assertIn('href="/photo/weekly/issue-2/"', card)
        self.assertIn('href="/photo/weekly/"', card)
        self.assertIn("第2号", card)
        self.assertNotIn(build.GRAVURE_SOURCE_URL, card)
        with patch.object(build, "PHOTO_ISSUES_AVAILABLE", True):
            nav = build.layout("Page", "Description", "/example/", "body")
        self.assertIn('href="/photo/weekly/">グラビア誌</a>', nav)

    def test_withdrawn_mystery_leaves_the_public_corner_and_market_stays(self):
        source = serials.load_serials(ROOT, include_withdrawn=True)
        series = next(item for item in source if item["slug"] == "case-files")
        original = copy.deepcopy(series)
        for ep in series["episodes"]:
            with self.assertRaisesRegex(ValueError, "withdrawn"):
                serials.render_episode_page(series, ep, just_main)
        with self.assertRaisesRegex(ValueError, "withdrawn"):
            serials.render_series_page(series, just_main)
        self.assertEqual(series, original)
        self.assertEqual(render_serial_bridge({"category": "社会", "title": "市役所の謎"}, source), "")
        market = next(item for item in source if item["slug"] == "market-lab")
        page = serials.render_episode_page(market, market["episodes"][-1], just_main)
        self.assertIn("次回は未公開", page)
        self.assertIn("続編をRSSで確認", page)
        self.assertNotIn("連載終了", page)


if __name__ == "__main__":
    unittest.main()
