"""The production workflow must verify actual published bytes, not an optional app check."""
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class DeploymentWorkflowTests(unittest.TestCase):
    def test_live_artwork_checks_follow_actual_text_free_article_design(self):
        checker=(ROOT/"scripts/check_live.py").read_text(encoding="utf-8")
        self.assertIn('b"data-visual-topic="',checker)
        self.assertIn('b"<text" in cover',checker)
        self.assertIn('class="share-control"',checker)
        self.assertIn('if "創作イラスト" not in page:',checker)
        self.assertNotIn('class="reader-follow"',checker)

    def test_live_checks_follow_daily_counter_labels_and_units(self):
        """Release checks must not keep demanding retired lifetime count labels."""
        checker=(ROOT/"scripts/check_live.py").read_text(encoding="utf-8")
        builder=(ROOT/"scripts/build.py").read_text(encoding="utf-8")
        for token in ("今日の来訪","昨日の来訪","今日の記事閲覧",
                      "data-observer-yesterday","data-observer-reads",
                      "1ブラウザ・1記事につき1日1回の集計"):
            self.assertIn(token,checker)
            self.assertIn(token,builder)
        self.assertIn("Daily JST audience counters missing or invalid",checker)
        self.assertIn("Previous JST audience counters missing or invalid",checker)
        self.assertIn("Daily per-story browser count missing or invalid",checker)
        self.assertNotIn('"観測されたブラウザ" not in body',checker)

    def test_rss_live_checker_uses_all_current_categories(self):
        from scripts.build import CATEGORIES
        from xml.etree import ElementTree as ET
        checker=(ROOT/"scripts/check_live.py").read_text(encoding="utf-8")
        self.assertIn('set(CATEGORIES[1:])', checker)
        from scripts.build import CATEGORY_PATHS
        self.assertIn('category_paths = CATEGORY_PATHS', checker)
        self.assertEqual(CATEGORY_PATHS["事件"], "incidents")
        self.assertIn("事件", CATEGORIES)
        feed=ET.fromstring("<rss><channel><item><category>事件</category></item></channel></rss>")
        self.assertIn(feed.findtext("./channel/item/category"), set(CATEGORIES[1:]))

    def test_live_checker_inspects_new_publications_not_old_source_news(self):
        """A newly published story with an older source still needs smoke checks."""
        from scripts.check_live import latest_articles
        from scripts.build import editorial_lead
        older={"slug":"older","published_at":"2026-11-02T18:30:00+09:00",
               "source":{"published_at":"2026-11-03T17:00:00+09:00"}}
        fresh={"slug":"fresh","published_at":"2026-11-03T18:00:00+09:00",
               "source":{"published_at":"2026-11-01T07:00:00+09:00"}}
        items=[older,fresh]
        self.assertEqual(latest_articles(items)[0]["slug"],"fresh")
        self.assertEqual(latest_articles(list(reversed(items)))[0]["slug"],"fresh")
        self.assertEqual(latest_articles(items,limit=1)[0]["slug"],"fresh")
        self.assertEqual(len(latest_articles(items,limit=None)),2)
        self.assertEqual(editorial_lead(items)["slug"],
                         latest_articles(items)[0]["slug"])

    def test_verified_motion_player_and_live_checker_agree_on_cache_bust(self):
        from scripts import build
        from unittest.mock import patch
        checker=(ROOT/"scripts/check_live.py").read_text(encoding="utf-8")
        test_article=build.load()[0]
        manifest={"sha256": "b"*64, "created_at": "2026-11-03T01:00:00+00:00"}
        with patch.object(build, "usable_clip", return_value=manifest):
            player=build.article_hero_media(test_article)
        self.assertIn('preload="metadata"', player)
        self.assertIn('?v=' + "b"*12, player)
        self.assertIn("data-video-fallback", player)
        self.assertIn('recorded_clip["sha256"][:12]', checker)
        self.assertIn('controls playsinline muted loop preload="metadata"', checker)
        self.assertIn("data-video-fallback", checker)
        self.assertIn('hashlib.sha256(movie).hexdigest() != recorded_clip["sha256"]', checker)

    def test_live_release_waits_for_real_public_commit(self):
        markup=(ROOT/".github/workflows/verify-public-photos.yml").read_text(encoding="utf-8")
        self.assertIn("concurrency:",markup)
        self.assertIn("cancel-in-progress: true",markup)
        self.assertIn('EXPECTED_SHA: ${{ github.sha }}',markup)
        self.assertIn('https://veripsa.com/build-revision.txt?release=',markup)
        self.assertIn('if latest == expected:',markup)
        self.assertIn("python3 scripts/check_live.py",markup)
        self.assertIn("python3 scripts/check_share_cards.py",markup)
        self.assertNotIn("Workers Builds: mihassei-news",markup)
        self.assertNotIn("GH_TOKEN:",markup)
        self.assertIn("read-only",markup.lower())

if __name__=="__main__":
    unittest.main()
