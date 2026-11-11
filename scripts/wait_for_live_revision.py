"""Wait for the public static asset revision, without trusting a build status."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import math
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request


REVISION_ENDPOINT = "https://veripsa.com/build-revision.txt?release="
USER_AGENT = "Mihassei-ReadOnly-ReleaseVerifier/1.0"
MAX_WAIT_SECONDS = 720


class RevisionDeadlineExceeded(Exception):
    """Interrupt blocking I/O without being mistaken for a retryable socket error."""


@contextmanager
def _absolute_deadline(seconds):
    """The production Linux CLI also interrupts slow reads/DNS at its wall deadline."""
    if not hasattr(signal, "setitimer"):
        raise ValueError("An absolute public revision deadline requires a POSIX timer")
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()

    def interrupt(signum, frame):
        raise RevisionDeadlineExceeded(
            "Public revision wait exceeded its " + str(seconds) + "-second wall-time deadline")

    signal.signal(signal.SIGALRM, interrupt)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL,
                            max(0.000001, previous_timer[0] - (time.monotonic() - started)),
                            previous_timer[1])


def wait_for_live_revision(
    expected_sha: str,
    *,
    max_wait_seconds: float = MAX_WAIT_SECONDS,
    interval_seconds: float = 20,
    request_timeout_seconds: float = 9,
    opener=None,
    clock=None,
    sleep=None,
    log=None,
    equivalent_revision=None,
) -> str:
    """Require the full public SHA before a monotonic, at-most-twelve-minute deadline."""
    if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ValueError("Expected revision must be a complete lowercase 40-character Git SHA")
    for name, value in (("max_wait_seconds", max_wait_seconds),
                        ("interval_seconds", interval_seconds),
                        ("request_timeout_seconds", request_timeout_seconds)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(name + " must be a finite positive number")
    if max_wait_seconds > MAX_WAIT_SECONDS:
        raise ValueError("Public revision wait cannot exceed 720 seconds")
    if equivalent_revision is not None and not callable(equivalent_revision):
        raise ValueError("equivalent_revision must be callable")

    opener = urllib.request.urlopen if opener is None else opener
    clock = time.monotonic if clock is None else clock
    sleep = time.sleep if sleep is None else sleep
    log = (lambda message: print(message, flush=True)) if log is None else log
    deadline = clock() + max_wait_seconds
    request = urllib.request.Request(REVISION_ENDPOINT + expected_sha[:12], headers={
        "User-Agent": USER_AGENT,
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    latest = "(not reached)"
    last_logged = None

    def expired():
        return TimeoutError(
            "Public site never served commit " + expected_sha
            + " before the revision deadline; last observation: " + latest
            + ". Check Cloudflare Workers > Settings > Builds and Git integration."
        )

    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise expired()
        try:
            with opener(request, timeout=min(request_timeout_seconds, remaining)) as response:
                if response.status != 200:
                    raise RuntimeError("HTTP " + str(response.status))
                actual = response.read(256).decode("ascii").strip()
            latest = actual if re.fullmatch(r"[0-9a-f]{40}", actual) else "invalid public revision marker"
        except (OSError, urllib.error.URLError, UnicodeError, RuntimeError) as exc:
            actual = None
            latest = type(exc).__name__ + ": " + str(exc)[:140]

        # A slow response that arrives at/after the deadline cannot prove a
        # release within the allowed wait, even when its contents now match.
        if clock() >= deadline:
            raise expired()
        equivalent = (actual != expected_sha and equivalent_revision is not None
                      and isinstance(actual, str) and re.fullmatch(r"[0-9a-f]{40}", actual)
                      and equivalent_revision(expected_sha, actual))
        if clock() >= deadline:
            raise expired()
        if actual == expected_sha or equivalent:
            if actual == expected_sha:
                log("Public site confirmed serving requested commit: " + actual)
            else:
                log("Public site confirmed serving commit: " + actual + "; requested "
                    + expected_sha + " differs only in docs; public files unchanged")
            return actual
        if latest != last_logged:
            log("Public revision not updated yet: expected " + expected_sha
                + "; observed " + latest)
            last_logged = latest
        remaining = deadline - clock()
        if remaining <= 0:
            raise expired()
        sleep(min(interval_seconds, remaining))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", default=os.environ.get("EXPECTED_SHA"))
    parser.add_argument("--allow-docs-only", action="store_true",
                        help="Allow a public ancestor only after Git proves a docs-only diff")
    args = parser.parse_args(argv)
    options = {}
    if args.allow_docs_only:
        if __package__:
            from .check_live import public_revision_matches
        else:
            from check_live import public_revision_matches
        options["equivalent_revision"] = public_revision_matches
    try:
        with _absolute_deadline(MAX_WAIT_SECONDS):
            wait_for_live_revision(args.expected_sha, **options)
    except (ValueError, TimeoutError, RevisionDeadlineExceeded) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
