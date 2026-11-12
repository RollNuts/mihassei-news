#!/usr/bin/env python3
"""Report photograph recovery work without making valid fallbacks fail delivery."""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import subprocess

try:
    from . import visual_review
except ImportError:
    import visual_review


def collect(root: Path, live_checks: dict[str, str]) -> dict:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        result = visual_review.main(["--root", str(root)])
    if result != 0:
        raise RuntimeError("Photo state unavailable; do not treat it as zero pending: "
                           + output.getvalue().strip())
    report = json.loads(output.getvalue())
    revision = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RuntimeError("Cannot identify the checked-out revision")
    report.update({
        "checkout_sha": revision,
        "reported_at": datetime.now(timezone.utc).isoformat(),
        "scope": "All articles in this checkout; live checks have their own coverage.",
        "live_checks": live_checks,
        "photo_followup_counts": {
            status: sum(item["status"] == status for item in report["photo_followup"])
            for status in ("pending", "missing", "rejected")
        },
    })
    return report


def cell(value: object) -> str:
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("|", "\\|")
            .replace("\r", " ").replace("\n", " "))


def summary(report: dict) -> str:
    lines = [
        "## Photograph completion and recovery",
        "",
        "- Checkout SHA: `" + report["checkout_sha"] + "`",
        "- Reported at: " + report["reported_at"],
        "- Scope: " + report["scope"],
        "- Live check outcomes: " + ", ".join(
            key + "=" + value for key, value in report["live_checks"].items()),
        "",
        "These image counts describe source selection at the checkout SHA, not a new visual review "
        "or proof that every article was checked live. A successful delivery check can include a "
        "valid fallback while a photograph remains unfinished.",
        "",
    ]
    for title, counts in (
        ("Selected materials", report["publication_counts"]),
        ("Generated JPEG records (all routes, including intentional illustrations)", report["counts"]),
        ("Unfinished photographs (photo routes only)", report["photo_followup_counts"]),
    ):
        lines += ["### " + title, "", "| State | Articles |", "| --- | ---: |"]
        lines += ["| " + cell(key) + " | " + str(value) + " |" for key, value in counts.items()]
        lines.append("")
    lines += ["### Photograph recovery", ""]
    if report["photo_followup"]:
        lines += ["| Slug | State | Selected material | Next action |", "| --- | --- | --- | --- |"]
        for item in report["photo_followup"]:
            lines.append("| " + " | ".join(cell(value) for value in (
                item["slug"], item["status"], item["publication"]["kind"], item["next_action"])) + " |")
    else:
        lines.append("No unfinished photograph is reported by the current selection rules.")
    lines += ["", "### Other image and diagram attention", ""]
    photo_slugs = {item["slug"] for item in report["photo_followup"]}
    # Photo recovery above already includes the pending/generic photo action.
    # Keep independent SVG, social PNG and diagram defects on those same slugs.
    other = [item for item in report["attention"]
             if item["slug"] not in photo_slugs
             or item["reason"] not in ("pending_jpeg", "generic_fallback")]
    if other:
        lines += ["| Slug | Reason | Next action |", "| --- | --- | --- |"]
        for item in other:
            lines.append("| " + " | ".join(cell(item[key]) for key in (
                "slug", "reason", "next_action")) + " |")
    else:
        lines.append("No additional image or diagram repair is reported.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = collect(args.root, {
        "revision": os.environ.get("PHOTO_REVISION_CHECK") or "not_run",
        "articles": os.environ.get("PHOTO_ARTICLE_CHECK") or "not_run",
        "share_cards": os.environ.get("PHOTO_SHARE_CARD_CHECK") or "not_run",
    })
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rendered = summary(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as destination:
            destination.write(rendered)
    print(rendered)
    # Pending/rejected/missing photographs are work to recover, not delivery errors.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
