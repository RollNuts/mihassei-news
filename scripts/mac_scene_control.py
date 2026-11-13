#!/usr/bin/env python3
"""Persistent, narrowly scoped controls for the local newspaper photo worker."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = Path.home() / 'Library/Application Support/VeripsaScenes'
CACHE_DIR = Path.home() / 'Library/Caches/VeripsaScenes'
LABEL = 'com.veripsa.local-comedy-scenes'
WORKFLOW = 'free-cpu-scenes.yml'
REPO = 'RollNuts/mihassei-news'
MODEL_SHA = '31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b'


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def display_time(value) -> str:
    if not value:
        return '未記録'
    try:
        return datetime.fromisoformat(str(value)).astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S JST')
    except ValueError:
        return str(value)


def command_env() -> dict:
    return {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GH_PROMPT_DISABLED': '1',
            'PATH': '/Library/Developer/CommandLineTools/usr/bin:/opt/homebrew/bin:'
                    + str(Path.home() / '.local/bin') + ':' + os.environ.get('PATH', '')}



def state_dir(app_dir=None) -> Path:
    path = Path(app_dir) if app_dir is not None else APP_DIR
    if path.is_symlink():
        raise RuntimeError('Unsafe app state symlink')
    return path


def read_json(path: Path) -> dict:
    if path.is_symlink():
        raise RuntimeError('Unsafe state file symlink: ' + str(path))
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    if not isinstance(obj, dict):
        raise RuntimeError('Invalid state file: ' + str(path))
    return obj


def write_json(path: Path, payload: dict) -> None:
    state_dir(path.parent).mkdir(parents=True, exist_ok=True)
    stage = path.with_name(path.name + '.tmp')
    if path.is_symlink() or stage.is_symlink():
        raise RuntimeError('Unsafe state file: ' + str(path))
    with stage.open('w', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(stage, path)


@contextmanager
def transition_lock(app_dir=None):
    directory = state_dir(app_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'control.lock'
    if path.is_symlink():
        raise RuntimeError('Unsafe control lock')
    with path.open('a+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def check_allowed(app_dir=None) -> None:
    # Existence is sufficient: corrupt/malicious latch contents never enable a job.
    if (state_dir(app_dir) / 'stopped.json').exists() or (state_dir(app_dir) / 'stopped.json').is_symlink():
        raise RuntimeError('Image generation is OFF; explicitly run mac_scene_control.py resume')


def process_env() -> dict:
    # macOS ps renders non-ASCII argv as M-escaped bytes in the C locale.
    # Keep English start-time fields stable without losing Japanese repo paths.
    locale = 'en_US.UTF-8' if sys.platform == 'darwin' else 'C.UTF-8'
    return {**os.environ, 'LC_ALL': locale, 'LANG': locale, 'TZ': 'UTC'}


def process_identity(pid: int) -> dict | None:
    if type(pid) is not int or pid <= 1:
        return None
    result = subprocess.run(['/bin/ps', '-ww', '-p', str(pid), '-o',
                             'pid=,pgid=,uid=,stat=,lstart=,command='], capture_output=True,
                            text=True, encoding='utf-8', timeout=5, check=False, env=process_env())
    parts = result.stdout.strip().split(None, 9)
    if result.returncode or len(parts) != 10 or parts[3].startswith('Z'):
        return None
    try:
        return {'pid': int(parts[0]), 'pgid': int(parts[1]), 'uid': int(parts[2]),
                'started': ' '.join(parts[4:9]), 'command': parts[9]}
    except ValueError:
        return None


def has_arg(command: str, value: str) -> bool:
    return re.search(r'(?:^|\s)' + re.escape(value) + r'(?=\s|$)', command) is not None


def normalize_python_command(command: str, app_dir: Path) -> str:
    # macOS Homebrew launchers exec Python.app while preserving PID/start time.
    # Normalize only known Python executable aliases; retain every script/arg.
    paths = {Path(sys.executable), Path(getattr(sys, '_base_executable', sys.executable)),
             app_dir / 'venv/bin/python',
             Path(sys.base_prefix) / 'Resources/Python.app/Contents/MacOS/Python'}
    names = {str(p) for p in paths} | {str(p.resolve()) for p in paths}
    first = command.split(' ', 1)[0]
    if first.startswith('/') and str(Path(first).resolve()) in names:
        names.add(first)
    for name in sorted(names, key=len, reverse=True):
        if command.startswith(name + ' '):
            return 'VERIPSA_PYTHON ' + command[len(name) + 1:]
    return command


def same_identity(left, right, app_dir: Path) -> bool:
    return (isinstance(left, dict) and isinstance(right, dict)
            and all(left.get(key) == right.get(key) for key in ('pid', 'pgid', 'uid', 'started'))
            and normalize_python_command(left.get('command', ''), app_dir)
            == normalize_python_command(right.get('command', ''), app_dir))


def expected_process(identity: dict, role: str, root: Path, app_dir: Path) -> bool:
    if identity.get('uid') != os.getuid():
        return False
    command = identity.get('command', '')
    if not normalize_python_command(command, app_dir).startswith('VERIPSA_PYTHON '):
        return False
    if role == 'worker':
        return has_arg(command, str(root / 'scripts/mac_scene_worker.py'))
    if role == 'sync':
        return has_arg(command, str(root / 'scripts/local_scene_sync.py')) and identity['pgid'] == identity['pid']
    if role == 'server':
        return (has_arg(command, str(app_dir / 'ComfyUI/main.py'))
                and re.search(r'(?:^|\s)--port\s+18188(?=\s|$)', command) is not None
                and re.search(r'(?:^|\s)--listen\s+127\.0\.0\.1(?=\s|$)', command) is not None
                and identity['pgid'] == identity['pid'])
    return False


def register_runtime(role: str, pid: int, *, root=None, app_dir=None) -> None:
    directory = state_dir(app_dir)
    root = Path(root) if root is not None else ROOT
    # Homebrew's macOS launcher execs Python.app just after Popen returns.
    # Wait for a stable identity so an immediate stop does not reject our own
    # child merely because the launcher changed its executable display name.
    identity = None
    previous = None
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        identity = process_identity(pid)
        if identity is not None and identity == previous and expected_process(identity, role, root, directory):
            break
        previous = identity
        time.sleep(.1)
    else:
        raise RuntimeError('Unable to establish owned process identity for ' + role)
    path = directory / 'runtime.json'
    data = read_json(path)
    data.setdefault('processes', {})[role] = identity
    data['root'] = str(root)
    data['updated_at'] = now()
    write_json(path, data)


def clear_runtime(app_dir=None) -> None:
    path = state_dir(app_dir) / 'runtime.json'
    if path.is_symlink():
        raise RuntimeError('Unsafe runtime state')
    path.unlink(missing_ok=True)


def matching_process(saved: dict, role: str, root: Path, app_dir: Path) -> dict | None:
    current = process_identity(saved.get('pid'))
    if not same_identity(current, saved, app_dir) or not current or not expected_process(current, role, root, app_dir):
        return None
    return current


def signal_owned(saved: dict, role: str, root: Path, app_dir: Path, sig: int) -> bool:
    current = matching_process(saved, role, root, app_dir)
    if not current:
        return False
    try:
        if role in ('server', 'sync'):
            os.killpg(current['pgid'], sig)
        else:
            os.kill(current['pid'], sig)
    except ProcessLookupError:
        return False
    return True


def group_members(pgid: int) -> dict[int, dict]:
    result = subprocess.run(['/bin/ps', '-axo', 'pid=,pgid=,uid='], capture_output=True,
                            text=True, encoding='utf-8', timeout=5, check=True, env=process_env())
    members = {}
    for row in result.stdout.splitlines():
        fields = row.split()
        if len(fields) == 3 and int(fields[1]) == pgid and int(fields[2]) == os.getuid():
            identity = process_identity(int(fields[0]))
            if identity is not None:
                members[identity['pid']] = identity
    return members


def stop_owned(app_dir=None, grace: float = 20) -> list[str]:
    directory = state_dir(app_dir)
    data = read_json(directory / 'runtime.json')
    root = Path(data.get('root', ROOT))
    if root.resolve() != ROOT.resolve():
        raise RuntimeError('Runtime belongs to another checkout; no process was signalled')
    records = data.get('processes', {})
    if not isinstance(records, dict):
        raise RuntimeError('Invalid runtime processes')
    # Capture descendants only while their session leader still matches the
    # registered start time, exact command, UID and dedicated path. This lets
    # us safely reap Git children even after their sync parent exits first.
    groups = {}
    known = {}
    for role in ('sync', 'server'):
        saved = records.get(role)
        if isinstance(saved, dict) and matching_process(saved, role, root, directory):
            groups[role] = saved['pgid']
            known.update(group_members(saved['pgid']))
    signalled = []
    for role in ('worker', 'sync', 'server'):
        saved = records.get(role)
        if isinstance(saved, dict) and signal_owned(saved, role, root, directory, signal.SIGTERM):
            signalled.append(role)

    def survivors():
        alive = {}
        for role, pgid in groups.items():
            members = group_members(pgid)
            if matching_process(records[role], role, root, directory):
                known.update(members)
            # Unknown newcomers are never killed under an unverifiable group.
            for pid, identity in members.items():
                alive[pid] = identity
        saved = records.get('worker')
        if isinstance(saved, dict):
            current = matching_process(saved, 'worker', root, directory)
            if current:
                known[current['pid']] = current
                alive[current['pid']] = current
        return alive

    deadline = time.monotonic() + grace
    alive = survivors()
    while alive and time.monotonic() < deadline:
        time.sleep(.1)
        alive = survivors()
    if alive:
        for pid, identity in alive.items():
            if same_identity(known.get(pid), identity, directory) and same_identity(process_identity(pid), identity, directory):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 5
        while alive and time.monotonic() < deadline:
            time.sleep(.1)
            alive = survivors()
    if alive:
        raise RuntimeError('Dedicated group still has live/unverified children; OFF latch remains set')
    clear_runtime(directory)
    return signalled


def launch_loaded() -> bool:
    result = subprocess.run(['launchctl', 'print', f'gui/{os.getuid()}/{LABEL}'],
                            capture_output=True, text=True, check=False, timeout=10)
    return result.returncode == 0


def unload_schedule() -> None:
    # Address the service label even if its plist has already been removed.
    if launch_loaded():
        result = subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}'],
                                capture_output=True, text=True, check=False, timeout=30)
        if result.returncode and launch_loaded():
            raise RuntimeError('launchd service could not be unloaded: ' + result.stderr[-250:])
    plist = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
    if plist.is_symlink():
        raise RuntimeError('Unsafe LaunchAgent symlink')
    plist.unlink(missing_ok=True)


def gh(*args: str, timeout: float = 45) -> dict:
    result = subprocess.run(['gh', *args], capture_output=True, text=True, timeout=timeout, check=False,
                            env=command_env())
    if result.returncode:
        raise RuntimeError('GitHub image workflow request failed: ' + result.stderr.strip()[-400:])
    return json.loads(result.stdout) if result.stdout.strip() else {}


def cpu_state() -> dict:
    return gh('api', f'repos/{REPO}/actions/workflows/{WORKFLOW}')


def stop_cpu(app_dir=None) -> dict:
    directory = state_dir(app_dir)
    state = cpu_state()
    if state.get('path') != '.github/workflows/' + WORKFLOW:
        raise RuntimeError('GitHub returned an unexpected workflow; refusing mutation')
    prior_file = directory / 'cpu-restore.json'
    prior = read_json(prior_file)
    was_active = state.get('state') == 'active'
    # Retain the original active state on repeated stop-all calls.
    restore = bool(prior.get('restore_active')) or was_active
    write_json(prior_file, {'restore_active': restore, 'stopped_at': now(), 'workflow': WORKFLOW})
    gh('api', '--method', 'PUT', f'repos/{REPO}/actions/workflows/{WORKFLOW}/disable')
    # A run can move queued -> in_progress between separate status-filtered
    # queries, and newly dispatched runs may take a moment to appear. Sweep
    # the complete workflow listing repeatedly after disabling new triggers.
    cancelled = set()
    pending = set()
    started = time.monotonic()
    deadline = started + 30
    quiet_passes = 0
    def request(*args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('CPU workflow disabled but cancellation verification timed out')
        return gh(*args, timeout=min(45, remaining))
    while True:
        if time.monotonic() >= deadline:
            raise RuntimeError('CPU workflow disabled but active runs did not settle: ' + str(sorted(pending)))
        payload = request('api', '--paginate', '--slurp',
                     f'repos/{REPO}/actions/workflows/{WORKFLOW}/runs?per_page=100')
        pages = payload if isinstance(payload, list) else [payload]
        active = []
        for run in [run for page in pages for run in page.get('workflow_runs', [])]:
            if run.get('workflow_id') != state.get('id'):
                raise RuntimeError('Unexpected workflow run returned; refusing cancellation')
            if run.get('status') != 'completed':
                active.append(int(run['id']))
        for run_id in active:
            pending.add(run_id)
            if run_id in cancelled:
                continue
            try:
                request('api', '--method', 'POST', f'repos/{REPO}/actions/runs/{run_id}/cancel')
            except RuntimeError:
                current = request('api', f'repos/{REPO}/actions/runs/{run_id}')
                if current.get('status') != 'completed':
                    raise
            cancelled.add(run_id)
        # Listing absence alone is insufficient: prove every targeted run has
        # completed, even if cancellation is still propagating to the list.
        pending = {run_id for run_id in pending
                   if request('api', f'repos/{REPO}/actions/runs/{run_id}').get('status') != 'completed'}
        quiet_passes = quiet_passes + 1 if not active and not pending else 0
        at = time.monotonic()
        if at >= deadline:
            raise RuntimeError('CPU workflow disabled but cancellation verification timed out: ' + str(sorted(pending)))
        # Minimum settling window also catches a pre-disable dispatch that was
        # accepted by GitHub but not visible in the first listing response.
        if quiet_passes >= 2 and at - started >= 5:
            break
        time.sleep(min(2, deadline - at))
    after = cpu_state()
    if after.get('state') != 'disabled_manually':
        raise RuntimeError('CPU image workflow was not disabled')
    return {'state': after['state'], 'cancelled_or_completed': sorted(cancelled)}


def resume_cpu(*, only_if_stopped=False, app_dir=None) -> dict:
    directory = state_dir(app_dir)
    saved = read_json(directory / 'cpu-restore.json')
    if only_if_stopped and not saved.get('restore_active'):
        return {'state': 'unchanged'}
    state = cpu_state()
    if state.get('path') != '.github/workflows/' + WORKFLOW:
        raise RuntimeError('Unexpected image workflow path')
    gh('api', '--method', 'PUT', f'repos/{REPO}/actions/workflows/{WORKFLOW}/enable')
    result = cpu_state()
    if result.get('state') != 'active':
        raise RuntimeError('CPU image workflow did not resume')
    write_json(directory / 'cpu-restore.json', {'restore_active': False, 'resumed_at': now(), 'workflow': WORKFLOW})
    return {'state': 'active', 'note': 'Existing YAML triggers are preserved; no schedule is added.'}


def quality_ready(app_dir=None) -> bool:
    data = read_json(state_dir(app_dir) / 'quality-pilot.json')
    images = data.get('images')
    return (data.get('approved') is True and data.get('model_sha256') == MODEL_SHA
            and isinstance(data.get('reviewed_at'), str)
            and re.fullmatch('[0-9a-f]{40}', str(data.get('published_commit', ''))) is not None
            and isinstance(images, dict) and len(images) >= 3
            and all(re.fullmatch('[0-9a-f]{64}', str(v)) for v in images.values()))


def stop(*, all_images=False) -> dict:
    with transition_lock():
        write_json(APP_DIR / 'stopped.json', {'stopped_at': now(), 'reason': 'explicit user stop',
                                             'scope': 'all' if all_images else 'mac'})
        errors = []
        try:
            unload_schedule()
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            errors.append(str(exc))
        try:
            stopped = stop_owned()
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            errors.append(str(exc))
            stopped = []
        cpu = None
        if all_images:
            try:
                cpu = stop_cpu()
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                errors.append(str(exc))
        result = {'automatic': 'OFF', 'stopped_processes': stopped, 'cpu': cpu, 'errors': errors}
        write_json(APP_DIR / 'last-control.json', {**result, 'at': now(), 'action': 'stop-all' if all_images else 'stop'})
        return result


def resume() -> dict:
    try:
        from . import mac_scene_setup as setup
    except ImportError:
        import mac_scene_setup as setup
    with transition_lock():
        if not quality_ready():
            raise RuntimeError('Three reviewed and publicly verified pilot images are required (quality-pilot.json)')
        # Setup validates installation, free storage and actual launchctl result.
        latch = APP_DIR / 'stopped.json'
        if latch.is_symlink():
            raise RuntimeError('Unsafe stop latch')
        previous = read_json(latch)
        latch.unlink(missing_ok=True)
        try:
            setup.enable_schedule()
            cpu = resume_cpu(only_if_stopped=True)
        except Exception:
            write_json(latch, previous or {'stopped_at': now(), 'reason': 'resume failed'})
            unload_schedule()
            stop_owned()
            raise
        result = {'automatic': 'ON', 'cpu': cpu, 'at': now(), 'action': 'resume'}
        write_json(APP_DIR / 'last-control.json', result)
        return result


def record_publish_result(status: str, detail: str, sha: str = '', app_dir=None) -> None:
    write_json(state_dir(app_dir) / 'publish-result.json',
               {'status': status, 'detail': detail[:400], 'sha': sha, 'at': now()})


def image_status_counts(articles: list[dict], root) -> dict:
    """Preserve JPEG states while recognizing complete, intentional illustrations."""
    try:
        from .generated_photos import usable_generated_scene
        from .social_cards import reviewed_scene_png
        from .visual_review import _scene_state
    except ImportError:
        from generated_photos import usable_generated_scene
        from social_cards import reviewed_scene_png
        from visual_review import _scene_state
    states = [_scene_state(article, root=root)['status'] for article in articles]
    generated = [usable_generated_scene(article, root=root) for article in articles]
    illustrations = [article.get('visual_strategy') == 'illustration' for article in articles]
    # This existing gate requires both the current reviewed SVG and its intact,
    # matching share PNG. A photo article's fallback never completes its queue.
    complete = [illustration and reviewed_scene_png(article, root=root) is not None
                for article, illustration in zip(articles, illustrations)]
    return {'generated_images': sum(generated),
            'published_images': states.count('accepted'),
            'pending_review_images': states.count('pending'),
            'pending_generation_articles': sum(state == 'missing' and not ready
                                               for state, ready in zip(states, complete)),
            'held_articles': states.count('rejected'),
            'pending_articles': sum(not ready if illustration else not jpeg
                                    for illustration, ready, jpeg in zip(illustrations, complete, generated))}


def status() -> dict:
    try:
        from .local_scene_photos import load_articles
    except ImportError:
        from local_scene_photos import load_articles
    last = read_json(APP_DIR / 'last-run.json')
    runtime = read_json(APP_DIR / 'runtime.json')
    records = runtime.get('processes', {})
    running = any(isinstance(v, dict) and matching_process(v, k, ROOT, APP_DIR)
                  for k, v in records.items()) if isinstance(records, dict) else False
    loaded = launch_loaded()
    latch = APP_DIR / 'stopped.json'
    stopped = latch.exists() or latch.is_symlink()
    stop_scope = None
    if stopped:
        stop_scope = 'unknown'
        if not latch.is_symlink():
            try:
                scope = read_json(latch).get('scope')
                if scope in ('all', 'mac'):
                    stop_scope = scope
            except (OSError, ValueError, RuntimeError):
                pass  # An unreadable or legacy latch must never imply Mac-only intent.
    source = 'current local checkout (origin/main unavailable)'
    counts_error = None
    fetched = False
    try:
        # Refresh remote-tracking data even when the GPU worker is deferring
        # for memory pressure. This never checks out or merges user files.
        subprocess.run(['git', 'fetch', '--no-tags', 'origin', 'main'], cwd=ROOT,
                       capture_output=True, check=True, text=True, timeout=15, env=command_env())
        fetched = True
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, 'stderr', '') or str(exc)
        counts_error = 'Latest main fetch failed; cached counts used: ' + str(detail).strip()[:200]
    try:
        revision = subprocess.run(['git', 'rev-parse', 'refs/remotes/origin/main'], cwd=ROOT,
                                  capture_output=True, check=True, text=True, timeout=10, env=command_env()).stdout.strip()
        blobs = {}
        missing = set()
        def blob(path):
            # Pin every read to this captured commit; another worker may fetch
            # or push origin/main while status is calculating the counts.
            if path in missing:
                raise FileNotFoundError(path)
            if path not in blobs:
                if (path == 'content/visual_reviews.json'
                        or path.startswith('assets/editorial-scenes/') and path.endswith(('.svg', '.png'))):
                    # Missing optional reviews or illustration assets mean
                    # unfinished work in this snapshot, not local fallback.
                    listing = subprocess.run(['git', 'ls-tree', revision, '--', path], cwd=ROOT,
                                             capture_output=True, check=True, timeout=10, env=command_env())
                    if not listing.stdout:
                        missing.add(path)
                        raise FileNotFoundError(path)
                data = subprocess.run(['git', 'show', revision + ':' + path], cwd=ROOT,
                                      capture_output=True, check=True, timeout=30, env=command_env()).stdout
                if path.lower().endswith('.jpg'):
                    return data  # Do not retain the whole JPEG archive in RAM.
                blobs[path] = data
            return blobs[path]
        class Snapshot:
            # Reuse the full existing integrity gate against immutable git blobs,
            # without checking out files or touching the user's working tree.
            def __init__(self, path=''): self.path = path
            def __truediv__(self, path): return Snapshot('/'.join(x for x in (self.path, str(path)) if x))
            def read_bytes(self): return blob(self.path)
            def read_text(self, encoding='utf-8'): return self.read_bytes().decode(encoding)
            def is_symlink(self): return False
            def stat(self):
                from types import SimpleNamespace
                return SimpleNamespace(st_size=len(self.read_bytes()))
        articles = json.loads(blob('content/articles.json'))
        counts = image_status_counts(articles, Snapshot())
        source = ('origin/main at ' if fetched else 'cached origin/main at ') + revision
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        detail = 'Remote snapshot unavailable; local counts used: ' + str(exc)[:200]
        counts_error = counts_error + '; ' + detail if counts_error else detail
        articles = load_articles(ROOT)
        counts = image_status_counts(articles, ROOT)
    byte_count = sum(p.stat().st_size for p in (ROOT / 'assets/generated').glob('*.jpg') if p.is_file())
    try:
        workflow = cpu_state()
        cpu = {'state': workflow.get('state')}
        if workflow.get('id') is not None:
            try:
                runs = gh('api', f'repos/{REPO}/actions/workflows/{WORKFLOW}/runs?per_page=1', timeout=15)
                latest = (runs.get('workflow_runs') or [None])[0]
                if latest is not None and latest.get('workflow_id') != workflow['id']:
                    raise RuntimeError('Unexpected CPU workflow run returned')
                cpu['latest_run'] = ({key: latest.get(key) for key in
                                      ('id', 'status', 'conclusion', 'created_at', 'updated_at', 'html_url')}
                                     if latest is not None else None)
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                cpu['latest_run'] = {'status': 'unknown', 'error': str(exc)[:300]}
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        cpu = {'state': 'unknown', 'error': str(exc)}
    return {'automatic': 'ON' if loaded and not stopped else 'OFF', 'launchd_loaded': loaded,
            'persistent_stop': stopped, 'stop_scope': stop_scope, 'running': bool(running),
            'last_success_at': last.get('last_success_at'), 'last_error': last.get('last_error'),
            'last_run': last, 'publish': read_json(APP_DIR / 'publish-result.json'),
            'resource_guard': read_json(APP_DIR / 'resources.json'),
            'quality_pilot_passed': quality_ready(), **counts,
            'counts_source': source, 'counts_error': counts_error, 'cpu': cpu,
            'jpeg_bytes': byte_count, 'jpeg_cap_bytes': 1536 * 1024**2,
            'free_gib': round(shutil.disk_usage(ROOT).free / 1024**3, 2),
            'app_gib': round(sum(p.stat().st_size for p in APP_DIR.rglob('*') if p.is_file() and not p.is_symlink()) / 1024**3, 2)}


def install_shortcuts(destination=None) -> Path:
    destination = Path(destination) if destination else Path.home() / 'Desktop/未発生新聞画像生成'
    if destination.is_symlink():
        raise RuntimeError('Unsafe shortcuts directory')
    destination.mkdir(parents=True, exist_ok=True)
    interpreter = APP_DIR / 'venv/bin/python'
    script = ROOT / 'scripts/mac_scene_control.py'
    for name, command in (('画像生成を停止', 'stop'), ('画像生成を再開', 'resume'),
                          ('画像生成の状態確認', 'status'), ('全画像生成を停止', 'stop-all')):
        path = destination / (name + '.command')
        if path.is_symlink():
            raise RuntimeError('Unsafe shortcut symlink')
        header = '# VeripsaScenes managed shortcut v1'
        if path.exists() and header not in path.read_text(encoding='utf-8'):
            raise RuntimeError('Existing unrelated shortcut will not be overwritten: ' + str(path))
        path.write_text('#!/bin/zsh\n' + header + '\nexport PATH="/Library/Developer/CommandLineTools/usr/bin:/opt/homebrew/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"\n'
                        + shlex.quote(str(interpreter)) + ' ' + shlex.quote(str(script)) + ' ' + command
                        + '\nresult=$?\nprint "終了コード: $result"\nread -k 1 "?キーを押すと閉じます。"\nexit $result\n', encoding='utf-8')
        path.chmod(0o755)
    return destination


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='未発生新聞の画像生成だけを停止・再開・確認')
    parser.add_argument('action', choices=['stop', 'resume', 'status', 'stop-all', 'resume-cpu', 'install-shortcuts'])
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--directory', default='')
    args = parser.parse_args(argv)
    try:
        if args.action in ('stop', 'stop-all'):
            result = stop(all_images=args.action == 'stop-all')
        elif args.action == 'resume':
            result = resume()
        elif args.action == 'resume-cpu':
            with transition_lock():
                result = resume_cpu()
        elif args.action == 'install-shortcuts':
            result = {'shortcuts': str(install_shortcuts(args.directory or None))}
        else:
            result = status()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.action == 'status':
            error = result.get('last_error') or 'なし'
            publish = result.get('publish') or {'status': '未確認'}
            print('Mac自動運転：' + result['automatic'])
            print('Mac現在生成中：' + ('はい' if result['running'] else 'いいえ'))
            print('Mac停止状態を維持：' + ('はい' if result['persistent_stop'] else 'いいえ'))
            if result['stop_scope'] is not None:
                print('停止範囲：' + {'mac': 'Macのみ', 'all': '全画像', 'unknown': '不明（安全側で停止）'}[result['stop_scope']])
            resource = result.get('resource_guard') or {}
            if resource:
                print('Mac負荷保護：' + ('実行可能' if resource.get('allowed') else '生成を見送り')
                      + ' ／ ' + str(resource.get('reason') or '正常')
                      + ' ／ 確認 ' + display_time(resource.get('checked_at')))
                print(f"メモリ：空き{resource.get('free_percent', '未確認')}% ／ スワップ{resource.get('swap_gib', '未確認')}GiB")
            print('Mac最後の成功日時：' + display_time(result.get('last_success_at')))
            print('Mac最後のエラー：' + (json.dumps(error, ensure_ascii=False) if isinstance(error, dict) else error))
            print(f"生成JPEG：{result['generated_images']}枚 ／ 掲載採用：{result.get('published_images', '未確認')}枚")
            print(f"目視待ち：{result.get('pending_review_images', '未確認')}枚 ／ 要生成：{result.get('pending_generation_articles', '未確認')}記事 ／ 品質保留：{result.get('held_articles', 0)}件 ／ 素材未完了：{result.get('pending_articles', '未確認')}記事")
            print(f"ストレージ：空き{result['free_gib']}GiB ／ 専用領域{result['app_gib']}GiB ／ JPEG {result['jpeg_bytes']/1024**2:.2f}MiB（上限1536MiB）")
            print('MacからGitHubへの最終反映：' + json.dumps(publish, ensure_ascii=False))
            print('画像数の参照元：' + result['counts_source'])
            cpu = dict(result['cpu'])
            latest = cpu.pop('latest_run', None)
            print('GitHub CPU画像生成：' + json.dumps(cpu, ensure_ascii=False))
            print('CPU直近ジョブ：' + (json.dumps(latest, ensure_ascii=False) if latest is not None else '未記録'))
            if result.get('counts_error'):
                print('画像数の取得エラー：' + result['counts_error'])
        else:
            if 'automatic' in result:
                print('自動運転：' + result['automatic'])
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get('errors') else 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({'success': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
