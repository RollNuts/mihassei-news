#!/usr/bin/env python3
"""Validate a reviewed CPU image bundle and publish images against latest main.

The source bundle is immutable recovery data. Publishing uses a private copied
outbox and the existing image-only, bounded normal-push implementation.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

try:
    from .cpu_photo_model import MODEL, LEGACY_MODEL, ORIGIN
    from . import local_scene_sync as sync
    from .generated_photos import (
        RELATIVE_DIR, SAFE_SLUG, generated_manifest, usable_generated_scene,
        visually_held_scene,
    )
    from .local_scene_photos import stable_seed
except ImportError:
    from cpu_photo_model import MODEL, LEGACY_MODEL, ORIGIN
    import local_scene_sync as sync
    from generated_photos import (
        RELATIVE_DIR, SAFE_SLUG, generated_manifest, usable_generated_scene,
        visually_held_scene,
    )
    from local_scene_photos import stable_seed

ROOT = Path(__file__).resolve().parents[1]
MAX_BUNDLE_IMAGES = 3
MIN_FREE_GIB = 12


def read_bounded_json(path: Path, maximum: int) -> object:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise ValueError("Unsafe or oversized bundle JSON: " + str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def validate_bundle(source: Path) -> list[dict]:
    """Accept only an exact, small CPU-generated article/manifest/JPEG bundle."""
    source = Path(source)
    if source.is_symlink() or not source.is_dir():
        raise ValueError("Review bundle must be a real directory")
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError("Symlink in review bundle: " + str(path))
        if not path.is_dir() and not path.is_file():
            raise ValueError("Non-regular file in review bundle: " + str(path))
    articles = read_bounded_json(source / "content/articles.json", 1024**2)
    manifest = read_bounded_json(source / RELATIVE_DIR / "manifest.json", 256 * 1024)
    if (not isinstance(articles, list) or not 1 <= len(articles) <= MAX_BUNDLE_IMAGES
            or any(not isinstance(article, dict) for article in articles)):
        raise ValueError("Review bundle must contain one to three article objects")
    slugs = [article.get("slug") for article in articles]
    if (any(not isinstance(slug, str) or not SAFE_SLUG.fullmatch(slug) for slug in slugs)
            or len(set(slugs)) != len(slugs)):
        raise ValueError("Invalid or duplicate review article slug")
    if (not isinstance(manifest, dict) or set(manifest) != {"version", "images"}
            or type(manifest.get("version")) is not int or manifest["version"] != 1
            or not isinstance(manifest.get("images"), dict)
            or set(manifest["images"]) != set(slugs)):
        raise ValueError("Review manifest must contain exactly the bundled images")
    expected = {"content/articles.json", "assets/generated/manifest.json"}
    expected.update("assets/generated/" + slug + ".jpg" for slug in slugs)
    actual = {path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file()}
    if actual != expected:
        raise ValueError("Unexpected or missing files in review bundle")
    for article in articles:
        record = manifest["images"][article["slug"]]
        if (not isinstance(record, dict) or record.get("origin") not in ("github-cpu-lcm", ORIGIN)
                or record.get("model") not in (MODEL, LEGACY_MODEL)
                or type(record.get("seed")) is not int
                or record["seed"] != stable_seed(article)
                or not usable_generated_scene(article, root=source)):
            raise ValueError("Invalid CPU image, fingerprint or exact integer seed: " + article["slug"])
    return articles


def trusted_ci() -> bool:
    """Publishing remains confined to the explicitly enabled public main job."""
    required = {
        "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "RollNuts/mihassei-news",
        "GITHUB_REF": "refs/heads/main", "RUNNER_OS": "Linux",
        "VERIPSA_ENABLE_FREE_CPU": "1",
    }
    if (any(os.getenv(key) != value for key, value in required.items())
            or os.getenv("GITHUB_EVENT_NAME") not in {"schedule", "workflow_dispatch", "push"}):
        return False
    try:
        event = read_bounded_json(Path(os.environ["GITHUB_EVENT_PATH"]), 4 * 1024**2)
        repository = event.get("repository", {}) if isinstance(event, dict) else {}
        return (repository.get("private") is False
                and repository.get("full_name") == "RollNuts/mihassei-news")
    except (KeyError, OSError, ValueError):
        return False


def reject_held_reapproval(source: Path, target: Path, articles: list[dict]) -> None:
    """A held/stale image needs new bytes, including after a main-push race."""
    incoming, existing = generated_manifest(source), generated_manifest(target)
    for article in articles:
        slug = article["slug"]
        if (isinstance(incoming.get(slug), dict)
                and isinstance(existing.get(slug), dict)
                and incoming[slug].get("sha256") == existing[slug].get("sha256")
                and not usable_generated_scene(article, root=target)):
            reason = "Held" if visually_held_scene(article, root=target) else "Stale"
            raise ValueError(reason + " JPEG cannot be reapproved without new bytes: " + slug)


@contextmanager
def isolated_sync(root: Path, state: Path):
    """Scope the existing helper's state to this one disposable CI operation."""
    original = (sync.ROOT, sync.APP_DIR, sync.OUTBOX, sync.transfer_scenes)
    transfer = sync.transfer_scenes

    def guarded_transfer(source, target, articles, **options):
        reject_held_reapproval(source, target, articles)
        return transfer(source, target, articles, **options)

    sync.ROOT, sync.APP_DIR, sync.OUTBOX = root, state, state / "pending"
    sync.transfer_scenes = guarded_transfer
    try:
        yield
    finally:
        sync.ROOT, sync.APP_DIR, sync.OUTBOX, sync.transfer_scenes = original


