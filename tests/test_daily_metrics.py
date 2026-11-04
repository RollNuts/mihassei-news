"""Offline-only regression tests for a free, read-only audience snapshot."""
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from scripts import record_metrics as m


class FakeResponse(io.BytesIO):
    status = 200


class DailyMetricsTests(unittest.TestCase):
    def test_report_previous_completed_jst_day(self):
        now = datetime.fromisoformat("2026-11-02T16:07:00+00:00")
        self.assertEqual(m.report_date(now), "2026-11-02")
        self.assertEqual(m.report_date(datetime.fromisoformat(
            "2026-11-03T00:30:00+09:00")), "2026-11-02")

    def test_completed_jst_day_read_never_uses_lifetime_count(self):
        calls = []
        def successful(request, timeout):
            self.assertEqual(request.get_method(), "GET")
            calls.append(parse_qs(urlsplit(request.full_url).query))
            return FakeResponse(json.dumps({
                "unit": "anonymous-browser", "visitors": 999,
                "day": {"dateJst": "2026-11-02", "visitors": 7, "articleReads": 15}
            }).encode())
        self.assertEqual(m.read_day_totals("2026-11-02", opener=successful),
                         {"visitors": 7, "articleReads": 15})
        self.assertEqual(calls, [{"day": ["2026-11-02"]}])

    def test_invalid_or_legacy_day_payload_is_not_fabricated(self):
        for daily in (None, {"dateJst": "2026-11-03", "visitors": 6, "articleReads": 8},
                      {"dateJst": "2026-11-02", "visitors": "six", "articleReads": 8},
                      {"dateJst": "2026-11-02", "visitors": 0, "articleReads": -1}):
            def broken(request, timeout):
                return FakeResponse(json.dumps({
                    "unit": "anonymous-browser", "visitors": 999, "day": daily
                }).encode())
            with self.assertRaises(ValueError):
                m.read_day_totals("2026-11-02", opener=broken)

    def test_one_batch_per_twenty_and_only_get(self):
        calls = []

        def fake_open(request, timeout):
            self.assertEqual(request.get_method(), "GET")
            self.assertTrue(request.full_url.startswith("https://veripsa.com/api/engagement?"))
            names = parse_qs(urlsplit(request.full_url).query)["articles"][0].split(",")
            self.assertLessEqual(len(names), 20)
            calls.append(names)
            return FakeResponse(json.dumps({
                "unit": "anonymous-browser",
                "visitors": 42,
                "channels": {"x": 4, "search": 7, "external": 1, "unattributed": 12},
                "articles": {
                    slug: {"readers": 2, "likes": 1, "shareClicks": 0, "continuedReaders": 1,
                           "arrivals": {"x": 1, "search": 2}}
                    for slug in names
                },
            }).encode())

        names = ["story-" + str(i) for i in range(21)]
        visitors, metrics, channels = m.read_public_metrics(names, opener=fake_open)
        self.assertEqual(visitors, 42)
        self.assertEqual(channels["x"], 4)
        self.assertEqual(channels["search"], 7)
        self.assertEqual(len(metrics), 21)
        self.assertEqual(list(map(len, calls)), [20, 1])
        self.assertEqual(metrics[names[0]]["readers"], 2)
        self.assertEqual(metrics[names[0]]["continued_readers"], 1)
        self.assertEqual(metrics[names[0]]["x_arrivals"], 1)
        self.assertEqual(metrics[names[0]]["search_arrivals"], 2)

    def test_reject_missing_or_invented_metrics(self):
        def fake_open(req, timeout):
            return FakeResponse(json.dumps({
                "unit": "anonymous-browser",
                "visitors": 0,
                "articles": {},
            }).encode())
        with self.assertRaises(ValueError):
            m.read_public_metrics(["real-story"], opener=fake_open)

    def test_missing_arrival_history_is_omitted_not_made_up(self):
        def old_worker(request,timeout):
            return FakeResponse(json.dumps({
                "unit":"anonymous-browser","visitors":2,
                "articles":{"story":{"readers":1,"likes":0,"shareClicks":0,
                                     "continuedReaders":0}}
            }).encode())
        _,metrics,_=m.read_public_metrics(["story"],opener=old_worker)
        self.assertNotIn("x_arrivals",metrics["story"])
        self.assertNotIn("search_arrivals",metrics["story"])
        self.assertNotIn("page_views_cumulative",metrics["story"])

    def test_reject_corrupted_article_arrivals(self):
        for bad in ({"x":-1,"search":0},{"x":"many","search":1},
                    {"x":1},{"x":1,"search":0,"full_referrer":"private"}):
            def fetch(request,timeout):
                return FakeResponse(json.dumps({
                    "unit":"anonymous-browser","visitors":4,
                    "articles":{"story":{"readers":1,"likes":0,
                                          "shareClicks":0,"continuedReaders":0,
                                          "arrivals":bad}}
                }).encode())
            with self.assertRaises(ValueError):
                m.read_public_metrics(["story"],opener=fetch)

    def test_reject_unverified_attribution_values(self):
        def bad_source(req, timeout):
            return FakeResponse(json.dumps({
                "unit": "anonymous-browser",
                "visitors": 10,
                "channels": {"x": "a lot", "search": 0, "external": 0, "unattributed": 0},
                "articles": {"a": {"readers": 0, "likes": 0, "shareClicks": 0, "continuedReaders": 0}},
            }).encode())
        with self.assertRaises(ValueError):
            m.read_public_metrics(["a"], opener=bad_source)

    def test_snapshot_counts_actual_publications_not_source_dates(self):
        articles = [
            {"slug": "new-story", "category": "暮らし",
             "published_at": "2026-11-02T21:00:00+09:00",
             "fiction_at": "2031-11-02T20:00:00+09:00",
             "source": {"published_at": "2026-11-02T20:00:00+09:00"}},
            {"slug": "old-story", "category": "社会",
             "published_at": "2026-11-01T23:00:00+09:00",
             "fiction_at": "2031-11-02T19:00:00+09:00",
             "source": {"published_at": "2026-11-02T19:00:00+09:00"}},
        ]
        counts = {slug: {"readers": 4, "likes": 0, "share_clicks": 1, "continued_readers": 2}
                  for slug in ("new-story", "old-story")}
        result = m.build_snapshot(
            articles, datetime.fromisoformat("2026-11-03T01:07:00+09:00"), 15, counts,
            {"visitors": 7, "articleReads": 12})
        self.assertEqual(result["articles_published_on_date"], 1)
        self.assertEqual(result["published_categories"], {"暮らし": 1})
        self.assertEqual(result["visitors_cumulative"], 15)
        self.assertEqual(result["daily_visitors"], 7)
        self.assertEqual(result["daily_article_reads"], 12)
        self.assertEqual(result["schema_version"], 3)
        self.assertFalse(result["page_views_available"])
        self.assertIsNone(result["daily_page_views"])
        self.assertIsNone(result["daily_page_views_coverage"])
        self.assertEqual(result["unit"], "anonymous-browser")
        self.assertEqual(result["recent_articles_sampled"], 2)
        self.assertEqual(result["recent_articles"][0]["continued_readers"], 2)
        self.assertIn("referrer-stripping undercounts", result["continuation_note"])
        self.assertNotIn("channels_cumulative", result)
        with_channels=m.build_snapshot(
            articles,datetime.fromisoformat("2026-11-03T01:07:00+09:00"),15,counts,
            {"visitors": 7, "articleReads": 12},
            {"x": 2, "search": 3, "external": 1, "unattributed": 4})
        self.assertEqual(with_channels["channels_cumulative"]["search"], 3)
        self.assertIn("may overlap", with_channels["channels_note"])
        with self.assertRaises(ValueError):
            m.build_snapshot(articles, datetime.fromisoformat(
                "2026-11-03T01:07:00+09:00"), 15, counts, {"visitors": 7})


