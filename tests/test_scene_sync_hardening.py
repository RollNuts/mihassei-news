"""Regression tests for outbox preservation and remote-checkout storage gates."""
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import local_scene_photos as photos, local_scene_sync as sync
from scripts import generated_photos as generated
from tests import test_scene_recovery as fixture


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


class AbandonedCheckoutTests(unittest.TestCase):
    # All Git writes/publication below stay in disposable local fixture repos.
    setUp = fixture.SceneGitIntegrationTests.setUp
    tearDown = fixture.SceneGitIntegrationTests.tearDown
    run_git = fixture.SceneGitIntegrationTests.run_git
    checkout_remote = fixture.SceneGitIntegrationTests.checkout_remote

    def job(self, name, *, worktree=True):
        job = self.app / ("sync-" + name)
        job.mkdir(parents=True)
        (job / ".veripsa-scene-job").write_text("veripsa-local-scenes-v2\n")
        checkout = job / "checkout"
        if worktree:
            self.run_git("worktree", "add", "--detach", str(checkout), "HEAD", cwd=self.repo)
        else:
            checkout.mkdir()
        return job, checkout

    def snapshot(self, job):
        return {str(p.relative_to(job)): ("link", str(p.readlink())) if p.is_symlink() else
                ("file", p.read_bytes()) if p.is_file() else ("directory", b"")
                for p in job.rglob("*")}

    def prepare(self):
        self.args.prepare_only = True
        calls = []
        actual = sync.git
        def observe(*args, **kwargs):
            calls.append((args, kwargs.get("cwd")))
            return actual(*args, **kwargs)
        with patch.object(sync, "git", side_effect=observe), \
             patch.object(sync, "generate", side_effect=AssertionError("generation started")), \
             contextlib.redirect_stderr(io.StringIO()) as warning:
            result = sync.sync(self.args)
        return result, calls, warning.getvalue()

    def assert_quarantined(self, job, checkout, before, calls, warning):
        self.assertEqual(self.snapshot(job), before)
        self.assertIn(str(checkout), warning)
        self.assertIn("quarantined in place", warning)
        self.assertIn("JPEG candidates preserved; manual recovery pending", warning)
        self.assertFalse(any(args[0] == "diff" and cwd == checkout for args, cwd in calls))
        self.assertFalse(any(args[:2] == ("worktree", "remove") and str(checkout) in args for args, cwd in calls))
        self.assertTrue(any(args[:2] == ("worktree", "add") for args, cwd in calls))
        self.assertEqual(self.run_git("rev-parse", "HEAD", cwd=self.repo).stdout.strip(), self.initial)

    def test_missing_git_file_preserves_candidates_marker_and_unknown_data_then_continues(self):
        job, checkout = self.job("missing-git")
        photos.write_generated(fixture.article(), jpeg(), MODEL, root=checkout)
        (checkout / ".git").unlink()
        (checkout / "unknown.data").write_bytes(b"unclassified recovery data")
        before = self.snapshot(job)
        result, calls, warning = self.prepare()
        self.assertEqual(result, sync.PREPARE_NEEDS_GPU)
        self.assert_quarantined(job, checkout, before, calls, warning)
        self.assertFalse((self.outbox / "assets/generated/one-story.jpg").exists())

    def test_missing_private_head_is_preserved_without_diff_or_forced_removal(self):
        job, checkout = self.job("missing-head")
        metadata = Path(self.run_git("rev-parse", "--absolute-git-dir", cwd=checkout).stdout.strip())
        (metadata / "HEAD").unlink()
        (checkout / "candidate.jpg").write_bytes(b"unknown JPEG candidate")
        before = self.snapshot(job)
        result, calls, warning = self.prepare()
        self.assertEqual(result, sync.PREPARE_NEEDS_GPU)
        self.assert_quarantined(job, checkout, before, calls, warning)
        self.assertFalse((metadata / "HEAD").exists())

    def test_parent_repository_cannot_be_mistaken_for_candidate_top_level(self):
        # Git discovery succeeds here, but discovers the user's parent repo.
        self.app = self.repo / "state"
        self.outbox = self.app / "pending"
        with patch.object(sync, "APP_DIR", self.app), patch.object(sync, "OUTBOX", self.outbox):
            job, checkout = self.job("nested", worktree=False)
            (checkout / "unknown.txt").write_text("keep")
            discovered = Path(self.run_git("rev-parse", "--show-toplevel", cwd=checkout).stdout.strip())
            self.assertEqual(discovered.resolve(), self.repo.resolve())
            before = self.snapshot(job)
            result, calls, warning = self.prepare()
            self.assertEqual(result, sync.PREPARE_NEEDS_GPU)
            self.assert_quarantined(job, checkout, before, calls, warning)
            self.assertIn("Git top-level differs", warning)

    def test_valid_worktree_still_recovers_verified_photo_to_local_fixture_remote(self):
        job, checkout = self.job("valid")
        photos.write_generated(fixture.article(), jpeg(), MODEL, root=checkout)
        result, calls, warning = self.prepare()
        self.assertEqual(result, 0)
        self.assertEqual(warning, "")
        self.assertFalse(job.exists())
        published = self.checkout_remote("recovered")
        self.assertTrue(generated.usable_generated_scene(fixture.article(), root=published))
        self.assertEqual((published / "assets/generated/one-story.jpg").read_bytes(), jpeg())

    def test_unknown_marker_and_missing_checkout_keep_all_unknown_files(self):
        unknown, checkout = self.job("unknown-owner", worktree=False)
        (unknown / ".veripsa-scene-job").write_text("not our ownership marker\n")
        (checkout / "candidate.jpg").write_bytes(b"unknown")
        missing = self.app / "sync-missing-checkout"
        missing.mkdir()
        (missing / ".veripsa-scene-job").write_text("veripsa-local-scenes-v2\n")
        (missing / "notes.txt").write_text("unknown data")
        snapshots = {path: self.snapshot(path) for path in (unknown, missing)}
        result, calls, warning = self.prepare()
        self.assertEqual(result, sync.PREPARE_NEEDS_GPU)
        self.assertEqual(self.snapshot(unknown), snapshots[unknown])
        self.assert_quarantined(missing, missing / "checkout", snapshots[missing], calls, warning)
        self.assertFalse(any(cwd == checkout for args, cwd in calls))


if __name__ == "__main__":
    unittest.main()
