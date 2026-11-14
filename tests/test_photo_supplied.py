"""Owner-supplied photos retain bytes, aspect and observed review bindings."""
import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
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

