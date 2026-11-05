"""Reader-visible article copy never repeats fiction boilerplate.

The original article data and exact media SHA inputs must remain immutable;
legacy JPEG approvals cannot be recreated by changing fingerprint metadata.
"""
import json
import unittest
from pathlib import Path

from scripts import build
from scripts.editorial_copy import reader_copy
from scripts.generated_photos import article_fingerprint

ROOT = Path(__file__).resolve().parents[1]


class EditorialReaderCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles = build.load()

    def test_cleanup_is_narrow_and_idempotent(self):
        original = "2031年、架空の会社「ポロリ」は創作の制度を始めた。"
        expected = "2031年、会社「ポロリ」は制度を始めた。"
        self.assertEqual(reader_copy(original), expected)
        self.assertEqual(reader_copy(expected), expected)
        self.assertEqual(reader_copy("架空請求ではない"), "架空請求ではない")

    def test_all_existing_reader_copy_can_omit_disclaimer_without_source_edits(self):
        original = (ROOT / "content/articles.json").read_bytes()
        originals = json.loads(original)
        for article in originals:
            with self.subTest(slug=article["slug"]):
                for field in ("title", "summary", "source"):
                    text = (article["source"].get("topic", "")
                            if field == "source" else article[field])
                    self.assertNotIn("架空の", reader_copy(text))
                for paragraph in article["paragraphs"]:
                    self.assertNotIn("架空の", reader_copy(paragraph))
        self.assertEqual(original, (ROOT / "content/articles.json").read_bytes())

    def test_rendered_stories_and_shares_do_not_expose_legacy_qualifiers(self):
        examples = (
            "basketball-opening-day-game-seven",
            "music-show-surprise-face-rehearsal",
            "datacenter-unbuilt-ribbon-cutting",
            "ai-chair-removes-executives",
        )
        by_slug = {article["slug"]: article for article in self.articles}
        for slug in examples:
            with self.subTest(slug=slug):
                article = by_slug[slug]
                prior_sha = article_fingerprint(article)
                page = build.story(article, self.articles)
                body = page.split('<div class="article-body">', 1)[1].split("</div>", 1)[0]
                self.assertNotIn("架空の", body)
                self.assertIn("着想元を見る", page)
                self.assertIn('class="dek"', page)
                self.assertNotIn("架空の", build.share_url(article))
                self.assertEqual(article_fingerprint(article), prior_sha)

    def test_media_contract_stays_tied_to_original_canonical_source(self):
        originals = json.loads((ROOT / "content/articles.json").read_text(encoding="utf-8"))
        for a in originals:
            before = article_fingerprint(a)
            _ = reader_copy(a["summary"])
            _ = [reader_copy(p) for p in a["paragraphs"]]
            self.assertEqual(article_fingerprint(a), before)


if __name__ == "__main__":
    unittest.main()
