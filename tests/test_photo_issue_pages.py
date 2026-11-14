"""Small HTML contract tests for weekly-photo reader pages; no build or network."""
from __future__ import annotations

import copy
import unittest
from html.parser import HTMLParser

from scripts import photo_issue_pages as pages


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []
        self.links = []
        self.times = []
        self.figures = []
        self.paragraphs = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "img":
            self.images.append(attrs)
        elif tag == "a":
            self.links.append(attrs)
        elif tag == "time":
            self.times.append(attrs)
        elif tag == "figure":
            self.figures.append(attrs)
        elif tag == "p":
            self.paragraphs.append(attrs)


def issue(number=1, model_id="nagino-saya"):
    return {
        "slug": f"issue-{number:03d}", "number": number,
        "title": "小波の届く午後", "summary": "足元に届いた小さな波を、少し離れて眺めました。",
        "model_id": model_id, "setting": {"year": 2031, "month": 11, "location": "夕波ドーム"},
        "column": [f"私の未来コラムの第{n}段落です。" for n in range(1, 5)],
        "shots": [{"id": f"{n:02d}", "description": "INTERNAL SCENE PROMPT",
                   "caption": f"小波を眺める、午後の一枚。{n}",
                   "alt": f"浅い水辺で小さな波を見る凪野さや。写真{n}"}
                  for n in range(1, 11)],
        "images": {f"{n:02d}": {"path": "INTERNAL ASSET PATH", "sha256": f"{n:064x}"}
                   for n in range(1, 11)},
        "state": "published", "published_at": f"2026-11-{13 + number:02d}T13:30:00+09:00",
        "claim": "INTERNAL CLAIM", "review": "INTERNAL REVIEW",
    }


MODEL = {"id": "nagino-saya", "name": "凪野さや", "age": 27, "master": "INTERNAL MASTER"}


