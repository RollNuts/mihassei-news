"""The X share desk never auto-posts, fabricates popularity or calls paid APIs."""
from __future__ import annotations

import html
import re
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree as ET

from scripts.share_desk import TRACKING, draft, real_date, render, select_candidates

ROOT = Path(__file__).resolve().parents[1]


def sample(slug, category, day, title=None, summary=None):
    return {
        "slug": slug, "category": category,
        "published_at": day + "T18:20:00+09:00",
        "source": {"published_at": day + "T16:10:00+09:00"},
        "fiction_at": "2031-11-02T18:20:00+09:00",
        "title": title or ("架空ニュース：" + slug),
        "summary": summary or "ありそうで、まだ起きていない世界のニュースです。",
        "photo": None
    }


class ShareDeskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # CI test discovery can run this file before test_site.py builds dist.
        subprocess.run([sys.executable,str(ROOT/"scripts/build.py")],
                       cwd=ROOT,check=True,stdout=subprocess.DEVNULL)

    def test_newest_publication_day_and_category_diversity(self):
        items = [
            sample("old", "スポーツ", "2026-11-01"),
            sample("fresh-society", "社会", "2026-11-02"),
            sample("fresh-economy", "経済", "2026-11-02"),
            sample("fresh-society-two", "社会", "2026-11-02"),
            sample("fresh-tech", "技術", "2026-11-02"),
        ]
        picks = select_candidates(items, 3)
        self.assertEqual(len(picks), 3)
        self.assertEqual({p["category"] for p in picks}, {"社会", "経済", "技術"})
        self.assertTrue(all(real_date(p).startswith("2026-11-02") for p in picks))
        self.assertEqual(len({p["slug"] for p in picks}), 3)
        self.assertEqual(select_candidates(items, 0), [])

    def test_falls_back_to_prior_day_without_mislabeling_as_today(self):
        items = [sample("one", "社会", "2026-11-02"),
                 sample("two", "技術", "2026-11-01"),
                 sample("three", "経済", "2026-10-31")]
        picks = select_candidates(items, 3)
        self.assertEqual([a["slug"] for a in picks], ["one", "two", "three"])
        page = render(items, lambda a: "/og/articles/"+a["slug"]+".png",
                      lambda a: False)
        self.assertIn("前の発行分", page)
        self.assertIn("11月2日公開", page)
        self.assertIn("10月31日公開", page)
        self.assertNotIn("全員が注目", page)

    def test_draft_is_fiction_labeled_and_small_enough_to_copy(self):
        a = sample("very-long-story", "技術", "2026-11-02",
                   title="未来の新しいニュースを届ける不思議な世界" * 11,
                   summary="とても長い、まだ起きていない出来事を描いた紹介文。" * 12)
        details = draft(a)
        self.assertLessEqual(len(details["copy"]), 265)
        self.assertIn("※創作", details["copy"])
        self.assertIn("#未発生新聞", details["copy"])
        self.assertIn(TRACKING, details["copy"])
        self.assertIn("2031年の架空ニュース", details["text"])
        self.assertNotIn("人気第1位", details["copy"])
        params = parse_qs(urlparse(details["intent"]).query)
        self.assertEqual(params["text"], [details["text"]])
        self.assertEqual(params["url"], [details["tagged_url"]])
        self.assertEqual(params["url"][0].split("?")[0],
                         "https://veripsa.com/articles/very-long-story/")

    def test_render_is_safe_noindex_and_only_uses_local_images(self):
        a = sample("safe-story", "社会", "2026-11-02",
                   title='未来の"実験" & <script>alert(1)</script>',
                   summary='これは架空 & <想定外> のニュースです。')
        output = render([a], lambda item: "/og/articles/"+item["slug"]+".png",
                        lambda item: False)
        self.assertIn('name="robots" content="noindex,nofollow"', output)
        self.assertIn('href="/og/articles/safe-story.png" download=', output)
        self.assertIn("投稿文をコピー", output)
        self.assertIn("Xで下書きを開く", output)
        self.assertIn("画像を保存・開く", output)
        self.assertIn("aria-live=", output)
        self.assertIn("&lt;script&gt;", output)
        self.assertNotIn("<script>alert(1)</script>", output)
        self.assertNotIn("/api/comments", output)
        self.assertNotIn("api.twitter.com", output)
        self.assertNotIn("自動で投稿", output)
        self.assertEqual(len(re.findall(r'<article class="item">', output)), 1)
        self.assertIn("image-review/", output)
        with self.assertRaises(ValueError):
            render([a], lambda item: "https://images.unsplash.com/private.jpg",lambda item: False)

    def test_empty_case_does_not_invent_news(self):
        body = render([], lambda a: "/og/articles/demo.png",lambda a: False)
        self.assertIn("公開済みの記事がありません", body)
        self.assertEqual(body.count('<article class="item">'), 0)

    def test_new_build_contains_bounded_real_article_candidates_and_headers(self):
        import json
        from scripts.build import social_image
        from scripts.media_policy import usable_cached_photo
        articles=json.loads((ROOT/"content/articles.json").read_text(encoding="utf-8"))
        self.assertTrue(articles)
        path=ROOT/"dist/share-desk/index.html"
        self.assertFalse(path.exists(), "Promotion desk must not be publicly served")
        markup=render(articles, social_image, lambda a: usable_cached_photo(a,root=ROOT))
        candidates=select_candidates(articles,3)
        self.assertEqual(markup.count('<article class="item">'),len(candidates))
        self.assertIn("name=\"robots\" content=\"noindex,nofollow\"",markup)
        for article in candidates:
            self.assertIn(html.escape(article["title"]),markup)
            self.assertIn(html.escape(social_image(article),quote=True),markup)
            self.assertIn("/articles/"+article["slug"]+"/",markup)
            if usable_cached_photo(article,root=ROOT):
                self.assertIn(article["photo"]["credit"],markup)
        headers=(ROOT/"dist/_headers").read_text(encoding="utf-8")
        self.assertIn("/share-desk/*\n  X-Robots-Tag: noindex, nofollow",headers)
        sitemap=(ROOT/"dist/sitemap.xml").read_text(encoding="utf-8")
        self.assertNotIn("https://veripsa.com/share-desk/",sitemap)
        ET.fromstring((ROOT/"dist/feed.xml").read_text(encoding="utf-8"))


if __name__=="__main__":
    unittest.main()
