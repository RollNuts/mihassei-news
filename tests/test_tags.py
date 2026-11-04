import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.tags import (
    ROOT, article_hashtags, load_tags, render_tag_links, tag_collections,
    tag_url, tags_for_article, tags_for_episode, tags_for_series,
    topic_hashtags, validate_tag_assignments,
)


class TagRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = {
            "version": 1,
            "definitions": [
                {"slug": "ai", "label": "AI", "description": "AIの話。"},
                {"slug": "family", "label": "家族", "description": "家族の話。"},
                {"slug": "taipa", "label": "タイパ", "description": "時間の話。"},
            ],
            "articles": {"news-one": ["ai", "taipa"], "news-two": ["family"]},
            "episodes": {"case-files/chapter-one": ["family", "ai"], "market-lab/outlook-one": ["ai"]},
        }
        self.articles = [
            {"slug": "news-one", "published_at": "2026-11-03T16:00:00Z"},
            {"slug": "news-two", "published_at": "2026-11-04T00:30:00+09:00"},
        ]
        self.serials = [
            {"slug": "case-files", "kind": "fiction", "episodes": [{"slug": "chapter-one", "published_at": "2026-11-04"}]},
            {"slug": "market-lab", "kind": "analysis", "episodes": [{"slug": "outlook-one", "published_at": "2026-11-03"}]},
        ]

    def test_all_published_content_has_registered_topics(self):
        articles = json.loads((ROOT / "content/articles.json").read_text(encoding="utf-8"))
        serials = json.loads((ROOT / "content/serials.json").read_text(encoding="utf-8"))
        validate_tag_assignments(articles, serials)
        collections = {group["tag"]["slug"]: group for group in tag_collections(articles, serials)}
        self.assertEqual({item["kind"] for item in collections["ai"]["items"]}, {"news", "analysis"})
        self.assertEqual({item["kind"] for item in collections["family"]["items"]}, {"news", "fiction"})
        taipa_slugs = {item["article"]["slug"] for item in collections["taipa"]["items"]}
        self.assertIn("double-speed-drama-explainer-marathon", taipa_slugs)

    def test_invalid_assignments_fail_with_specific_reason(self):
        cases = [
            (lambda r: r["articles"].pop("news-one"), "Missing tags"),
            (lambda r: r["articles"].update({"missing-story": ["ai"]}), "Unknown articles"),
            (lambda r: r["episodes"].update({"case-files/missing": ["ai"]}), "Unknown episodes"),
            (lambda r: r["articles"].update({"news-one": ["unknown"]}), "Unknown tag"),
            (lambda r: r["articles"].update({"news-one": []}), "1-4 tags"),
            (lambda r: r["articles"].update({"news-one": ["ai"] * 5}), "1-4 tags"),
            (lambda r: r["articles"].update({"news-one": ["ai", "ai"]}), "Duplicate tag assignment"),
            (lambda r: r["definitions"].append({"slug": "unused", "label": "未使用"}), "Unused tag"),
            (lambda r: r["definitions"].append(copy.deepcopy(r["definitions"][0])), "Duplicate tag slug"),
            (lambda r: r["definitions"][0].update(slug="../escape"), "Invalid tag slug"),
            (lambda r: r["definitions"][0].update(label="A" * 25), "1-24"),
            (lambda r: r["definitions"][0].update(label="123"), "contain a letter"),
            (lambda r: r["definitions"][0].update(label="AI news"), "1-24"),
            (lambda r: r["definitions"][0].update(label="#AI"), "1-24"),
        ]
        for mutate, message in cases:
            with self.subTest(message=message):
                registry = copy.deepcopy(self.registry)
                mutate(registry)
                with self.assertRaisesRegex(ValueError, message):
                    validate_tag_assignments(self.articles, self.serials, registry)

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            content = Path(directory) / "content"
            content.mkdir()
            (content / "tags.json").write_text('{"version":1,"version":1}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate tag registry key"):
                load_tags(Path(directory))

    def test_lookup_tolerates_unregistered_ui_fixtures_but_validation_does_not(self):
        article = {"slug": "fixture"}
        self.assertEqual(tags_for_article(article, self.registry), [])
        self.assertEqual(tags_for_episode({"slug": "fixture"}, {"slug": "chapter"}, self.registry), [])
        with self.assertRaisesRegex(ValueError, "Missing tags"):
            validate_tag_assignments(self.articles + [article], self.serials, self.registry)

    def test_series_topics_are_a_stable_union_without_mutation(self):
        series = copy.deepcopy(self.serials[0])
        series["episodes"].append({"slug": "chapter-two"})
        self.registry["episodes"]["case-files/chapter-two"] = ["ai", "taipa"]
        before = copy.deepcopy((series, self.registry))
        self.assertEqual([tag["slug"] for tag in tags_for_series(series, self.registry)], ["family", "ai", "taipa"])
        self.assertEqual((series, self.registry), before)

    def test_cross_format_sort_normalizes_timezones_and_preserves_date_only(self):
        before = copy.deepcopy((self.articles, self.serials, self.registry))
        groups = {group["tag"]["slug"]: group for group in tag_collections(self.articles, self.serials, self.registry)}
        self.assertEqual([item["kind"] for item in groups["ai"]["items"]], ["news", "fiction", "analysis"])
        self.assertEqual(groups["ai"]["count"], 3)
        self.assertEqual((self.articles, self.serials, self.registry), before)
        self.assertEqual(groups["ai"]["items"][1]["episode"]["published_at"], "2026-11-04")

    def test_links_escape_editorial_text_and_reject_unsafe_routes(self):
        self.assertEqual(render_tag_links([]), "")
        rendered = render_tag_links([{"slug": "ai", "label": '<img src=x onerror="alert(1)">'}], label='" onmouseover="x', compact=True)
        self.assertIn('class="topic-tags topic-tags--compact"', rendered)
        self.assertIn('rel="tag" href="/tags/ai/"', rendered)
        self.assertIn("&lt;img", rendered)
        self.assertNotIn('<img', rendered)
        self.assertNotIn('aria-label="" onmouseover=', rendered)
        for slug in ("../secret", "ai?x=1", "", "/ai", "ai/#"):
            with self.subTest(slug=slug), self.assertRaises(ValueError):
                tag_url(slug)

    def test_x_hashtags_share_editorial_order_and_never_exceed_two(self):
        self.assertEqual(article_hashtags(self.articles[0], registry=self.registry), ["#AI", "#タイパ"])
        tags = [self.registry["definitions"][0]] * 2 + self.registry["definitions"][1:]
        self.assertEqual(topic_hashtags(tags, limit=10), ["#AI", "#家族"])
        self.assertEqual(topic_hashtags(tags, limit=1), ["#AI"])
        self.assertEqual(topic_hashtags(tags, limit=0), [])
        self.assertEqual(article_hashtags({"slug": "missing"}, registry=self.registry), [])
        self.assertEqual(topic_hashtags([{"label": "ＡＩ"}]), ["#AI"])
        for label in ("AI news", "#AI", "123", "<script>"):
            with self.subTest(label=label), self.assertRaises(ValueError):
                topic_hashtags([{"label": label}])


if __name__ == "__main__":
    unittest.main()
