"""Production checks bind each cast post to its actual person, time and photo."""
import copy
import html
import json
import re
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock

from scripts.check_live import verify_cast_story_delivery
from scripts.photo_issues import load_photo_issues, photo_image_url, published_issues
from scripts.photo_stories import (
    latest_model_stories, load_photo_stories, render_photo_timeline,
)

ROOT = Path(__file__).resolve().parents[1]


class CastStoryLiveDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        catalog = load_photo_issues(ROOT)
        cls.issues = published_issues(catalog, root=ROOT)
        cls.stories = load_photo_stories(ROOT, catalog=catalog, published=cls.issues)
        cls.posts = latest_model_stories(cls.stories)[:7]
        cls.images = {photo_image_url(issue, cut): ((ROOT / image["path"]).read_bytes(), "image/jpeg")
                      for issue in cls.issues for cut, image in issue["images"].items()}

    def fixture(self, stories=None):
        return render_photo_timeline(self.stories if stories is None else stories)

    def verify(self, markup, *, stories=None, fetch=None, issues=None, root=ROOT):
        return verify_cast_story_delivery(
            markup, self.stories if stories is None else stories,
            self.issues if issues is None else issues,
            fetch=fetch or self.images.__getitem__, root=root,
        )

    def replace_card(self, markup, before, after, *, post=None):
        post = post or self.posts[0]
        pattern = r'<article class="photo-post" id="post-' + re.escape(post["id"]) + r'">.*?</article>'
        card = re.search(pattern, markup, re.S).group(0)
        self.assertIn(before, card)
        return markup.replace(card, card.replace(before, after, 1), 1)

    def replace_payload(self, markup, transform):
        pattern = r'(<script type="application/json" data-photo-story-data>)(.*?)(</script>)'
        match = re.search(pattern, markup, re.S)
        payload = json.loads(match.group(2))
        transform(payload)
        return markup[:match.start(2)] + json.dumps(payload, ensure_ascii=False) + markup[match.end(2):]

    def test_latest_post_per_person_and_source_bytes_are_verified_with_bounded_gets(self):
        reads = []

        def fetch(path):
            reads.append(path)
            return self.images[path]

        self.assertEqual(self.verify(self.fixture(), fetch=fetch), 7)
        self.assertEqual(reads, list(dict.fromkeys(post["image"] for post in self.posts)))
        self.assertEqual(len({post["modelId"] for post in self.posts}), 7)
        self.assertTrue(all(re.fullmatch(r"/photo/weekly/[a-z0-9-]+/\d{2}\.jpg\?v=[0-9a-f]{12}", path)
                            for path in reads))

    def test_ampersands_quotes_and_literal_entities_are_decoded_once(self):
        stories = copy.deepcopy(list(self.stories))
        stories[0]["text"] = 'カフェで「Tea & Cake」。"一杯だけ"のつもりが、看板の &amp; まで気になった。'
        self.assertEqual(self.verify(self.fixture(stories), stories=stories), 7)

    def test_correct_text_in_modal_or_noscript_cannot_hide_a_stale_visible_card(self):
        markup = self.fixture()
        post = self.posts[0]
        changed = self.replace_card(markup, html.escape(post["text"]), "昨日のままの本文")
        self.assertIn(html.escape(post["text"]), changed)
        with self.assertRaisesRegex(RuntimeError, "visible text mismatch"):
            self.verify(changed)

    def test_existing_other_persons_profile_cannot_validate_the_wrong_author(self):
        markup = self.replace_card(self.fixture(),
                                   'href="' + self.posts[0]["profileUrl"] + '"',
                                   'href="' + self.posts[1]["profileUrl"] + '"')
        with self.assertRaisesRegex(RuntimeError, "person or profile mismatch"):
            self.verify(markup)

    def test_post_time_permalink_and_fictional_source_are_bound_to_that_card(self):
        post = self.posts[0]
        cases = (
            ('datetime="' + post["publishedAt"] + '"', 'datetime="2026-01-01T00:00:00+09:00"',
             "publication time mismatch"),
            ('href="' + post["profileUrl"] + '#post-' + post["id"] + '"',
             'href="' + post["profileUrl"] + '#post-older"', "dated permalink mismatch"),
            (html.escape(post["settingLabel"]), "舞台：2026年11月", "fictional setting mismatch"),
            ('<footer><span>' + html.escape(post["settingLabel"]) + '</span><a href="'
             + html.escape(post["sourceUrl"], quote=True) + '">',
             '<footer><span>' + html.escape(post["settingLabel"]) + '</span><a href="/photo/other/">',
             "photo source"),
        )
        for before, after, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(RuntimeError, error):
                self.verify(self.replace_card(self.fixture(), before, after))

    def test_wrong_image_in_an_otherwise_correct_post_fails(self):
        post = self.posts[0]
        before = '<a class="photo-post-image" href="' + html.escape(post["sourceUrl"], quote=True) + '\"><img src="'
        old = before + html.escape(post["image"], quote=True) + '"'
        new = before + html.escape(self.posts[1]["image"], quote=True) + '"'
        with self.assertRaisesRegex(RuntimeError, "photograph differs from the reviewed cut"):
            self.verify(self.replace_card(self.fixture(), old, new))

    def test_missing_duplicate_and_reordered_cards_fail_even_with_complete_modal_data(self):
        markup = self.fixture()
        cards = re.findall(r'<article class="photo-post".*?</article>', markup, re.S)
        cases = (
            markup.replace(cards[0], "", 1),
            markup.replace(cards[1], cards[0], 1),
            markup.replace(cards[0], "CARD_PLACEHOLDER", 1).replace(cards[1], cards[0], 1)
            .replace("CARD_PLACEHOLDER", cards[1], 1),
        )
        for candidate in cases:
            with self.subTest(candidate=candidate[:40]), self.assertRaisesRegex(RuntimeError, "latest post"):
                self.verify(candidate)

    def test_stale_modal_identity_text_photo_or_source_is_rejected(self):
        for field, value in (("modelId", self.posts[1]["modelId"]), ("text", "以前の投稿"),
                             ("image", self.posts[1]["image"]), ("sourceUrl", "/photo/old/#photo-01")):
            markup = self.replace_payload(self.fixture(), lambda rows: rows[0].update({field: value}))
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, "modal data differs"):
                self.verify(markup)

    def test_corrupt_or_duplicate_modal_payload_fails(self):
        markup = self.fixture()
        script = re.search(r'<script type="application/json" data-photo-story-data>.*?</script>', markup, re.S).group(0)
        candidates = (
            markup.replace(script, '<script type="application/json" data-photo-story-data>not json</script>'),
            markup.replace(script, script + script),
        )
        for candidate in candidates:
            with self.assertRaisesRegex(RuntimeError, "modal data"):
                self.verify(candidate)

    def test_correct_cards_cannot_hide_a_circle_linked_to_another_person(self):
        post = self.posts[0]
        before = '<a class="photo-story-open" href="' + html.escape(post["sourceUrl"], quote=True) + '"'
        after = '<a class="photo-story-open" href="' + html.escape(self.posts[1]["sourceUrl"], quote=True) + '"'
        markup = self.fixture().replace(before, after, 1)
        with self.assertRaisesRegex(RuntimeError, "latest source mismatch"):
            self.verify(markup)

    def test_live_jpeg_error_wrong_mime_truncation_or_same_size_corruption_fails(self):
        url = self.posts[0]["image"]
        data, _ = self.images[url]
        variants = (
            (urllib.error.HTTPError(url, 404, "missing", None, None), "unreachable"),
            ((data, "text/html"), "Content-Type mismatch"),
            ((data[:-1], "image/jpeg"), "reviewed JPEG"),
            ((data[:-1] + bytes([data[-1] ^ 1]), "image/jpeg"), "reviewed JPEG"),
        )
        for response, error in variants:
            def fetch(path):
                if path == url:
                    if isinstance(response, Exception):
                        raise response
                    return response
                return self.images[path]

            with self.subTest(error=error), self.assertRaisesRegex(RuntimeError, error):
                self.verify(self.fixture(), fetch=fetch)

    def test_photo_requires_current_published_source_metadata(self):
        with self.assertRaisesRegex(RuntimeError, "no validated published source"):
            self.verify(self.fixture(), issues=[])

    def test_empty_stories_do_not_fetch_and_cannot_leave_old_cards(self):
        fetch = Mock(side_effect=AssertionError("unexpected network read"))
        self.assertEqual(self.verify("<main></main>", stories=[], issues=[], fetch=fetch), 0)
        fetch.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "stale cast timeline"):
            self.verify(self.fixture(), stories=[], issues=[], fetch=fetch)

    def test_photo_motion_keeps_exact_served_bytes_and_source_binding_checks(self):
        stories = copy.deepcopy(list(self.stories))
        post = stories[0]
        post["motion"] = {"url": "/assets/photo-story-clips/verified.mp4?v=123456789abc",
                          "width": post["width"], "height": post["height"]}
        clip = b"validated-mp4-fixture"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "assets/photo-story-clips" / (post["id"] + ".mp4")
            path.parent.mkdir(parents=True)
            path.write_bytes(clip)
            files = dict(self.images)
            files[post["motion"]["url"]] = (clip, "video/mp4")
            self.assertEqual(self.verify(self.fixture(stories), stories=stories,
                                         fetch=files.__getitem__, root=root), 7)
            files[post["motion"]["url"]] = (b"stale-video", "video/mp4")
            with self.assertRaisesRegex(RuntimeError, "verified clip"):
                self.verify(self.fixture(stories), stories=stories,
                            fetch=files.__getitem__, root=root)


if __name__ == "__main__":
    unittest.main()