class ContinuationMetricsValidation(unittest.TestCase):
    def test_invalid_or_absent_continuation_counts_are_not_faked(self):
        def fake_open(req,timeout):
            return FakeResponse(json.dumps({
                "unit":"anonymous-browser", "visitors":2,
                "articles":{"a":{"readers":2,"likes":0,"shareClicks":0}}
            }).encode())
        with self.assertRaises(ValueError):
            m.read_public_metrics(["a"],opener=fake_open)

        def negative_open(req,timeout):
            return FakeResponse(json.dumps({
                "unit":"anonymous-browser", "visitors":2,
                "articles":{"a":{"readers":2,"likes":0,"shareClicks":0,
                                 "continuedReaders":-1}}
            }).encode())
        with self.assertRaises(ValueError):
            m.read_public_metrics(["a"],opener=negative_open)


class PageViewSnapshotsTests(unittest.TestCase):
    @staticmethod
    def payload(day="2026-11-04", start="2026-11-04T00:30:00.000Z",
                total=53, daily=40, coverage="partial"):
        return {
            "unit": "anonymous-browser", "visitors": 206,
            "pageViews": total,
            "pageViewTracking": {"startedAtUtc": start, "timezone": "Asia/Tokyo",
                                 "unit": "page-view"},
            "day": {"dateJst": day, "visitors": 8, "articleReads": 12,
                    "pageViews": daily, "pageViewsCoverage": coverage},
        }

    def read(self, payload):
        def get_only(request, timeout):
            self.assertEqual(request.get_method(), "GET")
            self.assertIsNone(request.data)
            return FakeResponse(json.dumps(payload).encode())
        return m.read_day_totals(payload["day"]["dateJst"], opener=get_only)

    @staticmethod
    def snapshot(daily):
        return m.build_snapshot(
            [{"slug": "story", "category": "社会",
              "published_at": "2026-11-04T18:00:00+09:00"}],
            datetime.fromisoformat("2026-11-05T01:07:00+09:00"), 206,
            {"story": {"readers": 2, "likes": 0, "share_clicks": 0,
                       "continued_readers": 0, "page_views_cumulative": 17}}, daily)

    def test_start_day_remains_partial_in_future_archive(self):
        result = self.snapshot(self.read(self.payload()))
        self.assertEqual(result["schema_version"], 3)
        self.assertTrue(result["page_views_available"])
        self.assertEqual(result["daily_page_views"], 40)
        self.assertEqual(result["daily_article_reads"], 12)
        self.assertEqual(result["daily_page_views_coverage"], "partial")
        self.assertEqual(result["page_views_cumulative"], 53)
        self.assertEqual(result["page_views_started_at_utc"], "2026-11-04T00:30:00.000Z")
        self.assertEqual(result["recent_articles"][0]["page_views_cumulative"], 17)
        self.assertEqual(result["recent_articles"][0]["readers"], 2)

    def test_later_completed_day_including_zero_is_not_missing(self):
        result = self.read(self.payload(day="2026-11-05", daily=0, coverage="complete"))
        self.assertEqual(result["pageViews"], 0)
        self.assertEqual(result["pageViewsCoverage"], "complete")

    def test_exact_jst_midnight_start_can_cover_a_complete_day(self):
        result = self.read(self.payload(start="2026-11-03T15:00:00Z", coverage="complete"))
        self.assertEqual(result["pageViewsCoverage"], "complete")

    def test_pre_start_and_unstarted_tracking_preserve_null(self):
        for payload in (
                self.payload(day="2026-11-03", daily=None, coverage="unmeasured"),
                self.payload(start=None, total=None, daily=None, coverage="unmeasured")):
            with self.subTest(payload=payload):
                result = self.read(payload)
                self.assertIsNone(result["pageViews"])
                self.assertEqual(result["pageViewsCoverage"], "unmeasured")

    def test_false_complete_history_and_current_day_are_rejected(self):
        invalid = [
            self.payload(coverage="complete"),
            self.payload(day="2026-11-03", daily=0, coverage="unmeasured"),
            self.payload(day="2026-11-05", coverage="partial"),
            self.payload(coverage="in-progress"),
            self.payload(start=None, total=0, daily=0, coverage="unmeasured"),
            self.payload(daily=54),
            self.payload(daily=-1),
            self.payload(daily=True),
            self.payload(start="2026-11-04T00:30:00"),
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.read(payload)

    def test_partly_missing_new_api_contract_is_not_legacy_zero(self):
        for key in ("pageViews", "pageViewTracking"):
            payload = self.payload()
            del payload[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.read(payload)
        payload = self.payload()
        del payload["day"]["pageViewsCoverage"]
        with self.assertRaises(ValueError):
            self.read(payload)

    def test_article_pv_is_separate_from_distinct_browsers_and_can_be_null(self):
        for value in (17, 0, None):
            def response(request, timeout):
                return FakeResponse(json.dumps({
                    "unit": "anonymous-browser", "visitors": 200,
                    "articles": {"story": {"readers": 2, "likes": 0,
                        "shareClicks": 0, "continuedReaders": 0, "pageViews": value}}
                }).encode())
            _, counts, _ = m.read_public_metrics(["story"], opener=response)
            self.assertEqual(counts["story"]["page_views_cumulative"], value)
            self.assertEqual(counts["story"]["readers"], 2)

    def test_invalid_article_pv_is_not_saved(self):
        for value in (-1, "17", True):
            def response(request, timeout):
                return FakeResponse(json.dumps({
                    "unit": "anonymous-browser", "visitors": 200,
                    "articles": {"story": {"readers": 2, "likes": 0,
                        "shareClicks": 0, "continuedReaders": 0, "pageViews": value}}
                }).encode())
            with self.subTest(value=value), self.assertRaises(ValueError):
                m.read_public_metrics(["story"], opener=response)

    def test_v3_check_requires_pv_metadata_and_consistent_coverage(self):
        original = self.snapshot(self.read(self.payload()))
        for key in ("daily_page_views", "daily_page_views_coverage", "page_views_started_at_utc"):
            broken = dict(original)
            del broken[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                m.validate_snapshot(broken, "2026-11-04")
        broken = dict(original, daily_page_views_coverage="complete")
        with self.assertRaises(ValueError):
            m.validate_snapshot(broken, "2026-11-04")

    def test_check_only_preserves_valid_legacy_v2_bytes(self):
        legacy = {"schema_version": 2, "date_jst": "2026-11-03", "daily_visitors": 30,
                  "daily_article_reads": 20, "articles_published_on_date": 15}
        content = json.dumps(legacy, indent=2) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "analytics" / "daily" / "2026-11-03.json"
            target.parent.mkdir(parents=True)
            target.write_text(content)
            with (patch.object(m, "ROOT", root),
                  patch.object(m, "report_date", return_value="2026-11-03"),
                  patch.object(m.sys, "argv", ["record_metrics.py", "--check-only"]),
                  patch.object(m, "read_public_metrics", side_effect=AssertionError("No network needed"))):
                m.main()
            self.assertEqual(target.read_text(), content)
            # A normal scheduled run also leaves an existing snapshot unchanged.
            with (patch.object(m, "ROOT", root),
                  patch.object(m, "report_date", return_value="2026-11-03"),
                  patch.object(m.sys, "argv", ["record_metrics.py"]),
                  patch.object(m, "read_public_metrics", side_effect=AssertionError("No overwrite"))):
                m.main()
            self.assertEqual(target.read_text(), content)


if __name__ == "__main__":
    unittest.main()

