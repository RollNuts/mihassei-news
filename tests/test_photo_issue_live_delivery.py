"""Offline production checks for the latest feature's ten reviewed photographs."""
import copy
import io
import re
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import build, check_live
from scripts.media_policy import jpeg_dimensions
from scripts.photo_issue_pages import render_photo_issue
from scripts.photo_issues import (
    cover_shot, issue_url, load_photo_issues, photo_image_url, published_issues,
)

ROOT = Path(__file__).resolve().parents[1]


class LatestPhotoIssueLiveDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_photo_issues(ROOT)
        cls.issues = published_issues(cls.catalog, root=ROOT)
        cls.latest = cls.issues[0]
        cls.url = issue_url(cls.latest)
        cls.images = {cut: (ROOT / image["path"]).read_bytes()
                      for cut, image in cls.latest["images"].items()}

    def fixture(self, issue=None):
        issue = issue or self.latest
        page = render_photo_issue(issue, issue["model_snapshot"], build.layout)
        files = {self.url: (page.encode("utf-8"), "text/html; charset=utf-8")}
        files.update({photo_image_url(issue, cut): (data, "image/jpeg")
                      for cut, data in self.images.items()})
        return files

    def replace(self, files, before, after):
        page, mime = files[self.url]
        before, after = before.encode("utf-8"), after.encode("utf-8")
        self.assertEqual(page.count(before), 1)
        files[self.url] = (page.replace(before, after, 1), mime)

    def figures(self, files):
        return re.findall(r'<figure class="photo-issue-shot\b.*?</figure>',
                          files[self.url][0].decode("utf-8"), re.S)

    def verify(self, files, issues=None):
        return check_live.verify_latest_photo_issue_delivery(
            self.issues if issues is None else issues, fetch=files.__getitem__)

    def test_latest_real_feature_fetches_html_and_all_ten_reviewed_jpegs_only(self):
        files = self.fixture()
        reads = []

        def fetch(path):
            reads.append(path)
            return files[path]

        self.assertEqual(check_live.verify_latest_photo_issue_delivery(self.issues, fetch=fetch), 10)
        first = cover_shot(self.latest)
        ordered = [first] + [shot for shot in self.latest["shots"] if shot != first]
        self.assertEqual(reads, [self.url] + [photo_image_url(self.latest, shot["id"])
                                            for shot in ordered])
        self.assertEqual(set(reads), set(files))

    def test_non_first_cover_is_first_without_changing_the_remaining_cut_order(self):
        issue = copy.deepcopy(self.latest)
        issue["cover_cut_id"] = issue["shots"][4]["id"]
        self.assertEqual(self.verify(self.fixture(issue), [issue]), 10)

    def test_empty_published_catalog_skips_without_network_reads(self):
        fetch = Mock(side_effect=AssertionError("unexpected network read"))
        self.assertEqual(check_live.verify_latest_photo_issue_delivery([], fetch=fetch), 0)
        fetch.assert_not_called()

    def test_unavailable_page_and_non_html_page_fail_closed(self):
        for error in (urllib.error.URLError("offline"), TimeoutError("timeout")):
            with self.subTest(error=type(error).__name__):
                with self.assertRaisesRegex(RuntimeError, "photo feature is unreachable"):
                    check_live.verify_latest_photo_issue_delivery(
                        self.issues, fetch=Mock(side_effect=error))
        files = self.fixture()
        files[self.url] = (files[self.url][0], "application/json")
        with self.assertRaisesRegex(RuntimeError, "not served as HTML"):
            self.verify(files)

    def test_non_cover_404_mime_truncation_and_same_length_corruption_fail(self):
        cut = next(shot["id"] for shot in reversed(self.latest["shots"])
                   if shot["id"] != cover_shot(self.latest)["id"])
        url = photo_image_url(self.latest, cut)
        data = self.images[cut]
        cases = [
            (urllib.error.HTTPError(url, 404, "missing", None, None), "unreachable"),
            ((data, "text/html"), "Content-Type mismatch"),
            ((data[:-1], "image/jpeg"), "JPEG bytes differ"),
            ((data[:-1] + bytes([data[-1] ^ 1]), "image/jpeg"), "JPEG bytes differ"),
        ]
        for response, message in cases:
            with self.subTest(message=message, response_type=type(response).__name__):
                files = self.fixture()

                def fetch(path):
                    if path == url:
                        if isinstance(response, Exception):
                            raise response
                        return response
                    return files[path]

                with self.assertRaisesRegex(RuntimeError, "cut " + cut + ".*" + message):
                    check_live.verify_latest_photo_issue_delivery(self.issues, fetch=fetch)

    def test_missing_duplicate_or_reordered_non_cover_figures_fail(self):
        files = self.fixture()
        figures = self.figures(files)
        mutations = [
            (figures[-1], ""),
            (figures[-1], figures[-1] + figures[-1]),
            (figures[1] + figures[2], figures[2] + figures[1]),
        ]
        for before, after in mutations:
            with self.subTest(length=len(after)):
                files = self.fixture()
                self.replace(files, before, after)
                with self.assertRaisesRegex(RuntimeError, "ten cuts in cover-first order"):
                    self.verify(files)

    def test_stale_image_url_srcset_alt_caption_and_display_dimensions_fail(self):
        files = self.fixture()
        figure = self.figures(files)[-1]
        mutations = [
            (re.sub(r'\.jpg\?v=[0-9a-f]+', '.jpg?v=stale', figure), "photo src differs"),
            (figure.replace('<img ', '<img srcset="/stale.jpg 2x" ', 1), "image selection mismatch"),
            (figure.replace('alt="', 'alt="stale ', 1), "photo alt differs"),
            (figure.replace('<span>', '<span>stale ', 1), "caption or image selection mismatch"),
            (figure.replace('width="', 'width="9', 1), "photo width differs"),
            (figure.replace('<img ', '<picture><img ', 1).replace('><figcaption>', '></picture><figcaption>'),
             "image or caption missing"),
        ]
        for replacement, message in mutations:
            with self.subTest(message=message):
                files = self.fixture()
                self.replace(files, figure, replacement)
                with self.assertRaisesRegex(RuntimeError, message):
                    self.verify(files)

    def test_actual_jpeg_dimensions_must_match_supplied_image_record(self):
        issue = copy.deepcopy(self.latest)
        cut = issue["shots"][0]["id"]
        width, height = jpeg_dimensions(self.images[cut])
        issue["images"][cut].update({
            "provenance": {"kind": "user_supplied_photo"},
            "width": width + 1, "height": height,
        })
        with self.assertRaisesRegex(RuntimeError, "JPEG dimensions mismatch"):
            self.verify(self.fixture(issue), [issue])

    def test_missing_changed_reordered_or_relocated_column_fails(self):
        issue = copy.deepcopy(self.latest)
        issue["column"] = ["朝の空を見上げた。", "午後は海辺を歩いた。"]
        files = self.fixture(issue)
        column = re.search(r'<section class="photo-issue-column".*?</section>',
                           files[self.url][0].decode("utf-8"), re.S).group(0)
        paragraphs = re.findall(r'<p>.*?</p>', column, re.S)
        replacements = [
            column.replace(paragraphs[0], ""),
            column.replace(paragraphs[0], '<p>古いコラム</p>') + paragraphs[0],
            column.replace("".join(paragraphs), "".join(reversed(paragraphs))),
            column + column,
        ]
        for replacement in replacements:
            with self.subTest(length=len(replacement)):
                files = self.fixture(issue)
                self.replace(files, column, replacement)
                with self.assertRaisesRegex(RuntimeError, "column (paragraphs mismatch|missing or duplicated)"):
                    self.verify(files, [issue])
        files = self.fixture(issue)
        first = self.figures(files)[0]
        self.replace(files, column, "")
        self.replace(files, first, column + first)
        with self.assertRaisesRegex(RuntimeError, "column reading position mismatch"):
            self.verify(files, [issue])

    def test_escaped_reader_text_is_decoded_once_but_body_identity_must_match(self):
        issue = copy.deepcopy(self.latest)
        text = 'A&Bは「<見通し>」と"条件"、\'今後\'を確認。'
        issue["title"] = issue["summary"] = issue["column"][0] = text
        issue["shots"][-1]["alt"] = issue["shots"][-1]["caption"] = text
        files = self.fixture(issue)
        self.assertEqual(self.verify(files, [issue]), 10)
        self.replace(files, 'data-engagement-key="photo-issue:' + issue["slug"] + '"',
                     'data-engagement-key="photo-issue:stale-feature"')
        with self.assertRaisesRegex(RuntimeError, "body missing, duplicated or stale"):
            self.verify(files, [issue])

    def test_existing_check_entrypoint_verifies_the_feature_before_other_pages(self):
        files = self.fixture()
        reads = []

        def fetch(path):
            reads.append(path)
            if path == "/":
                raise RuntimeError("next homepage check")
            return files[path]

        output = io.StringIO()
        with (patch.object(check_live, "verify_public_revision"),
              patch.object(check_live, "load_photo_issues", return_value=self.catalog),
              patch.object(check_live, "published_issues", return_value=self.issues),
              patch.object(check_live, "get", side_effect=fetch), redirect_stdout(output)):
            with self.assertRaisesRegex(RuntimeError, "next homepage check"):
                check_live.check()
        self.assertEqual(set(reads[:-1]), set(files))
        self.assertEqual(len(reads), 12)
        self.assertIn("Public latest photo feature, JPEGs and column OK: " + self.latest["slug"] + " 10",
                      output.getvalue())

    def test_catalog_read_and_validation_failures_are_not_zero_success(self):
        for name in ("load_photo_issues", "published_issues"):
            with self.subTest(name=name):
                with (patch.object(check_live, "verify_public_revision"),
                      patch.object(check_live, name, side_effect=ValueError("invalid catalog")),
                      patch.object(check_live, "get") as fetch):
                    with self.assertRaisesRegex(ValueError, "invalid catalog"):
                        check_live.check()
                    fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
