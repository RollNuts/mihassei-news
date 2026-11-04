"""Idle and outbox recovery must not load/checksum the model or start ComfyUI."""
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import local_scene_photos as photos, local_scene_sync as sync
from scripts import mac_scene_setup as setup, mac_scene_worker as worker
from scripts import generated_photos as generated
from tests import test_scene_recovery as fixture


class PrepareSyncGitTests(unittest.TestCase):
    setUp = fixture.SceneGitIntegrationTests.setUp
    tearDown = fixture.SceneGitIntegrationTests.tearDown
    run_git = fixture.SceneGitIntegrationTests.run_git
    checkout_remote = fixture.SceneGitIntegrationTests.checkout_remote

    def prepare(self):
        self.args.prepare_only = True
        with patch.object(sync, 'generate', side_effect=AssertionError('ComfyUI generation called')):
            return sync.sync(self.args)

    def publish_fixture(self, *, held=False):
        publisher = self.checkout_remote('publisher')
        if held:
            manifest = publisher / 'assets/generated/manifest.json'
            manifest.write_text(json.dumps({'version': 1, 'images': {}, 'rejected': {
                fixture.article()['slug']: {'article_sha256': generated.article_fingerprint(fixture.article())}
            }}))
        else:
            photos.write_generated(fixture.article(), fixture.jpeg(), fixture.MODEL, root=publisher)
        self.run_git('add', '.', cwd=publisher)
        self.run_git('commit', '-m', 'CPU completed or held scene', cwd=publisher)
        self.run_git('push', 'origin', 'main', cwd=publisher)
        return publisher

    def test_all_generated_is_idle_without_generation_or_user_head_changes(self):
        self.publish_fixture()
        (self.repo / 'keep.txt').write_text('unsaved edit')
        self.assertEqual(self.prepare(), 0)
        self.assertEqual((self.repo / 'keep.txt').read_text(), 'unsaved edit')
        self.assertEqual(self.run_git('rev-parse', 'HEAD', cwd=self.repo).stdout.strip(), self.initial)
        self.assertEqual(list(self.app.glob('sync-*')), [])

    def test_held_scene_does_not_trigger_gpu(self):
        self.publish_fixture(held=True)
        self.assertEqual(self.prepare(), 0)

    def test_new_remote_article_after_cpu_completion_requires_gpu(self):
        publisher = self.publish_fixture()
        self.assertEqual(self.prepare(), 0)
        (publisher / 'content/articles.json').write_text(json.dumps([fixture.article(), fixture.article('new-story')]))
        self.run_git('add', '.', cwd=publisher)
        self.run_git('commit', '-m', 'new story after idle poll', cwd=publisher)
        self.run_git('push', 'origin', 'main', cwd=publisher)
        self.assertEqual(self.prepare(), sync.PREPARE_NEEDS_GPU)
        self.assertEqual(self.run_git('rev-parse', 'HEAD', cwd=self.repo).stdout.strip(), self.initial)

    def test_saved_outbox_is_published_without_any_comfy_server(self):
        photos.write_generated(fixture.article(), fixture.jpeg(), fixture.MODEL, root=self.outbox)
        self.args.server = 'http://127.0.0.1:1'
        self.assertEqual(self.prepare(), 0)
        published = self.checkout_remote('verify')
        self.assertTrue(generated.usable_generated_scene(fixture.article(), root=published))
        self.assertFalse((self.outbox / 'assets/generated/one-story.jpg').exists())
        self.assertEqual(self.run_git('rev-parse', 'HEAD', cwd=self.repo).stdout.strip(), self.initial)

    def test_busy_sync_lock_defers_preflight_instead_of_claiming_fresh_idle(self):
        self.app.mkdir(parents=True, exist_ok=True)
        with (self.app / 'sync.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.prepare(), sync.PREPARE_DEFERRED)
            self.args.prepare_only = False
            with patch.object(sync, 'generate', side_effect=AssertionError('busy job generated')):
                self.assertEqual(sync.sync(self.args), 0)

    def test_fetch_failure_never_reports_idle_and_preserves_outbox(self):
        photos.write_generated(fixture.article(), fixture.jpeg(), fixture.MODEL, root=self.outbox)
        actual = sync.git
        def offline(*args, **kwargs):
            if args[0] == 'fetch':
                raise subprocess.CalledProcessError(128, args, stderr='authentication failed')
            return actual(*args, **kwargs)
        with patch.object(sync, 'git', side_effect=offline):
            with self.assertRaises(subprocess.CalledProcessError): self.prepare()
        self.assertTrue(generated.usable_generated_scene(fixture.article(), root=self.outbox))


class IdleWorkerTests(unittest.TestCase):
    def context(self, stack, root, *, installed=False):
        app, cache = root / 'app', root / 'cache'
        app.mkdir()
        cache.mkdir()
        if installed:
            for relative in ('ComfyUI/main.py', 'venv/bin/python', 'install-ok.txt',
                             'ComfyUI/models/checkpoints/sd_xl_base_1.0.safetensors'):
                path = app / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
        stack.enter_context(patch.object(worker, 'ROOT', root))
        stack.enter_context(patch.object(worker, 'APP_DIR', app))
        stack.enter_context(patch.object(worker, 'CACHE_DIR', cache))
        stack.enter_context(patch.object(worker, 'storage_ok', return_value=True))
        stack.enter_context(patch.object(worker, 'generated_mib', return_value=0))
        register = stack.enter_context(patch.object(worker.control, 'register_runtime'))
        clear = stack.enter_context(patch.object(worker.control, 'clear_runtime'))
        return app, register, clear

    def test_fresh_idle_never_checks_model_or_starts_server_even_without_installation(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            app, _, _ = self.context(stack, Path(temp))
            worker.update_status('ok', 'last actual publish')
            worker.update_status('skipped', 'past real error')
            history = json.loads((app / 'last-run.json').read_text())
            prep = stack.enter_context(patch.object(worker, 'prepare_sync', return_value=0))
            verify = stack.enter_context(patch.object(setup, 'verify_model', side_effect=AssertionError('7GB checksum')))
            spawn = stack.enter_context(patch.object(worker.subprocess, 'Popen', side_effect=AssertionError('server spawned')))
            guard = stack.enter_context(patch.object(worker.resources, 'Guard', side_effect=AssertionError('GPU guard entered')))
            self.assertEqual(worker.run_once(), 0)
            prep.assert_called_once_with(12)
            verify.assert_not_called(); spawn.assert_not_called(); guard.assert_not_called()
            result = json.loads((app / 'last-run.json').read_text())
            self.assertEqual(result['status'], 'idle')
            self.assertEqual(result['last_success_at'], history['last_success_at'])
            self.assertEqual(result['last_error'], history['last_error'])
            self.assertFalse((app / 'stopped.json').exists())

    def test_outbox_preparation_precedes_unsafe_gpu_guard(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            app, _, _ = self.context(stack, Path(temp), installed=True)
            events = []
            stack.enter_context(patch.object(worker, 'prepare_sync', side_effect=lambda _: events.append('outbox-prepared') or worker.PREPARE_NEEDS_GPU))
            guard = Mock()
            def unsafe(**kwargs):
                events.append('GPU-guard')
                raise worker.resources.ResourceDeferred('swap high')
            guard.check.side_effect = unsafe
            stack.enter_context(patch.object(worker.resources, 'Guard', return_value=guard))
            verify = stack.enter_context(patch.object(setup, 'verify_model'))
            spawn = stack.enter_context(patch.object(worker.subprocess, 'Popen'))
            with contextlib.redirect_stderr(io.StringIO()): self.assertEqual(worker.main([]), 0)
            self.assertEqual(events, ['outbox-prepared', 'GPU-guard'])
            verify.assert_not_called(); spawn.assert_not_called()
            self.assertEqual(json.loads((app / 'last-run.json').read_text())['status'], 'resource-paused')

    def test_preparation_failure_is_error_not_idle_and_never_starts_gpu(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            app, _, _ = self.context(stack, Path(temp))
            stack.enter_context(patch.object(worker, 'prepare_sync', side_effect=RuntimeError('fresh-main fetch failed')))
            verify = stack.enter_context(patch.object(setup, 'verify_model'))
            spawn = stack.enter_context(patch.object(worker.subprocess, 'Popen'))
            with contextlib.redirect_stderr(io.StringIO()): self.assertEqual(worker.main([]), 1)
            self.assertEqual(json.loads((app / 'last-run.json').read_text())['status'], 'skipped')
            verify.assert_not_called(); spawn.assert_not_called()

    def test_sigterm_during_owned_preflight_stops_group_and_preserves_outbox(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            app, register, clear = self.context(stack, Path(temp))
            (app / 'pending').mkdir()
            (app / 'pending/saved.jpg').write_bytes(b'keep saved JPEG')
            process = Mock(pid=12345)
            process.poll.return_value = None
            spawn = stack.enter_context(patch.object(worker.subprocess, 'Popen', return_value=process))
            kill = stack.enter_context(patch.object(worker.os, 'killpg'))
            verify = stack.enter_context(patch.object(setup, 'verify_model'))
            def interrupted(_):
                (app / 'stopped.json').write_text('{"reason":"explicit stop"}')
                os.kill(os.getpid(), signal.SIGTERM)
            stack.enter_context(patch.object(worker.time, 'sleep', side_effect=interrupted))
            with contextlib.redirect_stderr(io.StringIO()): self.assertEqual(worker.main([]), 1)
            self.assertEqual(spawn.call_count, 1)
            self.assertIn('--prepare-only', spawn.call_args.args[0])
            self.assertEqual([call.args[0] for call in register.call_args_list], ['worker', 'sync'])
            kill.assert_called_once_with(12345, signal.SIGTERM)
            process.wait.assert_called_once_with(timeout=20)
            clear.assert_called()
            verify.assert_not_called()
            self.assertEqual((app / 'pending/saved.jpg').read_bytes(), b'keep saved JPEG')
            self.assertEqual(json.loads((app / 'last-run.json').read_text())['status'], 'stopped')

    def test_preflight_nonzero_and_deadline_are_bounded_and_reap_owned_group(self):
        for returncode in (1, None):
            with self.subTest(returncode=returncode), tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
                self.context(stack, Path(temp))
                process = Mock(pid=12345, returncode=returncode)
                process.poll.return_value = returncode
                stack.enter_context(patch.object(worker.subprocess, 'Popen', return_value=process))
                stop = stack.enter_context(patch.object(worker, 'stop_process'))
                if returncode is None:
                    stack.enter_context(patch.object(worker.time, 'monotonic', side_effect=[0, 999]))
                with self.assertRaisesRegex(RuntimeError, 'preflight failed|preflight timed out'):
                    worker.prepare_sync(12)
                stop.assert_called_once_with(process, group=True)


if __name__ == '__main__': unittest.main()
