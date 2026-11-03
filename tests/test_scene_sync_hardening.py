"""Regression tests for outbox preservation and remote-checkout storage gates."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import local_scene_photos as photos, local_scene_sync as sync


ARTICLE = {"slug": "test-scene", "title": "A fictional scene", "summary": "An absurd prop",
           "category": "暮らし", "paragraphs": ["A staged scene"]}
MODEL = "sd_xl_base_1.0.safetensors"


def jpeg():
    return (b"\xff\xd8\xff\xc0\x00\x11\x08"
            + (675).to_bytes(2, "big") + (1200).to_bytes(2, "big")
            + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
            + b"\x00" * 17000 + b"\xff\xd9")


class SceneSyncHardeningTests(unittest.TestCase):
    def test_corrupt_outbox_manifest_is_preserved(self):
        for payload in ("{broken", '{"version":9,"images":{}}', '{"version":1,"images":[]}'):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                outbox = root / "outbox"
                published = root / "published"
                photos.write_generated(ARTICLE, jpeg(), MODEL, root=outbox)
                photos.write_generated(ARTICLE, jpeg(), MODEL, root=published)
                manifest = outbox / "assets/generated/manifest.json"
                manifest.write_text(payload)
                with self.assertRaises(ValueError):
                    sync.retire_published(outbox, published, [ARTICLE])
                self.assertEqual(manifest.read_text(), payload)
                self.assertEqual((outbox / "assets/generated/test-scene.jpg").read_bytes(), jpeg())

    def test_outbox_cleanup_rejects_symlinked_asset_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outside = root / "important"
            photos.write_generated(ARTICLE, jpeg(), MODEL, root=outside)
            outbox = root / "outbox"
            outbox.mkdir()
            (outbox / "assets").symlink_to(outside / "assets", target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, "Unsafe"):
                sync.retire_published(outbox, outside, [ARTICLE])
            self.assertEqual((outside / "assets/generated/test-scene.jpg").read_bytes(), jpeg())

    def test_copy_checks_space_before_modifying_outbox(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            target = root / "outbox"
            target.mkdir()
            photos.write_generated(ARTICLE, jpeg(), MODEL, root=source)
            with patch.object(sync, "check_storage", side_effect=RuntimeError("disk floor")) as guard:
                with self.assertRaisesRegex(RuntimeError, "disk floor"):
                    sync.transfer_scenes(source, target, [ARTICLE], missing_only=True, min_free_gb=18)
            guard.assert_called_once_with([target], 18)
            self.assertFalse((target / "assets/generated").exists())

    def test_publish_rechecks_new_remote_size_before_reset(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app = root / "app"
            outbox = app / "pending"
            checkout = app / "sync-test/checkout"
            photos.write_generated(ARTICLE, jpeg(), MODEL, root=outbox)
            photos.write_generated(ARTICLE, jpeg(), MODEL, root=checkout)
            original = (checkout / "assets/generated/manifest.json").read_bytes()
            calls = []

            def fake_git(*args, **kwargs):
                calls.append(args[0])
                if args[0] == "fetch":
                    return subprocess.CompletedProcess(args, 0, "", "")
                if args[0] == "ls-tree":
                    return subprocess.CompletedProcess(args, 0,
                        f"100644 blob {'a' * 40} {len(jpeg())} assets/generated/test-scene.jpg\n"
                        f"100644 blob {'b' * 40} {len(jpeg())} assets/generated/new-scene.jpg\n", "")
                self.fail("Checkout was modified before the enlarged remote passed its budget")

            with patch.object(sync, "ROOT", root / "repo"), \
                 patch.object(sync, "APP_DIR", app), patch.object(sync, "OUTBOX", outbox), \
                 patch.object(sync, "MAX_ARCHIVE_BYTES", 2 * len(jpeg()) + 100), \
                 patch.object(sync, "git", side_effect=fake_git), patch.object(sync, "check_storage"):
                with self.assertRaisesRegex(RuntimeError, "combined JPEG"):
                    sync.publish_outbox(checkout, outbox)
            self.assertEqual(calls, ["fetch", "ls-tree"])
            self.assertEqual((checkout / "assets/generated/manifest.json").read_bytes(), original)
            self.assertTrue((outbox / "assets/generated/test-scene.jpg").is_file())

    def test_materialization_reserves_disk_space_for_remote_growth(self):
        with tempfile.TemporaryDirectory() as temp:
            checkout = Path(temp)
            incoming = len(jpeg())
            tree = subprocess.CompletedProcess([], 0,
                f"100644 blob {'a' * 40} {incoming} assets/generated/test-scene.jpg\n", "")
            remaining = 12 * 1024**3 + 32 * 1024**2 + incoming - 1
            with patch.object(sync, "git", return_value=tree), \
                 patch.object(sync, "require_image_budget"), patch.object(sync, "check_storage"), \
                 patch.object(sync.shutil, "disk_usage", return_value=SimpleNamespace(free=remaining)):
                with self.assertRaisesRegex(RuntimeError, "materialize remote JPEGs"):
                    sync.check_checkout_budget(checkout, "FETCH_HEAD", 12)


if __name__ == "__main__":
    unittest.main()
