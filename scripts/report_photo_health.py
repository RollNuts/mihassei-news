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
    from . import content_operation_health, visual_review
except ImportError:
    import content_operation_health
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
        "content_operations": content_operation_health.collect(root),
    })
    return report


def cell(value: object) -> str:
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("|", "\\|")
            .replace("\r", " ").replace("\n", " "))


def rejection_lines(item: dict) -> list[str]:
    """Keep saved rejection evidence available in the readable job log too."""
    evidence = item.get("rejection_evidence")
    if (item.get("status") != "rejected" or not isinstance(evidence, dict)
            or evidence.get("source") not in (
                "assets/generated/manifest.json", "content/visual_reviews.json")
            or not isinstance(evidence.get("reason"), str) or not evidence["reason"].strip()):
        return []
    lines = ["", "#### Saved rejection: " + cell(item["slug"]), "",
             "- Reason: " + cell(evidence["reason"]),
             "- Record: " + cell(evidence["source"])]
    image_sha = evidence.get("image_sha256")
    if isinstance(image_sha, str) and re.fullmatch(r"[0-9a-f]{64}", image_sha):
        lines.append("- JPEG SHA-256: `" + image_sha + "`")
    reviewed_at = evidence.get("reviewed_at")
    if visual_review._timezone_qualified_iso(reviewed_at):
        lines.append("- Reviewed at: " + cell(reviewed_at))
    run = evidence.get("review_run")
    if isinstance(run, str) and re.fullmatch(
            r"(?:https://github\.com/RollNuts/mihassei-news/actions/runs/[1-9][0-9]*"
            r"|[A-Za-z0-9][A-Za-z0-9_.:-]{0,199})", run):
        lines.append("- Review run: " + cell(run))
    return lines


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
        for item in report["photo_followup"]:
            lines += rejection_lines(item)
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
    operations = report.get("content_operations")
    if operations:
        lines += operation_lines(operations)
    lines.append("")
    return "\n".join(lines)


def operation_lines(report: dict) -> list[str]:
    lines = ["", "## Content publication observations", "",
             "- Observed at (JST): " + cell(report["observed_at_jst"]),
             "- Source: publication metadata at the checkout SHA above; not additional live verification, "
             "image approval, or proof of task execution.",
             "- " + cell(report["note"]), "",
             "| Channel | Recorded publications by observation | Today (JST) | Latest actual publication (JST) | Future-dated records |",
             "| --- | ---: | ---: | --- | ---: |"]
    for key, label in (("news", "News"), ("market", "Market bulletins"), ("photo_editions", "Photo features")):
        item = report[key]
        if item["status"] == "unavailable":
            lines.append("| " + label + " | unavailable | unavailable | " + cell(item["reason"]) + " | unavailable |")
            continue
        latest = item["latest"]
        lines.append("| " + " | ".join(cell(value) for value in (
            label, item["published_by_observation_count"], item["today_count"],
            latest["published_at_jst"] if latest else "No recorded publication", item["future_dated_count"])) + " |")
    photo = report["photo_editions"]
    if photo["status"] == "observed":
        edition = photo["oldest_incomplete_daily_edition"]
        lines += ["", "### Daily photo edition carryover", ""]
        if edition:
            lines += ["- Oldest incomplete edition: " + cell(edition["slug"])
                      + " / week " + cell(edition["week_start_date_jst"]),
                      "- Published features: " + str(edition["published_feature_count"]) + "/"
                      + str(edition["scheduled_model_count"]) + " / recorded photos: " + str(edition["recorded_photo_count"]),
                      "- Next in frozen order: " + cell(edition["next_model_name"]) + " (" + cell(edition["next_model_id"])
                      + "), original scheduled date " + cell(edition["next_scheduled_date_jst"])]
        else:
            lines.append("No incomplete daily edition is recorded in this checkout.")
        lines.append("- Unpublished photo stock: not observed. " + cell(photo["stock_reason"]))
    cast = report["cast_stories"]
    lines += ["", "### Creator posts", ""]
    if cast["status"] == "unavailable":
        lines.append("Unavailable: " + cell(cast["reason"]))
    else:
        lines += ["Closed dates since latest post exclude today; no scheduled deadline is inferred.", "",
                  "| Model | Latest actual post (JST) | Today | Previous JST date | Closed dates since latest post |",
                  "| --- | --- | ---: | ---: | ---: |"]
        for model in cast["models"]:
            latest = model["latest"]
            lines.append("| " + " | ".join(cell(value) for value in (
                model["name"], latest["published_at_jst"] if latest else "No recorded post",
                model["today_count"], model["previous_day_count"],
                model["completed_days_since_latest_post"] if latest else "unknown")) + " |")
    return lines


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
