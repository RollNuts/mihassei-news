#!/usr/bin/env python3
"""GitHub-only, opt-in single-run publisher of approved JPEG photo-motion.

The uploader deliberately lives in the GitHub workflow, not here. This script
never calls network services, commits, deploys, or changes Mac settings.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from .motion_clips import candidates, generate
except ImportError:
    from motion_clips import candidates, generate

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path(".github/motion-clips.json")
REPO = "RollNuts/mihassei-news"


def load_settings(root: Path = ROOT) -> dict:
    path = root / CONFIG
    if path.is_symlink():
        raise RuntimeError("Unsafe motion clip config symlink")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Missing/invalid motion clip config; leave automation OFF") from exc
    if (not isinstance(config, dict)
            or config.get("version") != 1
            or type(config.get("enabled")) is not bool
            or type(config.get("max_per_run")) is not int
            or config["max_per_run"] != 1
            or type(config.get("min_free_gib")) is not int
            or not 2 <= config["min_free_gib"] <= 12):
        raise RuntimeError("Invalid motion clip limits; refusing to start")
    return config


def require_trusted_runner(env: dict | None = None) -> None:
    e = os.environ if env is None else env
    if (e.get("GITHUB_ACTIONS") != "true"
            or e.get("GITHUB_REPOSITORY") != REPO
            or e.get("GITHUB_REF") != "refs/heads/main"):
        raise RuntimeError("Automatic publishing only runs inside the owner's main-branch GitHub Actions")


def pending_count(root: Path = ROOT) -> int:
    """Read the selected, approved backlog without encoding or installing anything."""
    if not load_settings(root)["enabled"]:
        return 0
    return len(candidates(root))


def run(root: Path = ROOT, *, env: dict | None = None) -> list[str]:
    config = load_settings(root)
    if not config["enabled"]:
        return []
    require_trusted_runner(env)
    return generate(root, limit=config["max_per_run"],
                    min_free_gib=config["min_free_gib"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only status or bounded trusted-runner motion clip maker")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--status", action="store_true", help="Emit true/false for the durable switch")
    mode.add_argument("--pending", action="store_true", help="Count selected missing clips without encoding")
    mode.add_argument("--generate", action="store_true", help="Generate at most one video, only on main Actions")
    args = parser.parse_args(argv)
    try:
        if args.status:
            print("true" if load_settings()["enabled"] else "false")
            return 0
        if args.pending:
            print(pending_count())
            return 0
        produced = run()
        print("GitHub-only video generation:", produced or "nothing needed / disabled")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print("Motion clip publication halted safely: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
