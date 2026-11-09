"""Bounded public-byte verification, exercised without network or real waits."""
from __future__ import annotations

import unittest
import os
import signal
import threading
import urllib.error
from unittest.mock import patch

from scripts import wait_for_live_revision as gate


EXPECTED = "a" * 40
OLD = "b" * 40


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Response:
    def __init__(self, body, status=200):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.body[:limit]


class LiveRevisionWaitTests(unittest.TestCase):
    def run_wait(self, opener, clock=None, **kwargs):
        clock = Clock() if clock is None else clock
        logs = []
        result = gate.wait_for_live_revision(
            EXPECTED, opener=opener, clock=clock, sleep=clock.sleep, log=logs.append, **kwargs)
        return result, clock, logs

    def test_real_public_bytes_after_nine_minutes_succeed_with_get_and_cache_bypass(self):
        clock = Clock()
        requests = []

        def fetch(request, timeout):
            requests.append((request, timeout))
            return Response(((EXPECTED if clock.now >= 540 else OLD) + "\n").encode())

        result, clock, logs = self.run_wait(fetch, clock)
        self.assertEqual(result, EXPECTED)
        self.assertEqual(clock.now, 540)
        self.assertEqual(len(requests), 28)
        self.assertEqual(len(logs), 2)  # One stale observation and the final proof.
        self.assertIn(EXPECTED, logs[-1])
        for request, timeout in requests:
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.full_url, "https://veripsa.com/build-revision.txt?release=" + EXPECTED[:12])
            self.assertEqual(request.get_header("User-agent"), gate.USER_AGENT)
            self.assertEqual(request.get_header("Cache-control"), "no-cache")
            self.assertEqual(request.get_header("Pragma"), "no-cache")
            self.assertEqual(timeout, 9)

    def test_permanently_old_bytes_fail_at_deadline_without_repeating_logs(self):
        clock = Clock()
        logs = []
        requests = []

        def fetch(request, timeout):
            requests.append(timeout)
            return Response(OLD.encode())

        with self.assertRaisesRegex(TimeoutError, OLD):
            gate.wait_for_live_revision(EXPECTED, opener=fetch, clock=clock,
                                        sleep=clock.sleep, log=logs.append)
        self.assertEqual(clock.now, 720)
        self.assertEqual(len(requests), 36)
        self.assertEqual(len(logs), 1)

    def test_observed_670_second_delivery_delay_is_covered_by_the_next_poll(self):
        clock = Clock()

        def fetch(request, timeout):
            return Response((EXPECTED if clock.now >= 670 else OLD).encode())

        result, clock, logs = self.run_wait(fetch, clock)
        self.assertEqual(result, EXPECTED)
        self.assertEqual(clock.now, 680)
        self.assertLess(clock.now, 720)
        self.assertEqual(len(logs), 2)

    def test_http_failure_cannot_succeed_even_when_body_contains_expected_sha(self):
        for status in (404, 503):
            with self.subTest(status=status), self.assertRaisesRegex(TimeoutError, "HTTP " + str(status)):
                self.run_wait(lambda request, timeout: Response(EXPECTED.encode(), status),
                              max_wait_seconds=20)

    def test_http_exception_and_network_failure_remain_errors_at_deadline(self):
        failures = [urllib.error.HTTPError(gate.REVISION_ENDPOINT, 503, "unavailable", {}, None),
                    urllib.error.URLError("connection unavailable"), TimeoutError("socket timed out")]
        for failure in failures:
            def fetch(request, timeout):
                raise failure
            with self.subTest(failure=type(failure).__name__), self.assertRaisesRegex(TimeoutError, "last observation"):
                self.run_wait(fetch, max_wait_seconds=20)

    def test_short_or_malformed_public_marker_never_proves_a_release(self):
        for body in (EXPECTED[:12].encode(), b"<html>" + EXPECTED.encode(),
                     EXPECTED.upper().encode(), EXPECTED.encode() + b" extra", b"\xff"):
            with self.subTest(body=body), self.assertRaises(TimeoutError):
                self.run_wait(lambda request, timeout: Response(body), max_wait_seconds=20)

    def test_invalid_expected_sha_is_rejected_before_network(self):
        for value in (None, "", EXPECTED[:12], "z" * 40, EXPECTED.upper(), " " + EXPECTED, True):
            with self.subTest(value=value), patch.object(gate.urllib.request, "urlopen") as fetch:
                with self.assertRaises(ValueError):
                    gate.wait_for_live_revision(value)
                fetch.assert_not_called()

    def test_request_timeout_and_sleep_use_only_remaining_deadline(self):
        clock = Clock()
        requests = []

        def fetch(request, timeout):
            requests.append(timeout)
            clock.now += min(8, timeout)
            return Response(OLD.encode())

        with self.assertRaises(TimeoutError):
            self.run_wait(fetch, clock, max_wait_seconds=31)
        self.assertEqual(requests, [9, 3])
        self.assertEqual(clock.sleeps, [20])
        self.assertEqual(clock.now, 31)

    def test_last_sleep_is_cut_short_instead_of_exceeding_deadline(self):
        clock = Clock()
        with self.assertRaises(TimeoutError):
            self.run_wait(lambda request, timeout: Response(OLD.encode()), clock,
                          max_wait_seconds=25)
        self.assertEqual(clock.sleeps, [20, 5])
        self.assertEqual(clock.now, 25)

    def test_matching_response_at_or_after_deadline_is_not_success(self):
        for arrival in (720, 721):
            clock = Clock()

            def fetch(request, timeout):
                clock.now = arrival
                return Response(EXPECTED.encode())

            with self.subTest(arrival=arrival), self.assertRaises(TimeoutError):
                self.run_wait(fetch, clock)

    def test_wait_parameters_cannot_make_an_unbounded_wait(self):
        for kwargs in ({"max_wait_seconds": 721}, {"max_wait_seconds": float("inf")},
                       {"max_wait_seconds": 0}, {"max_wait_seconds": True},
                       {"interval_seconds": -1}, {"request_timeout_seconds": float("nan")}):
            with self.subTest(kwargs=kwargs), patch.object(gate.urllib.request, "urlopen") as fetch:
                with self.assertRaises(ValueError):
                    gate.wait_for_live_revision(EXPECTED, **kwargs)
                fetch.assert_not_called()

    @unittest.skipUnless(hasattr(signal, "setitimer"), "POSIX production CLI timer")
    def test_absolute_cli_deadline_interrupts_a_blocking_read_and_restores_the_timer(self):
        read_fd, write_fd = os.pipe()
        previous_handler = signal.getsignal(signal.SIGALRM)

        class BlockingResponse(Response):
            def read(self, limit):
                return os.read(read_fd, limit)

        # Ensure even a broken deadline produces a test failure, not a hung suite.
        release = threading.Timer(0.5, lambda: os.write(write_fd, EXPECTED.encode()))
        release.daemon = True
        release.start()
        try:
            with self.assertRaises(gate.RevisionDeadlineExceeded):
                with gate._absolute_deadline(0.03):
                    self.run_wait(lambda request, timeout: BlockingResponse(b""))
            self.assertEqual(signal.getsignal(signal.SIGALRM), previous_handler)
            self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        finally:
            release.cancel()
            release.join()
            os.close(read_fd)
            os.close(write_fd)

    def test_cli_returns_error_for_timeout_and_invalid_input(self):
        with patch.object(gate, "wait_for_live_revision", side_effect=TimeoutError("not deployed")), \
             patch("sys.stderr"):
            self.assertEqual(gate.main(["--expected-sha", EXPECTED]), 1)
        with patch.object(gate, "wait_for_live_revision", side_effect=gate.RevisionDeadlineExceeded("blocked")), \
             patch("sys.stderr"):
            self.assertEqual(gate.main(["--expected-sha", EXPECTED]), 1)
        with patch("sys.stderr"):
            self.assertEqual(gate.main(["--expected-sha", "short"]), 1)
        with patch.object(gate, "wait_for_live_revision", return_value=EXPECTED) as wait:
            with patch.dict("os.environ", {"EXPECTED_SHA": EXPECTED}):
                self.assertEqual(gate.main([]), 0)
            wait.assert_called_once_with(EXPECTED)


if __name__ == "__main__":
    unittest.main()
