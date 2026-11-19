#!/usr/bin/env python3
"""Capture the two recovery pages at a real 390px Chromium mobile viewport.

Manual evidence for human review, not a visual approval or a physical-device test.
No CSS/content substitutions, interaction clicks, write requests or revision wait.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


SITE = "https://veripsa.com"
VIEWPORT = {"width": 390, "height": 844}
TARGETS = (
    ("article", "/articles/old-coat-recut-as-strangers-jacket/", ".article-hero-figure img", 1),
    ("photo", "/photo/weekly/november-rooftop-pool-sumire-011/", ".photo-issue-shot img", 10),
)
MEASURE = """selector => {
  const root = document.documentElement;
  const images = [...document.images].filter(img => img.getClientRects().length);
  const image = img => ({src: img.currentSrc, complete: img.complete,
    natural_width: img.naturalWidth, natural_height: img.naturalHeight,
    displayed_width: img.getBoundingClientRect().width});
  return {inner_width: innerWidth, client_width: root.clientWidth,
    visual_width: visualViewport.width, device_pixel_ratio: devicePixelRatio,
    scroll_width: Math.max(root.scrollWidth, document.body.scrollWidth),
    scroll_height: Math.max(root.scrollHeight, document.body.scrollHeight),
    title: document.querySelector('main h1')?.textContent.trim() || '',
    user_agent: navigator.userAgent,
    images: images.map(image),
    required_images: [...document.querySelectorAll(selector)].map(image)};
}"""


def read_only_route(route, blocked):
    """Block even an unexpected beacon before it reaches the live service."""
    request = route.request
    if request.method in {"GET", "HEAD"}:
        route.continue_()
    else:
        blocked.append({"method": request.method, "url": request.url})
        route.abort("blockedbyclient")


def measurement_errors(measured, image_count):
    errors = []
    if (measured["inner_width"] != 390 or measured["client_width"] != 390
            or abs(measured["visual_width"] - 390) > 1):
        errors.append("Rendered viewport is not 390 CSS pixels")
    if measured["scroll_width"] > measured["client_width"] + 1:
        errors.append("Page has horizontal overflow")
    if not measured["title"]:
        errors.append("The main page heading is missing")
    if len(measured["required_images"]) != image_count:
        errors.append(f"Expected {image_count} main photographs")
    for img in measured["images"] + measured["required_images"]:
        if not img["complete"] or min(img["natural_width"], img["natural_height"]) <= 0:
            errors.append("A displayed photograph did not load: " + img["src"])
    return list(dict.fromkeys(errors))


def live_revision(request, expected):
    response = request.get(SITE + "/build-revision.txt?mobile-qa=" + expected,
                           headers={"Cache-Control": "no-cache"},
                           timeout=15000, max_redirects=0)
    actual = response.text().strip()
    if response.status != 200 or actual != expected:
        raise RuntimeError(f"Exact public revision required: expected {expected}, got {response.status} {actual[:80]}")
    return actual


def capture_page(context, target, output):
    name, path, selector, image_count = target
    url = SITE + path + "?analytics=off"
    result = {"name": name, "url": url, "errors": [], "screenshots": []}
    page = context.new_page()
    page.set_default_timeout(20000)
    try:
        response = page.goto(url, wait_until="load", timeout=45000)
        result["http_status"] = response.status if response else None
        result["final_url"] = page.url
        actual = urlsplit(page.url)
        if (result["http_status"] != 200 or actual.scheme != "https"
                or actual.netloc != "veripsa.com" or actual.path.rstrip("/") != path.rstrip("/")
                or actual.query != "analytics=off"):
            raise RuntimeError("The requested live page did not return HTTP 200 at its analytics-off URL")
        page.locator("main h1").wait_for(state="visible")
        # Actual scrolling activates native lazy loading without altering markup.
        for img in page.locator("img").all():
            if img.is_visible():
                img.scroll_into_view_if_needed()
        page.evaluate("window.scrollTo({left: 0, top: 0, behavior: 'instant'})")
        page.wait_for_function("""() => document.fonts.status === 'loaded' &&
            [...document.images].filter(img => img.getClientRects().length).every(img => img.complete)""")
        page.evaluate("""() => Promise.all([...document.images]
            .filter(img => img.getClientRects().length && img.naturalWidth)
            .map(img => img.decode()))""")
    except Exception as exc:
        result["errors"].append(str(exc))
    try:
        result["measured"] = page.evaluate(MEASURE, selector)
        result["errors"].extend(measurement_errors(result["measured"], image_count))
        for label, full_page in (("viewport", False), ("full", True)):
            filename = f"{name}-{label}-390.png"
            page.screenshot(path=str(output / filename), full_page=full_page, scale="css")
            result["screenshots"].append(filename)
    except Exception as exc:
        result["errors"].append(str(exc))
    finally:
        page.close()
    result["status"] = "failed" if result["errors"] else "captured"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_sha):
        parser.error("--expected-sha must be a complete lowercase Git SHA")
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"expected_revision": args.expected_sha, "viewport": VIEWPORT,
              "request": os.environ.get("CAPTURE_REQUEST"),
              "public_check_run": os.environ.get("PUBLIC_CHECK_RUN"),
              "render_mode": "Chromium mobile emulation; not a physical device",
              "started_at": datetime.now(timezone.utc).isoformat(),
              "pages": [], "blocked_write_requests": [], "errors": []}
    try:
        from playwright.sync_api import sync_playwright
        report["playwright_version"] = version("playwright")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                report["browser_version"] = browser.version
                context = browser.new_context(viewport=VIEWPORT, screen=VIEWPORT,
                    is_mobile=True, has_touch=True, device_scale_factor=1,
                    locale="ja-JP", timezone_id="Asia/Tokyo", accept_downloads=False,
                    service_workers="block")
                context.route("**/*", lambda route: read_only_route(route, report["blocked_write_requests"]))
                try:
                    report["revision_before"] = live_revision(context.request, args.expected_sha)
                    for target in TARGETS:
                        report["pages"].append(capture_page(context, target, args.output))
                    report["revision_after"] = live_revision(context.request, args.expected_sha)
                finally:
                    context.close()
            finally:
                browser.close()
    except Exception as exc:
        report["errors"].append(str(exc))
    report["status"] = ("captured" if not report["errors"] and len(report["pages"]) == 2
                        and all(page["status"] == "captured" for page in report["pages"]) else "failed")
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = ["## Manual 390px capture", f"Revision: `{args.expected_sha}`", f"Capture: **{report['status']}**",
               "Chromium mobile emulation. Screenshots still require human visual review."]
    for page in report["pages"]:
        measured = page.get("measured", {})
        summary.append(f"- {page['name']}: {page['status']}; client width {measured.get('client_width')}; "
                       f"scroll width {measured.get('scroll_width')}; main photographs {len(measured.get('required_images', []))}")
    summary.append(f"Blocked write requests: {len(report['blocked_write_requests'])}. See report.json for errors and measurements.")
    print("\n".join(summary))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write("\n".join(summary) + "\n")
    return 0 if report["status"] == "captured" else 1


if __name__ == "__main__":
    raise SystemExit(main())
