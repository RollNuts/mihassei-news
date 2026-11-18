"""Opt-in, exact-head editorial merge; run only from the trusted main workflow.

The PR-body handoff is an editorial attestation by trusted repository writers,
not cryptographic proof that two GPT runs have different human operators.
No PR code/artifact is executed and no credentials are provisioned here.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

try:
    from .review_release_guard import GitHub as ReadGitHub, owner_check, require, sha
except ImportError:
    from review_release_guard import GitHub as ReadGitHub, owner_check, require, sha

REPO = "RollNuts/mihassei-news"
ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/check.yml"
VERIFY = "/actions/workflows/verify-public-photos.yml/dispatches"
MARKER = re.compile(r"<!-- publication-finalize\s*\n(.*?)\n-->", re.S)
RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{7,199}")
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
DATA = frozenset({
    "content/articles.json", "content/serials.json", "content/visual_reviews.json",
    "content/approved_photos.json", "content/market_outlook.json",
    "content/market_outlooks.json", "content/market_bulletins.json",
    "content/market_figures.json", "content/photo_stories.json",
    "content/photo_issues.json", "content/photo_editions.json",
    "assets/generated/manifest.json",
})
EDITORIAL_DOCS = frozenset({"docs/DAILY_EDITORIAL_BRIEF.md", "docs/EDITORIAL_IDEAS.md"})
MEDIA = re.compile(r"assets/(?:generated|editorial-scenes|article-diagrams|photos)/[a-z0-9][a-z0-9-]*\.(?:jpg|jpeg|png|svg)")
SOURCE = re.compile(r"maintenance/(?:native-image-candidates|photo-issues|editorial-scenes|article-diagrams)/[a-z0-9][a-z0-9/._-]*\.(?:json|png|jpg|jpeg|txt|part-[0-9]+)")
PLAN = re.compile(r"maintenance/photo-issue-plans/[a-z0-9]+(?:-[a-z0-9]+)*/[a-z0-9][a-z0-9-]*\.json")
KIND_DATA = {
    "news": {"content/articles.json"},
    "market": {"content/articles.json", "content/serials.json", "content/market_outlook.json",
               "content/market_outlooks.json", "content/market_bulletins.json", "content/market_figures.json"},
    "cast_story": {"content/photo_stories.json"},
    "photo_issue": {"content/photo_issues.json", "content/photo_editions.json", "content/photo_stories.json"},
}
COMMON_DATA = {"content/visual_reviews.json", "content/approved_photos.json", "assets/generated/manifest.json"}
PHOTO_VALIDATION = """from pathlib import Path
import json
from scripts.photo_issues import load_photo_issues, published_issues
from scripts.photo_stories import validate_photo_stories
from scripts.photo_editions import load_photo_editions
root = Path.cwd()
catalog = load_photo_issues(root)
published = published_issues(catalog, root=root)
validate_photo_stories(json.loads((root / 'content/photo_stories.json').read_text()),
                       catalog=catalog, published=published, root=root)
