"""One fresh build per process for read-only checks of a dedicated checkout.

Tests that mutate inputs or outputs must keep their own isolated build. This
does not cache media validation or reuse dist from an earlier test process.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
from scripts.photo_stories import STORY_RECORD_PATHS

_built = False
MARKET_DATA_PATHS = frozenset({
    "content/market_bulletins.json", "content/market_outlooks.json",
    "content/market_outlook.json", "content/market_figures.json",
})
PHOTO_STORY_PATH = "content/photo_stories.json"
# Story data reaches home, weekly indexes and profiles. Retain their reader
# contracts, accepted photo-source schemas and global output/SEO checks.
PHOTO_STORY_MODULES = frozenset({
    "tests.test_site", "tests.test_site_build_fixture", "tests.test_serial_discovery",
    "tests.test_search_indexability", "tests.test_sitemap_freshness",
    "tests.test_photo_stories", "tests.test_photo_story_motion", "tests.test_photo_health_report",
    "tests.test_photo_model_pages", "tests.test_photo_issue_pages", "tests.test_photo_edition_pages",
    "tests.test_photo_issues", "tests.test_photo_editions", "tests.test_photo_supplied",
    "tests.test_photo_profile_portraits", "tests.test_photo_biographies",
    "tests.test_home_visual", "tests.test_gravure_corner", "tests.test_editorial_positioning",
    "tests.test_editorial_starter", "tests.test_daily_issues", "tests.test_returning_shelf",
    "tests.test_route_scripts", "tests.test_reader_paths", "tests.test_mobile_reading_flow",
    "tests.test_share_cards", "tests.test_image_context", "tests.test_internal_links",
})


def ensure_site_build() -> None:
    global _built
    if not _built:
        subprocess.run([sys.executable, str(ROOT / "scripts/build.py")],
                       cwd=ROOT, check=True)
        _built = True


def changed_files(base, event):
    """A complete PR/push diff, or None when narrowing cannot be proved safe."""
    if (event not in ("pull_request", "push") or not isinstance(base, str)
            or not re.fullmatch(r"[0-9a-f]{40}", base) or base == "0" * 40):
        return None
    revision_range = base + ("...HEAD" if event == "pull_request" else "..HEAD")
    try:
        raw = subprocess.run(
            ["git", "diff", "--name-status", "-z", "--no-renames", revision_range, "--"],
            cwd=ROOT, check=True, capture_output=True,
        ).stdout
        if not raw:
            return []
        fields = raw.split(b"\0")
        if fields.pop() != b"" or len(fields) % 2:
            return None
        return [(fields[i].decode("ascii"), fields[i + 1].decode("utf-8"))
                for i in range(0, len(fields), 2)]
    except (OSError, subprocess.CalledProcessError, UnicodeError):
        return None


def market_data_only(changes):
    return bool(changes) and all(status == "M" and path in MARKET_DATA_PATHS
                                 for status, path in changes)


def photo_story_data_only(changes):
    if not changes or len(changes) != 1:
        return False
    status, path = changes[0]
    return status == "M" and (path == PHOTO_STORY_PATH or path in STORY_RECORD_PATHS)


def native_image_engine_tests_required(changes, event):
    """Only these two proven data-only PR scopes leave the native engine alone.

    Keep main pushes, unknown diffs and any future Python test scopes full.
    """
    return not (event == "pull_request"
                and (market_data_only(changes) or photo_story_data_only(changes)))


def python_test_command(changes):
    """Keep a fresh full build and each data scope's public/source/SEO contracts.

    Only separate, exact market-data or photo-story-data diffs can narrow the
    suite. Mixed data, code, CI, tests, unknown paths and non-modifications stay full.
    """
    if not (market_data_only(changes) or photo_story_data_only(changes)):
        return [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
    if photo_story_data_only(changes):
        modules = set(PHOTO_STORY_MODULES)
    else:
        modules = {
            "tests.test_site", "tests.test_site_build_fixture", "tests.test_serials",
            "tests.test_serial_history", "tests.test_forecast_ledger",
            "tests.test_search_indexability", "tests.test_sitemap_freshness",
            "tests.test_serial_discovery",
        }
        modules.update("tests." + path.stem for path in (ROOT / "tests").glob("test_market*.py"))
    # test_site's existing fixture performs the same clean full site build once;
    # every subsequent output test shares that snapshot in this one process.
    ordered = ["tests.test_site"] + sorted(modules - {"tests.test_site"})
    return [sys.executable, "-m", "unittest", "-v", *ordered]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run full Python CI or verified data-only checks")
    parser.add_argument("--base", default="")
    parser.add_argument("--event", default="")
    args = parser.parse_args(argv)
    changes = changed_files(args.base, args.event)
    command = python_test_command(changes)
    if market_data_only(changes):
        scope = "market-data-only with fresh full build"
    elif photo_story_data_only(changes):
        scope = "photo-story-data-only with fresh full build"
    else:
        scope = "full discovery"
    print("Python test scope: " + scope, flush=True)
    if os.environ.get("GITHUB_OUTPUT"):
        required = native_image_engine_tests_required(changes, args.event)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("run_native_image_engine_tests=" + str(required).lower() + "\n")
    # A test/build failure is returned unchanged, never retried as a lighter suite.
    # Child tests may call main() for other scopes; only this parent owns the
    # workflow output, while all unrelated test/build environment is retained.
    child_env = {key: value for key, value in os.environ.items() if key != "GITHUB_OUTPUT"}
    return subprocess.run(command, cwd=ROOT, env=child_env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
