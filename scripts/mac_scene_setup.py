#!/usr/bin/env python3
"""Opt-in Apple Silicon installer and launchd scheduler for local comedy photos.

The CLI is read-only by default. It never installs cloud APIs or paid plans,
never alters a system Python, never deletes unowned media, and refuses to
download a ~7 GB model without explicit model-license acknowledgment.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import fcntl
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

try:
    from .mac_scene_worker import (
        APP_DIR, CACHE_DIR, ROOT, MAX_GENERATED_MIB, generated_mib, safe_dir,
    )
except ImportError:
    from mac_scene_worker import (
        APP_DIR, CACHE_DIR, ROOT, MAX_GENERATED_MIB, generated_mib, safe_dir,
    )

LABEL = "com.veripsa.local-comedy-scenes"
PLIST = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
COMFY = APP_DIR / "ComfyUI"
VENV = APP_DIR / "venv"
PYTHON = VENV / "bin" / "python"
CHECKPOINT = COMFY / "models" / "checkpoints" / "sd_xl_base_1.0.safetensors"
MODEL_URL = ("https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/"
             "resolve/main/sd_xl_base_1.0.safetensors")
MODEL_SHA256 = "31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b"
MODEL_MAX_BYTES = 7_500_000_000
MODEL_MIN_BYTES = 6_000_000_000
MIN_RUNTIME_GIB = 12
MIN_INSTALL_GIB = 30
MIN_REUSE_GIB = 30
COMFY_RELEASE = "v0.43.0"


def gib_free(path: Path) -> float:
    return shutil.disk_usage(path).free / 1024**3


def ram_gib() -> float:
    try:
        output = subprocess.run(["sysctl", "-n", "hw.memsize"],
                                capture_output=True, text=True, check=True)
        return int(output.stdout.strip()) / 1024**3
    except (subprocess.CalledProcessError, ValueError):
        output = subprocess.run(["system_profiler", "SPHardwareDataType", "-json"],
                                capture_output=True, text=True, check=True)
        memory = json.loads(output.stdout)["SPHardwareDataType"][0]["physical_memory"]
        value, unit = memory.split()
        if unit not in ("GB", "TB"):
            raise RuntimeError("Unable to verify unified memory")
        return float(value) * (1024 if unit == "TB" else 1)


def require_supported_mac() -> None:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("Installation requires a local Apple Silicon Mac (arm64)")
    if ram_gib() < 15.5:
        raise RuntimeError("SDXL automation needs at least 16 GiB unified memory for this conservative setup")


def candidate_python(source: str) -> str:
    resolved = shutil.which(source) if not Path(source).is_file() else source
    if not resolved:
        raise RuntimeError("Python not found; install a supported Python 3.11–3.13 first")
    probe = subprocess.run([resolved, "-c", "import sys; print(sys.version_info.major,sys.version_info.minor)"],
                           capture_output=True, text=True, check=True)
    major, minor = map(int, probe.stdout.strip().split())
    if major != 3 or not 11 <= minor <= 13:
        raise RuntimeError("Please use Python 3.11–3.13 (e.g. python3.12); system Python is not changed")
    return resolved


def checksum(path: Path, limit: int = MODEL_MAX_BYTES) -> str:
    size = path.stat().st_size
    if not MODEL_MIN_BYTES <= size <= limit:
        raise RuntimeError("Model size is inconsistent with the official SDXL checkpoint")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model(path: Path) -> None:
    if not path.is_file() or checksum(path) != MODEL_SHA256:
        raise RuntimeError("Model does not match the official SDXL SHA-256; refusing to run")


def guard_install_paths() -> None:
    for item in (APP_DIR, CACHE_DIR, COMFY, VENV):
        if item.is_symlink():
            raise RuntimeError("Refusing an app/cache symlink: " + str(item))


@contextmanager
def maintenance_lock():
    """Serialize setup/cache cleanup with the generator using its existing lock."""
    if APP_DIR.is_symlink():
        raise RuntimeError("Refusing symlinked app directory")
    APP_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = APP_DIR / "worker.lock"
    if lock_path.is_symlink():
        raise RuntimeError("Refusing symlinked worker lock")
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("A photo worker or setup is running; retry after it finishes") from exc
        yield


def run(args: list[str], *, cwd: Path | None = None) -> None:
    print("+", " ".join(args[:4]), flush=True)
    subprocess.run(args, cwd=cwd, check=True)


def install_packages(env: dict) -> None:
    """Stop the owned pip subprocess if storage falls to the safety floor."""
    command = [str(PYTHON), "-m", "pip", "--no-cache-dir", "install",
               "-r", str(COMFY / "requirements.txt")]
    process = subprocess.Popen(command, cwd=COMFY, env=env)
    try:
        deadline = time.monotonic() + 1800
        while process.poll() is None:
            if min(gib_free(APP_DIR), gib_free(CACHE_DIR)) < MIN_RUNTIME_GIB + 0.25:
                raise RuntimeError("Storage safety floor reached during dependency installation")
            if time.monotonic() >= deadline:
                raise RuntimeError("Dependency installation timed out; rerun setup to resume")
            time.sleep(2)
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def download_checkpoint(destination: Path) -> None:
    """Hard-capped official URL, exact SHA-256, and no persistent duplicate cache."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(".part")
    if part.is_symlink():
        raise RuntimeError("Refusing symlinked temporary model path")
    part.unlink(missing_ok=True)  # From a previous interrupted run, never a complete model
    downloaded = 0
    digest = hashlib.sha256()
    try:
        request = urllib.request.Request(MODEL_URL, headers={
            "User-Agent": "Veripsa-Local-ComfyUI-Setup/1.0",
        })
        with urllib.request.urlopen(request, timeout=120) as resp, part.open("xb") as sink:
            if resp.geturl().split(":", 1)[0] != "https":
                raise RuntimeError("Refusing non-TLS model download redirect")
            declared = resp.headers.get("Content-Length")
            if declared and int(declared) > MODEL_MAX_BYTES:
                raise RuntimeError("Model exceeds the permitted 7.5 GB download cap")
            while True:
                block = resp.read(8 * 1024 * 1024)
                if not block:
                    break
                downloaded += len(block)
                if downloaded > MODEL_MAX_BYTES:
                    raise RuntimeError("Download exceeded the hard 7.5 GB cap")
                # Reserve the next write too: testing only the current free
                # bytes can cross the 12 GiB floor by an entire 8 MiB block.
                if gib_free(destination.parent) < MIN_RUNTIME_GIB + len(block) / 1024**3:
                    raise RuntimeError("Disk free space fell below the 12 GiB safety floor")
                sink.write(block)
                digest.update(block)
        if not MODEL_MIN_BYTES <= downloaded <= MODEL_MAX_BYTES or digest.hexdigest() != MODEL_SHA256:
            raise RuntimeError("Downloaded model failed exact SHA-256 verification")
        os.replace(part, destination)
    finally:
        part.unlink(missing_ok=True)


