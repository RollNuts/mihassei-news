"""Offline-only regression tests for a free, read-only audience snapshot."""
import io
import json
import unittest
from datetime import datetime
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
                    slug: {"readers": 2, "likes": 1, "shareClicks": 0, "continuedReaders": 1}
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

    def test_reject_missing_or_invented_metrics(self):
        def fake_open(req, timeout):
            return FakeResponse(json.dumps({
                "unit": "anonymous-browser",
                "visitors": 0,
                "articles": {},
            }).encode())
        with self.assertRaises(ValueError):
            m.read_public_metrics(["real-story"], opener=fake_open)

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
            articles, datetime.fromisoformat("2026-11-03T01:07:00+09:00"), 15, counts)
        self.assertEqual(result["articles_published_on_date"], 1)
        self.assertEqual(result["published_categories"], {"暮らし": 1})
        self.assertEqual(result["visitors_cumulative"], 15)
        self.assertEqual(result["unit"], "anonymous-browser")
        self.assertEqual(result["recent_articles_sampled"], 2)
        self.assertEqual(result["recent_articles"][0]["continued_readers"], 2)
        self.assertIn("referrer-stripping undercounts", result["continuation_note"])
        self.assertNotIn("channels_cumulative", result)
        with_channels=m.build_snapshot(
            articles,datetime.fromisoformat("2026-11-03T01:07:00+09:00"),15,counts,
            {"x": 2, "search": 3, "external": 1, "unattributed": 4})
        self.assertEqual(with_channels["channels_cumulative"]["search"], 3)
        self.assertIn("may overlap", with_channels["channels_note"])


if __name__ == "__main__":
    unittest.main()

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
