"""Accepted profile portraits retain original proofs and publish JPEGs only."""
from __future__ import annotations

import copy
import hashlib
import io
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from scripts import build, photo_issue_pages as pages, photo_profile_portraits as portraits
from tests.test_photo_issue_pages import PageParser, issue

NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def image_bytes(format_name, dimensions):
    image = Image.new("RGB", dimensions, (83, 132, 165))
    stream = io.BytesIO()
    image.save(stream, format=format_name)
    return stream.getvalue()


class ProfilePortraitTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.png = image_bytes("PNG", (640, 960))
        self.jpeg = image_bytes("JPEG", (320, 480))
        self.model = {"id": "shioba-rio", "name": "汐羽りお", "age": 29, "fictional": True,
                      "master": {"path": "PRIVATE MASTER"}, "reference_state": "PRIVATE PREVIEW",
                      "character": {"occupation": "報道アナウンサー", "personality": "落ち着いた観察役。",
                                    "likes": ["朝の散歩"], "weakness": "休日だけ予定がずれる。"}}
        self.model["profile_portrait"] = {
            "path": "", "sha256": "", "bytes": 0, "width": 320, "height": 480,
            "source_png": {"path": "", "sha256": "", "bytes": 0},
            "source": {"kind": "user-supplied-chatgpt-photo",
                       "chat_url": "https://chatgpt.com/c/10000000-0000-4000-8000-000000000001",
                       "turn_id": "20000000-0000-4000-8000-000000000002", "gallery_index": 1},
            "review": {"decision": "accepted", "reviewed_at": "2026-11-14T12:00:00+09:00",
                       "note": "PRIVATE SOURCE REVIEW: checked the actual image and fictional identity."},
        }
        self.save()

    @property
    def record(self):
        return self.model["profile_portrait"]

    def save(self):
        self.record.update(path=f'assets/profile-portraits/{self.model["id"]}-{digest(self.jpeg)[:12]}.jpg',
                           sha256=digest(self.jpeg), bytes=len(self.jpeg))
        self.record["source_png"].update(
            path=f'maintenance/photo-profile-sources/{self.model["id"]}/source-{digest(self.png)}.png',
            sha256=digest(self.png), bytes=len(self.png))
        for data, relative in ((self.jpeg, self.record["path"]), (self.png, self.record["source_png"]["path"])):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def validate(self):
        return portraits.validate_profile_portrait(self.model, root=self.root, now=NOW)

    def test_real_formats_and_bindings_yield_only_reader_projection(self):
        before = copy.deepcopy(self.model)
        result = self.validate()
        self.assertEqual((result["width"], result["height"]), (320, 480))
        self.assertEqual(result["url"], "/" + self.record["path"] + "?v=" + digest(self.jpeg)[:12])
        for field in ("source", "source_png", "review", "chat_url", "turn_id", "note"):
            self.assertNotIn(field, result)
        self.assertEqual(self.model, before)
        missing = copy.deepcopy(self.model)
        missing.pop("profile_portrait")
        self.assertIsNone(portraits.validate_profile_portrait(missing, root=self.root, now=NOW))

    def test_build_copies_only_accepted_jpeg_and_enables_profile_og_sitemap(self):
        written, output = {}, self.root / "dist"
        pages.build_photo_models([self.model], [], build.layout, written.__setitem__, root=self.root, output=output)
        self.assertEqual([str(path.relative_to(output)) for path in output.rglob("*") if path.is_file()],
                         [self.record["path"]])
        self.assertEqual((output / self.record["path"]).read_bytes(), self.jpeg)
        profile = written[pages.model_url(self.model).lstrip("/") + "index.html"]
        url = "/" + self.record["path"] + "?v=" + digest(self.jpeg)[:12]
        parser = PageParser()
        parser.feed(profile)
        self.assertEqual(len(parser.images), 1)
        self.assertEqual(parser.images[0]["src"], url)
        self.assertIn('property="og:image" content="https://veripsa.com' + url + '"', profile)
        self.assertIn('name="twitter:image" content="https://veripsa.com' + url + '"', profile)
        self.assertIn('property="og:image:width" content="320"', profile)
        self.assertIn('property="og:image:height" content="480"', profile)
        self.assertNotIn('content="noindex,follow"', profile)
        self.assertIn('<strong>0</strong><span>掲載カット</span>', profile)
        self.assertIn('<strong>0</strong><span>掲載号</span>', profile)
        self.assertIn("掲載号はまだありません。", profile)
        self.assertNotIn('class="photo-model-grid-link"', profile)
        for private in ("PRIVATE", "maintenance/", "chatgpt.com", self.record["source"]["turn_id"]):
            self.assertNotIn(private, profile)
        expected = [(path, self.record["review"]["reviewed_at"], url)
                    for path in (pages.MODEL_INDEX_URL, pages.model_url(self.model))]
        self.assertEqual(pages.model_sitemap_entries([self.model], [], root=self.root), expected)
        directory = written["photo/models/index.html"]
        self.assertIn('src="' + url + '"', directory)
        self.assertIn('property="og:image" content="https://veripsa.com' + url, directory)

    def test_published_cover_keeps_priority_and_portrait_does_not_add_to_grid_or_counts(self):
        released = issue(model_id=self.model["id"])
        output, written = self.root / "dist", {}
        pages.build_photo_models([self.model], [released], build.layout, written.__setitem__,
                                 root=self.root, output=output)
        profile = written[pages.model_url(self.model).lstrip("/") + "index.html"]
        self.assertNotIn("/assets/profile-portraits/", profile)
        self.assertIn('src="/photo/weekly/issue-001/01.jpg?v=', profile)
        self.assertIn('<strong>10</strong><span>掲載カット</span>', profile)
        self.assertIn('<strong>1</strong><span>掲載号</span>', profile)
        self.assertEqual(profile.count('class="photo-model-grid-link"'), 10)
        self.assertFalse(output.exists())

    def test_changed_jpeg_or_original_bytes_cannot_reuse_a_review(self):
        for relative in (self.record["path"], self.record["source_png"]["path"]):
            with self.subTest(path=relative):
                target = self.root / relative
                original = target.read_bytes()
                target.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
                with self.assertRaisesRegex(ValueError, "SHA"):
                    self.validate()
                target.write_bytes(original)

    def test_corrupt_png_crc_is_rejected_even_after_rebinding_hash_and_path(self):
        changed = bytearray(self.png)
        changed[-1] ^= 1
        self.png = bytes(changed)
        self.save()
        with self.assertRaisesRegex(ValueError, "PNG"):
            self.validate()

    def test_jpeg_scan_must_decode_even_if_file_hash_dimensions_and_end_match(self):
        self.jpeg = self.jpeg[:self.jpeg.index(b"\xff\xda") + 4] + b"\xff\xd9"
        self.save()
        with self.assertRaises(ValueError):
            self.validate()

    def test_format_dimensions_and_full_frame_aspect_must_match(self):
        for data, width, height in ((self.png, 320, 480),
                                    (image_bytes("JPEG", (400, 400)), 400, 400),
                                    (image_bytes("JPEG", (640, 960)), 320, 480),
                                    (image_bytes("JPEG", (800, 1200)), 800, 1200)):
            with self.subTest(dimensions=(width, height)):
                self.jpeg = data
                self.record.update(width=width, height=height)
                self.save()
                with self.assertRaises(ValueError):
                    self.validate()

    def test_wrong_source_extension_cannot_turn_a_jpeg_into_original_png(self):
        self.png = image_bytes("JPEG", (640, 960))
        self.save()
        with self.assertRaisesRegex(ValueError, "PNG"):
            self.validate()

    def test_record_paths_identity_metadata_and_source_are_strict(self):
        changes = [
            lambda record: record.update(path="assets/profile-portraits/other-model-" + digest(self.jpeg)[:12] + ".jpg"),
            lambda record: record.update(path="../outside.jpg"),
            lambda record: record.update(bytes=True),
            lambda record: record.update(width=True),
            lambda record: record.update(extra="PRIVATE"),
            lambda record: record["source_png"].update(path="maintenance/other/source.png"),
            lambda record: record["source"].update(chat_url="https://example.com/c/10000000-0000-4000-8000-000000000001"),
            lambda record: record["source"].update(chat_url="https://chatgpt.com/c/" + portraits.ZERO_UUID),
            lambda record: record["source"].update(turn_id=portraits.ZERO_UUID),
            lambda record: record["source"].update(gallery_index=0),
            lambda record: record["source"].update(gallery_index=True),
            lambda record: record["source"].update(kind="unreviewed-preview"),
            lambda record: record["review"].update(decision="rejected"),
            lambda record: record["review"].update(note=" "),
            lambda record: record["review"].update(reviewed_at="2026-11-14T12:00:00"),
            lambda record: record["review"].update(reviewed_at="2026-11-16T12:00:00+09:00"),
        ]
        original = copy.deepcopy(self.record)
        for change in changes:
            self.model["profile_portrait"] = copy.deepcopy(original)
            change(self.record)
            with self.subTest(record=self.record), self.assertRaises(ValueError):
                self.validate()
        self.model["profile_portrait"] = None
        with self.assertRaises(ValueError):
            self.validate()

    def test_symlinked_assets_originals_and_output_are_rejected(self):
        for relative in (self.record["path"], self.record["source_png"]["path"]):
            with self.subTest(relative=relative):
                path = self.root / relative
                original = path.read_bytes()
                outside = self.root / "outside-image"
                outside.write_bytes(original)
                path.unlink()
                path.symlink_to(outside)
                with self.assertRaisesRegex(ValueError, "symlink"):
                    self.validate()
                path.unlink()
                path.write_bytes(original)
        result = self.validate()
        output = self.root / "dist"
        output.mkdir()
        (output / "assets").symlink_to(self.root / "assets", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            portraits.copy_profile_portrait(result, output, root=self.root)

    def test_output_conflict_is_not_overwritten(self):
        result = self.validate()
        output = self.root / "dist"
        target = output / result["path"]
        target.parent.mkdir(parents=True)
        target.write_bytes(b"existing unrelated asset")
        with self.assertRaisesRegex(ValueError, "conflicts"):
            portraits.copy_profile_portrait(result, output, root=self.root)
        self.assertEqual(target.read_bytes(), b"existing unrelated asset")

    def test_symlinked_source_or_output_root_is_rejected(self):
        alias = self.root / "source-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            portraits.validate_profile_portrait(self.model, root=alias, now=NOW)
        actual_output = self.root / "actual-output"
        actual_output.mkdir()
        output_alias = self.root / "output-alias"
        output_alias.symlink_to(actual_output, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            portraits.copy_profile_portrait(self.validate(), output_alias, root=self.root)
        self.assertEqual(list(actual_output.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
