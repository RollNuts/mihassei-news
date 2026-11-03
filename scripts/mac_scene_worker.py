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
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = Path.home() / "Library" / "Application Support" / "VeripsaScenes"
CACHE_DIR = Path.home() / "Library" / "Caches" / "VeripsaScenes"
PORT = 18188
OWN_PNG = re.compile(r"mihassei-staged-scene_[0-9]{5}_\.png\Z")
MAX_GENERATED_MIB = 1536


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
    APP_DIR.mkdir(parents=True, exist_ok=True)
    target = APP_DIR / "last-run.json"
    stage = APP_DIR / "last-run.json.tmp"
    stage.write_text(json.dumps({
        "status": status, "detail": detail[:300],
        "at_utc": datetime.now(timezone.utc).isoformat()
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(stage, target)


def port_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", PORT)) == 0


def wait_for_server(proc: subprocess.Popen, seconds: int = 120) -> None:
    for _ in range(seconds):
        if proc.poll() is not None:
            raise RuntimeError("Dedicated local ComfyUI exited during startup")
        if port_in_use():
            return
        time.sleep(1)
    raise RuntimeError("Dedicated local ComfyUI did not start")


def run_once(limit: int = 4, min_free_gb: int = 12,
             timeout: int = 900) -> int:
    if not 1 <= limit <= 8:
        raise ValueError("Limit must be 1–8 images each run")
    if not 12 <= min_free_gb <= 100:
        raise ValueError("Free disk floor must be at least 12 GiB")
    if not 30 <= timeout <= 3600:
        raise ValueError("Per-image timeout must be between 30 and 3600")
    comfy = APP_DIR / "ComfyUI"
    interpreter = APP_DIR / "venv" / "bin" / "python"
    checkpoint = comfy / "models" / "checkpoints" / "sd_xl_base_1.0.safetensors"
    if not (interpreter.is_file() and (comfy / "main.py").is_file() and checkpoint.is_file()):
        raise RuntimeError("ComfyUI/model missing; install on your Mac first")
    if generated_mib() >= MAX_GENERATED_MIB:
        raise RuntimeError("Generated JPEG cap reached; existing photos preserved")
    if any(path.exists() and path.is_symlink() for path in (APP_DIR, CACHE_DIR, comfy)):
        raise RuntimeError("Refusing symlinked worker installation directories")
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    output_dir = CACHE_DIR / "output"
    temp_dir = CACHE_DIR / "temp"
    for d in (output_dir, temp_dir):
        if d.exists() and not safe_dir(d):
            raise RuntimeError("Unsafe cache directory: " + str(d))
        d.mkdir(exist_ok=True)
    if not storage_ok([ROOT, CACHE_DIR, APP_DIR], min_free_gb):
        raise RuntimeError(f"Less than {min_free_gb} GiB free; no image generation started")

    with (APP_DIR / "worker.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another photo run is active; skipping")
            return 0
        if port_in_use():
            raise RuntimeError("Dedicated ComfyUI port occupied; refusing unsafe reuse")
        clean_owned_intermediates(output_dir)
        cmd = [
            str(interpreter), str(comfy / "main.py"),
            "--listen", "127.0.0.1", "--port", str(PORT),
            "--output-directory", str(output_dir),
            "--temp-directory", str(temp_dir),
            "--disable-auto-launch",
        ]
        server = None
        try:
            server = subprocess.Popen(cmd, cwd=comfy, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
            wait_for_server(server)
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
            result = subprocess.run(
                invoke, cwd=ROOT, check=False,
                timeout=limit * timeout + 240,
            )
            update_status("ok" if result.returncode == 0 else "skipped",
                          "Local photo sync completed" if result.returncode == 0
                          else "Generation or git publish failed; site fallback preserved")
            return result.returncode
        finally:
            if server is not None and server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=10)
            clean_owned_intermediates(output_dir)
            # This dedicated temp directory is never shared with other apps.
            if safe_dir(temp_dir):
                shutil.rmtree(temp_dir)
                temp_dir.mkdir()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded Mac-only ComfyUI news photo worker")
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--min-free-gb", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)
    try:
        return run_once(args.limit, args.min_free_gb, args.timeout)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        try:
            update_status("skipped", str(exc))
        except OSError:
            pass
        print("Safe stop: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
