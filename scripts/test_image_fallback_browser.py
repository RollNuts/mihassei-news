#!/usr/bin/env python3
"""Exercise the actual image controller in isolated Chrome, with no live traffic."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://image-fallback.invalid"


def run_case(browser, source, jpeg, svg, mode):
    """Real image GET/error/load events; fixtures supply only repository bytes."""
    original = ORIGIN + "/original.jpg?v=" + hashlib.sha256(jpeg).hexdigest()[:12]
    fallback = ORIGIN + "/fallback.svg"
    html = ("<!doctype html><html><body><figure>"
            '<a class="story-image-link story-image-link--generated">'
            f'<img id="photo" src="{original}" width="390" height="219" alt="approved photo" '
            f'data-fallback="{fallback}" data-fallback-alt="fallback illustration" data-generated-scene="true">'
            '</a><figcaption>AI photo</figcaption></figure></body></html>')
    counts = {"original": 0, "fallback": 0}
    blocked, script_errors = [], []
    context = browser.new_context(viewport={"width": 390, "height": 844}, service_workers="block")
    try:
        def serve(route):
            request = route.request
            if request.method != "GET":
                blocked.append({"method": request.method, "url": request.url})
                route.abort("blockedbyclient")
            elif request.url == ORIGIN + "/":
                route.fulfill(status=200, content_type="text/html", body=html)
            elif request.url == fallback:
                counts["fallback"] += 1
                route.fulfill(status=200, content_type="image/svg+xml", body=svg)
            elif request.url == original:
                counts["original"] += 1
                if mode == "permanent" or mode == "transient" and counts["original"] == 1:
                    route.abort("failed")
                else:
                    route.fulfill(status=200, content_type="image/jpeg", body=jpeg)
            else:
                blocked.append({"method": request.method, "url": request.url})
                route.abort("blockedbyclient")

        context.route("**/*", serve)
        page = context.new_page()
        page.set_default_timeout(5000)
        page.on("pageerror", lambda error: script_errors.append(str(error)))
        page.goto(ORIGIN + "/", wait_until="load")
        page.add_script_tag(content=source)
        expected = original if mode == "normal" else fallback
        ready = """src => { const image = document.querySelector('#photo');
            return image.currentSrc === src && image.complete && image.naturalWidth > 0; }"""
        page.wait_for_function(ready, arg=expected)
        for attempt in range(5):
            if mode == "permanent" and attempt < 2:
                with page.expect_event("requestfailed", predicate=lambda request: request.url == original):
                    page.evaluate("window.dispatchEvent(new Event('online'))")
            else:
                page.evaluate("window.dispatchEvent(new Event('online'))")
            if mode == "transient":
                page.wait_for_function(ready, arg=original)
            # Let real resource errors and the controller's callbacks finish
            # before another return signal. This is not a timer-based retry.
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        measured = page.locator("#photo").evaluate("""image => ({
            src: image.currentSrc, complete: image.complete, natural_width: image.naturalWidth,
            displayed_width: image.getBoundingClientRect().width,
            alt: image.alt, generated: image.getAttribute('data-generated-scene'),
            generated_link: image.closest('a').classList.contains('story-image-link--generated'),
            caption: image.closest('figure').querySelector('figcaption').textContent
        })""")
        assert not blocked, blocked
        assert not script_errors, script_errors
        assert measured["complete"] and measured["natural_width"] > 0 and measured["displayed_width"] > 0, measured
        if mode == "permanent":
            assert measured["src"] == fallback and counts["original"] == 3, (measured, counts)
            assert measured["alt"] == "fallback illustration" and measured["generated"] is None, measured
            assert not measured["generated_link"] and measured["caption"] == "イメージ画像", measured
        else:
            assert measured["src"] == original and measured["alt"] == "approved photo", measured
            assert measured["generated"] == "true" and measured["generated_link"], measured
            assert measured["caption"] == "AI photo", measured
            if mode == "normal":
                assert counts == {"original": 1, "fallback": 0}, counts
            else:
                assert 2 <= counts["original"] <= 3 and counts["fallback"] == 1, counts
        return {"case": mode, "requests": counts, "measured": measured}
    finally:
        context.close()


def main():
    chrome = shutil.which("google-chrome")
    if not chrome:
        raise RuntimeError("The runner's preinstalled Google Chrome is required")
    cli_version = subprocess.run([chrome, "--product-version"], check=True,
                                 capture_output=True, text=True, timeout=10).stdout.strip()
    source = (ROOT / "assets/engagement.js").read_text(encoding="utf-8")
    boundary = "// Keep the genuine edition label current"
    if source.count(boundary) != 1:
        raise RuntimeError("Cannot isolate the actual image-controller section")
    # Run the production controller, without unrelated analytics/UI requests.
    source = source.split(boundary)[0] + "})();"
    jpeg = (ROOT / "assets/generated/old-coat-recut-as-strangers-jacket.jpg").read_bytes()
    svg = (ROOT / "assets/editorial-scenes/unsolvable-ai-puzzle-cup-zero-pieces-wins.svg").read_bytes()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome")
        try:
            assert browser.version == cli_version, (browser.version, cli_version)
            cases = [run_case(browser, source, jpeg, svg, mode) for mode in ("normal", "transient", "permanent")]
            print(json.dumps({"status": "passed", "browser_version": browser.version,
                "scope": "isolated real Chrome resource-failure tests; not live-site or physical-device QA",
                "cases": cases}, ensure_ascii=False, indent=2))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
