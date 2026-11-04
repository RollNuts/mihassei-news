"""Single-clip fictional newsroom: opt-in, accessibility and static fallbacks."""
from __future__ import annotations
import unittest
from unittest.mock import patch
from pathlib import Path
from scripts import build

ROOT = Path(__file__).resolve().parents[1]


class NewsBroadcastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles = build.load()

    def test_broadcast_is_single_and_not_a_live_news_claim(self):
        marked = [a for a in self.articles if a.get("broadcast_feature") is True]
        self.assertTrue(marked)
        self.assertLessEqual(len(build.broadcast_playlist(self.articles)), 3)
        home = build.home(self.articles)
        self.assertEqual(home.count('class="broadcast-spotlight"'), 1)
        self.assertEqual(home.count('class="broadcast-video"'), 1)
        self.assertIn('UNHAPPENED TV', home)
        self.assertIn('創作ニュースの演出映像', home)
        self.assertIn('controls playsinline muted loop preload="none"', home)
        self.assertIn('src="/assets/news-broadcast.js"', home)
        self.assertNotIn('autoplay', home)
        self.assertIn('架空ニュース', home)

    def test_missing_clip_drops_newsroom_but_keeps_headlines(self):
        with patch.object(build, "usable_clip", return_value=None):
            page = build.home(self.articles)
            self.assertNotIn('class="broadcast-spotlight"', page)
            self.assertIn('class="hero-grid"', page)
            self.assertIn('class="hero-image"', page)

    def test_video_failure_has_a_real_image_fallback(self):
        article = next(a for a in self.articles if a.get("broadcast_feature"))
        with patch.object(build, "usable_clip", return_value={
            "sha256": "b"*64, "created_at": "2026-11-03T00:00:00+00:00"
        }):
            markup = build.article_hero_media(article)
            self.assertIn('data-video-shell', markup)
            self.assertIn('data-video-fallback', markup)
            self.assertIn('hidden loading="eager"', markup)
            self.assertIn('?v=' + "b"*12, markup)
        js = (ROOT/"assets/news-broadcast.js").read_text(encoding="utf-8")
        self.assertIn("addEventListener('error'", js)
        self.assertIn("fallback.hidden = false", js)
        self.assertIn("prefers-reduced-motion: reduce", js)
        self.assertIn("navigator.connection?.saveData", js)
        self.assertIn("IntersectionObserver", js)

    def test_three_captions_belong_to_the_exact_current_article(self):
        article = next(a for a in self.articles if a["slug"] == "bakery-no-important-announcement-panic")
        clip = {"motion_style_version": 3, "duration_ms": 12000}
        cues = build.broadcast_cues(article, clip)
        self.assertEqual([cue["at"] for cue in cues], [0, 4, 8])
        self.assertIn("完売", cues[1]["text"])
        self.assertIn("スマホ", cues[2]["text"])
        self.assertEqual(build.broadcast_cues({**article, "summary": "An edited story"}, clip), [])
        self.assertEqual(build.broadcast_cues(article, {"motion_style_version": 2}), [])
        # Scripts are optional; invalid JSON must not break the newspaper build.
        with patch.object(Path, "read_text", return_value="not json"):
            self.assertEqual(build.broadcast_cues(article, clip), [])

    def test_new_video_length_and_captions_reach_all_reader_surfaces(self):
        article = next(a for a in self.articles if a["slug"] == "bakery-no-important-announcement-panic")
        with patch.object(build, "usable_clip", return_value={
            "sha256": "c"*64, "created_at": "2026-11-04T00:00:00+00:00",
            "motion_style_version": 3, "duration_ms": 12000,
        }):
            player = build.article_hero_media(article)
            self.assertIn("12秒の無音演出映像", player)
            self.assertIn("data-broadcast-overlay", player)
            self.assertIn("data-cues=", player)
            self.assertIn("▶ 12秒映像", build.card(article))
            home = build.broadcast_feature([article])
            self.assertIn('poster="' + build.editorial_image(article) + '"', home)
            self.assertNotIn("broadcast-ticker", home)
            self.assertIn("data-broadcast-progress", home)

    def test_incident_is_separate_from_social_and_linked(self):
        incidents = [a for a in self.articles if a["category"] == "事件"]
        self.assertTrue(incidents)
        self.assertIn("事件", build.CATEGORIES)
        self.assertEqual(build.CATEGORY_PATHS["事件"], "incidents")
        archive = build.archive(self.articles)
        self.assertIn('data-category-filter="事件"', archive)
        page = build.story(incidents[0], self.articles)
        self.assertIn('この記事は創作です', page)
        self.assertIn("J-CASTニュース", page)


if __name__ == "__main__":
    unittest.main()
