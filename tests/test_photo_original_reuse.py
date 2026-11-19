"""Cross-feature reuse checks use real originals in temporary, offline fixtures."""
import copy
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image, __version__ as pillow_version

from scripts import photo_issues as p


ROOT = Path(__file__).resolve().parents[1]


class PhotoOriginalReuseTests(unittest.TestCase):
    def setUp(self):
        catalog = p.load_photo_issues(ROOT)
        slugs = ("winter-coat-swimsuit-saya-001", "november-water-and-quiet-hours-saya-010")
        self.issues = [copy.deepcopy(next(i for i in catalog["issues"] if i["slug"] == slug))
                       for slug in slugs]
        self.catalog = {"schema_version": 1, "models": catalog["models"], "issues": self.issues}
        self.now = max(p._stamp(issue["published_at"]) for issue in self.issues)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        paths = set()
        for issue in self.issues:
            paths.add(issue.get("master_archive", issue["model_snapshot"]["master"])["path"])
            if "body_profile" in issue["model_snapshot"]:
                paths.update(reference["path"] for reference in p.generation_inputs(issue, "01")["references"])
            for image in issue["images"].values():
                self.assertEqual(image["provenance"]["kind"], "user_supplied_photo")
                receipt_path = image["provenance"]["receipt_path"]
                receipt = json.loads((ROOT / receipt_path).read_bytes())
                paths.update((image["path"], receipt_path, receipt["source"]["path"]))
        for relative in paths:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)

    def reuse_first_original(self, *, reencode):
        """Bind the first feature's cut 03 into the second feature only in the fixture."""
        first, second = self.issues
        previous = first["images"]["03"]
        receipt = json.loads((self.root / previous["provenance"]["receipt_path"]).read_bytes())
        original = (self.root / receipt["source"]["path"]).read_bytes()
        data = (self.root / previous["path"]).read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(), previous["source_sha256"])
        if reencode:
            with Image.open(io.BytesIO(original)) as source:
                converted = source.convert("RGB")
            converted.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            converted.save(output, format="JPEG", quality=84, optimize=True,
                           progressive=False, subsampling=0)
            data = output.getvalue()
            self.assertNotEqual(hashlib.sha256(data).hexdigest(), previous["sha256"],
                                "The old JPEG-hash check must not detect this raw-original reuse")
            receipt["transform"] = {"encoder": "Pillow", "max_edge": 1200,
                                    "jpeg_quality": 84, "geometry": "preserve-aspect"}
            receipt["encoding_details"] = {"pillow_version": pillow_version, "resample": "LANCZOS",
                                           "mode": "RGB", "optimize": True,
                                           "progressive": False, "subsampling": 0}
        image = second["images"]["03"]
        width, height = p.jpeg_dimensions(data)
        image.update(source_sha256=previous["source_sha256"], sha256=hashlib.sha256(data).hexdigest(),
                     bytes=len(data), width=width, height=height)
        (self.root / image["path"]).write_bytes(data)
        directory = "maintenance/photo-issues/" + second["slug"] + "/03"
        receipt["issue_slug"] = second["slug"]
        receipt["review_reference_sha256"] = second["model_snapshot"]["master"]["sha256"]
        receipt["source"]["path"] = directory + "/source-" + image["source_sha256"] + ".png"
        (self.root / receipt["source"]["path"]).write_bytes(original)
        receipt["jpeg"] = {key: image[key] for key in ("path", "sha256", "bytes", "width", "height")}
        receipt_path = directory + "/receipt-supplied-" + image["source_sha256"][:12] + "-" + image["sha256"][:12] + ".json"
        raw = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode()
        (self.root / receipt_path).write_bytes(raw)
        image["provenance"].update(receipt_path=receipt_path, receipt_sha256=hashlib.sha256(raw).hexdigest())
        image["review"].update(image_sha256=image["sha256"], framing=previous["review"]["framing"],
                               swimwear_visible=previous["review"]["swimwear_visible"],
                               note="Synthetic offline reuse fixture; no new visual acceptance or publication.")

    def test_distinct_published_features_keep_their_existing_acceptances(self):
        p.validate_catalog(self.catalog)
        self.assertEqual(len(p.published_issues(self.catalog, root=self.root, now=self.now)), 2)

    def test_another_feature_cannot_reuse_an_original_even_after_jpeg_reencoding(self):
        for reencode in (False, True):
            with self.subTest(reencode=reencode):
                self.reuse_first_original(reencode=reencode)
                p.validate_catalog(self.catalog)
                # Both per-feature provenance/byte/review gates pass: the failure
                # must come from checking reuse across published features.
                for issue in self.issues:
                    p.validate_published_issue(issue, root=self.root, now=self.now)
                with self.assertRaises(ValueError) as caught:
                    p.published_issues(self.catalog, root=self.root, now=self.now)
                for issue in self.issues:
                    self.assertIn(issue["slug"] + "/03", str(caught.exception))
                self.assertIn(self.issues[0]["images"]["03"]["source_sha256"], str(caught.exception))

    def test_unpublished_draft_with_pending_provenance_is_not_a_release(self):
        self.reuse_first_original(reencode=True)
        self.issues[1].update(state="draft", published_at=None)
        del self.issues[1]["images"]["03"]["source_sha256"]
        p.validate_catalog(self.catalog)
        self.assertEqual(p.published_issues(self.catalog, root=self.root, now=self.now), [self.issues[0]])

    def test_a_published_feature_cannot_guess_a_missing_original_sha(self):
        del self.issues[1]["images"]["03"]["source_sha256"]
        with self.assertRaisesRegex(ValueError, "actual original SHA"):
            p.published_issues(self.catalog, root=self.root, now=self.now)


if __name__ == "__main__":
    unittest.main()
