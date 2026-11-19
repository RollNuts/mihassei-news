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
import shutil
import subprocess
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


def installed_browser_evidence():
    """Read the runner's installed software; never install a browser here."""
    chrome = shutil.which("google-chrome")
    if not chrome:
        raise RuntimeError("The runner's preinstalled Google Chrome is required")
    chrome_version = subprocess.run([chrome, "--product-version"], check=True,
        capture_output=True, text=True, timeout=10).stdout.strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", chrome_version):
        raise RuntimeError("Could not detect the installed Google Chrome version")
    fonts = subprocess.run(["fc-list", ":lang=ja", "family"], check=True,
        capture_output=True, text=True, timeout=10).stdout.splitlines()
    fonts = sorted({family.strip() for family in fonts if family.strip()})
    if not fonts:
        raise RuntimeError("Japanese font support is required before capture")
    return {"chrome_command": chrome, "chrome_cli_version": chrome_version,
            "japanese_font_families": fonts}


def read_only_route(route, blocked):
    """Block even an unexpected beacon before it reaches the live service."""
    request = route.request
    if request.method in {"GET", "HEAD"}:
        route.continue_()
    else:
        blocked.append({"method": request.method, "url": request.url})
        route.abort("blockedbyclient")


def expected_image_urls(target, root=Path(__file__).resolve().parents[1]):
    """Bind the browser's main images to this trusted checkout's exact JPEGs."""
    try:
        from .generated_photos import generated_url
        from .photo_issues import cover_shot, photo_image_url
        from .visual_review import reviewed_generated_scene
    except ImportError:
        from generated_photos import generated_url
        from photo_issues import cover_shot, photo_image_url
        from visual_review import reviewed_generated_scene
    name, path, _selector, image_count = target
    slug = path.rstrip("/").rsplit("/", 1)[-1]
    if target not in TARGETS:
        raise ValueError("Only the two fixed capture targets are allowed")
    if name == "article":
        articles = json.loads((root / "content/articles.json").read_text(encoding="utf-8"))
        article = next(article for article in articles if article["slug"] == slug)
        if not reviewed_generated_scene(article, root=root):
            raise ValueError("The capture article must have a current accepted JPEG")
        urls = [SITE + generated_url(article, root=root)]
    else:
        catalog = json.loads((root / "content/photo_issues.json").read_text(encoding="utf-8"))
        issue = next(issue for issue in catalog["issues"] if issue["slug"] == slug)
        if issue.get("state") != "published":
            raise ValueError("The capture photo issue must be published")
        first = cover_shot(issue)
        shots = [first] + [shot for shot in issue["shots"] if shot["id"] != first["id"]]
        urls = [SITE + photo_image_url(issue, shot["id"]) for shot in shots]
    if (len(urls) != image_count or len(set(urls)) != image_count
            or any(not re.fullmatch(r"https://veripsa\.com/.+\.jpg\?v=[a-f0-9]{12}", url) for url in urls)):
        raise ValueError("Expected main photographs need distinct versioned JPEG URLs")
    return urls


def measurement_errors(measured, expected_sources):
    errors = []
    if (measured["inner_width"] != 390 or measured["client_width"] != 390
            or abs(measured["visual_width"] - 390) > 1):
        errors.append("Rendered viewport is not 390 CSS pixels")
    if measured["scroll_width"] > measured["client_width"] + 1:
        errors.append("Page has horizontal overflow")
    if not measured["title"]:
        errors.append("The main page heading is missing")
    if len(measured["required_images"]) != len(expected_sources):
        errors.append(f"Expected {len(expected_sources)} main photographs")
    if [image["src"] for image in measured["required_images"]] != expected_sources:
        errors.append("Main photograph identity differs from the expected checkout JPEGs")
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
    name, path, selector, _image_count = target
    expected_sources = expected_image_urls(target)
    url = SITE + path + "?analytics=off"
    result = {"name": name, "url": url, "expected_image_sources": expected_sources,
              "errors": [], "screenshots": []}
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
        result["errors"].extend(measurement_errors(result["measured"], expected_sources))
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
              "render_mode": "Google Chrome mobile emulation; not a physical device",
              "browser_channel": "chrome",
              "runner_image": {"os": os.environ.get("ImageOS"), "version": os.environ.get("ImageVersion")},
              "started_at": datetime.now(timezone.utc).isoformat(),
              "pages": [], "blocked_write_requests": [], "errors": []}
    try:
        report.update(installed_browser_evidence())
        from playwright.sync_api import sync_playwright
        report["playwright_version"] = version("playwright")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome")
            try:
                report["browser_version"] = browser.version
                if browser.version != report["chrome_cli_version"]:
                    raise RuntimeError("Launched Chrome version differs from the installed Chrome CLI")
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
               "Google Chrome mobile emulation. Screenshots still require human visual review.",
               f"Runner image: `{report['runner_image']['os']}` / `{report['runner_image']['version']}`; "
               f"Chrome CLI: `{report.get('chrome_cli_version')}`; browser: `{report.get('browser_version')}`."]
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