def install(*, python_command: str, source_model: str = "",
            download_model: bool = False, accepted_license: bool = False) -> None:
    require_supported_mac()
    guard_install_paths()
    if not accepted_license:
        raise RuntimeError("Review the SDXL OpenRAIL++ terms; pass --acknowledge-model-license to confirm")
    if source_model and download_model:
        raise RuntimeError("Choose either --model-path (reuse) or --download-model, not both")
    if not (source_model or download_model or CHECKPOINT.is_file()):
        raise RuntimeError("No existing checkpoint: choose --model-path or --download-model explicitly")
    source = Path(source_model).expanduser().resolve() if source_model else None
    if source is not None:
        verify_model(source)
    if CHECKPOINT.is_file():
        verify_model(CHECKPOINT)
    free_required = MIN_REUSE_GIB if (source or CHECKPOINT.is_file()) else MIN_INSTALL_GIB
    if min(gib_free(ROOT), gib_free(Path.home())) < free_required:
        raise RuntimeError(f"Need {free_required} GiB free before installation; no changes made")
    executable = candidate_python(python_command)
    with maintenance_lock():
        _install_runtime(executable, source)


def _install_runtime(executable: str, source: Path | None) -> None:
    """Mutate the dedicated installation only while the worker lock is held."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    guard_install_paths()
    setup_tmp = CACHE_DIR / "setup-tmp"
    if setup_tmp.is_symlink():
        raise RuntimeError("Refusing symlinked setup temporary directory")
    setup_tmp.mkdir(exist_ok=True)
    env = {**os.environ, "TMPDIR": str(setup_tmp), "PIP_NO_CACHE_DIR": "1",
           "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    try:
        if not COMFY.exists():
            run(["git", "clone", "--depth", "1", "--single-branch", "--branch", COMFY_RELEASE,
                 "https://github.com/Comfy-Org/ComfyUI.git", str(COMFY)])
        elif not (safe_dir(COMFY) and (COMFY / "main.py").is_file()):
            raise RuntimeError("Existing app path is not a valid ComfyUI checkout; not overwriting it")
        if not VENV.exists():
            run([executable, "-m", "venv", str(VENV)])
        elif not PYTHON.is_file():
            raise RuntimeError("Existing virtual environment is incomplete; refusing to overwrite it")
        if not (APP_DIR / "install-ok.txt").exists():
            install_packages(env)
        # Always validate GPU support; a CPU-only installation would be impractical.
        subprocess.run([str(PYTHON), "-c",
                        "import torch; assert torch.backends.mps.is_available(), 'Apple MPS unavailable'"],
                       cwd=COMFY, check=True)
        CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
        if not CHECKPOINT.exists():
            if source:
                # Reuse the verified weights without downloading another 7 GB copy.
                CHECKPOINT.symlink_to(source)
            else:
                download_checkpoint(CHECKPOINT)
        verify_model(CHECKPOINT)
        if gib_free(APP_DIR) < MIN_RUNTIME_GIB:
            raise RuntimeError("Insufficient free storage after setup")
        (APP_DIR / "install-ok.txt").write_text("ComfyUI / official SDXL verified\n", encoding="utf-8")
        print("Installed locally; images will run only after the optional scheduler is enabled.")
    finally:
        if safe_dir(setup_tmp):
            shutil.rmtree(setup_tmp)


def agent_payload() -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [str(PYTHON), str(ROOT / "scripts" / "mac_scene_worker.py"),
                             "--limit", "4", "--min-free-gb", "12"],
        "WorkingDirectory": str(ROOT),
        "StartCalendarInterval": [{"Hour": hour, "Minute": 45} for hour in range(24)],
        "RunAtLoad": True,
        "KeepAlive": False,
        "Nice": 10,
        "ProcessType": "Background",
        "EnvironmentVariables": {
            "PATH": ":".join((str(VENV / "bin"), "/Library/Developer/CommandLineTools/usr/bin",
                              "/opt/homebrew/bin", str(Path.home() / ".local/bin"),
                              "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")),
            "GIT_TERMINAL_PROMPT": "0",
        },
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }


def disable_schedule() -> None:
    if platform.system() != "Darwin":
        raise RuntimeError("launchd is available only on macOS")
    if PLIST.is_file():
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(PLIST)],
                       capture_output=True, check=False)
        PLIST.unlink()
    print("Local photo schedule disabled; generated images and model left intact")


def enable_schedule() -> None:
    require_supported_mac()
    if not ((COMFY / "main.py").is_file() and PYTHON.is_file() and CHECKPOINT.is_file()):
        raise RuntimeError("Install the model/runtime successfully before enabling launchd")
    if not (APP_DIR / "install-ok.txt").is_file():
        raise RuntimeError("Installation validation marker missing")
    # Schedule only after a real successful manual generation/push.
    try:
        status = json.loads((APP_DIR / "last-run.json").read_text())
    except (OSError, ValueError):
        status = {}
    if status.get("status") != "ok":
        raise RuntimeError("Run mac_scene_worker.py successfully once before enabling the schedule")
    if min(gib_free(ROOT), gib_free(APP_DIR)) < MIN_RUNTIME_GIB:
        raise RuntimeError("Low free space: refusing scheduler installation")
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    if PLIST.is_symlink():
        raise RuntimeError("Refusing symlinked LaunchAgent")
    if PLIST.exists():
        disable_schedule()
    PLIST.write_bytes(plistlib.dumps(agent_payload()))
    try:
        subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST)],
                       check=True)
    except subprocess.CalledProcessError:
        PLIST.unlink(missing_ok=True)
        raise
    print("Local scheduler enabled: up to 4 missing scenes hourly, also after login")


def check() -> None:
    print("Platform:", platform.system(), platform.machine())
    print("Repository:", ROOT)
    for name, path in (("App", APP_DIR), ("Cache", CACHE_DIR), ("Model", CHECKPOINT)):
        print(name + ":", path, "exists=" + str(path.exists()))
    print("Free disk GiB:", round(gib_free(ROOT), 1))
    print("Generated JPG MiB:", round(generated_mib(), 1), "/", MAX_GENERATED_MIB)
    print("Automatic scheduling enabled:", PLIST.is_file())
    status = APP_DIR / "last-run.json"
    if status.is_file() and not status.is_symlink():
        print("Last run:", status.read_text(encoding="utf-8")[:1000])


def cleanup_cache() -> None:
    require_supported_mac()
    if CACHE_DIR.is_symlink():
        raise RuntimeError("Refusing symlinked cache")
    with maintenance_lock():
        if safe_dir(CACHE_DIR):
            shutil.rmtree(CACHE_DIR)
    print("Only VeripsaScenes temporary/cache files were deleted; model/photos preserved")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Storage-aware, optional Mac image generation setup")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--install", action="store_true")
    group.add_argument("--enable-schedule", action="store_true")
    group.add_argument("--disable-schedule", action="store_true")
    group.add_argument("--cleanup-cache", action="store_true")
    parser.add_argument("--python", default="python3", help="Supported Python 3.11–3.13")
    parser.add_argument("--model-path", default="", help="Reuse an existing verified SDXL .safetensors")
    parser.add_argument("--download-model", action="store_true", help="Opt in to one official 6.94 GB model download")
    parser.add_argument("--acknowledge-model-license", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.install:
            install(python_command=args.python, source_model=args.model_path,
                    download_model=args.download_model,
                    accepted_license=args.acknowledge_model_license)
        elif args.enable_schedule:
            enable_schedule()
        elif args.disable_schedule:
            disable_schedule()
        elif args.cleanup_cache:
            cleanup_cache()
        else:
            check()  # Default is *strictly* read-only.
        return 0
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print("Safe stop:", str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
