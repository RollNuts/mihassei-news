"""CPU bundle publication gates using local Git fixtures, never network or GPU."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import publish_cpu_scenes as publish
from scripts import local_scene_sync as sync
from scripts.local_scene_photos import write_generated, stable_seed

ROOT = Path(__file__).resolve().parents[1]
GIT = "/Library/Developer/CommandLineTools/usr/bin/git"
if not Path(GIT).is_file():
    GIT = shutil.which("git") or "git"


class CpuPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        images = list((ROOT / "assets/generated").glob("*.jpg"))
        cls.jpeg = images[0].read_bytes()
        cls.other_jpeg = next(path.read_bytes() for path in images[1:]
                              if path.read_bytes() != cls.jpeg)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cpu-bundle-test-")
        self.base = Path(self.temp.name)
        self.article = {"slug": "cpu-fixture-news", "title": "A fictional fixture",
                        "summary": "A short comedy", "category": "社会",
                        "paragraphs": ["A fictional article."],
                        "scene_prompt": "A bright dinner plate beside a volleyball."}

    def tearDown(self):
        self.temp.cleanup()

    def bundle(self, *, article=None, name="bundle", data=None):
        article = article or self.article
        source = self.base / name
        (source / "content").mkdir(parents=True)
        (source / "content/articles.json").write_text(json.dumps([article], ensure_ascii=False))
        write_generated(article, data or self.jpeg, publish.MODEL, root=source, origin=publish.ORIGIN)
        return source

    def edit_manifest(self, source, edit):
        path = source / "assets/generated/manifest.json"
        payload = json.loads(path.read_text())
        edit(payload)
        path.write_text(json.dumps(payload))

    def digest_tree(self, root):
        return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob("*") if p.is_file()}

    def git(self, *args, cwd):
        env = {**os.environ, "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@test.invalid",
               "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@test.invalid",
               "PATH": str(Path(GIT).parent) + ":" + os.environ.get("PATH", "")}
        return subprocess.run([GIT, *args], cwd=cwd, env=env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def repository(self, *, article=None, held=False):
        article = article or self.article
        repo, remote = self.base / "repo", self.base / "remote.git"
        repo.mkdir()
        self.git("init", "--initial-branch=main", cwd=repo)
        self.git("init", "--bare", str(remote), cwd=self.base)
        (repo / "content").mkdir()
        (repo / "content/articles.json").write_text(json.dumps([article], ensure_ascii=False))
        (repo / "assets/generated").mkdir(parents=True)
        (repo / "assets/generated/manifest.json").write_text('{"version":1,"images":{}}')
        if held:
            write_generated(article, self.jpeg, publish.MODEL, root=repo, origin=publish.ORIGIN)
            def hold(payload):
                payload["rejected"] = {article["slug"]: {
                    "article_sha256": payload["images"][article["slug"]]["article_sha256"],
                    "reason": "Visual mismatch"}}
            self.edit_manifest(repo, hold)
        self.git("add", ".", cwd=repo)
        self.git("commit", "-m", "fixture", cwd=repo)
        self.git("remote", "add", "origin", str(remote), cwd=repo)
        self.git("push", "-u", "origin", "main", cwd=repo)
        (repo / "keep-user.txt").write_text("uncommitted user file")
        return repo, remote

    @contextlib.contextmanager
    def fixture_publication(self):
        runner = self.base / "runner"
        runner.mkdir()
        # The production exact-GitHub remote guard is bypassed only for this
        # local bare repository fixture; no test can push to the real remote.
        with patch.object(publish, "trusted_ci", return_value=True), \
             patch.object(sync, "safe_to_publish"), \
             patch.dict(os.environ, {"RUNNER_TEMP": str(runner),
                                     "PATH": str(Path(GIT).parent) + ":" + os.environ.get("PATH", "")}):
            yield runner

    def test_default_mode_validates_exact_bundle_without_git_or_writes(self):
        source = self.bundle()
        before = self.digest_tree(source)
        output = io.StringIO()
        with patch.object(sync, "git", side_effect=AssertionError("network/Git used")), \
             contextlib.redirect_stdout(output):
            self.assertEqual(publish.main(["--source", str(source)]), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "validated")
        self.assertEqual(self.digest_tree(source), before)

    def test_corruption_wrong_origin_and_rounded_seed_are_refused(self):
        changes = [lambda x: x.update(origin="local-comfyui"),
                   lambda x: x.update(seed=float(x["seed"])),
                   lambda x: x.update(sha256="0" * 64),
                   lambda x: x.update(article_sha256="0" * 64)]
        for index, change in enumerate(changes):
            source = self.bundle(name="bundle" + str(index))
            self.edit_manifest(source, lambda payload: change(payload["images"][self.article["slug"]]))
            with self.assertRaises(ValueError): publish.validate_bundle(source)
        source = self.bundle(name="corrupt-jpeg")
        (source / "assets/generated" / (self.article["slug"] + ".jpg")).write_bytes(b"corrupted")
        with self.assertRaises(ValueError): publish.validate_bundle(source)

    def test_extra_file_symlink_duplicate_and_oversized_batch_are_refused(self):
        source = self.bundle()
        (source / "other.txt").write_text("unrelated")
        with self.assertRaisesRegex(ValueError, "Unexpected"): publish.validate_bundle(source)
        (source / "other.txt").unlink()
        (source / "alias").symlink_to(source / "content")
        with self.assertRaisesRegex(ValueError, "Symlink"): publish.validate_bundle(source)
        (source / "alias").unlink()
        articles = source / "content/articles.json"
        articles.write_text(json.dumps([self.article, self.article]))
        with self.assertRaisesRegex(ValueError, "duplicate"): publish.validate_bundle(source)
        articles.write_text(json.dumps([self.article] * 4))
        with self.assertRaisesRegex(ValueError, "one to three"): publish.validate_bundle(source)

    def test_publish_requires_main_public_ci_and_preserves_original_globals(self):
        source = self.bundle()
        original = sync.ROOT, sync.APP_DIR, sync.OUTBOX, sync.transfer_scenes
        with patch.object(publish, "trusted_ci", return_value=False), \
             patch.object(sync, "git", side_effect=AssertionError("Git used")):
            with self.assertRaisesRegex(RuntimeError, "public main"): publish.publish_bundle(source)
        self.assertEqual((sync.ROOT, sync.APP_DIR, sync.OUTBOX, sync.transfer_scenes), original)
        self.assertFalse((self.base / "runner").exists())

    def test_ci_guard_rejects_private_pr_and_fork_events(self):
        event = self.base / "event.json"
        public = {"repository": {"private": False, "full_name": "RollNuts/mihassei-news"}}
        event.write_text(json.dumps(public))
        env = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "RollNuts/mihassei-news",
               "GITHUB_REF": "refs/heads/main", "RUNNER_OS": "Linux", "VERIPSA_ENABLE_FREE_CPU": "1",
               "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_EVENT_PATH": str(event)}
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(publish.trusted_ci())
            with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request"}):
                self.assertFalse(publish.trusted_ci())
            event.write_text(json.dumps({"repository": {"private": True, "full_name": "RollNuts/mihassei-news"}}))
            self.assertFalse(publish.trusted_ci())
            event.write_text(json.dumps({"repository": {"private": False, "full_name": "fork/mihassei-news"}}))
            self.assertFalse(publish.trusted_ci())

    def test_real_local_push_changes_only_image_files_and_preserves_source(self):
        source = self.bundle()
        repo, remote = self.repository()
        before = self.digest_tree(source)
        local_head = self.git("rev-parse", "HEAD", cwd=repo)
        original = sync.ROOT, sync.APP_DIR, sync.OUTBOX, sync.transfer_scenes
        with self.fixture_publication() as runner:
            result = publish.publish_bundle(source, root=repo)
            self.assertEqual(list(runner.iterdir()), [])
        self.assertEqual(result["status"], "ok")
        head = self.git("rev-parse", "main", cwd=remote)
        self.assertEqual(result["publish"]["sha"], head)
        changed = self.git("diff-tree", "--no-commit-id", "--name-only", "-r", head, cwd=remote).splitlines()
        self.assertEqual(set(changed), {"assets/generated/manifest.json", "assets/generated/" + self.article["slug"] + ".jpg"})
        manifest = json.loads(self.git("show", "main:assets/generated/manifest.json", cwd=remote))
        record = manifest["images"][self.article["slug"]]
        self.assertEqual(record["origin"], publish.ORIGIN)
        self.assertEqual(record["seed"], stable_seed(self.article))
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=repo), local_head)
        self.assertEqual((repo / "keep-user.txt").read_text(), "uncommitted user file")
        self.assertEqual(self.digest_tree(source), before)
        self.assertEqual((sync.ROOT, sync.APP_DIR, sync.OUTBOX, sync.transfer_scenes), original)

    def test_main_article_edit_drops_stale_bundle_without_touching_user_checkout(self):
        source = self.bundle()
        repo, remote = self.repository(article={**self.article, "title": "A changed article"})
        before = self.git("rev-parse", "main", cwd=remote)
        with self.fixture_publication():
            result = publish.publish_bundle(source, root=repo)
        self.assertEqual(result["status"], "ok")
        self.assertIn("No unpublished matching", result["publish"]["detail"])
        self.assertEqual(self.git("rev-parse", "main", cwd=remote), before)
        self.assertEqual(publish.validate_bundle(source), [self.article])

    def test_held_same_jpeg_cannot_be_reapproved_on_latest_main_transfer(self):
        source = self.bundle()
        repo, remote = self.repository(held=True)
        before = self.git("rev-parse", "main", cwd=remote)
        original = sync.ROOT, sync.APP_DIR, sync.OUTBOX, sync.transfer_scenes
        with self.fixture_publication() as runner:
            with self.assertRaisesRegex(ValueError, "Held JPEG"):
                publish.publish_bundle(source, root=repo)
            self.assertEqual(list(runner.iterdir()), [])
        self.assertEqual(self.git("rev-parse", "main", cwd=remote), before)
        self.assertEqual(publish.validate_bundle(source), [self.article])
        self.assertEqual((sync.ROOT, sync.APP_DIR, sync.OUTBOX, sync.transfer_scenes), original)

    def test_stale_same_jpeg_cannot_receive_a_new_article_fingerprint(self):
        changed = {**self.article, "title": "A changed article"}
        source = self.bundle(article=changed)
        target = self.bundle(name="old-article-image")
        with self.assertRaisesRegex(ValueError, "Stale JPEG"):
            publish.reject_held_reapproval(source, target, [changed])

    def test_new_jpeg_replaces_held_image(self):
        source = self.bundle(data=self.other_jpeg)
        repo, remote = self.repository(held=True)
        with self.fixture_publication():
            result = publish.publish_bundle(source, root=repo)
        self.assertEqual(result["status"], "ok")
        manifest = json.loads(self.git("show", "main:assets/generated/manifest.json", cwd=remote))
        self.assertNotIn(self.article["slug"], manifest.get("rejected", {}))
        self.assertEqual(manifest["images"][self.article["slug"]]["sha256"],
                         hashlib.sha256(self.other_jpeg).hexdigest())

    def test_failed_push_is_bounded_and_source_bundle_survives_for_artifacts(self):
        source = self.bundle()
        repo, remote = self.repository()
        before = self.digest_tree(source)
        remote_head = self.git("rev-parse", "main", cwd=remote)
        original_git = sync.git
        pushes = []

        def rejected_push(*args, **kwargs):
            if args[0] == "push":
                pushes.append(args)
                return subprocess.CompletedProcess(args, 1, "", "fixture authentication failure")
            return original_git(*args, **kwargs)

        with self.fixture_publication() as runner, patch.object(sync, "git", side_effect=rejected_push):
            result = publish.publish_bundle(source, root=repo)
            self.assertEqual(list(runner.iterdir()), [])
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(len(pushes), 3)
        self.assertTrue(all(args == ("push", "origin", "HEAD:refs/heads/main") for args in pushes))
        self.assertEqual(self.git("rev-parse", "main", cwd=remote), remote_head)
        self.assertEqual(self.digest_tree(source), before)


if __name__ == "__main__":
    unittest.main()