class PhotoIssuePageTests(unittest.TestCase):
    pages = pages

    def setUp(self):
        self.call = None

    def layout(self, title, description, path, main, **kwargs):
        self.call = (title, description, path, kwargs)
        return main

    def parse(self, markup):
        parser = PageParser()
        parser.feed(markup)
        return parser

    def test_issue_has_ten_complete_photos_four_column_paragraphs_and_one_credit(self):
        value = issue()
        markup = self.pages.render_photo_issue(value, MODEL, self.layout)
        parsed = self.parse(markup)
        self.assertEqual(len(parsed.images), 10)
        self.assertEqual([figure["id"] for figure in parsed.figures], [f"photo-{n:02d}" for n in range(1, 11)])
        for number, image in enumerate(parsed.images, 1):
            version = value["images"][f"{number:02d}"]["sha256"][:12]
            self.assertEqual(image["src"], f'/photo/weekly/issue-001/{number:02d}.jpg?v={version}')
            self.assertEqual((image["width"], image["height"]), ("1200", "675"))
            self.assertEqual((image["loading"], image["decoding"]), ("eager" if number == 1 else "lazy", "async"))
            self.assertEqual(image["alt"], value["shots"][number - 1]["alt"])
        for paragraph in value["column"]:
            self.assertIn(f"<p>{paragraph}</p>", markup)
        self.assertIn("凪野さや（27歳）", markup)
        self.assertIn("舞台：2031年11月、夕波ドーム", markup)
        self.assertIn("公開日 <time", markup)
        self.assertIn("2026年11月14日", markup)
        self.assertEqual(markup.count(self.pages.CREDIT), 1)
        self.assertNotIn("INTERNAL", markup)
        self.assertEqual(self.call[:3], (value["title"], value["summary"], "/photo/weekly/issue-001/"))
        self.assertEqual(self.call[3], {"active": "photos", "kind": "photo_issue", "photo_issue": value,
                                        "feed_url": "/feeds/weekly-photo.xml"})

    def test_index_preserves_the_issue_model_snapshot(self):
        value = issue()
        value["model_snapshot"] = {**MODEL, "name": "公開時の名前", "age": 27}
        current_model = {**MODEL, "name": "現在の名前", "age": 28}
        markup = self.pages.render_photo_index([value], [current_model], self.layout)
        self.assertIn("公開時の名前（27歳）", markup)
        self.assertNotIn("現在の名前", markup)

    def test_selected_cover_leads_the_issue_and_index_without_duplicating_a_photo(self):
        value = issue()
        value["cover_cut_id"] = "04"
        page = self.parse(self.pages.render_photo_issue(value, MODEL, self.layout))
        index = self.parse(self.pages.render_photo_index([value], [MODEL], self.layout))
        self.assertEqual(len(page.images), 10)
        self.assertEqual(page.images[0]["src"], index.images[0]["src"])
        self.assertEqual(page.images[0]["alt"], value["shots"][3]["alt"])
        self.assertEqual(page.figures[0]["id"], "photo-04")
        self.assertEqual(len({figure["id"] for figure in page.figures}), 10)

    def test_index_shows_latest_first_with_model_names_and_issue_links(self):
        other = {"id": "other-model", "name": "別のモデル", "age": 28}
        first, second = issue(), issue(2, other["id"])
        markup = self.pages.render_photo_index([first, second], [MODEL, other], self.layout)
        self.assertEqual(markup, self.pages.render_photo_index([second, first], [MODEL, other], self.layout))
        parsed = self.parse(markup)
        self.assertLess(markup.index("第2号"), markup.index("第1号"))
        self.assertIn("凪野さや（27歳）", markup)
        self.assertIn("別のモデル（28歳）", markup)
        self.assertEqual(len(parsed.images), 2)
        self.assertEqual([image["src"] for image in parsed.images], [
            "/photo/weekly/issue-002/01.jpg?v=000000000000", "/photo/weekly/issue-001/01.jpg?v=000000000000"])
        self.assertIn({"class": "photo-issue-card-link", "href": "/photo/weekly/issue-002/"}, parsed.links)
        self.assertEqual(self.call[2:], ("/photo/weekly/", {"active": "photos", "kind": "photo_collection",
                                                           "feed_url": "/feeds/weekly-photo.xml"}))
        self.assertIn("/feeds/weekly-photo.xml", [link["href"] for link in self.parse(markup).links])
        self.assertNotIn("INTERNAL", markup)

    def test_no_published_issues_has_no_future_or_placeholder_issue_link(self):
        markup = self.pages.render_photo_index([], [MODEL], self.layout)
        parsed = self.parse(markup)
        self.assertEqual(parsed.images, [])
        self.assertIn("公開号はまだありません。", markup)
        self.assertNotIn("準備中", markup)
        self.assertNotIn("予約", markup)
        self.assertEqual([link["href"] for link in parsed.links], ["/"])

    def test_internal_character_and_real_person_reference_names_are_not_rendered(self):
        value = issue()
        model = {**MODEL, "character": {"first_person": "INTERNAL FIRST PERSON", "voice": "INTERNAL VOICE"},
                 "internal_references": ["藤嶌果歩", "齋藤飛鳥", "INTERNAL REFERENCE"]}
        for markup in (self.pages.render_photo_issue(value, model, self.layout),
                       self.pages.render_photo_index([value], [model], self.layout)):
            for internal in ("藤嶌果歩", "齋藤飛鳥", "INTERNAL FIRST PERSON", "INTERNAL VOICE", "INTERNAL REFERENCE"):
                self.assertNotIn(internal, markup)

    def test_reader_content_is_escaped_and_issue_data_is_not_changed(self):
        value = issue()
        value["title"] = '<script>alert("title")</script>'
        value["summary"] = "summary & <strong>escape</strong>"
        value["setting"]["location"] = '<img src=x onerror="alert(1)">'
        value["shots"][0]["caption"] = "caption <script>bad</script>"
        value["shots"][0]["alt"] = 'scene " onload="bad'
        value["column"][0] = "column <script>bad</script>"
        model = {**MODEL, "name": "name <script>bad</script>"}
        before = copy.deepcopy(value)
        markup = self.pages.render_photo_issue(value, model, self.layout)
        self.assertEqual(value, before)
        self.assertNotIn("<script>", markup)
        self.assertNotIn('<img src=x', markup)
        self.assertEqual(len(self.parse(markup).images), 10)
        self.assertIn("&lt;script&gt;", markup)
        self.assertIn("summary &amp; &lt;strong&gt;escape&lt;/strong&gt;", markup)
        self.assertEqual(self.parse(markup).images[0]["alt"], value["shots"][0]["alt"])

    def test_publication_day_uses_jst_and_does_not_label_the_setting_as_publication(self):
        value = issue()
        value["published_at"] = "2026-11-14T23:30:00Z"
        markup = self.pages.render_photo_issue(value, MODEL, self.layout)
        self.assertIn("2026年11月15日", markup)
        self.assertEqual(self.parse(markup).times, [{"datetime": "2026-11-14T23:30:00Z"}])
        value["published_at"] = None
        with self.assertRaises(ValueError):
            self.pages.render_photo_issue(value, MODEL, self.layout)


if __name__ == "__main__":
    unittest.main()
