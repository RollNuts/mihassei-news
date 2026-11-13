"""Persistent OFF, scoped process ownership, launchd and exact workflow controls."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from unittest.mock import patch, Mock

from scripts import mac_scene_control as control
from scripts import mac_scene_worker as worker


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="未発生新聞-停止テスト-")
        self.base = Path(self.temp.name)
        self.app = self.base / 'app'
        self.app.mkdir()
        self.root = self.base / 'repo'
        (self.root / 'scripts').mkdir(parents=True)
        self.patches = [patch.object(control, 'APP_DIR', self.app), patch.object(control, 'ROOT', self.root)]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def test_stop_latch_blocks_new_worker_and_survives_new_reader(self):
        with patch.object(control, 'unload_schedule'), patch.object(control, 'stop_owned', return_value=[]):
            result = control.stop()
        self.assertEqual(result['automatic'], 'OFF')
        self.assertTrue((self.app / 'stopped.json').exists())
        with self.assertRaisesRegex(RuntimeError, 'OFF'):
            control.check_allowed(app_dir=self.app)
        # An independent interpreter would read exactly the same persisted marker.
        with patch.object(worker, 'APP_DIR', self.app):
            with self.assertRaisesRegex(RuntimeError, 'OFF'):
                worker.run_once()

    def test_stop_scope_is_latched_before_shutdown_and_cpu_requests(self):
        for all_images, scope in ((False, 'mac'), (True, 'all')):
            with self.subTest(scope=scope):
                phases = []
                def check_intent(phase):
                    self.assertEqual(control.read_json(self.app / 'stopped.json')['scope'], scope)
                    with self.assertRaisesRegex(RuntimeError, 'OFF'):
                        control.check_allowed(self.app)
                    phases.append(phase)
                with patch.object(control, 'unload_schedule', side_effect=lambda: check_intent('launchd')), \
                     patch.object(control, 'stop_owned', side_effect=lambda: check_intent('processes') or []), \
                     patch.object(control, 'stop_cpu', side_effect=lambda: check_intent('cpu') or {}):
                    result = control.stop(all_images=all_images)
                self.assertEqual(result['errors'], [])
                self.assertEqual(phases, ['launchd', 'processes', 'cpu'] if all_images else ['launchd', 'processes'])

    def test_corrupt_or_symlink_latch_cannot_enable_generation(self):
        latch = self.app / 'stopped.json'
        latch.write_text('invalid')
        with self.assertRaises(RuntimeError): control.check_allowed(self.app)
        latch.unlink()
        latch.symlink_to(self.app / 'not-present')
        with self.assertRaises(RuntimeError): control.check_allowed(self.app)

    def test_pid_reuse_or_command_change_is_never_signalled(self):
        saved = {'pid': 98765, 'pgid': 98765, 'uid': os.getuid(), 'started': 'old',
                 'command': str(self.root / 'scripts/local_scene_sync.py')}
        with patch.object(control, 'process_identity', return_value={**saved, 'started': 'new'}), \
             patch.object(control.os, 'killpg') as kill:
            self.assertFalse(control.signal_owned(saved, 'sync', self.root, self.app, signal.SIGTERM))
        kill.assert_not_called()

    def test_python_launcher_transition_preserves_exact_owned_script_arguments(self):
        app_python = str(Path(sys.base_prefix) / 'Resources/Python.app/Contents/MacOS/Python')
        initial = {'pid': 321, 'pgid': 321, 'uid': os.getuid(), 'started': 'same',
                   'command': sys.executable + ' ' + str(self.root / 'scripts/local_scene_sync.py') + ' --limit 4'}
        transitioned = {**initial, 'command': app_python + ' ' + str(self.root / 'scripts/local_scene_sync.py') + ' --limit 4'}
        with patch.object(control, 'process_identity', return_value=transitioned):
            self.assertIsNotNone(control.matching_process(initial, 'sync', self.root, self.app))
        for changed in ({**transitioned, 'started': 'reused'},
                        {**transitioned, 'command': transitioned['command'] + ' --unexpected'},
                        {**transitioned, 'command': '/unrelated/python ' + str(self.root / 'scripts/local_scene_sync.py') + ' --limit 4'}):
            with patch.object(control, 'process_identity', return_value=changed):
                self.assertIsNone(control.matching_process(initial, 'sync', self.root, self.app))

    def test_intentional_off_keeps_prior_real_error(self):
        with patch.object(worker, 'APP_DIR', self.app), contextlib.redirect_stderr(io.StringIO()):
            worker.update_status('skipped', 'previous network failure')
            control.write_json(self.app / 'stopped.json', {'reason': 'user'})
            self.assertEqual(worker.main([]), 1)
        result = control.read_json(self.app / 'last-run.json')
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(result['last_error']['detail'], 'previous network failure')

    def test_generic_comfy_and_lookalike_listen_are_rejected(self):
        owned = {'pid': 42, 'pgid': 42, 'uid': os.getuid(), 'started': 'today',
                 'command': sys.executable + ' ' + str(self.app / 'ComfyUI/main.py') + ' --listen 127.0.0.1 --port 18188'}
        self.assertTrue(control.expected_process(owned, 'server', self.root, self.app))
        self.assertFalse(control.expected_process({**owned, 'command': owned['command'].replace('127.0.0.1', '127.0.0.10')}, 'server', self.root, self.app))
        self.assertFalse(control.expected_process({**owned, 'command': owned['command'].replace(str(self.app), '/other-app')}, 'server', self.root, self.app))
        self.assertFalse(control.expected_process({**owned, 'pgid': 77}, 'server', self.root, self.app))

    def test_live_stop_leaves_unrelated_python_running(self):
        script = self.root / 'scripts/mac_scene_worker.py'
        script.write_text('import time\ntime.sleep(60)\n')
        owned = subprocess.Popen([sys.executable, str(script)], start_new_session=True)
        foreign = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
        try:
            control.register_runtime('worker', owned.pid, root=self.root, app_dir=self.app)
            self.assertEqual(control.stop_owned(app_dir=self.app, grace=2), ['worker'])
            owned.wait(timeout=3)
            self.assertIsNone(foreign.poll())
            self.assertFalse((self.app / 'runtime.json').exists())
        finally:
            for proc in (owned, foreign):
                if proc.poll() is None: proc.terminate()
                proc.wait(timeout=5)

    def test_native_identity_preserves_japanese_path_under_c_parent_locale(self):
        script = self.root / 'scripts/mac_scene_worker.py'
        script.write_text('import time\ntime.sleep(60)\n')
        process = subprocess.Popen([sys.executable, str(script)], start_new_session=True)
        try:
            with patch.dict(os.environ, {'LC_ALL': 'C', 'LANG': 'C'}):
                control.register_runtime('worker', process.pid, root=self.root, app_dir=self.app)
                identity = control.process_identity(process.pid)
                self.assertIn('未発生新聞-停止テスト-', identity['command'])
                self.assertTrue(control.has_arg(identity['command'], str(script)))
                self.assertIn(process.pid, control.group_members(os.getpgid(process.pid)))
                self.assertEqual(control.stop_owned(self.app, grace=2), ['worker'])
            process.wait(timeout=3)
        finally:
            if process.poll() is None: process.terminate()
            process.wait(timeout=5)

    def test_child_group_record_is_verified_before_capture(self):
        worker_record = {'pid': 22, 'pgid': 22, 'uid': os.getuid(), 'started': 'old', 'command': 'old'}
        control.write_json(self.app / 'runtime.json', {'root': str(self.root), 'processes': {'sync': worker_record}})
        with patch.object(control, 'matching_process', return_value=None), \
             patch.object(control, 'group_members', side_effect=AssertionError('unverified group queried')), \
             patch.object(control.os, 'kill') as kill:
            self.assertEqual(control.stop_owned(app_dir=self.app, grace=0), [])
        kill.assert_not_called()

    def test_stop_with_wrong_checkout_record_never_signals(self):
        control.write_json(self.app / 'runtime.json', {'root': '/other/repository', 'processes': {}})
        with patch.object(control.os, 'kill') as kill:
            with self.assertRaisesRegex(RuntimeError, 'another checkout'): control.stop_owned(self.app)
        kill.assert_not_called()

    def test_unload_addresses_label_even_without_plist(self):
        with patch.object(control, 'launch_loaded', return_value=True), \
             patch.object(control.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run, \
             patch.object(control.Path, 'home', return_value=self.base):
            control.unload_schedule()
        self.assertEqual(run.call_args.args[0], ['launchctl', 'bootout', f'gui/{os.getuid()}/{control.LABEL}'])

    def test_auth_failure_is_reported_while_local_stop_remains_off(self):
        with patch.object(control, 'unload_schedule'), patch.object(control, 'stop_owned', return_value=[]), \
             patch.object(control, 'stop_cpu', side_effect=RuntimeError('GitHub authentication failed')):
            result = control.stop(all_images=True)
        self.assertEqual(result['automatic'], 'OFF')
        self.assertIn('authentication failed', result['errors'][0])
        self.assertTrue((self.app / 'stopped.json').exists())
        self.assertEqual(control.read_json(self.app / 'stopped.json')['scope'], 'all')

    def test_cpu_stop_only_mutates_exact_workflow_and_its_runs(self):
        state = {'id': 7, 'path': '.github/workflows/free-cpu-scenes.yml', 'state': 'active'}
        calls = []
        def github(*args, **kwargs):
            calls.append(args)
            if 'disable' in args[-1]: return {}
            if args[-1].endswith('/cancel'): return {}
            if '/runs?per_page=' in args[-1]:
                return [{'workflow_runs': [{'id': 11, 'workflow_id': 7, 'status': 'completed' if any(a[-1].endswith('/cancel') for a in calls) else 'queued'}]}]
            if args[-1].endswith('/runs/11'): return {'status': 'completed'}
            raise AssertionError(args)
        with patch.object(control, 'cpu_state', side_effect=[state, {**state, 'state': 'disabled_manually'}]), \
             patch.object(control, 'gh', side_effect=github), \
             patch.object(control.time, 'monotonic', side_effect=range(0, 1000)), \
             patch.object(control.time, 'sleep'):
            result = control.stop_cpu(self.app)
        self.assertEqual(result['cancelled_or_completed'], [11])
        mutations = [a[-1] for a in calls if '--method' in a]
        self.assertEqual(mutations, ['repos/RollNuts/mihassei-news/actions/workflows/free-cpu-scenes.yml/disable',
                                     'repos/RollNuts/mihassei-news/actions/runs/11/cancel'])
        self.assertTrue(control.read_json(self.app / 'cpu-restore.json')['restore_active'])

    def test_cpu_stop_catches_late_run_and_status_transition_between_scans(self):
        state = {'id': 7, 'path': '.github/workflows/free-cpu-scenes.yml', 'state': 'active'}
        scans = iter([[], [{'id': 44, 'workflow_id': 7, 'status': 'queued'}],
                      [{'id': 44, 'workflow_id': 7, 'status': 'in_progress'}], [], []])
        calls = []
        checks = 0
        def github(*args, **kwargs):
            nonlocal checks
            calls.append(args)
            endpoint = args[-1]
            if endpoint.endswith('/disable') or endpoint.endswith('/cancel'): return {}
            if '/runs?per_page=' in endpoint:
                self.assertNotIn('status=', endpoint)
                return [{'workflow_runs': next(scans)}]
            if endpoint.endswith('/runs/44'):
                checks += 1
                return {'status': 'in_progress' if checks == 1 else 'completed'}
            raise AssertionError(args)
        clock = [0.0]
        def sleep(seconds): clock[0] += seconds
        with patch.object(control, 'cpu_state', side_effect=[state, {**state, 'state': 'disabled_manually'}]), \
             patch.object(control, 'gh', side_effect=github), \
             patch.object(control.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(control.time, 'sleep', side_effect=sleep):
            result = control.stop_cpu(self.app)
        self.assertEqual(result['cancelled_or_completed'], [44])
        self.assertEqual(sum(a[-1].endswith('/cancel') for a in calls), 1)
        self.assertGreaterEqual(checks, 2)
        self.assertGreaterEqual(clock[0], 5)

    def test_cpu_stop_reports_unfinished_cancellation_at_deadline(self):
        state = {'id': 7, 'path': '.github/workflows/free-cpu-scenes.yml', 'state': 'active'}
        def github(*args, **kwargs):
            if args[-1].endswith('/disable') or args[-1].endswith('/cancel'): return {}
            if '/runs?per_page=' in args[-1]:
                return [{'workflow_runs': [{'id': 55, 'workflow_id': 7, 'status': 'in_progress'}]}]
            if args[-1].endswith('/runs/55'): return {'status': 'in_progress'}
            raise AssertionError(args)
        clock = [0.0]
        def sleep(seconds): clock[0] += seconds
        with patch.object(control, 'cpu_state', return_value=state), patch.object(control, 'gh', side_effect=github), \
             patch.object(control.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(control.time, 'sleep', side_effect=sleep):
            with self.assertRaisesRegex(RuntimeError, 'did not settle|timed out'): control.stop_cpu(self.app)
        self.assertEqual(clock[0], 30)

    def test_cpu_run_from_different_workflow_never_cancelled(self):
        state = {'id': 7, 'path': '.github/workflows/free-cpu-scenes.yml', 'state': 'active'}
        def github(*args, **kwargs):
            if args[-1].endswith('/disable'): return {}
            if '/runs?' in args[-1]: return [{'workflow_runs': [{'id': 9, 'workflow_id': 99}]}]
            raise AssertionError('Unrelated run was mutated')
        with patch.object(control, 'cpu_state', return_value=state), patch.object(control, 'gh', side_effect=github):
            with self.assertRaisesRegex(RuntimeError, 'Unexpected workflow run'): control.stop_cpu(self.app)

    def test_resume_does_not_enable_previously_disabled_cpu(self):
        control.write_json(self.app / 'cpu-restore.json', {'restore_active': False})
        with patch.object(control, 'gh', side_effect=AssertionError('unexpected GitHub change')):
            self.assertEqual(control.resume_cpu(only_if_stopped=True, app_dir=self.app)['state'], 'unchanged')

    def test_quality_gate_needs_three_publicly_verified_images(self):
        self.assertFalse(control.quality_ready(self.app))
        data = {'approved': True, 'reviewed_at': 'today', 'published_commit': 'a' * 40,
                'model_sha256': control.MODEL_SHA, 'images': {'one': 'b' * 64, 'two': 'c' * 64, 'three': 'd' * 64}}
        control.write_json(self.app / 'quality-pilot.json', data)
        self.assertTrue(control.quality_ready(self.app))
        data['images'].pop('three')
        control.write_json(self.app / 'quality-pilot.json', data)
        self.assertFalse(control.quality_ready(self.app))

    def test_resume_missing_pilot_keeps_stop_latch(self):
        control.write_json(self.app / 'stopped.json', {'reason': 'user'})
        with self.assertRaisesRegex(RuntimeError, 'pilot'): control.resume()
        self.assertTrue((self.app / 'stopped.json').exists())

    def test_resume_failure_rolls_back_local_schedule_and_preserves_data(self):
        from scripts import mac_scene_setup as setup
        control.write_json(self.app / 'stopped.json', {'reason': 'user'})
        (self.app / 'keep.model').write_bytes(b'keep')
        with patch.object(control, 'quality_ready', return_value=True), \
             patch.object(setup, 'enable_schedule', side_effect=RuntimeError('launchctl failed')), \
             patch.object(control, 'unload_schedule') as unload, patch.object(control, 'stop_owned', return_value=[]):
            with self.assertRaisesRegex(RuntimeError, 'launchctl failed'): control.resume()
        unload.assert_called_once()
        self.assertEqual((self.app / 'keep.model').read_bytes(), b'keep')
        self.assertTrue((self.app / 'stopped.json').exists())

    def test_shortcuts_quote_paths_and_do_not_overwrite_unknown_files(self):
        folder = self.base / "Desktop space's/画像生成"
        control.install_shortcuts(folder)
        paths = list(folder.glob('*.command'))
        self.assertEqual(len(paths), 4)
        for path in paths:
            self.assertTrue(path.stat().st_mode & 0o111)
            self.assertIn('VeripsaScenes managed shortcut v1', path.read_text())
            self.assertIn('mac_scene_control.py', path.read_text())
        unknown = folder / '画像生成を停止.command'
        unknown.write_text('important existing script')
        with self.assertRaisesRegex(RuntimeError, 'not be overwritten'): control.install_shortcuts(folder)
        self.assertEqual(unknown.read_text(), 'important existing script')

    def test_status_uses_origin_snapshot_without_mutating_worktree(self):
        (self.root / 'content').mkdir()
        article = {'slug': 'one', 'title': 'One', 'paragraphs': ['one']}
        (self.root / 'content/articles.json').write_text(json.dumps([article]))
        env = {**os.environ, 'PATH': '/Library/Developer/CommandLineTools/usr/bin:' + os.environ['PATH'],
               'GIT_AUTHOR_NAME': 'test', 'GIT_AUTHOR_EMAIL': 'test@test.invalid',
               'GIT_COMMITTER_NAME': 'test', 'GIT_COMMITTER_EMAIL': 'test@test.invalid'}
        def git(*args): return subprocess.run(['git', *args], cwd=self.root, env=env, check=True, capture_output=True)
        git('init'); git('add', '.'); git('commit', '-m', 'initial'); git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        (self.root / 'content/articles.json').write_text(json.dumps([article, {**article, 'slug': 'uncommitted'}]))
        with patch.dict(os.environ, {'PATH': env['PATH']}), patch.object(control, 'launch_loaded', return_value=False), \
             patch.object(control, 'cpu_state', return_value={'state': 'active'}), \
             patch.object(control, 'gh', side_effect=AssertionError('unexpected run API without workflow ID')):
            # Include an empty tracked manifest so the immutable snapshot is usable.
            (self.root / 'assets/generated').mkdir(parents=True)
            (self.root / 'assets/generated/manifest.json').write_text('{"version":1,"images":{}}')
            git('add', 'assets'); git('commit', '-m', 'manifest'); git('update-ref', 'refs/remotes/origin/main', 'HEAD')
            result = control.status()
        self.assertEqual(result['pending_articles'], 1)
        self.assertTrue(result['counts_source'].startswith('cached origin/main at'))
        self.assertIn('fetch failed', result['counts_error'])
        self.assertEqual(len(json.loads((self.root / 'content/articles.json').read_text())), 2)

    def status_repository(self):
        from scripts.local_scene_photos import write_generated
        env = {**os.environ, 'PATH': '/Library/Developer/CommandLineTools/usr/bin:' + os.environ['PATH'],
               'GIT_AUTHOR_NAME': 'test', 'GIT_AUTHOR_EMAIL': 'test@test.invalid',
               'GIT_COMMITTER_NAME': 'test', 'GIT_COMMITTER_EMAIL': 'test@test.invalid'}
        def git(*args, cwd=None):
            return subprocess.run(['git', *args], cwd=cwd or self.root, env=env,
                                  check=True, capture_output=True).stdout.decode().strip()
        (self.root / 'content').mkdir()
        (self.root / 'assets/generated').mkdir(parents=True)
        one = {'slug': 'one', 'title': 'One', 'paragraphs': ['first']}
        (self.root / 'content/articles.json').write_text(json.dumps([one]))
        (self.root / 'assets/generated/manifest.json').write_text('{"version":1,"images":{}}')
        git('init', '--initial-branch=main'); git('add', '.'); git('commit', '-m', 'initial')
        original = git('rev-parse', 'HEAD')
        remote = self.base / 'remote.git'
        git('init', '--bare', str(remote))
        git('remote', 'add', 'origin', str(remote)); git('push', '-u', 'origin', 'main')
        publisher = self.base / 'publisher'
        git('clone', '--branch', 'main', str(remote), str(publisher))
        two = {'slug': 'two', 'title': 'Two', 'paragraphs': ['second']}
        (publisher / 'content/articles.json').write_text(json.dumps([one, two]))
        jpeg = (b'\xff\xd8\xff\xc0\x00\x11\x08' + (675).to_bytes(2, 'big')
                + (1200).to_bytes(2, 'big') + b'\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00'
                + b'\x00' * 17000 + b'\xff\xd9')
        write_generated(two, jpeg, 'sd_xl_base_1.0.safetensors', root=publisher)
        git('add', '.', cwd=publisher); git('commit', '-m', 'new article and CPU-published photo', cwd=publisher)
        latest = git('rev-parse', 'HEAD', cwd=publisher)
        git('push', 'origin', 'main', cwd=publisher)
        return git, original, latest

    def test_status_fetches_new_cpu_images_without_touching_local_files_or_stop_latch(self):
        git, original, latest = self.status_repository()
        local = self.root / 'content/articles.json'
        local.write_text('[{"slug":"unsaved-local-edit","title":"Keep me"}]')
        before = local.read_bytes()
        control.write_json(self.app / 'stopped.json', {'reason': 'user stop'})
        latch = (self.app / 'stopped.json').read_bytes()
        self.assertEqual(git('rev-parse', 'origin/main'), original)
        with patch.object(control, 'launch_loaded', return_value=True), \
             patch.object(control, 'cpu_state', return_value={'state': 'active'}):
            result = control.status()
        self.assertEqual(result['generated_images'], 1)
        self.assertEqual(result['pending_articles'], 1)
        self.assertEqual(result['counts_source'], 'origin/main at ' + latest)
        self.assertIsNone(result['counts_error'])
        self.assertEqual(result['automatic'], 'OFF')
        self.assertEqual(result['stop_scope'], 'unknown')
        self.assertEqual(local.read_bytes(), before)
        self.assertEqual((self.app / 'stopped.json').read_bytes(), latch)
        self.assertEqual(git('rev-parse', 'HEAD'), original)

    def test_status_distinguishes_mac_all_and_unknown_stop_intent(self):
        self.status_repository()
        latch = self.app / 'stopped.json'
        # A stale control result is not a current stop instruction after resume.
        control.write_json(self.app / 'last-control.json', {'action': 'stop-all'})
        cases = ((None, None), ({'scope': 'mac'}, 'mac'), ({'scope': 'all'}, 'all'),
                 ({'reason': 'legacy user stop'}, 'unknown'), ({'scope': 'unexpected'}, 'unknown'),
                 ('invalid JSON', 'unknown'))
        with patch.object(control, 'launch_loaded', return_value=True), \
             patch.object(control, 'cpu_state', return_value={'state': 'active'}):
            for record, expected in cases:
                with self.subTest(record=record):
                    if record is None:
                        latch.unlink(missing_ok=True)
                    elif isinstance(record, str):
                        latch.write_text(record)
                    else:
                        control.write_json(latch, record)
                    result = control.status()
                    self.assertEqual(result['stop_scope'], expected)
                    self.assertEqual(result['persistent_stop'], expected is not None)
                    self.assertEqual(result['automatic'], 'ON' if expected is None else 'OFF')
                    self.assertEqual(result['cpu']['state'], 'active')
            latch.unlink()
            latch.symlink_to(self.app / 'absent-stop-target')
            result = control.status()
            self.assertEqual(result['stop_scope'], 'unknown')
            self.assertTrue(result['persistent_stop'])
            self.assertEqual(result['automatic'], 'OFF')

    def test_status_fetch_failure_uses_cached_snapshot_with_explicit_error(self):
        git, original, _ = self.status_repository()
        git('remote', 'set-url', 'origin', str(self.base / 'missing.git'))
        with patch.object(control, 'launch_loaded', return_value=False), \
             patch.object(control, 'cpu_state', return_value={'state': 'active'}):
            result = control.status()
        self.assertEqual(result['generated_images'], 0)
        self.assertEqual(result['pending_articles'], 1)
        self.assertEqual(result['counts_source'], 'cached origin/main at ' + original)
        self.assertIn('fetch failed', result['counts_error'])

    def test_status_pins_all_blob_reads_even_when_remote_ref_moves_mid_count(self):
        git, original, _ = self.status_repository()
        publisher = self.base / 'publisher'
        article = json.loads((publisher / 'content/articles.json').read_text())[1]
        self.write_count_review(publisher, article)
        git('add', '.', cwd=publisher); git('commit', '-m', 'review exact image', cwd=publisher)
        latest = git('rev-parse', 'HEAD', cwd=publisher)
        git('push', 'origin', 'main', cwd=publisher)
        actual_run = subprocess.run
        reads = []
        moved = False
        def moving_run(command, *args, **kwargs):
            nonlocal moved
            result = actual_run(command, *args, **kwargs)
            if command[:2] == ['git', 'show']:
                reads.append(command[2])
                if not moved:
                    moved = True
                    actual_run(['git', 'update-ref', 'refs/remotes/origin/main', original],
                               cwd=self.root, env=control.command_env(), capture_output=True, check=True)
            return result
        with patch.object(control, 'launch_loaded', return_value=False), \
             patch.object(control, 'cpu_state', return_value={'state': 'active'}), \
             patch.object(control.subprocess, 'run', side_effect=moving_run):
            result = control.status()
        self.assertEqual(result['generated_images'], 1)
        self.assertEqual(result['pending_articles'], 1)
        self.assertTrue(reads)
        self.assertIn(latest + ':content/visual_reviews.json', reads)
        self.assertTrue(all(path.startswith(latest + ':') for path in reads))
        self.assertEqual(result['published_images'], 1)
        self.assertEqual(result['pending_review_images'], 0)
        self.assertEqual(result['pending_generation_articles'], 1)
        self.assertEqual(git('rev-parse', 'origin/main'), original)

    def test_status_uses_remote_illustration_pair_instead_of_local_assets(self):
        git, original, _ = self.status_repository()
        publisher = self.base / 'publisher'
        articles = json.loads((publisher / 'content/articles.json').read_text())
        illustration = {**articles[0], 'visual_strategy': 'illustration'}
        articles[0] = illustration
        (publisher / 'content/articles.json').write_text(json.dumps(articles))
        self.write_count_illustration(publisher, illustration)
        git('add', '.', cwd=publisher); git('commit', '-m', 'complete illustration pair', cwd=publisher)
        latest = git('rev-parse', 'HEAD', cwd=publisher)
        git('push', 'origin', 'main', cwd=publisher)
        with patch.object(control, 'launch_loaded', return_value=False), \
             patch.object(control, 'cpu_state', return_value={'state': 'active'}):
            result = control.status()
        self.assertEqual(result['generated_images'], 1)
        self.assertEqual(result['pending_generation_articles'], 0)
        self.assertEqual(result['pending_articles'], 0)
        self.assertEqual(result['counts_source'], 'origin/main at ' + latest)
        self.assertIsNone(result['counts_error'])
        self.assertEqual(git('rev-parse', 'HEAD'), original)
        self.assertFalse((self.root / 'assets/editorial-scenes').exists())

        # A local complete pair must not hide a missing asset on remote main.
        local_svg, local_png = self.write_count_illustration(self.root, illustration)
        original_pair = (local_svg.read_bytes(), local_png.read_bytes())
        for missing in ('one.svg', 'one.png'):
            with self.subTest(missing=missing):
                self.write_count_illustration(publisher, illustration)
                (publisher / 'assets/editorial-scenes' / missing).unlink()
                git('add', '.', cwd=publisher); git('commit', '-m', 'missing remote ' + missing, cwd=publisher)
                latest = git('rev-parse', 'HEAD', cwd=publisher)
                git('push', 'origin', 'main', cwd=publisher)
                with patch.object(control, 'launch_loaded', return_value=False), \
                     patch.object(control, 'cpu_state', return_value={'state': 'active'}):
                    result = control.status()
                self.assertEqual(result['pending_generation_articles'], 1)
                self.assertEqual(result['pending_articles'], 1)
                self.assertEqual(result['counts_source'], 'origin/main at ' + latest)
                self.assertIsNone(result['counts_error'])
                self.assertEqual((local_svg.read_bytes(), local_png.read_bytes()), original_pair)

    def test_cpu_latest_job_reports_metadata_without_claiming_new_images(self):
        self.status_repository()
        latest = {'id': 42, 'workflow_id': 7, 'status': 'completed', 'conclusion': 'success',
                  'created_at': '2026-11-04T00:00:00Z', 'updated_at': '2026-11-04T00:01:00Z',
                  'html_url': 'https://github.com/RollNuts/mihassei-news/actions/runs/42'}
        with patch.object(control, 'launch_loaded', return_value=False), \
             patch.object(control, 'cpu_state', return_value={'id': 7, 'state': 'active'}), \
             patch.object(control, 'gh', return_value={'workflow_runs': [latest]}) as github:
            result = control.status()
        self.assertEqual(result['cpu']['latest_run']['id'], 42)
        self.assertEqual(result['cpu']['latest_run']['conclusion'], 'success')
        self.assertNotIn('generated', result['cpu']['latest_run'])
        github.assert_called_once_with('api', 'repos/RollNuts/mihassei-news/actions/workflows/free-cpu-scenes.yml/runs?per_page=1', timeout=15)
        out = io.StringIO()
        with patch.object(control, 'status', return_value=result), contextlib.redirect_stdout(out):
            self.assertEqual(control.main(['status']), 0)
        self.assertIn('CPU直近ジョブ：', out.getvalue())
        self.assertIn('Mac最後の成功日時：', out.getvalue())
        self.assertIn('Mac最後のエラー：', out.getvalue())
        self.assertIn('MacからGitHubへの最終反映：', out.getvalue())
        self.assertNotIn('CPU画像生成成功', out.getvalue())

    def test_cpu_latest_job_auth_error_is_visible(self):
        self.status_repository()
        with patch.object(control, 'launch_loaded', return_value=False), \
             patch.object(control, 'cpu_state', return_value={'id': 7, 'state': 'active'}), \
             patch.object(control, 'gh', side_effect=RuntimeError('authentication failed')):
            result = control.status()
        self.assertEqual(result['cpu']['latest_run']['status'], 'unknown')
        self.assertIn('authentication failed', result['cpu']['latest_run']['error'])

    def write_count_review(self, root, article, decision='accepted'):
        from scripts.generated_photos import generated_manifest
        record = generated_manifest(root)[article['slug']]
        payload = {'version': 1, 'reviews': {article['slug']: {
            'decision': decision, 'article_sha256': record['article_sha256'],
            'image_sha256': record['sha256'], 'reviewed_at': '2026-11-04T05:00:00+00:00',
            'note': 'Unit-test fixture review; no real generated photo was approved.'}}}
        path = root / 'content/visual_reviews.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))

    def count_fixture(self):
        from scripts.local_scene_photos import write_generated
        article = {'slug': 'review-status', 'title': 'Fixture story', 'paragraphs': ['Original ending']}
        jpeg = (b'\xff\xd8\xff\xc0\x00\x11\x08' + (675).to_bytes(2, 'big')
                + (1200).to_bytes(2, 'big') + b'\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00'
                + b'\x00' * 17000 + b'\xff\xd9')
        write_generated(article, jpeg, 'sd_xl_base_1.0.safetensors', root=self.root)
        return article, jpeg

    def write_count_illustration(self, root, article):
        """Independent SVG/PNG fixture; no renderer or actual image approval."""
        from scripts.editorial_covers import story_subject
        from scripts.generated_photos import article_fingerprint
        svg = root / 'assets/editorial-scenes' / (article['slug'] + '.svg')
        svg.parent.mkdir(parents=True, exist_ok=True)
        svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 675" '
            'role="img" aria-label="Synthetic test-only 創作イラスト" '
            f'data-visual-topic="{story_subject(article)}" '
            f'data-article-sha256="{article_fingerprint(article)}">'
            '<title>Synthetic fixture, not an actual visual approval</title>'
            '<rect width="1200" height="675" fill="#ffffff"/></svg>', encoding='utf-8')
        def chunk(kind, data):
            return (struct.pack('>I', len(data)) + kind + data
                    + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff))
        data = chunk(b'IHDR', struct.pack('>IIBBBBB', 1200, 675, 8, 2, 0, 0, 0))
        data += chunk(b'IDAT', zlib.compress((b'\0' + b'\xff' * 3600) * 675))
        for key, value in {'article_sha256': article_fingerprint(article),
                           'svg_sha256': hashlib.sha256(svg.read_bytes()).hexdigest(),
                           'renderer': 'rsvg-convert 2.62.1'}.items():
            data += chunk(b'tEXt', key.encode() + b'\0' + value.encode())
        png = svg.with_suffix('.png')
        png.write_bytes(b'\x89PNG\r\n\x1a\n' + data + chunk(b'IEND', b''))
        return svg, png

    def test_current_illustration_is_complete_without_a_generated_jpeg(self):
        from scripts.editorial_covers import reviewed_story_scene
        article = {'slug': 'illustration-count', 'title': 'Illustration fixture',
                   'paragraphs': ['Original ending'], 'visual_strategy': 'illustration'}
        svg, png = self.write_count_illustration(self.root, article)
        original_pair = (svg.read_bytes(), png.read_bytes())
        counts = control.image_status_counts([article], self.root)
        self.assertEqual(counts, {'generated_images': 0, 'published_images': 0,
                                 'pending_review_images': 0, 'pending_generation_articles': 0,
                                 'held_articles': 0, 'pending_articles': 0})
        self.assertEqual((svg.read_bytes(), png.read_bytes()), original_pair)
        self.assertIsNotNone(reviewed_story_scene(article, root=str(self.root)))
        # A complete fallback must not disguise a real photo backlog.
        photo = {**article, 'visual_strategy': 'cpu-photo'}
        counts = control.image_status_counts([photo], self.root)
        self.assertEqual(counts['pending_generation_articles'], 1)
        self.assertEqual(counts['pending_articles'], 1)

    def test_incomplete_or_stale_illustration_assets_remain_pending(self):
        article = {'slug': 'illustration-count', 'title': 'Illustration fixture',
                   'paragraphs': ['Original ending'], 'visual_strategy': 'illustration'}
        for cause in ('missing SVG', 'stale article', 'invalid SVG', 'missing PNG', 'stale PNG', 'broken PNG'):
            with self.subTest(cause=cause):
                svg, png = self.write_count_illustration(self.root, article)
                current = article
                if cause == 'missing SVG':
                    svg.unlink()
                elif cause == 'stale article':
                    current = {**article, 'paragraphs': ['A different ending']}
                elif cause == 'invalid SVG':
                    svg.write_text(svg.read_text().replace('role="img"', 'role="none"'))
                elif cause == 'missing PNG':
                    png.unlink()
                elif cause == 'stale PNG':
                    svg.write_text(svg.read_text().replace('#ffffff', '#f8f6f0'))
                else:
                    png.write_bytes(png.read_bytes()[:-10])
                counts = control.image_status_counts([current], self.root)
                self.assertEqual(counts['generated_images'], 0)
                self.assertEqual(counts['pending_generation_articles'], 1)
                self.assertEqual(counts['pending_articles'], 1)

    def test_old_accepted_jpeg_does_not_hide_an_illustration_defect(self):
        article, _ = self.count_fixture()
        self.write_count_review(self.root, article)
        illustration = {**article, 'visual_strategy': 'illustration'}
        counts = control.image_status_counts([illustration], self.root)
        self.assertEqual(counts['generated_images'], 1)
        self.assertEqual(counts['published_images'], 1)  # Preserve raw JPEG review semantics.
        self.assertEqual(counts['pending_articles'], 1)
        self.write_count_illustration(self.root, illustration)
        self.assertEqual(control.image_status_counts([illustration], self.root)['pending_articles'], 0)

    def test_verified_jpeg_waits_for_exact_visual_review_before_published_count(self):
        article, _ = self.count_fixture()
        initial = control.image_status_counts([article], self.root)
        self.assertEqual(initial, {'generated_images': 1, 'published_images': 0,
                                  'pending_review_images': 1, 'pending_generation_articles': 0,
                                  'held_articles': 0, 'pending_articles': 0})
        self.write_count_review(self.root, article)
        adopted = control.image_status_counts([article], self.root)
        self.assertEqual(adopted['generated_images'], 1)
        self.assertEqual(adopted['published_images'], 1)
        self.assertEqual(adopted['pending_review_images'], 0)
        manifest = (self.root / 'assets/generated/manifest.json').read_bytes()
        reviews = (self.root / 'content/visual_reviews.json').read_bytes()
        control.image_status_counts([article], self.root)
        self.assertEqual((self.root / 'assets/generated/manifest.json').read_bytes(), manifest)
        self.assertEqual((self.root / 'content/visual_reviews.json').read_bytes(), reviews)

    def test_changed_image_bytes_do_not_inherit_old_published_count(self):
        from scripts.local_scene_photos import write_generated
        article, jpeg = self.count_fixture()
        self.write_count_review(self.root, article)
        changed = jpeg[:-2] + b'new fixture pixels' + jpeg[-2:]
        write_generated(article, changed, 'sd_xl_base_1.0.safetensors', root=self.root)
        counts = control.image_status_counts([article], self.root)
        self.assertEqual(counts['generated_images'], 1)
        self.assertEqual(counts['published_images'], 0)
        self.assertEqual(counts['pending_review_images'], 1)
        self.assertEqual(counts['pending_generation_articles'], 0)

    def test_changed_article_needs_generation_then_a_new_matching_review(self):
        from scripts.local_scene_photos import write_generated
        article, jpeg = self.count_fixture()
        self.write_count_review(self.root, article)
        changed = {**article, 'paragraphs': ['A different ending']}
        stale = control.image_status_counts([changed], self.root)
        self.assertEqual(stale['generated_images'], 0)
        self.assertEqual(stale['published_images'], 0)
        self.assertEqual(stale['pending_generation_articles'], 1)
        write_generated(changed, jpeg, 'sd_xl_base_1.0.safetensors', root=self.root)
        pending = control.image_status_counts([changed], self.root)
        self.assertEqual(pending['generated_images'], 1)
        self.assertEqual(pending['published_images'], 0)
        self.assertEqual(pending['pending_review_images'], 1)
        self.assertEqual(pending['pending_generation_articles'], 0)

    def test_matching_rejection_is_quality_held_not_waiting_to_generate(self):
        article, _ = self.count_fixture()
        self.write_count_review(self.root, article, decision='rejected')
        counts = control.image_status_counts([article], self.root)
        self.assertEqual(counts['generated_images'], 1)  # Preserve raw JPEG semantics.
        self.assertEqual(counts['published_images'], 0)
        self.assertEqual(counts['pending_review_images'], 0)
        self.assertEqual(counts['pending_generation_articles'], 0)
        self.assertEqual(counts['held_articles'], 1)

    def test_status_history_retains_last_success_when_later_run_errors(self):
        with patch.object(worker, 'APP_DIR', self.app):
            worker.update_status('ok', 'published')
            before = json.loads((self.app / 'last-run.json').read_text())['last_success_at']
            worker.update_status('skipped', 'offline')
            result = json.loads((self.app / 'last-run.json').read_text())
        self.assertEqual(result['last_success_at'], before)
        self.assertEqual(result['last_error']['detail'], 'offline')

    def test_status_displays_resource_deferral_separately_from_persistent_stop(self):
        result = {'automatic': 'ON', 'running': False, 'persistent_stop': False, 'stop_scope': None,
                  'generated_images': 1, 'pending_articles': 2, 'held_articles': 0,
                  'published_images': 0, 'pending_review_images': 1, 'pending_generation_articles': 2,
                  'free_gib': 100, 'app_gib': 8, 'jpeg_bytes': 1024,
                  'counts_source': 'origin/main', 'cpu': {'state': 'active'},
                  'resource_guard': {'allowed': False, 'reason': 'Swap exceeds safety limit',
                                     'swap_gib': 5, 'free_percent': 40}}
        output = io.StringIO()
        with patch.object(control, 'status', return_value=result), contextlib.redirect_stdout(output):
            self.assertEqual(control.main(['status']), 0)
        self.assertIn('自動運転：ON', output.getvalue())
        self.assertIn('Mac負荷保護：生成を見送り', output.getvalue())
        self.assertIn('スワップ5GiB', output.getvalue())
        self.assertIn('生成JPEG：1枚 ／ 掲載採用：0枚', output.getvalue())
        self.assertIn('目視待ち：1枚 ／ 要生成：2記事 ／ 品質保留：0件', output.getvalue())
        self.assertIn('素材未完了：2記事', output.getvalue())


if __name__ == '__main__': unittest.main()
