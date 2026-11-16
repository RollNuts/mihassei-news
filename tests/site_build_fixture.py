"""One fresh build per process for read-only checks of a dedicated checkout.

Tests that mutate inputs or outputs must keep their own isolated build. This
does not cache media validation or reuse dist from an earlier test process.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_built = False
MARKET_DATA_PATHS = frozenset({
    "content/market_bulletins.json", "content/market_outlooks.json",
    "content/market_outlook.json", "content/market_figures.json",
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


def python_test_command(changes):
    """Keep the full build, all public output/schema/image/SEO checks and markets.

    Only unrelated implementation regressions are omitted for four known data
    files. Any code, CI, test, unknown, added, removed or renamed path stays full.
    """
    if not market_data_only(changes):
        return [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
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
    parser = argparse.ArgumentParser(description="Run full Python CI or verified market-data checks")
    parser.add_argument("--base", default="")
    parser.add_argument("--event", default="")
    args = parser.parse_args(argv)
    changes = changed_files(args.base, args.event)
    command = python_test_command(changes)
    scope = "market-data-only with fresh full build" if market_data_only(changes) else "full discovery"
    print("Python test scope: " + scope, flush=True)
    # A test/build failure is returned unchanged, never retried as a lighter suite.
    return subprocess.run(command, cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
