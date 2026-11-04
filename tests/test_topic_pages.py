"""Reader-facing topic navigation across news, fiction and market analysis.

Inspect rendered links and structured data offline; no browser or API required.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from scripts import build
from scripts.audit_search_index import Page as SearchPage
from scripts.serials import load_serials, render_episode_page, render_series_page
from scripts.tags import (
    tag_collections, tag_url, tags_for_article, tags_for_episode, tags_for_series,
)


class Page(SearchPage):
    """Keep card boundaries and visible link text in addition to SEO metadata."""

    def __init__(self, markup):
        super().__init__()
        self.anchors = []
        self.cards = []
        self.nested_anchors = 0
        self._anchors = []
        self._cards = []
        self.feed(markup)
        self.close()

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        values = dict(attrs)
        if tag == "article":
            self._cards.append({"attrs": values, "links": [], "text": []})
        elif tag == "a":
            self.nested_anchors += bool(self._anchors)
            self._anchors.append({**values, "text": []})
            for card in self._cards:
                card["links"].append(values.get("href", ""))

    def handle_data(self, data):
        super().handle_data(data)
        for anchor in self._anchors:
            anchor["text"].append(data)
        for card in self._cards:
            card["text"].append(data)

    def handle_endtag(self, tag):
        super().handle_endtag(tag)
        if tag == "a" and self._anchors:
            anchor = self._anchors.pop()
            anchor["text"] = "".join(anchor["text"]).strip()
            self.anchors.append(anchor)
        elif tag == "article" and self._cards:
            card = self._cards.pop()
            card["text"] = " ".join(card["text"])
            self.cards.append(card)


def item_url(item):
    if item["kind"] == "news":
        return "/articles/" + item["article"]["slug"] + "/"
    return "/serials/" + item["series"]["slug"] + "/" + item["episode"]["slug"] + "/"


class TopicPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.articles = build.load()
        cls.serials = load_serials(build.BASE)
        cls.groups = tag_collections(cls.articles, cls.serials)

    def assert_tag_links(self, markup, expected_tags):
        page = Page(markup)
        self.assertEqual(page.nested_anchors, 0, "A tag link must not nest inside an article link")
        tag_links = {a.get("href"): a for a in page.anchors
                     if "tag" in a.get("rel", "").split()}
        for tag in expected_tags:
            with self.subTest(tag=tag["slug"]):
                self.assertIn(tag_url(tag), tag_links)
                self.assertEqual(tag_links[tag_url(tag)]["text"], "#" + tag["label"])
        return page

    def test_all_tags_have_static_hub_links_with_correct_counts(self):
        hub = Page(build.tag_index(self.articles, self.serials))
        self.assertEqual(hub.canonicals, [build.SITE + "/tags/"])
        self.assertNotIn("noindex", hub.meta.get("robots", ""))
        self.assertTrue(any(a.get("href") == "/tags/" and a.get("aria-current") == "page"
                            for a in hub.anchors))
        for group in self.groups:
            with self.subTest(tag=group["tag"]["slug"]):
                links = [a for a in hub.anchors if a.get("href") == tag_url(group["tag"])]
                self.assertEqual(len(links), 1)
                self.assertIn("#" + group["tag"]["label"], links[0]["text"])
                self.assertIn(str(group["count"]) + "本", links[0]["text"])
        home = Page(build.home(self.articles, self.serials))
        self.assertIn("/tags/", home.links)
        self.assertTrue(any(tag_url(g["tag"]) in home.links for g in self.groups))

    def test_tag_cards_and_itemlist_agree_on_count_order_and_content_kind(self):
        """A static listing must not omit serials or relabel real analysis as fiction."""
        labels = {"news": "創作ニュース", "fiction": "推理小説", "analysis": "市場分析"}
        mixed_kinds = set()
        for group in self.groups:
            with self.subTest(tag=group["tag"]["slug"]):
                page = Page(build.tag_page(group))
                expected = [item_url(item) for item in group["items"]]
                expected_set = set(expected)
                self.assertEqual(len(expected), len(expected_set))
                cards = [c for c in page.cards if set(c["links"]) & expected_set]
                self.assertEqual(len(cards), group["count"])
                card_urls = []
                for card, item in zip(cards, group["items"]):
                    urls = set(card["links"]) & expected_set
                    self.assertEqual(len(urls), 1)
                    card_urls.append(next(iter(urls)))
                    self.assertEqual(card["attrs"].get("data-content-kind"), item["kind"])
                    self.assertIn(labels[item["kind"]], card["text"])
                    if item["kind"] == "analysis":
                        self.assertNotIn("創作ニュース", card["text"])
                self.assertEqual(card_urls, expected)
                listing = next(s for s in page.schemas if s.get("@type") == "ItemList")
                self.assertEqual(listing["numberOfItems"], group["count"])
                self.assertEqual([i["url"] for i in listing["itemListElement"]],
                                 [build.SITE + url for url in expected])
                self.assertEqual([i["position"] for i in listing["itemListElement"]],
                                 list(range(1, len(expected) + 1)))
                self.assertEqual(page.nested_anchors, 0)
                kinds = {item["kind"] for item in group["items"]}
                if "news" in kinds:
                    mixed_kinds.update(kinds)
        self.assertEqual(mixed_kinds, {"news", "fiction", "analysis"},
                         "Topic navigation should actually connect all three editorial forms")

    def test_tag_page_metadata_is_specific_and_never_all_fiction(self):
        titles = set()
        for group in self.groups:
            page = Page(build.tag_page(group))
            path = tag_url(group["tag"])
            with self.subTest(path=path):
                self.assertEqual(page.canonicals, [build.SITE + path])
                self.assertEqual(len(page.titles), 1)
                self.assertIn(group["tag"]["label"], page.titles[0])
                self.assertNotIn(page.titles[0], titles)
                titles.add(page.titles[0])
                self.assertNotIn("（創作ニュース）", page.titles[0])
                primary = next(s for s in page.schemas if s.get("url") == build.SITE + path)
                self.assertEqual(primary["@type"], "CollectionPage")
                self.assertNotEqual(primary.get("genre"), "風刺・フィクション")
                crumbs = next(s for s in page.schemas if s.get("@type") == "BreadcrumbList")
                self.assertEqual([i["item"] for i in crumbs["itemListElement"]],
                                 [build.SITE + "/", build.SITE + "/tags/", build.SITE + path])

    def test_news_and_serial_reading_surfaces_link_back_to_their_tags(self):
        for article in self.articles:
            with self.subTest(article=article["slug"]):
                expected = tags_for_article(article)
                self.assertTrue(expected)
                self.assert_tag_links(build.card(article, archive=True), expected)
                page = self.assert_tag_links(
                    build.story(article, self.articles, serials=self.serials), expected)
                schema = next(s for s in page.schemas if s.get("@type") == "BlogPosting")
                self.assertEqual(schema["keywords"], [tag["label"] for tag in expected])
        for series in self.serials:
            self.assert_tag_links(render_series_page(series, build.layout), tags_for_series(series))
            for episode in series["episodes"]:
                with self.subTest(series=series["slug"], episode=episode["slug"]):
                    expected = tags_for_episode(series, episode)
                    self.assertTrue(expected)
                    page = self.assert_tag_links(render_episode_page(series, episode, build.layout), expected)
                    schema = next(s for s in page.schemas if s.get("@type") == "Article")
                    self.assertEqual(schema["keywords"], [tag["label"] for tag in expected])

    def test_thin_topics_remain_browsable_but_are_not_submitted_for_indexing(self):
        """A one-story theme must work for readers without becoming a thin SEO page."""
        group = self.groups[0]
        thin = {**group, "items": group["items"][:1], "count": 1}
        thin_page = Page(build.tag_page(thin))
        self.assertIn("noindex", thin_page.meta.get("robots", ""))
        self.assertIn("follow", thin_page.meta["robots"])
        self.assertIn(item_url(thin["items"][0]), thin_page.links)
        with tempfile.TemporaryDirectory() as temporary, patch.object(build, "OUT", Path(temporary)):
            build.other_files(self.articles, serials=self.serials)
            ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            sitemap = ET.parse(Path(temporary) / "sitemap.xml").getroot()
            routes = {entry.findtext("s:loc", namespaces=ns) for entry in sitemap.findall("s:url", ns)}
        self.assertIn(build.SITE + "/tags/", routes)
        for group in self.groups:
            path = tag_url(group["tag"])
            with self.subTest(path=path):
                page = Page(build.tag_page(group))
                self.assertEqual(build.SITE + path in routes, group["count"] >= 3)
                self.assertEqual("noindex" in page.meta.get("robots", ""), group["count"] < 3)


if __name__ == "__main__":
    unittest.main()
