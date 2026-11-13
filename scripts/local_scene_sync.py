#!/usr/bin/env python3
"""Retryable image-only publishing from a disposable checkout of latest main.

Validated JPEGs survive generation/push failures in a bounded local outbox.
Never rebase or push the user's HEAD, edit articles, or use force push.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from .generated_photos import RELATIVE_DIR, generated_manifest, usable_generated_scene, MAX_ARCHIVE_BYTES, MAX_SCENE_BYTES
    from .local_scene_photos import load_articles, pending_articles, write_generated, check_storage, archive_bytes
    from .mac_scene_worker import APP_DIR
except ImportError:
    from generated_photos import RELATIVE_DIR, generated_manifest, usable_generated_scene, MAX_ARCHIVE_BYTES, MAX_SCENE_BYTES
    from local_scene_photos import load_articles, pending_articles, write_generated, check_storage, archive_bytes
    from mac_scene_worker import APP_DIR

PREPARE_NEEDS_GPU = 10
PREPARE_DEFERRED = 75

ROOT = Path(__file__).resolve().parents[1]
OUTBOX = APP_DIR / "pending"
REMOTES = {
    "https://github.com/RollNuts/mihassei-news.git",
    "https://github.com/RollNuts/mihassei-news",
    "git@github.com:RollNuts/mihassei-news.git",
    "ssh://git@github.com/RollNuts/mihassei-news.git",
}


def require_running() -> None:
    """Honor the persistent stop latch without introducing an import cycle."""
    try:
        from .mac_scene_control import check_allowed
    except ImportError:
        from mac_scene_control import check_allowed
    check_allowed(app_dir=APP_DIR)


def record_publish(status: str, detail: str, sha: str = "") -> None:
    try:
        from .mac_scene_control import record_publish_result
    except ImportError:
        from mac_scene_control import record_publish_result
    record_publish_result(status, detail, sha=sha, app_dir=APP_DIR)


def managed_jpeg_bytes() -> int:
    roots = {ROOT.resolve(), OUTBOX.resolve()}
    roots.update(p.resolve() for p in APP_DIR.glob("sync-*/checkout") if not p.is_symlink())
    return sum(archive_bytes(path) for path in roots)


def require_image_budget(additional: int = 0) -> None:
    if managed_jpeg_bytes() + additional > MAX_ARCHIVE_BYTES:
        raise RuntimeError("1.5 GiB combined JPEG budget reached; all existing photos preserved")


def check_checkout_budget(checkout: Path, revision: str, min_free_gb: int) -> None:
    """Reserve new remote JPEGs before a checkout/reset can materialize them."""
    sizes = git("ls-tree", "-rl", revision, "--", str(RELATIVE_DIR),
                cwd=checkout if checkout.is_dir() else ROOT).stdout.splitlines()
    incoming = sum(int(line.split()[3]) for line in sizes if line.endswith(".jpg"))
    change = incoming - archive_bytes(checkout)
    require_image_budget(change)
    volume = checkout if checkout.is_dir() else APP_DIR
    check_storage([volume], min_free_gb)
    required = min_free_gb * 1024**3 + 32 * 1024**2 + max(0, change)
    if shutil.disk_usage(volume).free < required:
        raise RuntimeError("Not enough free storage to materialize remote JPEGs safely")


def git(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = ["git", *args]
    options = {"cwd": cwd or ROOT, "text": True,
               "env": {**os.environ, "GIT_TERMINAL_PROMPT": "0"}}
    if not args or args[0] != "push":
        return subprocess.run(command, check=check, capture_output=True, timeout=120, **options)
    try:
        from .mac_scene_control import transition_lock
    except ImportError:
        from mac_scene_control import transition_lock
    # Serialize only process creation with stop/resume. Holding the lock while
    # waiting for a network push would make the stop button unresponsive.
    with transition_lock(app_dir=APP_DIR):
        require_running()
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
    try:
        stdout, stderr = process.communicate(timeout=120)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if check and result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command, stdout, stderr)
    return result


def safe_to_publish() -> None:
    remote = git("remote", "get-url", "origin").stdout.strip()
    push_remote = git("remote", "get-url", "--push", "origin").stdout.strip()
    if remote not in REMOTES or push_remote not in REMOTES:
        raise RuntimeError("Origin is not exactly the expected GitHub newspaper repository")


def transfer_scenes(source: Path, target: Path, articles: list[dict], *, missing_only: bool,
                    min_free_gb: int = 12) -> list[str]:
    copied = []
    records = generated_manifest(source)
    for article in articles:
        if not usable_generated_scene(article, root=source):
            continue
        if missing_only and usable_generated_scene(article, root=target):
            continue
        slug = article["slug"]
        record = records[slug]
        data = (source / RELATIVE_DIR / (slug + ".jpg")).read_bytes()
        target_image = target / RELATIVE_DIR / (slug + ".jpg")
        replaced = target_image.stat().st_size if target_image.is_file() else 0
        check_storage([target if target.exists() else target.parent], min_free_gb)
        require_image_budget(len(data) - replaced)
        write_generated(article, data, record["model"], root=target, origin=record["origin"])
        copied.append(slug)
    return copied


def interrupted_scenes(checkout: Path) -> list[dict]:
    """Recover new work, never replay an unchanged published snapshot."""
    changed = set(git("diff", "--name-only", "-z", "HEAD", "--", str(RELATIVE_DIR),
                      cwd=checkout).stdout.split("\0"))
    changed.update(git("ls-files", "--others", "--exclude-standard", "-z", "--",
                       str(RELATIVE_DIR), cwd=checkout).stdout.split("\0"))
    changed.discard("")
    if not changed:
        return []
    manifest = str(RELATIVE_DIR / "manifest.json")
    previous = {}
    if git("ls-tree", "--name-only", "HEAD", "--", manifest, cwd=checkout).stdout.strip():
        previous = json.loads(git("show", "HEAD:" + manifest, cwd=checkout).stdout)["images"]
    current = generated_manifest(checkout)
    return [article for article in load_articles(checkout)
            if str(RELATIVE_DIR / (article["slug"] + ".jpg")) in changed
            or current.get(article["slug"]) != previous.get(article["slug"])]


def retire_published(outbox: Path, published: Path, articles: list[dict]) -> None:
    """Remove only disposable outbox copies proven to be on remote main."""
    manifest = outbox / RELATIVE_DIR / "manifest.json"
    stage = manifest.with_suffix(".json.tmp")
    if any(path.is_symlink() for path in
           (outbox, outbox / "assets", manifest.parent, manifest, stage)):
        raise RuntimeError("Unsafe outbox manifest")
    if not manifest.exists():
        return
    # The read-only fallback loader deliberately tolerates corrupt manifests.
    # Cleanup must fail closed instead of replacing recovery data with {}.
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if (not isinstance(payload, dict) or payload.get("version") != 1
            or not isinstance(payload.get("images"), dict)):
        raise ValueError("Invalid outbox manifest; refusing to discard recovery data")
    records = payload["images"]
    for article in articles:
        slug = article["slug"]
        if usable_generated_scene(article, root=published) and usable_generated_scene(article, root=outbox):
            (outbox / RELATIVE_DIR / (slug + ".jpg")).unlink()
            records.pop(slug, None)
    if manifest.parent.is_dir():
        stage.write_text(json.dumps({"version": 1, "images": records}, indent=2) + "\n")
        os.replace(stage, manifest)


def publish_outbox(checkout: Path, outbox: Path, attempts: int = 3,
                   min_free_gb: int = 12) -> bool:
    for _ in range(attempts):
        require_running()
        check_storage([checkout, APP_DIR], min_free_gb)
        git("fetch", "origin", "main", cwd=checkout)
        require_running()
        check_checkout_budget(checkout, "FETCH_HEAD", min_free_gb)
        # Only this function's disposable checkout is reset, never ROOT.
        # Include new JPEGs in the index so reset removes stale untracked
        # generation results too. Their verified copies are in the outbox.
        git("add", "--", str(RELATIVE_DIR), cwd=checkout)
        git("reset", "--hard", "FETCH_HEAD", cwd=checkout)
        articles = load_articles(checkout)
        transfer_scenes(outbox, checkout, articles, missing_only=True,
                        min_free_gb=min_free_gb)
        git("add", "--", str(RELATIVE_DIR), cwd=checkout)
        changed = git("diff", "--cached", "--name-only", cwd=checkout).stdout.splitlines()
        if not changed:
            require_running()
            record_publish("ok", "No unpublished matching images; remote main verified",
                           git("rev-parse", "HEAD", cwd=checkout).stdout.strip())
            retire_published(outbox, checkout, articles)
            return True
        if any(not path.startswith("assets/generated/") for path in changed):
            raise RuntimeError("Refusing a commit containing non-image changes")
        git("-c", "user.name=Veripsa local photos", "-c",
            "user.email=277724446+RollNuts@users.noreply.github.com",
            "commit", "-m", "images: locally generate staged comedy-news photos", cwd=checkout)
        revision = git("rev-parse", "HEAD", cwd=checkout).stdout.strip()
        # Stop can arrive during model inference, fetch, transfer or commit.
        # Never begin another push after observing the persistent latch.
        require_running()
        pushed = git("push", "origin", "HEAD:refs/heads/main", cwd=checkout, check=False)
        if pushed.returncode == 0:
            record_publish("ok", "Image-only normal push succeeded", revision)
            require_running()
            retire_published(outbox, checkout, articles)
            return True
        record_publish("error", "Push deferred; JPEGs retained. " + pushed.stderr.strip()[-400:])
        print("Push deferred; retrying against latest main with saved JPEGs.", flush=True)
    print("Verified images remain in the local outbox for the next run.", file=sys.stderr)
    return False


def generate(args, checkout: Path) -> int:
    if not args.dry_run:
        require_running()
    command = [sys.executable, str(checkout / "scripts/local_scene_photos.py"),
               "--server", args.server, "--checkpoint", args.checkpoint,
               "--limit", str(args.limit), "--timeout", str(args.timeout),
               "--min-free-gb", str(args.min_free_gb)]
    if args.output_dir:
        command += ["--output-dir", args.output_dir]
    if args.dry_run:
        command += ["--dry-run"]
    elif args.acknowledge_model_license:
        command += ["--acknowledge-model-license"]
    env = dict(os.environ)
    if args.publish:
        # Include other working copies and reserve room for the durable outbox.
        extra = managed_jpeg_bytes() - archive_bytes(checkout) + args.limit * MAX_SCENE_BYTES
        env["VERIPSA_JPEG_RESERVED_BYTES"] = str(extra)
    return subprocess.run(command, cwd=checkout, check=False, env=env,
                          timeout=args.limit * args.timeout + 120).returncode


def sync(args) -> int:
    require_running()
    safe_to_publish()
    for directory in (APP_DIR, OUTBOX):
        if directory.is_symlink():
            raise RuntimeError("Refusing symlinked local sync state")
        directory.mkdir(parents=True, exist_ok=True)
    check_storage([ROOT, APP_DIR], args.min_free_gb)
    lock_path = APP_DIR / "sync.lock"
    if lock_path.is_symlink():
        raise RuntimeError("Unsafe sync lock")
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another image sync is active; skipping")
            # A busy lock has not checked fresh main. It must not be mistaken
            # for the prepare-only success code that means no GPU work exists.
            return PREPARE_DEFERRED if getattr(args, "prepare_only", False) else 0
        require_running()
        # A killed process can leave its private checkout behind. Recover it
        # under the same lock before starting a new run, then remove only it.
        for job in APP_DIR.glob("sync-*"):
            abandoned = job / "checkout"
            marker = job / ".veripsa-scene-job"
            if job.is_symlink() or abandoned.is_symlink() or marker.is_symlink():
                continue
            if not marker.is_file() or marker.read_text() != "veripsa-local-scenes-v2\n":
                continue
            if abandoned.is_dir():
                transfer_scenes(abandoned, OUTBOX, interrupted_scenes(abandoned), missing_only=True,
                                min_free_gb=args.min_free_gb)
                git("worktree", "remove", "--force", str(abandoned))
            marker.unlink()
            job.rmdir()
        require_running()
        git("fetch", "origin", "main")
        require_running()
        revision = git("rev-parse", "FETCH_HEAD").stdout.strip()
        temp = Path(tempfile.mkdtemp(prefix="sync-", dir=APP_DIR))
        marker = temp / ".veripsa-scene-job"
        marker.write_text("veripsa-local-scenes-v2\n")
        preserved = True
        checkout = temp / "checkout"
        try:
            require_running()
            check_checkout_budget(checkout, revision, args.min_free_gb)
            git("worktree", "add", "--detach", str(checkout), revision)
            try:
                # Recover old-runner files only when latest main needs them.
                missing = pending_articles(load_articles(checkout), root=checkout, limit=0)
                transfer_scenes(ROOT, OUTBOX, missing, missing_only=True,
                                min_free_gb=args.min_free_gb)
                if not publish_outbox(checkout, OUTBOX, min_free_gb=args.min_free_gb):
                    return 1
                check_storage([checkout, APP_DIR], args.min_free_gb)
                missing = pending_articles(load_articles(checkout), root=checkout, limit=0)
                if getattr(args, "prepare_only", False):
                    print(f"Fresh main checked: {len(missing)} unheld scene(s) still need generation", flush=True)
                    return PREPARE_NEEDS_GPU if missing else 0
                preserved = False
                try:
                    result = generate(args, checkout)
                finally:
                    # Keep successful scenes even if another scene fails.
                    transfer_scenes(checkout, OUTBOX, missing, missing_only=True,
                                    min_free_gb=args.min_free_gb)
                    preserved = True
                published = publish_outbox(checkout, OUTBOX, min_free_gb=args.min_free_gb)
                return result if published else 1
            finally:
                # Disposable worktree only. Original working tree is untouched.
                if preserved:
                    git("worktree", "remove", "--force", str(checkout))
        finally:
            if preserved and not checkout.exists():
                marker.unlink()
                temp.rmdir()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Resumable image-only local GitHub publishing")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--prepare-only", action="store_true",
                    help="Publish saved JPEGs without ComfyUI; exit 10 only if fresh main needs generation")
    ap.add_argument("--checkpoint", default="sd_xl_base_1.0.safetensors")
    ap.add_argument("--server", default="http://127.0.0.1:8188")
    ap.add_argument("--limit", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--output-dir", default="")
    ap.add_argument("--min-free-gb", type=int, default=12)
    ap.add_argument("--acknowledge-model-license", action="store_true")
    args = ap.parse_args(argv)
    if not 1 <= args.limit <= 4 or not 12 <= args.min_free_gb <= 100 or not 30 <= args.timeout <= 3600:
        ap.error("limit must be 1–4, free-space floor 12–100 GiB, timeout 30–3600 seconds")
    if args.publish and args.dry_run:
        ap.error("Choose either --publish or --dry-run")
    if args.prepare_only and not args.publish:
        ap.error("--prepare-only requires --publish")
    if args.publish and not args.acknowledge_model_license and not args.prepare_only:
        ap.error("Review the model license before publishing generated scenes")
    return sync(args) if args.publish else generate(args, ROOT)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        detail = str(exc) + ("; " + exc.stderr.strip()[-400:]
                             if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else "")
        try:
            # A stop may arrive after an already in-flight push succeeded.
            # Preserve that truthful SHA/result instead of relabelling it as
            # a GitHub failure merely because later cleanup was deferred.
            require_running()
            record_publish("error", detail)
        except (OSError, RuntimeError):
            pass
        print("Local scene sync stopped safely: " + detail, file=sys.stderr)
        sys.exit(1)
