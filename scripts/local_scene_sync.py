#!/usr/bin/env python3
"""Optional one-command local picture sync for a scheduled home PC.

Never runs on GitHub Actions or Cloudflare. Only publishes images from the
user's computer after explicit --publish; keeps all normal content workflows.
Uses git credentials already configured locally and no paid image APIs.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=check, capture_output=True, text=True
    )


def safe_to_publish() -> None:
    if git("branch", "--show-current").stdout.strip() != "main":
        raise RuntimeError("For automatic publishing, check out the local main branch first")
    remote = git("remote", "get-url", "origin").stdout.strip()
    if "RollNuts/mihassei-news" not in remote:
        raise RuntimeError("Origin is not the expected newspaper repository")
    # Avoid touching developer changes or rebasing uncommitted files.
    if git("status", "--porcelain", "--untracked-files=all").stdout.strip():
        raise RuntimeError("Working tree is not clean. Review local changes before automatic publish")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Local scene generator + optional GitHub image-only push")
    ap.add_argument("--dry-run", action="store_true", help="Preview prompts; no git writes, model or network")
    ap.add_argument("--publish", action="store_true", help="Opt in to git push of generated assets only")
    ap.add_argument("--checkpoint", default="sd_xl_base_1.0.safetensors")
    ap.add_argument("--server", default="http://127.0.0.1:8188")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--acknowledge-model-license", action="store_true")
    args = ap.parse_args(argv)

    if args.publish and args.dry_run:
        ap.error("Choose either --publish or --dry-run")
    if args.publish:
        safe_to_publish()
        print("Fetching published articles before making images…", flush=True)
        git("pull", "--ff-only", "origin", "main")

    command = [
        sys.executable, str(ROOT / "scripts/local_scene_photos.py"),
        "--server", args.server, "--checkpoint", args.checkpoint,
        "--limit", str(args.limit), "--timeout", str(args.timeout),
    ]
    if args.dry_run:
        command += ["--dry-run"]
    elif args.acknowledge_model_license:
        command += ["--acknowledge-model-license"]
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        print("Image generation was incomplete; no automatic push was attempted.", file=sys.stderr)
        return result.returncode
    if not args.publish:
        if not args.dry_run:
            print("Generated files remain local. For automatic publishing, run with --publish.")
        return 0

    git("add", "--all", "--", "assets/generated")
    staged = git("diff", "--cached", "--quiet", check=False)
    if staged.returncode == 0:
        print("No new images; site publication remains unchanged.")
        return 0
    if staged.returncode != 1:
        raise RuntimeError("Could not inspect staged image changes")
    # Commit ONLY images + manifest, never article content or authentication files.
    git("commit", "-m", "images: locally generate staged comedy-news photos")
    # An intervening news publication is rebased in, rather than force-pushed.
    git("pull", "--rebase", "origin", "main")
    git("push", "origin", "HEAD:main")
    print("Published verified local scene images. Cloudflare Git integration will deploy them.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print("Local scene sync stopped safely: " + str(exc), file=sys.stderr)
        sys.exit(1)
