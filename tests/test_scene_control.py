"""Persistent OFF, scoped process ownership, launchd and exact workflow controls."""
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

from scripts import mac_scene_control as control
from scripts import mac_scene_worker as worker


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
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

    def test_generic_comfy_and_lookalike_listen_are_rejected(self):
        owned = {'pid': 42, 'pgid': 42, 'uid': os.getuid(), 'started': 'today',
                 'command': 'python ' + str(self.app / 'ComfyUI/main.py') + ' --listen 127.0.0.1 --port 18188'}
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

    def test_cpu_stop_only_mutates_exact_workflow_and_its_runs(self):
        state = {'id': 7, 'path': '.github/workflows/free-cpu-scenes.yml', 'state': 'active'}
        calls = []
        def github(*args):
            calls.append(args)
            if 'disable' in args[-1]: return {}
            if args[-1].endswith('/cancel'): return {}
            if '/runs?status=queued' in args[-1]:
                return [{'workflow_runs': [{'id': 11, 'workflow_id': 7}]}]
            if '/runs?status=' in args[-1]: return [{'workflow_runs': []}]
            if args[-1].endswith('/runs/11'): return {'status': 'completed'}
            raise AssertionError(args)
        with patch.object(control, 'cpu_state', side_effect=[state, {**state, 'state': 'disabled_manually'}]), \
             patch.object(control, 'gh', side_effect=github):
            result = control.stop_cpu(self.app)
        self.assertEqual(result['cancelled_or_completed'], [11])
        mutations = [a[-1] for a in calls if '--method' in a]
        self.assertEqual(mutations, ['repos/RollNuts/mihassei-news/actions/workflows/free-cpu-scenes.yml/disable',
                                     'repos/RollNuts/mihassei-news/actions/runs/11/cancel'])
        self.assertTrue(control.read_json(self.app / 'cpu-restore.json')['restore_active'])

    def test_cpu_run_from_different_workflow_never_cancelled(self):
        state = {'id': 7, 'path': '.github/workflows/free-cpu-scenes.yml', 'state': 'active'}
        def github(*args):
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
             patch.object(control, 'cpu_state', return_value={'state': 'active'}):
            # Include an empty tracked manifest so the immutable snapshot is usable.
            (self.root / 'assets/generated').mkdir(parents=True)
            (self.root / 'assets/generated/manifest.json').write_text('{"version":1,"images":{}}')
            git('add', 'assets'); git('commit', '-m', 'manifest'); git('update-ref', 'refs/remotes/origin/main', 'HEAD')
            result = control.status()
        self.assertEqual(result['pending_articles'], 1)
        self.assertTrue(result['counts_source'].startswith('origin/main at'))
        self.assertEqual(len(json.loads((self.root / 'content/articles.json').read_text())), 2)

    def test_status_history_retains_last_success_when_later_run_errors(self):
        with patch.object(worker, 'APP_DIR', self.app):
            worker.update_status('ok', 'published')
            before = json.loads((self.app / 'last-run.json').read_text())['last_success_at']
            worker.update_status('skipped', 'offline')
            result = json.loads((self.app / 'last-run.json').read_text())
        self.assertEqual(result['last_success_at'], before)
        self.assertEqual(result['last_error']['detail'], 'offline')


if __name__ == '__main__': unittest.main()
