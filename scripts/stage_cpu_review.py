#!/usr/bin/env python3
"""Durably stage one CPU review bundle. Never approve, publish, or update a ref."""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

try:
    from .generated_photos import article_fingerprint, SAFE_SLUG
    from .publish_cpu_scenes import validate_bundle, trusted_ci
    from .cpu_photo_model import ORIGIN, REFERENCE_ORIGIN
except ImportError:
    from generated_photos import article_fingerprint, SAFE_SLUG
    from publish_cpu_scenes import validate_bundle, trusted_ci
    from cpu_photo_model import ORIGIN, REFERENCE_ORIGIN

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "RollNuts/mihassei-news"
GIT_SHA = re.compile(r"[0-9a-f]{40}")
MAX_FILE = 1024 * 1024


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def blob_sha(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def git_sha(value):
    if not isinstance(value, str) or not GIT_SHA.fullmatch(value):
        raise ValueError("Invalid Git SHA response")
    return value


class MissingRef(RuntimeError):
    pass


class GitHub:
    def __init__(self):
        self.token = os.environ.get("GH_TOKEN", "")
        if not self.token:
            raise RuntimeError("GH_TOKEN is required")

    def api(self, method, path, body=None):
        url = "https://api.github.com/repos/" + REPOSITORY + path
        request = urllib.request.Request(url, data=encoded(body) if body is not None else None,
                                         method=method, headers={
            "Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            # Only an actual 404 from the exact ref read means absent. Other
            # failures, malformed results and ambiguous writes stop this run.
            if method == "GET" and path.startswith("/git/ref/heads/") and exc.code == 404:
                raise MissingRef("Candidate ref is absent") from exc
            raise RuntimeError("GitHub " + method + " failed: HTTP " + str(exc.code)) from exc
        if len(raw) > 8 * 1024 * 1024:
            raise RuntimeError("Oversized GitHub response")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise RuntimeError("Invalid GitHub response")
        return result

    def ref(self, branch):
        result = self.api("GET", "/git/ref/heads/" + urllib.parse.quote(branch, safe="/"))
        if result.get("ref") != "refs/heads/" + branch or result.get("object", {}).get("type") != "commit":
            raise RuntimeError("Unexpected candidate ref response")
        return git_sha(result["object"]["sha"])

    def tree(self, commit):
        record = self.api("GET", "/git/commits/" + git_sha(commit))
        tree_sha = git_sha(record.get("tree", {}).get("sha"))
        result = self.api("GET", "/git/trees/" + tree_sha + "?recursive=1")
        if result.get("truncated") is not False or result.get("sha") != tree_sha:
            raise RuntimeError("Incomplete Git tree")
        files = {}
        for entry in result.get("tree", []):
            if entry.get("type") != "tree":
                path = entry.get("path")
                if not isinstance(path, str) or path in files:
                    raise RuntimeError("Invalid Git tree entries")
                files[path] = {key: entry.get(key) for key in ("mode", "type", "sha")}
        return tree_sha, files

    def read(self, files, path):
        record = files.get(path, {})
        if record.get("mode") != "100644" or record.get("type") != "blob":
            raise RuntimeError("Missing or unsafe candidate file: " + path)
        result = self.api("GET", "/git/blobs/" + git_sha(record.get("sha")))
        if result.get("encoding") != "base64" or not isinstance(result.get("content"), str):
            raise RuntimeError("Unexpected blob encoding")
        data = base64.b64decode("".join(result["content"].split()), validate=True)
        if len(data) > MAX_FILE or result.get("size") != len(data) or blob_sha(data) != record["sha"]:
            raise RuntimeError("Git blob readback mismatch: " + path)
        return data


def one_article(data, slug):
    articles = json.loads(data)
    matches = [a for a in articles if isinstance(a, dict) and a.get("slug") == slug] if isinstance(articles, list) else []
    if len(matches) != 1:
        raise ValueError("Expected one exact source article")
    return matches[0]


def prepare(root, *, slug, run_id, source_commit, request):
    if not SAFE_SLUG.fullmatch(slug) or not re.fullmatch(r"[1-9][0-9]*", run_id):
        raise ValueError("An exact slug and original run ID are required")
    git_sha(source_commit)
    original = one_article((root / "content/articles.json").read_bytes(), slug)
    fingerprint = article_fingerprint(original)
    return {"version": 1, "kind": "cpu_image_candidate", "status": "awaiting_visual_review",
            "repository": REPOSITORY, "slug": slug, "source_article_sha256": fingerprint,
            "logical_run_id": run_id, "source_commit": source_commit, "request": request,
            "branch": f"cpu-image-candidate/{slug}/{fingerprint[:16]}-{run_id}",
            "directory": "maintenance/cpu-image-candidates/" + slug,
            "original": original}


def validate_plan(plan):
    """Keep even direct recovery callers confined to a CPU candidate ref."""
    keys = {"version", "kind", "status", "repository", "slug", "source_article_sha256",
            "logical_run_id", "source_commit", "request", "branch", "directory", "original"}
    if not isinstance(plan, dict) or set(plan) != keys:
        raise ValueError("Invalid CPU candidate plan")
    slug, run_id, request = plan["slug"], plan["logical_run_id"], plan["request"]
    if (not isinstance(slug, str) or not SAFE_SLUG.fullmatch(slug)
            or not isinstance(run_id, str) or not re.fullmatch(r"[1-9][0-9]*", run_id)
            or not isinstance(plan["original"], dict) or plan["original"].get("slug") != slug
            or not isinstance(request, dict)
            or set(request) != {"backfill_scene", "backfill_reference", "retry_held"}
            or not isinstance(request["backfill_scene"], str)
            or type(request["backfill_reference"]) is not bool or type(request["retry_held"]) is not bool):
        raise ValueError("Invalid CPU candidate source or request")
    fingerprint = article_fingerprint(plan["original"])
    expected = {"version": 1, "kind": "cpu_image_candidate", "status": "awaiting_visual_review",
                "repository": REPOSITORY, "source_article_sha256": fingerprint,
                "branch": f"cpu-image-candidate/{slug}/{fingerprint[:16]}-{run_id}",
                "directory": "maintenance/cpu-image-candidates/" + slug}
    if any(plan[key] != value for key, value in expected.items()):
        raise ValueError("CPU candidate identity or isolated destination mismatch")
    git_sha(plan["source_commit"])


def validate_source(api, plan):
    validate_plan(plan)
    _, source_files = api.tree(plan["source_commit"])
    original = one_article(api.read(source_files, "content/articles.json"), plan["slug"])
    if original != plan["original"]:
        raise RuntimeError("Original source article snapshot mismatched; do not regenerate or save")
    prefix = plan["directory"] + "/"
    if any(path.startswith(prefix) for path in source_files):
        raise RuntimeError("Candidate destination already exists in the source tree")
    return source_files


def source_state(api, plan):
    """A later main edit never restamps or discards the original candidate."""
    revision = api.ref("main")
    _, files = api.tree(revision)
    try:
        current = one_article(api.read(files, "content/articles.json"), plan["slug"])
    except ValueError:
        return {"source_state": "missing", "current_article_sha256": None}
    return {"source_state": "current" if current == plan["original"] else "stale",
            "current_article_sha256": article_fingerprint(current)}


def bundle_files(source, plan):
    articles = validate_bundle(source)
    expected = copy.deepcopy(plan["original"])
    request = plan["request"]
    if request["backfill_scene"]:
        expected.update(scene_prompt=request["backfill_scene"], visual_strategy="cpu-photo")
    if articles != [expected]:
        raise ValueError("Review article does not match the original source and requested scene")
    manifest = json.loads((source / "assets/generated/manifest.json").read_bytes())
    record = manifest["images"][plan["slug"]]
    origin = REFERENCE_ORIGIN if request["backfill_reference"] else ORIGIN
    if record.get("origin") != origin:
        raise ValueError("Candidate must retain its actual CPU origin")
    if request["backfill_scene"] and record.get("source_article_sha256") != plan["source_article_sha256"]:
        raise ValueError("Backfill original source article hash mismatch")
    prefix = plan["directory"] + "/"
    files = {prefix + "bundle/" + path.relative_to(source).as_posix(): path.read_bytes()
             for path in source.rglob("*") if path.is_file()}
    files[prefix + "source-article.json"] = encoded(plan["original"])
    if any(len(data) > MAX_FILE for data in files.values()):
        raise ValueError("Oversized candidate file")
    return files


def verify_candidate(api, plan, commit, source_files, expected_files=None):
    _, files = api.tree(commit)
    prefix = plan["directory"] + "/"
    receipt_path = prefix + "receipt.json"
    receipt = json.loads(api.read(files, receipt_path))
    baseline = {key: value for key, value in plan.items() if key != "original"}
    if set(receipt) != set(baseline) | {"files"} or any(receipt.get(key) != value for key, value in baseline.items()):
        raise RuntimeError("Candidate identity conflict; refusing mutation")
    records = receipt.get("files")
    expected_paths = {prefix + "source-article.json", prefix + "bundle/content/articles.json",
                      prefix + "bundle/assets/generated/manifest.json",
                      prefix + "bundle/assets/generated/" + plan["slug"] + ".jpg"}
    if not isinstance(records, dict) or set(records) != expected_paths:
        raise RuntimeError("Invalid candidate file list")
    if {p: r for p, r in files.items() if p not in expected_paths | {receipt_path}} != source_files:
        raise RuntimeError("Candidate changed files outside its isolated directory")
    data = {}
    for path, record in records.items():
        content = api.read(files, path)
        if record != {"sha256": sha256(content), "bytes": len(content)}:
            raise RuntimeError("Candidate SHA256 readback mismatch: " + path)
        data[path] = content
    if data[prefix + "source-article.json"] != encoded(plan["original"]):
        raise RuntimeError("Candidate original source snapshot mismatch")
    if expected_files is not None and data != expected_files:
        raise RuntimeError("A different candidate already occupies this run identity")
    with tempfile.TemporaryDirectory(prefix="cpu-candidate-readback-") as temporary:
        source = Path(temporary)
        for path, content in data.items():
            if path.startswith(prefix + "bundle/"):
                target = source / path.removeprefix(prefix + "bundle/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        if bundle_files(source, plan) != data:
            raise RuntimeError("Candidate provenance readback mismatch")
    return {"status": "saved", "branch": plan["branch"], "commit": commit,
            "jpeg_sha256": records[prefix + "bundle/assets/generated/" + plan["slug"] + ".jpg"]["sha256"]}


def probe(api, plan, source_files, expected_files=None):
    try:
        commit = api.ref(plan["branch"])
    except MissingRef:
        return None
    return verify_candidate(api, plan, commit, source_files, expected_files)


def save(api, plan, source):
    source_files = validate_source(api, plan)
    files = bundle_files(source, plan)
    existing = probe(api, plan, source_files, files)
    if existing:
        return existing
    receipt = {key: value for key, value in plan.items() if key != "original"}
    receipt["files"] = {path: {"sha256": sha256(data), "bytes": len(data)} for path, data in files.items()}
    upload = {**files, plan["directory"] + "/receipt.json": encoded(receipt)}
    entries = []
    for path, data in upload.items():
        result = api.api("POST", "/git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
        if result.get("sha") != blob_sha(data):
            raise RuntimeError("Uploaded Git blob SHA mismatch")
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": result["sha"]})
    base_tree, _ = api.tree(plan["source_commit"])
    tree = git_sha(api.api("POST", "/git/trees", {"base_tree": base_tree, "tree": entries}).get("sha"))
    commit = git_sha(api.api("POST", "/git/commits", {
        "message": "Stage CPU photo candidate: " + plan["slug"], "tree": tree,
        "parents": [plan["source_commit"]]}).get("sha"))
    verify_candidate(api, plan, commit, source_files, files)
    # Recheck the immutable original snapshot before making the candidate
    # reachable. Later main edits leave it bound to the original source SHA.
    validate_source(api, plan)
    try:
        api.api("POST", "/git/refs", {"ref": "refs/heads/" + plan["branch"], "sha": commit})
    except (RuntimeError, OSError, ValueError):
        recovered = probe(api, plan, source_files, files)
        if recovered:
            return recovered
        raise
    confirmed = probe(api, plan, source_files, files)
    if not confirmed:
        raise RuntimeError("Candidate save is unconfirmed; retain original artifact, do not regenerate")
    return confirmed


def recover(api, plan, source, attempt):
    if attempt < 1:
        raise ValueError("Invalid original run attempt")
    source_files = validate_source(api, plan)
    existing = probe(api, plan, source_files)
    if existing or attempt == 1:
        return existing
    if source.exists():
        raise RuntimeError("Retry download directory already exists")
    # The previous attempt's always-upload artifact is the only allowed source.
    # Missing/expired/failed download stops; a rerun never invokes generation.
    subprocess.run(["gh", "run", "download", plan["logical_run_id"], "--repo", REPOSITORY,
                    "--name", "cpu-scenes-review-" + plan["logical_run_id"], "--dir", str(source)], check=True)
    return save(api, plan, source)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("recover", "save"))
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if not trusted_ci() or os.getenv("GITHUB_EVENT_NAME") != "workflow_dispatch" or os.getenv("SCENE_PUBLISH") != "0":
            raise RuntimeError("Candidate saving requires manual public-main CPU review with publish=false")
        source_commit = os.environ["GITHUB_SHA"]
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
        if head != source_commit:
            raise RuntimeError("Checkout must match the original run SHA")
        plan = prepare(ROOT, slug=os.environ["SCENE_SLUG"].strip(), run_id=os.environ["GITHUB_RUN_ID"],
                       source_commit=source_commit, request={"backfill_scene": os.getenv("BACKFILL_SCENE", "").strip(),
                       "backfill_reference": os.getenv("BACKFILL_REFERENCE") == "1", "retry_held": os.getenv("RETRY_HELD") == "1"})
        api = GitHub()
        result = (recover(api, plan, args.source, int(os.environ["GITHUB_RUN_ATTEMPT"]))
                  if args.mode == "recover" else save(api, plan, args.source))
        if result:
            result.update(source_state(api, plan))
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
                summary.write("\n### CPU candidate saved for visual review\n\n")
                summary.write(f"- Branch: `{result['branch']}`\n- Commit: `{result['commit']}`\n")
                summary.write(f"- JPEG SHA256: `{result['jpeg_sha256']}`\n- Original article SHA256: `{plan['source_article_sha256']}`\n")
                summary.write(f"- Original source versus current main: **{result['source_state']}**\n")
                summary.write("- Status: awaiting visual review; no publication or approval. A stale candidate remains bound to its original article.\n")
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("saved=" + ("1" if result else "0") + "\n")
        print(json.dumps(result or {"status": "absent", "branch": plan["branch"]}))
        return 0
    except (KeyError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print("CPU candidate not confirmed saved; retain original bytes and do not regenerate: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