def publish_bundle(source: Path, *, root: Path = ROOT) -> dict:
    articles = validate_bundle(source)
    if not trusted_ci():
        raise RuntimeError("CPU publication requires an opt-in public main GitHub CPU job")
    runner_temp = Path(os.getenv("RUNNER_TEMP", ""))
    if not os.getenv("RUNNER_TEMP") or runner_temp.is_symlink() or not runner_temp.is_dir():
        raise RuntimeError("A real RUNNER_TEMP directory is required for isolated publishing")
    source, root = Path(source).resolve(), Path(root).resolve()
    if runner_temp.resolve().is_relative_to(source) or runner_temp.resolve().is_relative_to(root):
        raise RuntimeError("RUNNER_TEMP must be outside the review bundle and repository")
    # Nothing here writes into the Mac app directory or the source review bundle.
    with tempfile.TemporaryDirectory(prefix="veripsa-cpu-publish-", dir=runner_temp) as temporary:
        state = Path(temporary)
        with isolated_sync(root, state):
            sync.safe_to_publish()
            sync.check_storage([root, state], MIN_FREE_GIB)
            sync.OUTBOX.mkdir()
            sync.transfer_scenes(source, sync.OUTBOX, articles, missing_only=True,
                                 min_free_gb=MIN_FREE_GIB)
            sync.git("fetch", "origin", "main")
            revision = sync.git("rev-parse", "FETCH_HEAD").stdout.strip()
            checkout = state / "checkout"
            sync.check_checkout_budget(checkout, revision, MIN_FREE_GIB)
            sync.git("worktree", "add", "--detach", str(checkout), revision)
            try:
                successful = sync.publish_outbox(checkout, sync.OUTBOX, attempts=3,
                                                  min_free_gb=MIN_FREE_GIB)
                record = json.loads((state / "publish-result.json").read_text(encoding="utf-8"))
                return {"status": "ok" if successful else "deferred",
                        "bundle_slugs": [article["slug"] for article in articles],
                        "publish": record}
            finally:
                # Only the worktree created inside this owned temporary directory.
                if checkout.is_dir() and not checkout.is_symlink():
                    sync.git("worktree", "remove", "--force", str(checkout))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate or safely publish a CPU review image bundle")
    parser.add_argument("--source", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--publish", action="store_true")
    mode.add_argument("--dry-run", action="store_true", help="Validate only (the default)")
    args = parser.parse_args(argv)
    try:
        if args.publish:
            result = publish_bundle(args.source)
        else:
            articles = validate_bundle(args.source)
            result = {"status": "validated", "bundle_slugs": [article["slug"] for article in articles]}
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result["status"] == "deferred" else 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print("CPU publication stopped safely: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
