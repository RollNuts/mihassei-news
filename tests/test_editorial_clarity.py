"""Reader-first satire fixes: concrete causes, transparent but quiet imagery labels.

These assertions protect known regressions and content integrity; passing them
does not amount to a machine judgement of whether a joke is genuinely funny.
"""
from __future__ import annotations

import hashlib
import json
import unittest
from datetime import datetime
from html import escape
from pathlib import Path
from unittest.mock import patch

from scripts import build
from scripts.generated_photos import article_fingerprint, usable_generated_scene, generated_url as generated_photo_url
from scripts.media_policy import jpeg_dimensions

ROOT = Path(__file__).resolve().parents[1]
REVISED = {
    "volleyball-team-lunch-plate-merch": ("皿の型番", "広報", "弁当箱", "2026-11-03T17:37:14+09:00"),
    "mortgage-bank-clerk-invited-to-live-in": ("家計会議", "食費", "利息", "2026-11-03T13:28:44+09:00"),
    "research-completed-opening-day": ("開所式", "あいさつ", "必要ありません", "2026-11-02T17:36:39+09:00"),
    "robot-job-hopping": ("充電", "17台", "省人化", "2026-11-02T17:36:39+09:00"),
}
# These exact JPEGs failed visual review. A genuinely new, reviewed image may
# replace them; changing their manifest fingerprint must never reapprove them.
# Identities are from the image manifest at b466868, before the new CPU images.
REJECTED_JPEG_SHA256 = {
    "family-dinner-gacha-243-rejections": "9d6d701b9a253f68f89a3119554fa4cbbb3bff29dc0170ca63fd632895d0eb1b",
    "volleyball-team-lunch-plate-merch": "1bd7bbecba52b761eeabcaa17729e9447f54515be33730c8ad8d3202a8d54710",
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
                published = datetime.fromisoformat(article["published_at"])
                self.assertEqual(datetime.fromisoformat(article["fiction_at"]),
                                 published.replace(year=published.year + 5))
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
        for slug in ("mortgage-bank-clerk-invited-to-live-in",
                     "music-ai-friends-monopolize-the-song"):
            with self.subTest(slug=slug):
                # Later editorial changes can invalidate a genuine photograph;
                # publication must continue with an honest fallback in that case.
                self.assert_story_image_contract(self.articles[slug])
        css = (ROOT/"assets/editorial-polish.css").read_text(encoding="utf-8")
        basic_css = (ROOT/"assets/newsroom.css").read_text(encoding="utf-8")
        self.assertNotIn('content:"AI生成・創作"', css)
        self.assertIn('content:"イメージ"', basic_css)
        policy = build.editorial_policy()
        self.assertIn("実写風の合成画像", policy)
        # The photo/fiction disclosure stays; unrelated visible AI badges do not.
        self.assertIn("「再現イメージ（合成）」と表示", policy)

    def test_revised_stories_use_new_verified_photos_or_honest_fallback(self):
        for slug in ("family-dinner-gacha-243-rejections", *REVISED):
            with self.subTest(slug=slug):
                self.assert_story_image_contract(self.articles[slug])

    def test_editorial_changes_can_use_fallback_until_a_new_photo_is_verified(self):
        for slug in ("family-dinner-gacha-243-rejections", *REVISED):
            with self.subTest(slug=slug):
                changed = {**self.articles[slug],
                           "summary": self.articles[slug]["summary"] + " 改稿後の検証用。"}
                self.assertFalse(usable_generated_scene(changed, root=ROOT))
                self.assert_story_image_contract(changed)

    def assert_story_image_contract(self, article):
        slug = article["slug"]
        generated_url = generated_photo_url(article, root=ROOT)
        with patch.object(build, "BASE", ROOT):
            page = build.story(article, list(self.articles.values()))
            safe_image = build.editorial_image(article)
        self.assertIn("<h1>" + escape(article["title"]) + "</h1>", page)
        self.assertIn('class="article-visual"', page)
        self.assertIn('src="' + safe_image + '"', page)
        self.assertIn('class="fiction-mark">創作報道', page)
        self.assertNotIn("AI生成の架空ニュース再現画像", page)

        if safe_image == generated_url:
            # Verify the actual published bytes and source independently of the
            # builder's boolean gate, rather than pinning a permanent hold list.
            manifest = json.loads((ROOT / "assets/generated/manifest.json").read_text(encoding="utf-8"))
            record = manifest["images"][slug]
            data = (ROOT / "assets/generated" / (slug + ".jpg")).read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            self.assertNotIn(digest, REJECTED_JPEG_SHA256.values())
            self.assertEqual(record["sha256"], digest)
            self.assertEqual(record["article_sha256"], article_fingerprint(article))
            self.assertEqual(record["bytes"], len(data))
            self.assertLessEqual(len(data), 700 * 1024)
            self.assertEqual(jpeg_dimensions(data), (1200, 675))
            rejection = manifest.get("rejected", {}).get(slug, {})
            self.assertNotEqual(rejection.get("article_sha256"), article_fingerprint(article))
            self.assertTrue(usable_generated_scene(article, root=ROOT))
            self.assertIn("再現イメージ（合成）", page)
            self.assertIn("<figcaption>再現イメージ（合成）", page)
            self.assertNotIn("<figcaption>創作イラスト", page)
        else:
            self.assertFalse(usable_generated_scene(article, root=ROOT))
            self.assertIn(safe_image, ("/images/" + slug + ".svg",
                                       "/photos/" + slug + ".jpg"))
            self.assertTrue("創作イラスト" in page or "イメージ写真" in page)

        # A removed hold marker must not turn either known bad JPEG into a
        # public generated image, even if somebody restamps its metadata.
        image = ROOT / "assets/generated" / (slug + ".jpg")
        if image.is_file() and hashlib.sha256(image.read_bytes()).hexdigest() in REJECTED_JPEG_SHA256.values():
            self.assertNotEqual(safe_image, generated_url)


if __name__ == "__main__":
    unittest.main()