load_photo_editions(root, catalog=catalog, published=published)
"""


def allowed(path):
    return (isinstance(path, str) and ".." not in path and "//" not in path
            and (path in DATA or path in EDITORIAL_DOCS or MEDIA.fullmatch(path)
                 or SOURCE.fullmatch(path) or PLAN.fullmatch(path)))


def allowed_for(kind, path):
    return (allowed(path) and (path not in DATA or path in KIND_DATA[kind] | COMMON_DATA)
            and (not PLAN.fullmatch(path) or kind == "photo_issue"))


def approval(pr):
    blocks = MARKER.findall(pr.get("body") or "")
    require(len(blocks) == 1, "Exactly one publication-finalize handoff is required")
    obj = json.loads(blocks[0])
    required = {"version", "kind", "approved_head", "authorized_by", "author_run",
                "reviewer_run", "first_read", "merge_after_ci"}
    require(isinstance(obj, dict) and required <= obj.keys()
            and obj.keys() <= required | {"article_sha256"}, "Invalid handoff fields")
    require(type(obj["version"]) is int and obj["version"] == 1
            and obj["merge_after_ci"] is True and obj["first_read"] == "passed",
            "Explicit passed first read and merge opt-in are required")
    require(obj["kind"] in {"news", "market", "cast_story", "photo_issue"}, "Invalid publication kind")
    sha(obj["approved_head"])
    require(isinstance(obj["authorized_by"], str) and LOGIN.fullmatch(obj["authorized_by"]), "Invalid maintainer")
    require(all(isinstance(obj[k], str) and RUN.fullmatch(obj[k]) for k in ("author_run", "reviewer_run")), "Missing actual run references")
    if obj["kind"] in {"news", "market"}:
        require(obj["author_run"] != obj["reviewer_run"], "News/market require an independent reviewer run")
        fingerprints = obj.get("article_sha256")
        require(isinstance(fingerprints, dict) and (fingerprints or obj["kind"] == "market"), "Article fingerprints required")
        require(all(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", k) and isinstance(v, str)
                    and re.fullmatch(r"[a-f0-9]{64}", v) for k, v in fingerprints.items()), "Invalid article fingerprints")
    return obj


class GitHub(ReadGitHub):
    def get(self, path):
        if path != "":
            return super().get(path)
        # The inherited path reader deliberately rejects an empty path. Read
        # only this exact repository root, through the same no-redirect opener.
        headers = {"Accept": "application/vnd.github+json", "Cache-Control": "no-cache",
                   "User-Agent": "Veripsa-exact-head-finalizer"}
        if os.environ.get("GH_TOKEN"):
            headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
        request = urllib.request.Request("https://api.github.com/repos/" + REPO, headers=headers)
        with self.opener.open(request, timeout=20) as response:
            raw = response.read(64 * 1024 + 1)
        require(len(raw) <= 64 * 1024, "Repository response exceeded bound")
        return json.loads(raw)

    def request(self, method, path, body):
        require(method in {"PUT", "POST"} and
                (method == "PUT" and re.fullmatch(r"/pulls/[1-9][0-9]*/merge", path)
                 or method == "POST" and path == VERIFY), "Unapproved API mutation")
        token = os.environ.get("GH_TOKEN")
        require(bool(token), "Missing workflow token")
        request = urllib.request.Request("https://api.github.com/repos/" + REPO + path,
            data=json.dumps(body).encode(), method=method, headers={
                "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                "Authorization": "Bearer " + token, "User-Agent": "Veripsa-exact-head-finalizer",
                "X-GitHub-Api-Version": "2022-11-28"})
        # The inherited opener refuses redirects, including credential-bearing ones.
        with self.opener.open(request, timeout=20) as response:
            raw = response.read(64 * 1024 + 1)
        require(len(raw) <= 64 * 1024, "Mutation response exceeded bound")
        return json.loads(raw) if raw else None


def trusted(api, login):
    require(isinstance(login, str) and LOGIN.fullmatch(login), "Invalid repository writer")
    value = api.get("/collaborators/" + login + "/permission")
    require(value.get("permission") in {"admin", "maintain", "write"}, "Handoff requires a trusted repository writer")


def image_affecting(kind, files):
    return (kind in {"cast_story", "photo_issue"}
            or any(row["filename"].startswith(("assets/", "maintenance/"))
                   or row["filename"] in {"content/visual_reviews.json", "content/approved_photos.json",
                                          "content/market_figures.json"} for row in files))


def live_image_guard(api):
    repo = api.get("")
    require(repo.get("full_name") == REPO and repo.get("private") is False
            and repo.get("visibility") == "public", "Image publication requires the fixed public repository")
    workflow = api.get("/actions/workflows/free-cpu-scenes.yml")
    require(workflow.get("path") == ".github/workflows/free-cpu-scenes.yml"
            and workflow.get("state") == "active", "Image publication is stopped or unverified")


def git(root, *args):
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=root,
                          check=True, capture_output=True, text=True, timeout=60).stdout.strip()


def validate_merge(api, pr, run, root):
    """Merge only data into a temporary tree and freshly run trusted source guards.

    Harmless content/docs updates on main do not require repeating the long CI.
    Changed executable/config/test code since that CI does require fresh CI.
    """
    current = sha(api.get("/git/ref/heads/main")["object"]["sha"])
    tested = sha(next(p for p in run["pull_requests"] if p["number"] == pr["number"])["base"]["sha"])
    head = sha(pr["head"]["sha"])
    git(root, "fetch", "--no-tags", "https://github.com/" + REPO + ".git", current, tested, head)
    git(root, "merge-base", "--is-ancestor", tested, current)
    changed = git(root, "diff", "--name-only", tested + "..." + head).splitlines()
    require(changed and all(allowed(p) for p in changed), "PR includes code/configuration or unsupported publication paths")
    later = git(root, "diff", "--name-only", tested + ".." + current).splitlines()
    require(all(allowed(p) or p == "README.md" or p.startswith("docs/") and p.endswith(".md") for p in later),
            "Main runtime changed since CI; fresh CI is required")
    with tempfile.TemporaryDirectory(prefix="publication-finalize-") as folder:
        checkout = Path(folder) / "merged"
        git(root, "worktree", "add", "--detach", str(checkout), current)
        try:
            git(checkout, "-c", "user.name=Veripsa publication validation",
                "-c", "user.email=publication-validation@users.noreply.github.com",
                "merge", "--no-commit", "--no-ff", "--no-edit", head)
            # Reject symlinks/submodules: allowed paths must remain ordinary data.
            for path in changed:
                target = checkout / path
                require(target.is_file() and not target.is_symlink(), "Publication path is not an ordinary file")
            attestation = approval(pr)
            if attestation["kind"] in {"news", "market"}:
                try:
                    from .generated_photos import article_fingerprint
                except ImportError:
                    from generated_photos import article_fingerprint
                articles = json.loads((checkout / "content/articles.json").read_text())
                by_slug = {a["slug"]: a for a in articles}
                for slug, fingerprint in attestation["article_sha256"].items():
                    require(slug in by_slug and article_fingerprint(by_slug[slug]) == fingerprint,
                            "Approved article changed in the merged tree")
                old = json.loads(git(root, "show", tested + ":content/articles.json"))
                proposed = json.loads(git(root, "show", head + ":content/articles.json"))
                old = {a["slug"]: a for a in old}
                proposed = {a["slug"]: a for a in proposed}
                touched = {s for s in old.keys() | proposed.keys() if old.get(s) != proposed.get(s)}
                require(touched <= attestation["article_sha256"].keys(), "Changed article missing from approved fingerprint map")
            env = {k: v for k, v in os.environ.items()
                   if k not in {"GH_TOKEN", "GITHUB_TOKEN", "ACTIONS_RUNTIME_TOKEN", "PYTHONPATH"}}
            commands = [[sys.executable, "-S", "-c", "from scripts.build import load; load()"]]
            commands += [[sys.executable, "-S", "scripts/" + script, current] for script in
                         ("guard_serial_history.py", "guard_market_outlook_history.py", "guard_generated_media_history.py")]
            if attestation["kind"] in {"cast_story", "photo_issue"}:
                commands.append([sys.executable, "-S", "-c", PHOTO_VALIDATION])
            for command in commands:
                subprocess.run(command, cwd=checkout, env=env, check=True, timeout=90)
        finally:
            git(root, "worktree", "remove", "--force", str(checkout))
    return current


def dispatch(api, revision):
    revision = sha(revision)
    comparison = api.get("/compare/" + revision + "...main")
    require(comparison.get("status") in {"ahead", "identical"}, "Merged revision is not on main")
    api.request("POST", VERIFY, {"ref": "main", "inputs": {"expected_sha": revision}})
    return {"status": "verification_dispatched", "merge_sha": revision}


class PublicationBlocked(ValueError):
    """Expose the safe stopping snapshot without weakening a publication gate."""
    def __init__(self, pr, expected_head, stage, message):
        super().__init__(message)
        if pr["head"]["sha"] != expected_head:
            operation = "Current owner: review the changed head and refresh its CI and exact-head approval."
        elif pr.get("draft") is True:
            operation = "Current owner: after saving the passed current-head review, mark this same PR ready and reread draft=false."
        elif pr.get("mergeable") is False or pr.get("mergeable_state") == "dirty":
            operation = "Current owner: resolve latest-main conflicts in this same PR without force, then run CI and renew the changed-head review."
        elif pr.get("mergeable") is None or pr.get("mergeable_state") == "unknown":
            operation = "Current owner: reread GitHub mergeability at the next existing task; retain this PR and do not force a merge."
        else:
            operation = "Current owner: recheck this PR, its approval and current main before continuing the same handoff; do not force a merge."
        self.result = {"status": "unverified", "error": message, "pr": pr["number"],
                       "expected_head": expected_head, "current_head": pr["head"]["sha"],
                       "stage": stage, "blocking_snapshot": {
                           key: pr.get(key) for key in ("state", "draft", "mergeable", "mergeable_state")},
                       "next_operation": operation}


def same_approved_open_pr(pr, expected_head, attestation):
    return (pr.get("state") == "open" and pr.get("draft") is False
            and pr["head"]["sha"] == expected_head and approval(pr) == attestation
            and pr["head"]["repo"]["full_name"] == REPO
            and pr["base"]["repo"]["full_name"] == REPO and pr["base"]["ref"] == "main")


def await_mergeability(api, number, pr, expected_head, attestation):
    # GitHub briefly reports null/unknown while recalculating after a main push.
    # Only reread this same approved open PR; errors and changed gates still stop.
    # Two gates can each wait at most seven seconds; no job/CI rerun is requested.
    for delay in (2, 5):
        if not (pr.get("mergeable") is None and pr.get("mergeable_state") == "unknown"
                and same_approved_open_pr(pr, expected_head, attestation)):
            break
        time.sleep(delay)
        pr = api.get(f"/pulls/{number}")
    return pr


def finalize(api, number, expected_head, run_id=None, *, owner=owner_check, validate=validate_merge, root=ROOT):
    require(type(number) is int and number > 0
            and (run_id is None or type(run_id) is int and run_id > 0), "Invalid PR/run")
    expected_head = sha(expected_head)
    repo = api.get("")
    require(repo.get("full_name") == REPO and repo.get("private") is False
            and repo.get("visibility") == "public", "Only the fixed public repository is permitted")
    pr = api.get(f"/pulls/{number}")
    if not MARKER.search(pr.get("body") or ""):
        return {"status": "not_armed", "pr": number}
    attestation = approval(pr)
    require(pr["head"]["sha"] == expected_head == attestation["approved_head"], "Approved head changed")
    require(pr["head"]["repo"]["full_name"] == REPO and pr["base"]["repo"]["full_name"] == REPO
            and pr["base"]["ref"] == "main", "Only a same-repository PR to main is permitted")
    trusted(api, pr["user"]["login"])
    trusted(api, attestation["authorized_by"])
    if pr.get("merged") is True:
        return dispatch(api, pr["merge_commit_sha"])
    pr = await_mergeability(api, number, pr, expected_head, attestation)
    # An optional failed finalizer can itself leave a conflict-free PR unstable.
    # Exact primary CI and the fresh source/merge guards still precede every PUT.
    if not (same_approved_open_pr(pr, expected_head, attestation)
            and pr.get("mergeable") is True and pr.get("mergeable_state") in {"clean", "unstable"}):
        raise PublicationBlocked(pr, expected_head, "preflight", "PR is not safely mergeable")
    if run_id is None:
        # An editor can arm the existing PR body after CI has already finished.
        # No connector workflow-dispatch capability or second waiting task needed.
        listing = api.get("/actions/workflows/check.yml/runs?event=pull_request&head_sha="
                          + expected_head + "&per_page=20")
        candidates = listing.get("workflow_runs")
        require(isinstance(candidates, list), "CI run inventory unavailable")
        candidates = [r for r in candidates if r.get("head_sha") == expected_head
                      and r.get("event") == "pull_request" and r.get("path") == WORKFLOW]
        if not candidates:
            return {"status": "ci_pending", "pr": number, "head": expected_head}
        require(all(type(r.get("id")) is int and r["id"] > 0 for r in candidates), "Invalid CI run inventory")
        run_id = max(candidates, key=lambda r: r["id"])["id"]
    run = api.get(f"/actions/runs/{run_id}")
    if run.get("status") in {"queued", "in_progress", "waiting", "pending", "requested"}:
        return {"status": "ci_pending", "pr": number, "head": expected_head}
    require(run.get("path") == WORKFLOW and run.get("event") == "pull_request"
            and run.get("status") == "completed" and run.get("conclusion") == "success"
            and run.get("head_sha") == expected_head
            and run.get("head_repository", {}).get("full_name") == REPO, "CI is not successful for the exact head")
    matching = [p for p in run.get("pull_requests", []) if p.get("number") == number
                and p.get("head", {}).get("sha") == expected_head and p.get("base", {}).get("ref") == "main"]
    require(len(matching) == 1, "CI does not identify this PR/base")
    job_response = api.get(f"/actions/runs/{run_id}/jobs?per_page=100")
    jobs = job_response.get("jobs")
    require(isinstance(jobs, list) and job_response.get("total_count") == len(jobs)
            and len(jobs) <= 100, "Incomplete CI job listing")
    require(any(j.get("name") == "test" and j.get("conclusion") == "success" for j in jobs), "Validation job is unverified")
    files = api.pages(f"/pulls/{number}/files")
    require(files and all(f.get("status") in {"added", "modified"}
                         and allowed_for(attestation["kind"], f.get("filename")) for f in files),
            "Unsupported PR paths, publication kind or removals")
    images = image_affecting(attestation["kind"], files)
    if images:
        live_image_guard(api)
    current = validate(api, pr, run, root)
    require(owner(api, number, expected_head).get("status") == "clear", "Another PR owns this publication")
    fresh = api.get(f"/pulls/{number}")
    fresh = await_mergeability(api, number, fresh, expected_head, attestation)
    if not (same_approved_open_pr(fresh, expected_head, attestation)
            and fresh.get("mergeable") is True and fresh.get("mergeable_state") in {"clean", "unstable"}
            # PR base.sha may retain the creation/CI snapshot or refresh later.
            # The branch ref, not that PR metadata, binds our freshly checked tree.
            and api.get("/git/ref/heads/main")["object"]["sha"] == current):
        raise PublicationBlocked(fresh, expected_head, "premerge", "PR or main changed during finalization")
    latest = api.get(f"/actions/runs/{run_id}")
    require(latest.get("status") == "completed" and latest.get("conclusion") == "success"
            and latest.get("head_sha") == expected_head
            and latest.get("run_attempt") == run.get("run_attempt"), "CI was restarted during finalization")
    if images:
        live_image_guard(api)
    result = api.request("PUT", f"/pulls/{number}/merge", {"sha": expected_head, "merge_method": "merge"})
    require(isinstance(result, dict) and result.get("merged") is True, "Merge was not confirmed")
    return dispatch(api, result["sha"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--event", type=Path)
    mode.add_argument("--print-handoff", action="store_true", help="Print the PR-body opt-in; no API call or write")
    parser.add_argument("--kind", choices=tuple(KIND_DATA))
    parser.add_argument("--head")
    parser.add_argument("--authorized-by")
    parser.add_argument("--author-run")
    parser.add_argument("--reviewer-run")
    parser.add_argument("--article-sha", action="append", default=[], metavar="SLUG=SHA256")
    args = parser.parse_args(argv)
    try:
        if args.print_handoff:
            obj = {"version": 1, "kind": args.kind, "approved_head": args.head,
                   "authorized_by": args.authorized_by, "author_run": args.author_run,
                   "reviewer_run": args.reviewer_run, "first_read": "passed", "merge_after_ci": True}
            if args.kind in {"news", "market"}:
                pairs = [part.split("=", 1) for part in args.article_sha]
                require(all(len(pair) == 2 for pair in pairs), "Use SLUG=SHA256")
                require(len(dict(pairs)) == len(pairs), "Duplicate approved article")
                obj["article_sha256"] = dict(pairs)
            body = "<!-- publication-finalize\n" + json.dumps(obj, ensure_ascii=False, indent=2) + "\n-->"
            approval({"body": body})
            print(body)
            return 0
        event = json.loads(args.event.read_text())
        api = GitHub()
        if "workflow_run" in event:
            run = event["workflow_run"]
            candidates = run.get("pull_requests", [])
            if len(candidates) != 1:
                print(json.dumps({"status": "unverified_pr"}))
                return 0
            number, head, run_id = candidates[0]["number"], run["head_sha"], run["id"]
        elif "pull_request" in event:
            number, head, run_id = event["number"], event["pull_request"]["head"]["sha"], None
        else:
            inputs = event.get("inputs", {})
            number, head, run_id = int(inputs["finalize_pr"]), inputs["finalize_head"], int(inputs["ci_run_id"])
        print(json.dumps(finalize(api, number, head, run_id)))
        return 0
    except PublicationBlocked as exc:
        print(json.dumps(exc.result))
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
                summary.write("### Publication blocked\n\n```json\n"
                              + json.dumps(exc.result, indent=2) + "\n```\n")
        return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "unverified", "error": str(exc)[:400]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
