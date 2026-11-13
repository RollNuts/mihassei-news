#!/usr/bin/env python3
"""Read-only PR ownership and photo-backlog checks. Never generates or publishes."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.parse
import urllib.request

REPO = "RollNuts/mihassei-news"
SHA = re.compile(r"[0-9a-f]{40}")
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
JSON_PATHS = {
    "content/articles.json": None,
    "content/visual_reviews.json": ("reviews",),
    "content/approved_photos.json": ("approved",),
    "assets/generated/manifest.json": ("images", "rejected"),
}
ASSET = re.compile(r"assets/(?:generated|editorial-scenes|article-diagrams|photos)/([a-z0-9]+(?:-[a-z0-9]+)*)\.(?:jpg|jpeg|png|svg)")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(value: str) -> str:
    require(isinstance(value, str) and SHA.fullmatch(value) is not None, "Invalid commit SHA")
    return value


def records(path: str, data: object) -> dict:
    if path == "content/articles.json":
        require(isinstance(data, list), "Articles must be an array")
        result = {}
        for row in data:
            require(isinstance(row, dict) and isinstance(row.get("slug"), str), "Invalid article")
            key = row["slug"]
            require(SLUG.fullmatch(key) is not None and key not in result, "Invalid/duplicate article slug")
            result[key] = row
        return result
    require(isinstance(data, dict), "Invalid media document")
    result = {}
    for section in JSON_PATHS[path]:
        rows = data.get(section, {})
        require(isinstance(rows, dict), "Invalid media records")
        for key, row in rows.items():
            require(isinstance(key, str) and SLUG.fullmatch(key) is not None, "Invalid media slug")
            require(isinstance(row, dict), "Invalid media record")
            result.setdefault(key, {})[section] = row
    return result


def changed_slugs(path: str, before: object, after: object) -> set[str]:
    old, new = records(path, before), records(path, after)
    return {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}


class GitHub:
    """Bounded GETs only, with a fixed origin and no credential-bearing redirects."""
    def __init__(self):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        self.opener = urllib.request.build_opener(NoRedirect)
        self.cache = {}

    def get(self, path: str):
        require(path.startswith("/") and not path.startswith("//"), "Invalid API path")
        headers = {"Accept": "application/vnd.github+json", "Cache-Control": "no-cache",
                   "User-Agent": "Veripsa-read-only-release-guard"}
        if os.environ.get("GH_TOKEN"):
            headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
        request = urllib.request.Request("https://api.github.com/repos/" + REPO + path, headers=headers)
        with self.opener.open(request, timeout=20) as response:
            raw = response.read(6 * 1024 * 1024 + 1)
        require(len(raw) <= 6 * 1024 * 1024, "API response exceeded bound")
        return json.loads(raw)

    def pages(self, path: str) -> list:
        rows = []
        for page in range(1, 11):
            part = self.get(path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
            require(isinstance(part, list), "Incomplete API listing")
            rows.extend(part)
            if len(part) < 100:
                return rows
        raise ValueError("Listing limit reached; absence/ownership is unverified")

    def document(self, path: str, revision: str):
        key = path, sha(revision)
        if key not in self.cache:
            obj = self.get("/contents/" + urllib.parse.quote(path, safe="/") + "?ref=" + revision)
            require(obj.get("encoding") == "base64" and bool(obj.get("content")), "File bytes unavailable")
            raw = base64.b64decode("".join(obj["content"].split()), validate=True)
            digest = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            require(digest == obj.get("sha"), "File blob integrity mismatch")
            self.cache[key] = json.loads(raw)
        return self.cache[key]


def signature(pr: dict) -> tuple:
    return pr["number"], pr["state"], sha(pr["head"]["sha"]), sha(pr["base"]["sha"]), pr["base"]["ref"]


def touched(api, pr: dict) -> set[str]:
    files = api.pages(f'/pulls/{pr["number"]}/files')
    require(all(isinstance(row, dict) and isinstance(row.get("filename"), str) for row in files),
            "Malformed changed-file listing")
    paths = {name for row in files for name in (row["filename"], row.get("previous_filename", ""))}
    result = {match.group(1) for path in paths if (match := ASSET.fullmatch(path))}
    selected = paths & JSON_PATHS.keys()
    if not selected:
        return result
    comparison = api.get(f'/compare/{sha(pr["base"]["sha"])}...{sha(pr["head"]["sha"])}')
    base = sha(comparison["merge_base_commit"]["sha"])
    for path in sorted(selected):
        # Tracked canonical JSON documents already exist. Any unavailable read fails closed;
        # never reinterpret a 404, rename, truncation or permission failure as an empty baseline.
        result |= changed_slugs(path, api.document(path, base), api.document(path, pr["head"]["sha"]))
    return result


def owner_check(api, number: int, expected_head: str) -> dict:
    require(type(number) is int and number > 0, "Invalid PR number")
    current = api.get(f"/pulls/{number}")
    require(current["state"] == "open" and current["head"]["sha"] == sha(expected_head),
            "PR closed or head changed; refresh before writing or merging")
    baseline = signature(current)
    slugs = touched(api, current)
    owners = []
    observed = []
    if slugs:
        def earlier():
            items = api.pages("/pulls?state=open")
            rows = [p for p in items if p["number"] < number and p["base"]["ref"] == current["base"]["ref"]]
            return sorted(rows, key=lambda p: p["number"])
        peers = earlier()
        observed = [signature(p) for p in peers]
        for peer in peers:
            overlap = slugs & touched(api, peer)
            if overlap:
                owners.append({"pr": peer["number"], "head": peer["head"]["sha"], "slugs": sorted(overlap)})
        require(observed == [signature(p) for p in earlier()], "Earlier PR inventory changed during check")
    require(signature(api.get(f"/pulls/{number}")) == baseline, "PR changed during check")
    return {"status": "blocked" if owners else "clear", "pr": number,
            "head": expected_head, "slugs": sorted(slugs), "existing_owners": owners,
            "note": "Read-only snapshot, not a lock. Recheck immediately before each write and merge."}


def photo_check(report: dict, expected_sha: str) -> dict:
    require(isinstance(report, dict) and report.get("checkout_sha") == sha(expected_sha),
            "Photo report is absent or for a different revision")
    checks = report.get("live_checks")
    require(isinstance(checks, dict) and all(checks.get(k) == "success" for k in
            ("revision", "articles", "share_cards")), "Live delivery verification is incomplete")
    pending = report.get("photo_followup")
    attention = report.get("attention")
    require(isinstance(pending, list) and isinstance(attention, list), "Photo/diagram inventory unavailable")
    counted = {k: 0 for k in ("pending", "missing", "rejected")}
    seen = set()
    for item in pending:
        require(isinstance(item, dict) and item.get("status") in counted, "Invalid photo state")
        slug = item.get("slug")
        require(isinstance(slug, str) and SLUG.fullmatch(slug) is not None and slug not in seen,
                "Invalid/duplicate recovery slug")
        require(isinstance(item.get("next_action"), str) and bool(item["next_action"].strip()),
                "Missing recovery action")
        seen.add(slug)
        counted[item["status"]] += 1
    counts = report.get("photo_followup_counts")
    require(isinstance(counts, dict) and set(counts) == set(counted)
            and all(type(v) is int and v >= 0 for v in counts.values()) and counts == counted,
            "Recovery counts disagree with inventory")
    required_counts = {
        "publication_counts": {"generated_jpeg", "editorial_svg", "stock_photo", "generic_fallback"},
        "counts": {"accepted", "pending", "missing", "rejected"},
    }
    for key, required in required_counts.items():
        values = report.get(key)
        require(isinstance(values, dict) and required <= values.keys()
                and all(type(v) is int and v >= 0 for v in values.values()), "Material counts unavailable")
    require(sum(report["publication_counts"].values()) == sum(report["counts"].values()),
            "Material totals disagree")
    # Generic fallback is never photo completion, even if a malformed report omitted its row.
    generic = report["publication_counts"].get("generic_fallback", 0)
    for item in attention:
        require(isinstance(item, dict) and all(isinstance(item.get(k), str) and item[k]
                for k in ("slug", "reason", "next_action")), "Invalid image/diagram attention")
    return {"status": "recovery_required" if pending or attention or generic else "backlog_empty",
            "checkout_sha": expected_sha, "photo_followup": pending, "attention": attention,
            "generic_fallback": generic,
            "note": "Delivery and photograph completion are separate. Empty backlog is not an all-article visual audit."}


def photo_payload(report: dict, expected_sha: str, details_url: str) -> dict:
    require(re.fullmatch(r"https://github\.com/RollNuts/mihassei-news/actions/runs/[0-9]+", details_url)
            is not None, "Invalid verification run URL")
    result = photo_check(report, expected_sha)
    complete = result["status"] == "backlog_empty"
    lines = ["Delivery can succeed with a fallback; this is a separate recovery check.",
             "Checkout: `" + expected_sha + "`", "",
             "Read the existing live-photo-health artifact for the full inventory.",
             "Do not generate again when a saved candidate or an owner PR already exists.", ""]
    for row in result["photo_followup"][:30]:
        lines.append("- `" + row["slug"] + "`: " + row["status"])
    lines += ["", "Photo follow-ups: " + str(len(result["photo_followup"])),
              "Other image/diagram attention: " + str(len(result["attention"])),
              "Generic fallbacks: " + str(result["generic_fallback"]), "", result["note"]]
    return {"name": "Photograph completion", "head_sha": expected_sha, "status": "completed",
            "conclusion": "success" if complete else "failure", "details_url": details_url,
            "output": {"title": "Photo backlog empty" if complete else "Photo/image recovery required",
                       "summary": "\n".join(lines)}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    owner = commands.add_parser("owner")
    owner.add_argument("--pr", type=int, required=True)
    owner.add_argument("--expected-head", required=True)
    photos = commands.add_parser("photos")
    photos.add_argument("--report", type=Path, required=True)
    photos.add_argument("--expected-sha", required=True)
    payload = commands.add_parser("photo-check-payload")
    payload.add_argument("--report", type=Path, required=True)
    payload.add_argument("--expected-sha", required=True)
    payload.add_argument("--details-url", required=True)
    payload.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.mode == "photo-check-payload":
            result = photo_payload(json.loads(args.report.read_text(encoding="utf-8")),
                                   args.expected_sha, args.details_url)
            args.output.write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")
            print(json.dumps({"photo_check_conclusion": result["conclusion"], "checkout_sha": args.expected_sha}))
            return 0  # A valid payload is not a claim that photographs are complete.
        if args.mode == "owner":
            result = owner_check(GitHub(), args.pr, args.expected_head)
        else:
            result = photo_check(json.loads(args.report.read_text(encoding="utf-8")), args.expected_sha)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] in ("clear", "backlog_empty") else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "unverified", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
