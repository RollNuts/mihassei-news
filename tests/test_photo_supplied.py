"""Owner-supplied photos retain bytes, aspect and observed review bindings."""
import copy
import hashlib
import io
import json
import shutil
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image, __version__ as pillow_version
from scripts import photo_issues as p

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)
RETAINED_FP = "d1de2152b14f4676cf9624626005195d11ea36685e1202b157ec44c4ca3d200e"

class SuppliedPhotoTests(unittest.TestCase):
    def setUp(self):
        self.issue = copy.deepcopy(p.load_photo_issues(ROOT)["issues"][0])
        retained = next(entry for entry in self.issue["editorial_history"]
                        if entry["issue_sha256"] == RETAINED_FP)
        self.assertEqual(p.issue_fingerprint(retained["editorial"]), RETAINED_FP)
        self.issue.update(copy.deepcopy(retained["editorial"]), issue_sha256=RETAINED_FP,
                          images=copy.deepcopy(retained["images"]),
                          editorial_history=[entry for entry in self.issue["editorial_history"]
                                             if entry["issue_sha256"] != RETAINED_FP])
        self.issue.update(state="published", published_at=NOW.isoformat(), generation_holds={})
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        def copy_into(path, source=None):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / (source or path), target)
        master = self.issue.get("master_archive", self.issue["model_snapshot"]["master"])
        copy_into(master["path"])
        for image in self.issue["images"].values():
            asset = retained["retained_assets"][image["path"]]
            self.assertEqual(asset["sha256"], image["sha256"])
            self.assertEqual(hashlib.sha256((ROOT / asset["path"]).read_bytes()).hexdigest(), image["sha256"])
            copy_into(image["path"], asset["path"])
            receipt_path = image["provenance"]["receipt_path"]
            copy_into(receipt_path)
            receipt = json.loads((self.root / receipt_path).read_bytes())
            if receipt["kind"] == "user_supplied_photo":
                copy_into(receipt["source"]["path"])
        self.image = copy.deepcopy(self.issue["images"]["03"])
        self.data = (self.root / self.image["path"]).read_bytes()
        self.receipt = json.loads((self.root / self.image["provenance"]["receipt_path"]).read_bytes())
        self.original = (self.root / self.receipt["source"]["path"]).read_bytes()
        self.save()

    def save(self):
        source_sha = hashlib.sha256(self.original).hexdigest()
        directory = "maintenance/photo-issues/" + self.issue["slug"] + "/03"
        self.image["source_sha256"] = source_sha
        source = self.receipt["source"]
        extension = ".jpg" if source.get("format", "png") == "jpeg" else ".png"
        source.update(sha256=source_sha, bytes=len(self.original),
                      path=directory + "/source-" + source_sha + extension)
        target = self.root / source["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.original)
        receipt_path = directory + "/receipt-supplied-" + source_sha[:12] + "-" + self.image["sha256"][:12] + ".json"
        raw = (json.dumps(self.receipt, ensure_ascii=False, indent=2) + "\n").encode()
        (self.root / receipt_path).write_bytes(raw)
        self.image["provenance"].update(receipt_path=receipt_path, receipt_sha256=hashlib.sha256(raw).hexdigest())

    def validate(self):
        return p._validate_supplied_photo(self.issue, "03", self.image, self.data, root=self.root, now=NOW)

    def use_pillow_library_archive(self):
        """Make real ZIP/PNG/JPEG test bytes, not a renamed sips receipt."""
        member = "original.jpg" if self.receipt["source"].get("format", "png") == "jpeg" else "original.png"
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr(member, self.original)
        source = self.receipt["source"]
        source.update(url="https://chatgpt.com/library?file_id=libfile_test_archive&account_id=personal",
                      library_file_id="libfile_test_archive",
                      generation_turn_status="not_retained", generation_turn=None,
                      archive={"format": "zip", "filename": "photos.zip", "member": member,
                               "sha256": hashlib.sha256(archive.getvalue()).hexdigest(),
                               "bytes": len(archive.getvalue())})
        for key in ("turn_key", "image_number"):
            source.pop(key, None)
        with Image.open(io.BytesIO(self.original)) as original:
            converted = original.convert("RGB")
        converted.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
        encoded = io.BytesIO()
        converted.save(encoded, format="JPEG", quality=84, optimize=True, progressive=False, subsampling=0)
        self.data = encoded.getvalue()
        self.image.update(sha256=hashlib.sha256(self.data).hexdigest(), bytes=len(self.data),
                          width=converted.width, height=converted.height)
        self.image["review"]["image_sha256"] = self.image["sha256"]
        self.receipt["jpeg"].update({key: self.image[key] for key in ("path", "sha256", "bytes", "width", "height")})
        self.receipt["transform"] = {"encoder": "Pillow", "max_edge": 1200, "jpeg_quality": 84,
                                     "geometry": "preserve-aspect"}
        self.receipt["encoding_details"] = {"pillow_version": pillow_version, "resample": "LANCZOS", "mode": "RGB",
                                             "optimize": True, "progressive": False, "subsampling": 0}
        (self.root / self.image["path"]).write_bytes(self.data)
        self.issue["images"]["03"] = self.image
        self.save()

    def rewrite_receipt(self):
        raw = (json.dumps(self.receipt, ensure_ascii=False, indent=2) + "\n").encode()
        (self.root / self.image["provenance"]["receipt_path"]).write_bytes(raw)
        self.image["provenance"]["receipt_sha256"] = hashlib.sha256(raw).hexdigest()

    def test_real_pillow_archive_and_direct_library_original_keep_existing_gates(self):
        self.use_pillow_library_archive()
        self.assertEqual(self.validate(), ("upper_body", False))
        p.validate_published_issue(self.issue, root=self.root, now=NOW)
        del self.receipt["source"]["archive"]
        self.rewrite_receipt()
        self.assertEqual(self.validate(), ("upper_body", False))

    def test_pillow_library_portrait_retains_its_full_original_aspect(self):
        with Image.open(io.BytesIO(self.original)) as original:
            portrait = original.transpose(Image.Transpose.ROTATE_90) if original.width > original.height else original.copy()
        encoded = io.BytesIO()
        portrait.save(encoded, format="PNG")
        self.original = encoded.getvalue()
        self.receipt["source"].update(format="png", width=portrait.width, height=portrait.height)
        self.use_pillow_library_archive()
        self.assertEqual(self.image["height"], 1200)
        self.assertLess(self.image["width"], self.image["height"])
        p.validate_published_issue(self.issue, root=self.root, now=NOW)

    def test_pillow_library_archive_accepts_a_real_jpeg_member(self):
        self.original = self.data
        width, height = p.jpeg_dimensions(self.original)
        self.receipt["source"].update(format="jpeg", width=width, height=height)
        self.use_pillow_library_archive()
        self.assertEqual(self.receipt["source"]["archive"]["member"], "original.jpg")
        self.assertTrue(self.receipt["source"]["path"].endswith(".jpg"))
        p.validate_published_issue(self.issue, root=self.root, now=NOW)

    def test_library_file_id_account_and_unknown_turn_are_bound(self):
        self.use_pillow_library_archive()
        baseline = copy.deepcopy(self.receipt["source"])
        url = baseline["url"]
        bad_urls = [url.replace("https:", "http:"), url.replace("chatgpt.com/", "chatgpt.com.evil/"),
                    url.replace("libfile_test_archive", "libfile_other"), url.replace("personal", "team"),
                    url.replace("&account_id=personal", ""), url + "&account_id=personal",
                    url + "&file_id=libfile_other", url + "#fragment", url + "&extra=1",
                    "https://chatgpt.com/library/share/libfile_test_archive?account_id=personal"]
        changes = [("url", value) for value in bad_urls] + [
            ("library_file_id", None), ("library_file_id", "libfile_other"),
            ("generation_turn_status", "known"), ("generation_turn", "4c0a07b0-c1c9-4faf-8cf8-9af807531370"),
            ("turn_key", "4c0a07b0-c1c9-4faf-8cf8-9af807531370"), ("image_number", 1),
            ("generation_prompt", "A prompt that was never retained")]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                self.receipt["source"] = {**baseline, field: value}
                self.rewrite_receipt()
                with self.assertRaisesRegex(ValueError, "provenance"):
                    self.validate()
        self.receipt["source"] = copy.deepcopy(baseline)
        del self.receipt["source"]["generation_turn"]
        self.rewrite_receipt()
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.validate()

    def test_archive_cannot_hide_unsafe_members_or_invalid_archive_metadata(self):
        self.use_pillow_library_archive()
        baseline = copy.deepcopy(self.receipt["source"]["archive"])
        changes = [("format", "tar"), ("sha256", "not-a-sha"), ("bytes", 0), ("bytes", True),
                   ("bytes", "42"), ("filename", "../photos.zip"), ("filename", "/tmp/photos.zip"),
                   ("filename", "photos.tar"), ("member", "../original.png"), ("member", "folder/original.png"),
                   ("member", "/original.png"), ("member", "folder\\original.png"), ("member", "C:original.png"),
                   ("member", "original\x00.png"), ("member", " original.png"), ("member", "original.jpg")]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                self.receipt["source"]["archive"] = {**baseline, field: value}
                self.rewrite_receipt()
                with self.assertRaisesRegex(ValueError, "provenance"):
                    self.validate()
        for archive in (None, {}, {key: value for key, value in baseline.items() if key != "member"}):
            self.receipt["source"]["archive"] = archive
            self.rewrite_receipt()
            with self.assertRaisesRegex(ValueError, "provenance"):
                self.validate()

    def test_pillow_cannot_relax_encoder_quality_or_resampling_metadata(self):
        self.use_pillow_library_archive()
        baseline = copy.deepcopy(self.receipt)
        changes = [("transform", "encoder", "macOS sips"), ("transform", "encoder", "unknown"),
                   ("transform", "max_edge", 1199), ("transform", "max_edge", 1200.0),
                   ("transform", "jpeg_quality", 85), ("transform", "geometry", "crop"),
                   ("encoding_details", "pillow_version", "unknown"), ("encoding_details", "pillow_version", None),
                   ("encoding_details", "resample", "BICUBIC"), ("encoding_details", "mode", "CMYK"),
                   ("encoding_details", "optimize", False), ("encoding_details", "progressive", True),
                   ("encoding_details", "subsampling", 2), ("encoding_details", "subsampling", False)]
        for section, field, value in changes:
            with self.subTest(section=section, field=field, value=value):
                self.receipt = copy.deepcopy(baseline)
                self.receipt[section][field] = value
                self.rewrite_receipt()
                with self.assertRaisesRegex(ValueError, "provenance"):
                    self.validate()
        self.receipt = copy.deepcopy(baseline)
        del self.receipt["encoding_details"]
        self.rewrite_receipt()
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.validate()

    def test_pillow_library_route_still_checks_actual_original_and_jpeg_bindings(self):
        self.use_pillow_library_archive()
        baseline = copy.deepcopy(self.receipt)
        for section, field, value in [("source", "sha256", "0" * 64), ("source", "bytes", 1),
                                       ("source", "width", baseline["source"]["width"] + 1), ("source", "format", "jpeg"),
                                       ("jpeg", "sha256", "0" * 64), ("jpeg", "bytes", 1),
                                       ("jpeg", "width", baseline["jpeg"]["width"] + 1)]:
            with self.subTest(section=section, field=field):
                self.receipt = copy.deepcopy(baseline)
                self.receipt[section][field] = value
                self.rewrite_receipt()
                with self.assertRaises(ValueError):
                    self.validate()
        self.receipt = copy.deepcopy(baseline)
        self.rewrite_receipt()
        (self.root / self.image["path"]).write_bytes(self.data[:-2] + b"xx")
        with self.assertRaisesRegex(ValueError, "hash/dimensions"):
            p.validate_published_issue(self.issue, root=self.root, now=NOW)

    def test_supplied_portrait_keeps_original_aspect_and_towel_is_not_swimwear(self):
        self.assertEqual(self.validate(), ("upper_body", False))
        p.validate_published_issue(self.issue, root=self.root, now=NOW)
        image = self.issue["images"]["04"]
        self.assertGreater(image["height"], image["width"])
        self.assertTrue(image["review"]["swimwear_visible"])

    def test_corrupt_original_is_rejected_even_if_all_file_hashes_are_rebound(self):
        damaged = bytearray(self.original)
        damaged[-1] ^= 1
        self.original = bytes(damaged)
        self.save()
        with self.assertRaisesRegex(ValueError, "PNG"):
            self.validate()

    def test_real_jpeg_original_retains_jpg_path_and_original_aspect(self):
        self.original = self.data
        width, height = p.jpeg_dimensions(self.original)
        self.receipt["source"].update(format="jpeg", width=width, height=height)
        self.save()
        self.assertTrue(self.receipt["source"]["path"].endswith(".jpg"))
        self.assertNotIn("source_png_sha256", self.image)
        self.assertEqual(self.validate(), ("upper_body", False))
        self.original = self.original[:-2] + b"xx"
        self.save()
        with self.assertRaisesRegex(ValueError, "JPEG"):
            self.validate()

    def test_source_format_and_extension_cannot_disagree(self):
        self.receipt["source"]["format"] = "jpeg"
        self.save()
        # An actual PNG cannot gain JPEG provenance by renaming the file.
        with self.assertRaisesRegex(ValueError, "JPEG"):
            self.validate()
        self.receipt["source"]["format"] = "gif"
        self.save()
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.validate()

    def test_portrait_cannot_be_declared_a_landscape_without_changing_real_bytes(self):
        self.image.update(width=1200, height=675)
        self.receipt["jpeg"].update(width=1200, height=675)
        self.save()
        with self.assertRaisesRegex(ValueError, "dimensions"):
            self.validate()

    def test_unrelated_source_and_invented_prompt_do_not_gain_library_provenance(self):
        for field, value in [("url", "https://example.com/not-the-supplied-image"),
                             ("generation_prompt", "A prompt that was never retained")]:
            with self.subTest(field=field):
                original = self.receipt["source"][field]
                self.receipt["source"][field] = value
                self.save()
                with self.assertRaisesRegex(ValueError, "provenance"):
                    self.validate()
                self.receipt["source"][field] = original
        self.save()

    def test_current_editorial_and_master_review_are_required(self):
        for key in ("image_sha256", "issue_sha256", "master_sha256"):
            with self.subTest(key=key):
                original = self.image["review"][key]
                self.image["review"][key] = "0" * 64
                with self.assertRaisesRegex(ValueError, "acceptance"):
                    self.validate()
                self.image["review"][key] = original

    def test_observed_conversation_original_needs_its_canonical_turn_and_image_number(self):
        source = self.receipt["source"]
        source.update(url="https://chatgpt.com/c/6af854c9-0598-83ee-8958-b762ea028909",
                      turn_key="4c0a07b0-c1c9-4faf-8cf8-9af807531370", image_number=2)
        self.save()
        self.assertEqual(self.validate(), ("upper_body", False))
        baseline = copy.deepcopy(source)
        for field, value in [("url", baseline["url"] + "?fake=share"),
                             ("url", "https://chatgpt.com/c/6af854c9-0598-83ee-8958-b762ea028908"),
                             ("turn_key", None), ("turn_key", "not-a-dom-uuid"),
                             ("turn_key", "00000000-0000-0000-0000-000000000000"),
                             ("image_number", None), ("image_number", 0),
                             ("image_number", True), ("image_number", "2"),
                             ("generation_prompt", "An unobserved generation prompt")]:
            with self.subTest(field=field, value=value):
                source.clear()
                source.update(baseline)
                source[field] = value
                self.save()
                with self.assertRaisesRegex(ValueError, "provenance"):
                    self.validate()

    def test_library_reference_cannot_be_invented_for_a_conversation_turn(self):
        self.receipt["source"].update(turn_key="4c0a07b0-c1c9-4faf-8cf8-9af807531370", image_number=2)
        self.save()
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.validate()

    def test_conversation_references_are_bound_to_each_known_models_canonical_chat(self):
        baseline = {"turn_key": "4c0a07b0-c1c9-4faf-8cf8-9af807531370", "image_number": 2}
        self.assertEqual(len(p.MODEL_CHAT_URLS), 7)
        for model_id, url in p.MODEL_CHAT_URLS.items():
            with self.subTest(model_id=model_id):
                source = {**baseline, "url": url,
                          "observed_url": url.replace("/c/", "/g/project/c/")}
                self.assertTrue(p._supplied_source_reference(source, model_id))
                for wrong_model in [other for other in p.MODEL_CHAT_URLS if other != model_id]:
                    self.assertFalse(p._supplied_source_reference(source, wrong_model))
                self.assertFalse(p._supplied_source_reference(source, "unknown-model"))
                for changed in ({"url": url + "?share=1"},
                                {"url": url.replace("/c/", "/g/project/c/")},
                                {"url": "https://chatgpt.com/c/00000000-0000-0000-0000-000000000000"},
                                {"turn_key": "00000000-0000-0000-0000-000000000000"},
                                {"turn_key": "not-a-turn"}, {"image_number": 0},
                                {"image_number": True}, {"image_number": "1"}):
                    self.assertFalse(p._supplied_source_reference({**source, **changed}, model_id))
        library = {"url": "https://chatgpt.com/library/share/libfile_real-reference?account_id=personal"}
        self.assertTrue(p._supplied_source_reference(library, "unknown-model"),
                        "existing Library references stay independent of conversation routing")

    def test_receipt_validator_rejects_another_models_otherwise_valid_conversation(self):
        self.receipt["source"].update(url=p.MODEL_CHAT_URLS["shioba-rio"],
                                      turn_key="4c0a07b0-c1c9-4faf-8cf8-9af807531370", image_number=2)
        self.save()
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.validate()

    def test_independent_supplied_issue_validates_without_inventing_article_identity(self):
        catalog = p.load_photo_issues(ROOT)
        independent = copy.deepcopy(catalog["issues"][0])
        independent.update(source_article=None, editorial_history=[], article_photo_sources={})
        independent["issue_sha256"] = p.issue_fingerprint(independent)
        independent["published_at"] = NOW.isoformat()
        master = independent.get("master_archive", independent["model_snapshot"]["master"])
        paths = {master["path"]}
        for image in independent["images"].values():
            self.assertEqual(image["provenance"]["kind"], "user_supplied_photo")
            image["review"]["issue_sha256"] = independent["issue_sha256"]
            paths.add(image["path"])
            receipt_path = image["provenance"]["receipt_path"]
            paths.add(receipt_path)
            receipt = json.loads((ROOT / receipt_path).read_bytes())
            paths.add(receipt["source"]["path"])
        for relative in paths:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        local = {"schema_version": 1, "models": catalog["models"], "issues": [independent]}
        p.validate_catalog(local)
        self.assertEqual(p.published_issues(local, root=self.root, now=NOW), [independent])
        self.assertIsNone(independent["source_article"])
        self.assertEqual(p._article_photo_sources(independent), {})
        original = p.issue_fingerprint(independent)
        independent["source_article"] = catalog["issues"][0]["source_article"]
        self.assertNotEqual(p.issue_fingerprint(independent), original,
                            "the explicit null remains part of the editorial fingerprint")

    def test_a_later_acceptance_of_a_native_study_needs_its_retained_snapshot(self):
        image = self.issue["images"]["02"]
        self.assertTrue(image["review_history"])
        p._generation_source(self.issue, image, "02")
        earlier = image["review_history"][0]["issue_sha256"]
        entry = next(h for h in self.issue["editorial_history"] if h["issue_sha256"] == earlier)
        entry["images"]["02"]["review"]["note"] = "Unrecorded replacement of the original review"
        with self.assertRaisesRegex(ValueError, "retained acceptance"):
            p._generation_source(self.issue, image, "02")

    def test_study_cannot_drop_retained_reviews_or_change_an_observation_at_its_old_time(self):
        baseline = copy.deepcopy(self.issue["images"]["02"])
        for change in (lambda image: image.update(review_history=[]),
                       lambda image: image["review"].update(swimwear_visible=False),
                       lambda image: image["review"].update(framing="wide")):
            with self.subTest(change=change):
                image = copy.deepcopy(baseline)
                change(image)
                with self.assertRaises(ValueError):
                    p._generation_source(self.issue, image, "02")

if __name__ == "__main__":
    unittest.main()

