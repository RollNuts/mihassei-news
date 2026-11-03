"""Stop-latch integration tests using local bare Git; no GPU or external service."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import generated_photos as generated, local_scene_sync as sync
from tests import test_scene_recovery as fixture


class StopPublicationTests(unittest.TestCase):
    # Reuse only the local-Git fixture lifecycle, not its test methods.
    setUp = fixture.SceneGitIntegrationTests.setUp
    tearDown = fixture.SceneGitIntegrationTests.tearDown
    run_git = fixture.SceneGitIntegrationTests.run_git
    checkout_remote = fixture.SceneGitIntegrationTests.checkout_remote
    generate_one = fixture.SceneGitIntegrationTests.generate_one

    def latch_stop(self):
        self.app.mkdir(parents=True, exist_ok=True)
        (self.app / "stopped.json").write_text('{"stopped":true}\n')

    def test_stopped_run_does_not_even_start_git_or_generation(self):
        self.latch_stop()
        with patch.object(sync, "git", side_effect=AssertionError("git started while stopped")), \
             patch.object(sync, "generate", side_effect=AssertionError("generation started while stopped")):
            with self.assertRaisesRegex(RuntimeError, "OFF|[Ss]top"):
                sync.sync(self.args)
        self.assertEqual(self.run_git("rev-parse", "HEAD", cwd=self.repo).stdout.strip(), self.initial)

    def test_stop_after_generation_saves_successes_without_publishing_then_resumes(self):
        def finish_then_stop(args, checkout):
            self.generate_one(args, checkout)
            self.latch_stop()
            return 0

        with patch.object(sync, "generate", side_effect=finish_then_stop):
            with self.assertRaisesRegex(RuntimeError, "OFF|[Ss]top"):
                sync.sync(self.args)
        self.assertTrue(generated.usable_generated_scene(fixture.article(), root=self.outbox))
        before = self.checkout_remote("before-resume")
        self.assertFalse(generated.usable_generated_scene(fixture.article(), root=before))
        (self.app / "stopped.json").unlink()

        def saved_image_is_reused(args, checkout):
            self.assertTrue(generated.usable_generated_scene(fixture.article(), root=checkout))
            return 0

        with patch.object(sync, "generate", side_effect=saved_image_is_reused):
            self.assertEqual(sync.sync(self.args), 0)
        self.assertTrue(generated.usable_generated_scene(fixture.article(), root=self.checkout_remote("resumed")))
        self.assertEqual(list(self.app.glob("sync-*")), [])

    def test_stop_during_commit_is_rechecked_immediately_before_push(self):
        real_git = sync.git
        pushes = []

        def stop_after_commit(*args, **kwargs):
            if args[0] == "push":
                pushes.append(args)
            result = real_git(*args, **kwargs)
            if "commit" in args:
                self.latch_stop()
            return result

        with patch.object(sync, "generate", side_effect=self.generate_one), \
             patch.object(sync, "git", side_effect=stop_after_commit):
            with self.assertRaisesRegex(RuntimeError, "OFF|[Ss]top"):
                sync.sync(self.args)
        self.assertEqual(pushes, [])
        self.assertTrue(generated.usable_generated_scene(fixture.article(), root=self.outbox))
        self.assertFalse(generated.usable_generated_scene(fixture.article(), root=self.checkout_remote("not-pushed")))

    def test_stop_between_outer_guard_and_push_spawn_is_still_blocked(self):
        real_git = sync.git

        def stop_just_before_spawn(*args, **kwargs):
            if args[0] == "push":
                self.latch_stop()
            return real_git(*args, **kwargs)

        with patch.object(sync, "generate", side_effect=self.generate_one), \
             patch.object(sync, "git", side_effect=stop_just_before_spawn):
            with self.assertRaisesRegex(RuntimeError, "OFF|[Ss]top"):
                sync.sync(self.args)
        self.assertTrue(generated.usable_generated_scene(fixture.article(), root=self.outbox))
        self.assertFalse(generated.usable_generated_scene(fixture.article(), root=self.checkout_remote("spawn-blocked")))

    def test_already_inflight_push_is_recorded_and_outbox_is_reconciled_after_resume(self):
        real_git = sync.git

        def push_completes_while_stop_arrives(*args, **kwargs):
            result = real_git(*args, **kwargs)
            if args[0] == "push":
                self.latch_stop()
            return result

        with patch.object(sync, "generate", side_effect=self.generate_one), \
             patch.object(sync, "git", side_effect=push_completes_while_stop_arrives):
            with self.assertRaisesRegex(RuntimeError, "OFF|[Ss]top"):
                sync.sync(self.args)
        published = self.checkout_remote("inflight-published")
        self.assertTrue(generated.usable_generated_scene(fixture.article(), root=published))
        # The stop request does not erase pending copies while publication is in flight.
        self.assertTrue(generated.usable_generated_scene(fixture.article(), root=self.outbox))
        result = json.loads((self.app / "publish-result.json").read_text())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sha"], self.run_git("rev-parse", "HEAD", cwd=published).stdout.strip())
        (self.app / "stopped.json").unlink()
        with patch.object(sync, "generate", side_effect=self.generate_one):
            self.assertEqual(sync.sync(self.args), 0)
        self.assertFalse((self.outbox / "assets/generated/one-story.jpg").exists())

    def test_stop_keeps_existing_root_lock_and_user_files(self):
        shared_lock = self.repo / ".git/index.lock"
        shared_lock.write_text("unrelated editor owns this lock")
        self.latch_stop()
        with self.assertRaisesRegex(RuntimeError, "OFF|[Ss]top"):
            sync.sync(self.args)
        self.assertEqual(shared_lock.read_text(), "unrelated editor owns this lock")
        self.assertEqual((self.repo / "keep.txt").read_text(), "user file")

    def test_interrupted_private_image_stage_and_index_lock_do_not_wedge_resume(self):
        real_transfer = sync.transfer_scenes

        def interrupted_save(source, target, articles, **kwargs):
            if source.name == "checkout" and generated.usable_generated_scene(fixture.article(), root=source):
                raise OSError("interrupted before durable outbox save")
            return real_transfer(source, target, articles, **kwargs)

        def generate_and_leave_partial_stage(args, checkout):
            self.generate_one(args, checkout)
            image_dir = checkout / "assets/generated"
            (image_dir / "another-story.jpg.tmp").write_bytes(b"partial JPEG")
            (image_dir / "manifest.json.tmp").write_text('{"version":1,"images":')
            return 0

        with patch.object(sync, "generate", side_effect=generate_and_leave_partial_stage), \
             patch.object(sync, "transfer_scenes", side_effect=interrupted_save):
            with self.assertRaisesRegex(OSError, "interrupted"):
                sync.sync(self.args)
        abandoned = list(self.app.glob("sync-*/checkout"))[0]
        # SIGKILL can leave a lock in this owned worktree's private Git metadata.
        metadata = self.run_git("rev-parse", "--absolute-git-dir", cwd=abandoned).stdout.strip()
        (Path(metadata) / "index.lock").write_text("abandoned private lock")
        shared_lock = self.repo / ".git/index.lock"
        shared_lock.write_text("unrelated user editor")
        with patch.object(sync, "generate", side_effect=self.generate_one):
            self.assertEqual(sync.sync(self.args), 0)
        self.assertEqual(shared_lock.read_text(), "unrelated user editor")
        self.assertEqual(list(self.app.glob("sync-*")), [])
        self.assertTrue(generated.usable_generated_scene(fixture.article(), root=self.checkout_remote("stage-recovered")))


if __name__ == "__main__":
    unittest.main()
