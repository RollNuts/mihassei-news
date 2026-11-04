#!/usr/bin/env python3
"""Bounded, opt-in Mac worker for fictional comedy photos.

Only starts a local ComfyUI. It never installs packages or models, and it
touches only its own scratch directories and the generated-photo tree.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import mac_scene_control as control
    from . import mac_scene_resources as resources
except ImportError:
    import mac_scene_control as control
    import mac_scene_resources as resources

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = Path.home() / "Library" / "Application Support" / "VeripsaScenes"
CACHE_DIR = Path.home() / "Library" / "Caches" / "VeripsaScenes"
PORT = 18188
OWN_PNG = re.compile(r"mihassei-staged-scene_[0-9]{5}_\.png\Z")
MAX_GENERATED_MIB = 1536
PREPARE_NEEDS_GPU = 10


def capture_server_errors(stream, tail: bytearray) -> None:
    """Drain stderr without growing a log file or blocking ComfyUI."""
    try:
        for block in iter(lambda: stream.read(1024), b""):
            tail.extend(block)
            del tail[:-8192]
    finally:
        stream.close()


def safe_dir(path: Path) -> bool:
    """Refuse symlinked work paths instead of deleting through them."""
    return path.is_dir() and not path.is_symlink()


def storage_ok(paths: list[Path], min_free_gb: int) -> bool:
    return all(shutil.disk_usage(path).free >= min_free_gb * 1024**3 for path in paths)


def generated_mib(root: Path = ROOT) -> float:
    directory = root / "assets" / "generated"
    if not directory.is_dir():
        return 0
    return sum(p.stat().st_size for p in directory.glob("*.jpg")
               if p.is_file() and not p.is_symlink()) / 1024**2


def clean_owned_intermediates(output_dir: Path) -> int:
    """Remove only this worker's PNG names; no broad glob, no unrelated media."""
    if not safe_dir(output_dir):
        return 0
    removed = 0
    for file in output_dir.iterdir():
        if (OWN_PNG.fullmatch(file.name) and file.is_file()
                and not file.is_symlink() and file.parent.resolve() == output_dir.resolve()):
            file.unlink()
            removed += 1
    return removed


def update_status(status: str, detail: str) -> None:
    if APP_DIR.is_symlink():
        raise RuntimeError("Unsafe app directory")
    APP_DIR.mkdir(parents=True, exist_ok=True)
    target = APP_DIR / "last-run.json"
    stage = APP_DIR / "last-run.json.tmp"
    if stage.is_symlink() or target.is_symlink():
        raise RuntimeError("Unsafe status file")
    try:
        previous = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    except (OSError, ValueError):
        previous = {}
    at = datetime.now(timezone.utc).isoformat()
    payload = {**previous, "status": status, "detail": detail[:300], "at_utc": at}
    if status == "ok":
        payload["last_success_at"] = at
    elif status not in ("stopped", "resource-paused", "idle"):
        payload["last_error"] = {"at": at, "detail": detail[:300]}
    stage.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(stage, target)


def port_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", PORT)) == 0


def wait_for_server(proc: subprocess.Popen, seconds: int = 120,
                    guard: resources.Guard | None = None) -> None:
    for _ in range(seconds):
        control.check_allowed(app_dir=APP_DIR)
        if guard is not None:
            guard.check()
        if proc.poll() is not None:
            raise RuntimeError("Dedicated local ComfyUI exited during startup")
        if port_in_use():
            return
        time.sleep(1)
    raise RuntimeError("Dedicated local ComfyUI did not start")


