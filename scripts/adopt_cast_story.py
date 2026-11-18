#!/usr/bin/env python3
"""Check one creator draft; --write appends one local story without publishing."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
from datetime import date, datetime
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

if __package__:
    from .cloud_image_guard import check_cloud_images_allowed
    from .photo_issues import (ROOT, MODEL_CHAT_URLS, MAX_ARCHIVE_BYTES, archive_bytes,
                              load_photo_issues, published_issues)
    from .photo_stories import JST, _stamp, _text, read_story_document, validate_photo_stories
else:
    from cloud_image_guard import check_cloud_images_allowed
    from photo_issues import (ROOT, MODEL_CHAT_URLS, MAX_ARCHIVE_BYTES, archive_bytes,
                             load_photo_issues, published_issues)
    from photo_stories import JST, _stamp, _text, read_story_document, validate_photo_stories

SOURCE_REF = "refs/remotes/origin/cast-source-drafts"
SOURCE_BRANCH = "refs/heads/cast-source-drafts"
MAX_DRAFT_BYTES = 16 * 1024
MIN_FREE_BYTES = 12 * 1024**3
DRAFT_FIELDS = {"schema_version", "model_id", "name", "created_date", "source_chat_url",
                "photo_reference", "post_text", "spoken_line", "future_idea"}
PHOTO_FIELDS = {"issue_slug", "cut_id", "image_sha256"}


def _git(root, *arguments):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    env["PATH"] = "/Library/Developer/CommandLineTools/usr/bin:" + env.get("PATH", "")
    try:
        return subprocess.run(["git", *arguments], cwd=root, env=env, check=True,
                              capture_output=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Git source check failed: " + arguments[0]) from exc


def _source_head(root):
    return _git(root, "rev-parse", "--verify", SOURCE_REF + "^{commit}").decode().strip()


def _blob(root, commit, path):
    return _git(root, "rev-parse", "--verify", commit + ":" + path).decode().strip()


@contextmanager
def _write_lock(root):
    path = Path(_git(root, "rev-parse", "--git-path", "cast-story-adoption.lock").decode().strip())
    if not path.is_absolute():
        path = root / path
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another cast story write is in progress") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate field in creator draft")
        result[key] = value
    return result


def _read_draft(root, model, commit):
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("An exact 40-character source commit is required")
    path = "drafts/cast-voices/" + model["id"] + ".json"
    head = _source_head(root)
    _git(root, "merge-base", "--is-ancestor", commit, head)
    source_blob = _blob(root, commit, path)
    if _blob(root, head, path) != source_blob:
        raise ValueError("This model's draft changed after the pinned source commit")
    spec = commit + ":" + path
    if _git(root, "cat-file", "-t", spec).strip() != b"blob":
        raise ValueError("Creator draft is not a Git blob")
    size = int(_git(root, "cat-file", "-s", spec))
    if not 0 < size <= MAX_DRAFT_BYTES:
        raise ValueError("Creator draft exceeds the 16 KiB limit")
    raw = _git(root, "cat-file", "blob", spec)
    draft = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
    if (not isinstance(draft, dict) or set(draft) != DRAFT_FIELDS
            or type(draft["schema_version"]) is not int or draft["schema_version"] != 1
            or draft["model_id"] != model["id"] or draft["name"] != model["name"]
            or draft["source_chat_url"] != MODEL_CHAT_URLS[model["id"]]):
        raise ValueError("Creator draft does not match the fixed model, name or source chat")
    _text(draft["post_text"], 180, "text", multiline=True)
    if (not isinstance(draft["created_date"], str)
            or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", draft["created_date"])):
        raise ValueError("Creator draft needs its real creation date")
    date.fromisoformat(draft["created_date"])
    if not isinstance(draft["photo_reference"], dict) or not draft["photo_reference"]:
        raise ValueError("Creator draft needs a photo reference")
    for field in ("spoken_line", "future_idea"):
        if draft[field] is not None and not isinstance(draft[field], str):
            raise ValueError("Invalid creator draft " + field)
    return draft, path, source_blob


def _append_bytes(original, story):
    # Keep every existing record's original bytes, including multiline escapes.
    text = original.decode("utf-8")
    document = json.loads(text)
    if set(document) != {"schema_version", "stories"} or not isinstance(document["stories"], list):
        raise ValueError("Invalid story document")
    end = text.rfind("]")  # The only other top-level value is the numeric version.
    block = "\n".join("    " + line for line in json.dumps(story, ensure_ascii=False, indent=2).splitlines())
    separator = ",\n" if document["stories"] else "\n"
    result = (text[:end].rstrip() + separator + block + "\n  " + text[end:]).encode("utf-8")
    expected = {**document, "stories": [*document["stories"], story]}
    if json.loads(result) != expected:
        raise ValueError("Could not append a story without changing existing records")
    return result


def _adopt(model_id, source_commit, *, root, fetch, write, now):
    root = Path(root)
    if model_id not in MODEL_CHAT_URLS:
        raise ValueError("Unknown fixed cast model")
    if fetch:
        _git(root, "fetch", "--no-tags", "origin", SOURCE_BRANCH + ":" + SOURCE_REF)
    catalog_path = root / "content/photo_issues.json"
    history_path = root / "content/photo_stories.json"
    document_path = root / "content/photo_stories" / (model_id + ".json")
    if any(part.is_symlink() for path in (catalog_path, history_path, document_path) for part in (path, *path.parents)):
        raise ValueError("Unsafe story input path")
    history_bytes = history_path.read_bytes()
    catalog_bytes, original = catalog_path.read_bytes(), document_path.read_bytes()
    catalog = load_photo_issues(root)
    model = next(model for model in catalog["models"] if model["id"] == model_id)
    draft, draft_path, source_blob = _read_draft(root, model, source_commit)
    document = read_story_document(root)
    clock = now or datetime.now(JST)
    if clock.tzinfo is None:
        raise ValueError("Story clock needs a timezone")
    clock = clock.astimezone(JST)
    if date.fromisoformat(draft["created_date"]) > clock.date():
        raise ValueError("Creator draft is dated after the actual publication day")
    report = {"model_id": model_id, "source_commit": source_commit,
              "story_path": document_path.relative_to(root).as_posix()}
    same_day = next((story for story in document["stories"]
                     if story["model_id"] == model_id
                     and _stamp(story["published_at"]).astimezone(JST).date() == clock.date()), None)
    if same_day:
        return {**report, "status": "skipped", "reason": "this model already has a story on the actual JST publication day; verify its PR and production separately",
                "existing_story_id": same_day["id"]}
    duplicate = next((story for story in document["stories"]
                      if story["model_id"] == model_id and story["text"] == draft["post_text"]), None)
    if duplicate:
        return {**report, "status": "skipped", "reason": "same model post_text already exists in the story catalog; verify its PR and production separately",
                "existing_story_id": duplicate["id"]}
    reference = draft["photo_reference"]
    if not PHOTO_FIELDS & reference.keys():
        return {**report, "status": "pending", "reason": "photo_reference describes a photo but has no exact published issue/cut/hash; no photo was substituted"}
    if not PHOTO_FIELDS <= reference.keys():
        raise ValueError("Creator photo reference needs issue_slug, cut_id and image_sha256 together")
    source = {key: reference[key] for key in PHOTO_FIELDS}
    releases = published_issues(catalog, root=root, now=clock)
    issue = next((issue for issue in releases if issue["slug"] == source["issue_slug"]), None)
    if not issue or issue["model_id"] != model_id:
        raise ValueError("Creator photo reference is not this model's currently published issue")
    candidate = {"id": model_id + "-post-" + clock.strftime("%Y%m%d%H%M%S%f"),
                 "model_id": model_id, "published_at": clock.isoformat(),
                 "setting": {key: issue["setting"][key] for key in ("year", "month")},
                 "text": draft["post_text"], "source": source}
    proposed = {**document, "stories": [*document["stories"], candidate]}
    validate_photo_stories(proposed, catalog=catalog, published=releases, root=root, now=clock)
    if not write:
        return {**report, "status": "candidate", "candidate": candidate}
    check_cloud_images_allowed()
    if shutil.disk_usage(root).free < MIN_FREE_BYTES:
        raise RuntimeError("Free storage is below 12 GiB; story staging stopped")
    if archive_bytes(root) > MAX_ARCHIVE_BYTES:
        raise RuntimeError("Photo/news JPEG archive exceeds 1.5 GiB; story staging stopped")
    staged = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".photo-stories-", dir=document_path.parent, delete=False) as stream:
            staged = Path(stream.name)
            stream.write(_append_bytes(original, candidate))
            stream.flush()
            os.fsync(stream.fileno())
        # Refresh only this branch. Another creator's commit does not invalidate
        # the selected person's immutable source when its exact blob is unchanged.
        _git(root, "fetch", "--no-tags", "origin", SOURCE_BRANCH + ":" + SOURCE_REF)
        check_cloud_images_allowed()
        head = _source_head(root)
        _git(root, "merge-base", "--is-ancestor", source_commit, head)
        if _blob(root, head, draft_path) != source_blob:
            raise RuntimeError("This model's source draft changed; newer source preserved")
        if any(part.is_symlink() for path in (catalog_path, history_path, document_path) for part in (path, *path.parents)):
            raise RuntimeError("Story input path changed; newer local data preserved")
        if (document_path.read_bytes() != original or history_path.read_bytes() != history_bytes
                or catalog_path.read_bytes() != catalog_bytes):
            raise RuntimeError("Story inputs changed; newer local data preserved")
        latest = read_story_document(root)
        latest["stories"].append(candidate)
        validate_photo_stories(latest, catalog=catalog, published=releases, root=root, now=clock)
        if shutil.disk_usage(root).free < MIN_FREE_BYTES or archive_bytes(root) > MAX_ARCHIVE_BYTES:
            raise RuntimeError("Image storage protection changed; story staging stopped")
        os.replace(staged, document_path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    return {**report, "status": "staged", "candidate": candidate}


def adopt(model_id, source_commit, *, root=ROOT, fetch=False, write=False, now=None):
    root = Path(root)
    if root.is_symlink():
        raise ValueError("Unsafe story root")
    root = root.resolve()
    with _write_lock(root) if write else nullcontext():
        return _adopt(model_id, source_commit, root=root, fetch=fetch, write=write, now=now)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_id", choices=MODEL_CHAT_URLS)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--fetch", action="store_true", help="explicitly refresh only cast-source-drafts")
    parser.add_argument("--write", action="store_true", help="refresh the source branch and stage one local story; never commit or publish")
    args = parser.parse_args(argv)
    try:
        result = adopt(args.model_id, args.source_commit, fetch=args.fetch, write=args.write)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (OSError, ValueError, RuntimeError, StopIteration) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
