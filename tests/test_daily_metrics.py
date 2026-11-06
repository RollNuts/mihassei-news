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

    def test_article_metrics_can_capture_the_same_completed_report_day(self):
        calls = []
        def fake_open(request, timeout):
            query = parse_qs(urlsplit(request.full_url).query)
            calls.append(query)
            names = query["articles"][0].split(",")
            self.assertEqual(query["day"], ["2026-11-06"])
            return FakeResponse(json.dumps({
                "unit": "anonymous-browser",
                "visitors": 8,
                "channels": {"x": 2, "search": 1, "external": 0, "unattributed": 5},
                "articles": {
                    slug: {
                        "readers": 2, "likes": 0, "shareClicks": 0,
                        "continuedReaders": 1,
                        "pageViews": 12,
                        "last7DaysPageViews": 10,
                        "requestedDayPageViews": 6,
                    }
                    for slug in names
                },
            }).encode())
        _, metrics, _ = m.read_public_metrics(
            ["story"], opener=fake_open, day="2026-11-06"
        )
        self.assertEqual(metrics["story"]["page_views_report_day"], 6)
        self.assertEqual(metrics["story"]["page_views_last_7_days"], 10)
        self.assertEqual(len(calls), 1)

    def test_invalid_requested_day_article_pv_is_not_saved(self):
        for value in (-1, "6", True, 1.5):
            def fake_open(request, timeout):
                return FakeResponse(json.dumps({
                    "unit": "anonymous-browser",
                    "visitors": 1,
                    "articles": {
                        "story": {
                            "readers": 1, "likes": 0, "shareClicks": 0,
                            "continuedReaders": 0,
                            "requestedDayPageViews": value,
                        }
                    },
                }).encode())
            with self.subTest(value=value), self.assertRaises(ValueError):
                m.read_public_metrics(
                    ["story"], opener=fake_open, day="2026-11-06"
                )

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
        self.assertNotIn("page_views_last_7_days",metrics["story"])
        self.assertNotIn("page_views_report_day",metrics["story"])

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
        self.assertIsNone(result["page_views_last_7_days_window"])
        self.assertEqual(result["unit"], "anonymous-browser")
        self.assertEqual(result["recent_articles_sampled"], 2)
        self.assertEqual(result["recent_articles"][0]["continued_readers"], 2)
        self.assertIsNone(result["recent_articles"][0]["editorial_lane"])
        self.assertIsNone(result["recent_articles"][0]["editorial_tone"])
        self.assertEqual(result["published_editorial_metadata_tracked"], 0)
        self.assertEqual(result["published_editorial_lanes"], {})
        self.assertEqual(result["published_editorial_tones"], {})
        self.assertEqual(result["published_editorial_payoff_tracked"], 0)
        self.assertEqual(result["published_editorial_payoffs"], {})
        self.assertIn("not backfilled", result["editorial_metadata_note"])
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


    def test_snapshot_preserves_editorial_lane_and_tone_for_growth_comparison(self):
        articles = [
            {
                "slug": "trend-story", "category": "社会",
                "published_at": "2026-11-02T21:00:00+09:00",
                "editorial_lane": "trend",
                "editorial_tone": "plausible_future",
                "editorial_payoff": "quiet_aftertaste",
                "source": {"published_at": "2026-11-02T20:00:00+09:00"},
            },
            {
                "slug": "life-story", "category": "暮らし",
                "published_at": "2026-11-02T19:00:00+09:00",
                "editorial_lane": "social_life",
                "editorial_tone": "observational",
                "editorial_payoff": "action",
                "source": {"published_at": "2026-11-02T18:00:00+09:00"},
            },
            {
                "slug": "legacy-story", "category": "経済",
                "published_at": "2026-11-01T18:00:00+09:00",
                "source": {"published_at": "2026-11-01T17:00:00+09:00"},
            },
        ]
        counts = {
            article["slug"]: {
                "readers": 2, "likes": 0, "share_clicks": 0,
                "continued_readers": 1,
            }
            for article in articles
        }
        result = m.build_snapshot(
            articles, datetime.fromisoformat("2026-11-03T01:07:00+09:00"),
            12, counts, {"visitors": 5, "articleReads": 7},
        )
        self.assertEqual(result["published_editorial_metadata_tracked"], 2)
        self.assertEqual(
            result["published_editorial_lanes"],
            {"social_life": 1, "trend": 1},
        )
        self.assertEqual(
            result["published_editorial_tones"],
            {"observational": 1, "plausible_future": 1},
        )
        self.assertEqual(result["published_editorial_payoff_tracked"], 2)
        self.assertEqual(
            result["published_editorial_payoffs"],
            {"action": 1, "quiet_aftertaste": 1},
        )
        by_slug = {item["slug"]: item for item in result["recent_articles"]}
        self.assertEqual(by_slug["trend-story"]["editorial_lane"], "trend")
        self.assertEqual(
            by_slug["trend-story"]["editorial_tone"], "plausible_future"
        )
        self.assertEqual(
            by_slug["trend-story"]["editorial_payoff"], "quiet_aftertaste"
        )
        self.assertEqual(by_slug["life-story"]["editorial_lane"], "social_life")
        self.assertIsNone(by_slug["legacy-story"]["editorial_lane"])
        m.validate_snapshot(result, "2026-11-02")

    def test_partial_editorial_metadata_is_rejected_instead_of_silently_dropped(self):
        article = {
            "slug": "partial", "category": "社会",
            "published_at": "2026-11-02T21:00:00+09:00",
            "editorial_lane": "trend",
            "source": {"published_at": "2026-11-02T20:00:00+09:00"},
        }
        counts = {
            "partial": {
                "readers": 1, "likes": 0, "share_clicks": 0,
                "continued_readers": 0,
            }
        }
        with self.assertRaisesRegex(ValueError, "Partial or invalid editorial"):
            m.build_snapshot(
                [article],
                datetime.fromisoformat("2026-11-03T01:07:00+09:00"),
                1, counts, {"visitors": 1, "articleReads": 1},
            )

    def test_corrupt_editorial_snapshot_summary_fails_closed(self):
        articles = [{
            "slug": "tracked", "category": "社会",
            "published_at": "2026-11-02T21:00:00+09:00",
            "editorial_lane": "trend",
            "editorial_tone": "wildcard",
            "source": {"published_at": "2026-11-02T20:00:00+09:00"},
        }]
        counts = {
            "tracked": {
                "readers": 1, "likes": 0, "share_clicks": 0,
                "continued_readers": 0,
            }
        }
        result = m.build_snapshot(
            articles, datetime.fromisoformat("2026-11-03T01:07:00+09:00"),
            1, counts, {"visitors": 1, "articleReads": 1},
        )
        result["published_editorial_lanes"] = {"trend": 0}
        with self.assertRaisesRegex(ValueError, "Editorial lane counts"):
            m.validate_snapshot(result, "2026-11-02")


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

    def test_v2_live_breakdown_is_archived_without_relabeling_units(self):
        payload = self.payload(start="2026-11-03T15:00:00Z", coverage="complete")
        payload["sessions"] = 12
        payload["sessionTracking"] = {
            "startedAtUtc": "2026-11-03T15:00:00Z",
            "timezone": "Asia/Tokyo",
            "unit": "session",
            "inactivityTimeoutMinutes": 30,
        }
        payload["day"]["sessions"] = 5
        payload["day"]["sessionsCoverage"] = "complete"
        payload["topArticles"] = {"day": [
            {"article": "story-a", "pageViews": 25},
            {"article": "story-b", "pageViews": 10},
        ]}
        payload["hourly"] = {"day": [
            {"hourJst": "2026-11-04T" + str(hour).zfill(2) + ":00",
             "pageViews": 40 if hour == 12 else 0,
             "pageViewsCoverage": "complete"}
            for hour in range(24)
        ]}
        payload["pageViewSourcesDay"] = {
            "x": 10, "search": 5, "external": 3, "unattributed": 2,
        }
        daily = self.read(payload)
        self.assertEqual(daily["sessions"], 5)
        self.assertEqual(daily["sessionsCumulative"], 12)
        self.assertEqual(daily["topArticles"][0]["article"], "story-a")
        self.assertEqual(sum(row["pageViews"] for row in daily["hourlyPageViews"]), 40)
        self.assertEqual(daily["pageViewSources"]["x"], 10)

        result = self.snapshot(daily)
        self.assertEqual(result["daily_sessions"], 5)
        self.assertEqual(result["session_timeout_minutes"], 30)
        self.assertEqual(result["daily_top_articles"][0]["pageViews"], 25)
        self.assertEqual(result["daily_hourly_page_views"][12]["pageViews"], 40)
        self.assertEqual(result["daily_page_view_sources"]["search"], 5)

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
                        "shareClicks": 0, "continuedReaders": 0, "pageViews": value,
                        "last7DaysPageViews": value}}
                }).encode())
            _, counts, _ = m.read_public_metrics(["story"], opener=response)
            self.assertEqual(counts["story"]["page_views_cumulative"], value)
            self.assertEqual(counts["story"]["page_views_last_7_days"], value)
            self.assertEqual(counts["story"]["readers"], 2)

    def test_invalid_article_pv_is_not_saved(self):
        for key in ("pageViews", "last7DaysPageViews"):
            for value in (-1, "17", True, 1.5):
                def response(request, timeout):
                    return FakeResponse(json.dumps({
                        "unit": "anonymous-browser", "visitors": 200,
                        "articles": {"story": {"readers": 2, "likes": 0,
                            "shareClicks": 0, "continuedReaders": 0, key: value}}
                    }).encode())
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    m.read_public_metrics(["story"], opener=response)

    def test_seven_date_pv_keeps_opposite_browser_order_and_capture_window(self):
        payload = self.payload()
        payload["last7Days"] = {
            "startDateJst": "2026-10-30", "endDateJst": "2026-11-05",
            "pageViews": 53, "measuredDays": 2, "complete": False,
        }
        payload["articles"] = {
            "many-browsers": {"readers": 90, "likes": 0, "shareClicks": 0,
                              "continuedReaders": 0, "pageViews": 4,
                              "last7DaysPageViews": 3},
            "repeat-displays": {"readers": 2, "likes": 0, "shareClicks": 0,
                                "continuedReaders": 0, "pageViews": 41,
                                "last7DaysPageViews": 36},
        }
        def get_only(request, timeout):
            self.assertEqual(request.get_method(), "GET")
            self.assertIsNone(request.data)
            return FakeResponse(json.dumps(payload).encode())
        visitors, counts, channels = m.read_public_metrics(
            list(payload["articles"]), opener=get_only)
        result = m.build_snapshot(
            [{"slug": slug, "category": "社会", "published_at": "2026-11-04T18:00:00+09:00"}
             for slug in counts], datetime.fromisoformat("2026-11-05T01:07:00+09:00"),
            visitors, counts, self.read(payload), channels)
        saved = {item["slug"]: item for item in result["recent_articles"]}
        self.assertEqual(saved["many-browsers"]["readers"], 90)
        self.assertEqual(saved["repeat-displays"]["readers"], 2)
        self.assertEqual(saved["many-browsers"]["page_views_last_7_days"], 3)
        self.assertEqual(saved["repeat-displays"]["page_views_last_7_days"], 36)
        self.assertEqual(saved["repeat-displays"]["page_views_cumulative"], 41)
        self.assertEqual(result["daily_page_views"], 40)
        self.assertEqual(result["date_jst"], "2026-11-04")
        self.assertEqual(result["measured_at_utc"], "2026-11-04T16:07:00+00:00")
        self.assertEqual(result["page_views_last_7_days_window"], {
            "start_date_jst": "2026-10-30", "end_date_jst": "2026-11-05",
            "measured_days": 2, "complete": False,
        })
        self.assertIn("measured_at_utc", result["page_views_last_7_days_note"])
        self.assertIn("not seven completed days", result["page_views_last_7_days_note"])

    def test_seven_date_unmeasured_coverage_is_not_zero_history(self):
        payload = self.payload(start=None, total=None, daily=None, coverage="unmeasured")
        payload["last7Days"] = {
            "startDateJst": "2026-10-30", "endDateJst": "2026-11-05",
            "pageViews": None, "measuredDays": 0, "complete": False,
        }
        result = m.build_snapshot(
            [{"slug": "story", "category": "社会",
              "published_at": "2026-11-04T18:00:00+09:00"}],
            datetime.fromisoformat("2026-11-05T01:07:00+09:00"), 206,
            {"story": {"readers": 2, "likes": 0, "share_clicks": 0,
                       "continued_readers": 0, "page_views_cumulative": None,
                       "page_views_last_7_days": None}}, self.read(payload))
        self.assertEqual(result["page_views_last_7_days_window"]["measured_days"], 0)
        self.assertFalse(result["page_views_last_7_days_window"]["complete"])
        self.assertIsNone(result["recent_articles"][0]["page_views_last_7_days"])

    def test_seven_measured_dates_distinguish_full_and_partial_first_day(self):
        for start, complete in (("2026-10-29T15:00:00Z", True),
                                ("2026-10-29T15:00:00.001Z", False)):
            payload = self.payload(start=start, coverage="complete")
            payload["last7Days"] = {
                "startDateJst": "2026-10-30", "endDateJst": "2026-11-05",
                "pageViews": 53, "measuredDays": 7, "complete": complete,
            }
            with self.subTest(start=start):
                result = self.snapshot(self.read(payload))
                self.assertEqual(result["page_views_last_7_days_window"]["measured_days"], 7)
                self.assertEqual(result["page_views_last_7_days_window"]["complete"], complete)

    def test_seven_date_window_rejects_false_coverage_and_wrong_capture_date(self):
        valid = {"startDateJst": "2026-10-30", "endDateJst": "2026-11-05",
                 "pageViews": 53, "measuredDays": 2, "complete": False}
        for change in ({"complete": True}, {"complete": "false"},
                       {"measuredDays": 7}, {"measuredDays": True},
                       {"startDateJst": "2026-10-29"}, {"endDateJst": None}):
            payload = self.payload()
            payload["last7Days"] = {**valid, **change}
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.read(payload)
        payload = self.payload()
        # This is a valid seven-date API window, but it belongs to a different
        # capture date. It must not be labeled as a window ending November 5.
        payload["last7Days"] = {**valid, "startDateJst": "2026-10-29",
                               "endDateJst": "2026-11-04", "measuredDays": 1}
        with self.assertRaises(ValueError):
            self.snapshot(self.read(payload))

    def test_saved_seven_date_article_values_validate_without_requiring_legacy_fields(self):
        for value in (0, None, 17):
            result = self.snapshot(self.read(self.payload()))
            result["recent_articles"][0]["page_views_last_7_days"] = value
            m.validate_snapshot(result, "2026-11-04")
            self.assertEqual(result["recent_articles"][0]["page_views_last_7_days"], value)
        for value in (-1, True, "17", 1.5):
            result = self.snapshot(self.read(self.payload()))
            result["recent_articles"][0]["page_views_last_7_days"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                m.validate_snapshot(result, "2026-11-04")

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

    def test_check_only_preserves_valid_legacy_v2_and_v3_bytes(self):
        v2 = {"schema_version": 2, "date_jst": "2026-11-03", "daily_visitors": 30,
              "daily_article_reads": 20, "articles_published_on_date": 15}
        v3 = self.snapshot(self.read(self.payload()))
        del v3["page_views_last_7_days_window"]
        del v3["page_views_last_7_days_note"]
        for legacy in (v2, v3):
            day = legacy["date_jst"]
            content = json.dumps(legacy, indent=2) + "\n"
            with self.subTest(schema=legacy["schema_version"]), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / "analytics" / "daily" / (day + ".json")
                target.parent.mkdir(parents=True)
                target.write_text(content)
                with (patch.object(m, "ROOT", root),
                      patch.object(m, "report_date", return_value=day),
                      patch.object(m.sys, "argv", ["record_metrics.py", "--check-only"]),
                      patch.object(m, "read_public_metrics", side_effect=AssertionError("No network needed"))):
                    m.main()
                self.assertEqual(target.read_text(), content)
                # A normal scheduled run also leaves an existing snapshot unchanged.
                with (patch.object(m, "ROOT", root),
                      patch.object(m, "report_date", return_value=day),
                      patch.object(m.sys, "argv", ["record_metrics.py"]),
                      patch.object(m, "read_public_metrics", side_effect=AssertionError("No overwrite"))):
                    m.main()
                self.assertEqual(target.read_text(), content)


if __name__ == "__main__":
    unittest.main()