def stop_process(proc: subprocess.Popen | None, *, group: bool = False) -> None:
    """Reap an owned job even if it exits between poll and the stop signal."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if group:
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        try:
            if group:
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            pass
        proc.wait(timeout=10)


def request_stop(signum: int, frame) -> None:
    # launchd sends SIGTERM when unloading the job. Turn it into an exception
    # so run_once can stop its children and clean only its own PNGs in finally.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise RuntimeError("Local photo worker stopped by termination signal")


def prepare_sync(min_free_gb: int, timeout: int = 180) -> int:
    """Resend saved JPEGs/check fresh main before touching model weights or GPU."""
    invoke = [sys.executable, str(ROOT / "scripts/local_scene_sync.py"),
              "--publish", "--prepare-only", "--min-free-gb", str(min_free_gb)]
    process = None
    try:
        with control.transition_lock(app_dir=APP_DIR):
            control.check_allowed(app_dir=APP_DIR)
            process = subprocess.Popen(invoke, cwd=ROOT, start_new_session=True,
                                       env=control.command_env())
            control.register_runtime("sync", process.pid, root=ROOT, app_dir=APP_DIR)
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            control.check_allowed(app_dir=APP_DIR)
            if not storage_ok([ROOT, CACHE_DIR, APP_DIR], min_free_gb):
                raise RuntimeError("Free storage reached the safety floor during image preflight")
            if time.monotonic() >= deadline:
                raise RuntimeError("Fresh-main/outbox preflight timed out; generation deferred")
            time.sleep(.25)
        control.check_allowed(app_dir=APP_DIR)
        if process.returncode not in (0, PREPARE_NEEDS_GPU):
            raise RuntimeError("Fresh-main/outbox preflight failed; generation deferred (exit "
                               + str(process.returncode) + ")")
        return process.returncode
    finally:
        stop_process(process, group=True)


def run_once(limit: int = 1, min_free_gb: int = 12,
             timeout: int = 900) -> int:
    if not 1 <= limit <= 4:
        raise ValueError("Limit must be 1–4 images each run")
    if not 12 <= min_free_gb <= 100:
        raise ValueError("Free disk floor must be at least 12 GiB")
    if not 30 <= timeout <= 3600:
        raise ValueError("Per-image timeout must be between 30 and 3600")
    control.check_allowed(app_dir=APP_DIR)
    comfy = APP_DIR / "ComfyUI"
    interpreter = APP_DIR / "venv" / "bin" / "python"
    checkpoint = comfy / "models" / "checkpoints" / "sd_xl_base_1.0.safetensors"
    if generated_mib() >= MAX_GENERATED_MIB:
        raise RuntimeError("Generated JPEG cap reached; existing photos preserved")
    if any(path.is_symlink() for path in (APP_DIR, CACHE_DIR, comfy)):
        raise RuntimeError("Refusing symlinked worker installation directories")
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    output_dir = CACHE_DIR / "output"
    temp_dir = CACHE_DIR / "temp"
    for d in (output_dir, temp_dir):
        if d.is_symlink() or (d.exists() and not safe_dir(d)):
            raise RuntimeError("Unsafe cache directory: " + str(d))
        d.mkdir(exist_ok=True)
    if not storage_ok([ROOT, CACHE_DIR, APP_DIR], min_free_gb):
        raise RuntimeError(f"Less than {min_free_gb} GiB free; no image generation started")

    lock_path = APP_DIR / "worker.lock"
    if lock_path.is_symlink():
        raise RuntimeError("Unsafe worker lock")
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another photo run is active; skipping")
            return 0
        with control.transition_lock(app_dir=APP_DIR):
            control.check_allowed(app_dir=APP_DIR)
            control.register_runtime("worker", os.getpid(), root=ROOT, app_dir=APP_DIR)
        try:
            control.check_allowed(app_dir=APP_DIR)
            if prepare_sync(min_free_gb) == 0:
                clean_owned_intermediates(output_dir)
                update_status("idle", "Fresh main has no unheld missing scenes; saved JPEGs synchronized without GPU")
                print("No pending scenes; ComfyUI and model verification were not started", flush=True)
                return 0
            if not (interpreter.is_file() and (comfy / "main.py").is_file() and checkpoint.is_file()):
                raise RuntimeError("ComfyUI/model missing; install on your Mac first")
            if not (APP_DIR / "install-ok.txt").is_file():
                raise RuntimeError("Verified installation/model-license marker missing")
            guard = resources.Guard(APP_DIR)
            guard.check(force=True)
            if port_in_use():
                raise RuntimeError("Dedicated ComfyUI port occupied; refusing unsafe reuse")
            try:
                from .mac_scene_setup import verify_model
            except ImportError:
                from mac_scene_setup import verify_model
            verify_model(checkpoint)
            clean_owned_intermediates(output_dir)
            cmd = [
                str(interpreter), str(comfy / "main.py"),
                "--listen", "127.0.0.1", "--port", str(PORT),
                "--output-directory", str(output_dir),
                "--temp-directory", str(temp_dir),
                "--disable-auto-launch",
                "--disable-api-nodes", "--disable-all-custom-nodes",
                "--preview-method", "none", "--reserve-vram", "3",
            ]
            server = None
            sync_process = None
            server_errors = bytearray()
            error_reader = None
            try:
                guard.check(force=True)
                with control.transition_lock(app_dir=APP_DIR):
                    control.check_allowed(app_dir=APP_DIR)
                    server = subprocess.Popen(cmd, cwd=comfy, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.PIPE, start_new_session=True,
                                              env={**os.environ, "HF_HUB_OFFLINE": "1",
                                                   "TRANSFORMERS_OFFLINE": "1",
                                                   "OMP_NUM_THREADS": "2",
                                                   "VECLIB_MAXIMUM_THREADS": "2",
                                                   "PYTORCH_MPS_HIGH_WATERMARK_RATIO": "0.8",
                                                   "PYTORCH_MPS_LOW_WATERMARK_RATIO": "0.7"})
                    control.register_runtime("server", server.pid, root=ROOT, app_dir=APP_DIR)
                control.check_allowed(app_dir=APP_DIR)
                error_reader = threading.Thread(target=capture_server_errors,
                                                args=(server.stderr, server_errors), daemon=True)
                error_reader.start()
                wait_for_server(server, guard=guard)
                invoke = [
                    str(interpreter), str(ROOT / "scripts" / "local_scene_sync.py"),
                    "--publish", "--acknowledge-model-license",
                    "--checkpoint", checkpoint.name,
                    "--server", f"http://127.0.0.1:{PORT}",
                    "--limit", str(limit),
                    "--timeout", str(timeout),
                    "--output-dir", str(output_dir),
                    "--min-free-gb", str(min_free_gb),
                ]
                with control.transition_lock(app_dir=APP_DIR):
                    control.check_allowed(app_dir=APP_DIR)
                    sync_process = subprocess.Popen(invoke, cwd=ROOT, start_new_session=True)
                    control.register_runtime("sync", sync_process.pid, root=ROOT, app_dir=APP_DIR)
                deadline = time.monotonic() + limit * timeout + 900
                while sync_process.poll() is None:
                    control.check_allowed(app_dir=APP_DIR)
                    guard.check()
                    if not storage_ok([ROOT, CACHE_DIR, APP_DIR], min_free_gb):
                        raise RuntimeError("Free storage reached the safety floor; stopping local jobs")
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Local photo sync timed out; saved outbox will retry next run")
                    time.sleep(2)
                update_status("ok" if sync_process.returncode == 0 else "skipped",
                              "Local photo sync completed" if sync_process.returncode == 0
                              else "Generation or git publish failed; site fallback preserved. "
                              + server_errors.decode("utf-8", errors="replace")[-200:])
                return sync_process.returncode
            except resources.ResourceDeferred:
                raise
            except RuntimeError as exc:
                diagnostic = server_errors.decode("utf-8", errors="replace")[-200:]
                raise RuntimeError(str(exc) + ("; ComfyUI: " + diagnostic if diagnostic else "")) from exc
            finally:
                # A sync shutdown failure must not strand the GPU server or its
                # temporary images. Each cleanup gets its own finally boundary.
                try:
                    stop_process(sync_process, group=True)
                finally:
                    try:
                        stop_process(server)
                    finally:
                        if error_reader is not None:
                            error_reader.join(timeout=2)
                        control.clear_runtime(app_dir=APP_DIR)
                        clean_owned_intermediates(output_dir)
                        # This dedicated temp directory is never shared with other apps.
                        if safe_dir(temp_dir):
                            shutil.rmtree(temp_dir)
                            temp_dir.mkdir()
        finally:
            control.clear_runtime(app_dir=APP_DIR)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded Mac-only ComfyUI news photo worker")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--min-free-gb", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)
    previous_term = signal.signal(signal.SIGTERM, request_stop)
    try:
        return run_once(args.limit, args.min_free_gb, args.timeout)
    except resources.ResourceDeferred as exc:
        update_status("resource-paused", str(exc))
        print("Mac resource pause: " + str(exc), file=sys.stderr)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        try:
            latch = APP_DIR / "stopped.json"
            update_status("stopped" if latch.exists() or latch.is_symlink() else "skipped", str(exc))
        except (OSError, RuntimeError):
            pass
        print("Safe stop: " + str(exc), file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous_term)


if __name__ == "__main__":
    # Canonical arguments make PID ownership verifiable for both relative CLI
    # invocations and launchd, without scanning/killing unrelated Python jobs.
    absolute = str(Path(__file__).resolve())
    if sys.argv[0] != absolute:
        os.execv(sys.executable, [sys.executable, absolute, *sys.argv[1:]])
    sys.exit(main())
