"""Build-local parsing reuse must never reuse a publication decision."""
import hashlib
import json
import os
from contextlib import nullcontext
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from scripts import build
from scripts.generated_photos import article_fingerprint, generated_manifest
from scripts.mac_scene_control import image_status_counts
from scripts.publication_inputs import publication_json_snapshot, read_publication_json
from scripts.visual_review import reviewed_generated_scene
from tests.test_generated_photos import fake_jpeg, fixture


class PublicationInputTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.article = fixture()
        self.seed = 2**63 + 107
        self.jpeg = self.root / "assets/generated" / (self.article["slug"] + ".jpg")
        self.jpeg.parent.mkdir(parents=True)
        data = fake_jpeg()
        self.jpeg.write_bytes(data)
        record = {
            "origin": "local-comfyui", "model": "fixture.safetensors",
            "prompt_version": 2, "article_sha256": article_fingerprint(self.article),
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "seed": self.seed,
        }
        self.manifest = {"version": 1, "images": {self.article["slug"]: record}, "rejected": {}}
        self.reviews = {"version": 1, "reviews": {self.article["slug"]: {
            "decision": "accepted", "article_sha256": article_fingerprint(self.article),
            "image_sha256": record["sha256"], "reviewed_at": "2026-11-04T01:30:05Z",
            "note": "Synthetic fixture only; not an actual visual review.",
        }}}
        self.manifest_path = self.root / "assets/generated/manifest.json"
        self.review_path = self.root / "content/visual_reviews.json"
        self.review_path.parent.mkdir()
        self.write_inputs()

    def write_inputs(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.review_path.write_text(json.dumps(self.reviews), encoding="utf-8")

    def accepted(self):
        return reviewed_generated_scene(self.article, root=self.root)

    def test_repeated_gates_read_each_unchanged_json_once_and_keep_integer_seed(self):
        reads = Counter()
        original = Path.read_text

        def counted(path, *args, **kwargs):
            reads[path] += 1
            return original(path, *args, **kwargs)

        before = self.manifest_path.read_bytes()
        with patch.object(Path, "read_text", counted), publication_json_snapshot():
            for _ in range(5):
                self.assertTrue(self.accepted())
            seed = generated_manifest(self.root)[self.article["slug"]]["seed"]
            self.assertIs(type(seed), int)
            self.assertEqual(seed, self.seed)
        self.assertEqual(reads[self.manifest_path], 1)
        self.assertEqual(reads[self.review_path], 1)
        self.assertEqual(self.manifest_path.read_bytes(), before)

    def test_same_path_manifest_and_review_changes_revoke_acceptance_within_build(self):
        with publication_json_snapshot():
            self.assertTrue(self.accepted())
            old = self.manifest_path.stat()
            self.manifest["rejected"][self.article["slug"]] = {
                "article_sha256": article_fingerprint(self.article),
            }
            self.write_inputs()
            # Restoring mtime alone must not hide a changed ledger.
            os.utime(self.manifest_path, ns=(old.st_atime_ns, old.st_mtime_ns))
            self.assertFalse(self.accepted())
            self.manifest["rejected"].clear()
            self.write_inputs()
            self.assertTrue(self.accepted())
            self.reviews["reviews"][self.article["slug"]]["decision"] = "rejected"
            self.write_inputs()
            self.assertFalse(self.accepted())

    def test_jpeg_bytes_are_checked_again_even_when_json_is_reused(self):
        with publication_json_snapshot():
            self.assertTrue(self.accepted())
            data = bytearray(self.jpeg.read_bytes())
            data[-10] ^= 1
            self.jpeg.write_bytes(data)
            self.assertFalse(self.accepted())

    def test_manifest_replacement_during_jpeg_check_still_revokes_acceptance(self):
        original = Path.read_bytes

        def replace_after_read(path, *args, **kwargs):
            data = original(path, *args, **kwargs)
            if path == self.jpeg:
                self.manifest["rejected"][self.article["slug"]] = {
                    "article_sha256": article_fingerprint(self.article),
                }
                self.write_inputs()
            return data

        with publication_json_snapshot(), patch.object(Path, "read_bytes", replace_after_read):
            self.assertFalse(self.accepted())

    def test_each_build_starts_fresh_and_failure_leaves_live_readers(self):
        class BuildStopped(Exception):
            pass

        results = []

        def stop_after_gate(_root):
            results.append(self.accepted())
            raise BuildStopped

        with patch.object(build, "BASE", self.root), \
             patch.object(build, "load_photo_issues", side_effect=stop_after_gate):
            with self.assertRaises(BuildStopped):
                build.build()
            self.reviews["reviews"][self.article["slug"]]["decision"] = "rejected"
            self.write_inputs()
            with self.assertRaises(BuildStopped):
                build.build()
        self.assertEqual(results, [True, False])
        self.reviews["reviews"][self.article["slug"]]["decision"] = "accepted"
        self.write_inputs()
        self.assertTrue(self.accepted())
        # Generator consumers outside build still get independent live objects.
        generated_manifest(self.root)[self.article["slug"]]["seed"] = 0
        self.assertEqual(generated_manifest(self.root)[self.article["slug"]]["seed"], self.seed)

    def test_new_and_nested_contexts_parse_fresh_inputs(self):
        original = json.loads
        with patch("scripts.publication_inputs.json.loads", wraps=original) as loads:
            with publication_json_snapshot():
                read_publication_json(self.manifest_path)
                with publication_json_snapshot():
                    read_publication_json(self.manifest_path)
                read_publication_json(self.manifest_path)
            with publication_json_snapshot():
                read_publication_json(self.manifest_path)
        self.assertEqual(loads.call_count, 3)

    def test_failed_input_is_not_cached_and_changed_during_read_fails_closed(self):
        with publication_json_snapshot():
            self.manifest_path.write_text("invalid JSON", encoding="utf-8")
            self.assertFalse(self.accepted())
            self.write_inputs()
            self.assertTrue(self.accepted())
        with publication_json_snapshot(), \
             patch("scripts.publication_inputs._identity", side_effect=[(1,), (2,)]):
            with self.assertRaisesRegex(ValueError, "changed while reading"):
                read_publication_json(self.manifest_path)

    def test_virtual_snapshot_ledgers_stay_live_inside_and_outside_build(self):
        from types import SimpleNamespace

        reads = Counter()
        files = {
            "assets/generated/manifest.json": self.manifest_path.read_bytes(),
            "content/visual_reviews.json": self.review_path.read_bytes(),
            "assets/generated/" + self.jpeg.name: self.jpeg.read_bytes(),
        }

        class Snapshot:
            # Match the remote status reader: no __fspath__ or file timestamps.
            def __init__(self, path=""):
                self.path = path

            def __truediv__(self, path):
                return Snapshot("/".join(part for part in (self.path, str(path)) if part))

            def read_bytes(self):
                reads[self.path] += 1
                return files[self.path]

            def read_text(self, encoding="utf-8"):
                return self.read_bytes().decode(encoding)

            def is_symlink(self):
                return False

            def stat(self):
                return SimpleNamespace(st_size=len(self.read_bytes()))

        for context in (nullcontext, publication_json_snapshot):
            with self.subTest(context=context.__name__), context():
                self.reviews["reviews"][self.article["slug"]]["decision"] = "accepted"
                files["content/visual_reviews.json"] = json.dumps(self.reviews).encode()
                before = reads["content/visual_reviews.json"]
                counts = image_status_counts([self.article], Snapshot())
                self.assertEqual(counts["published_images"], 1)
                self.reviews["reviews"][self.article["slug"]]["decision"] = "rejected"
                files["content/visual_reviews.json"] = json.dumps(self.reviews).encode()
                counts = image_status_counts([self.article], Snapshot())
                self.assertEqual(counts["published_images"], 0)
                self.assertEqual(counts["held_articles"], 1)
                self.assertEqual(reads["content/visual_reviews.json"] - before, 2)
