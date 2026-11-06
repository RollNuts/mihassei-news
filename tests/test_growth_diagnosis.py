"""Offline tests for live growth diagnosis."""
from __future__ import annotations

import unittest

from scripts import growth_diagnosis as g


def hourly_row(hour: int, views: int | None, coverage: str) -> dict:
    return {
        "hourJst": f"2026-11-06T{hour:02d}:00",
        "pageViews": views,
        "pageViewsCoverage": coverage,
    }


class GrowthDiagnosisTests(unittest.TestCase):
    def live(self) -> dict:
        rows = [hourly_row(h, 0, "complete") for h in range(18)]
        rows += [
            hourly_row(18, 12, "complete"),
            hourly_row(19, 80, "complete"),
            hourly_row(20, 5, "in-progress"),
        ]
        rows += [hourly_row(h, None, "future") for h in range(21, 24)]
        return {
            "hourly": {"today": rows},
            "today": {
                "pageViews": 175,
                "pageViewsCoverage": "in-progress",
            },
            "yesterday": {
                "pageViews": 96,
                "pageViewsCoverage": "complete",
            },
        }

    def state(self) -> dict:
        return {
            "queued": {
                "patrol": {
                    "buffer_status_at_accept": "sent",
                    "buffer_sent_at_at_accept": "2026-11-06T10:18:11.588Z",
                    "slot_jst": "2026-11-06T19:05:00+09:00",
                    "buffer_external_link_at_accept": "https://x.example/post",
                },
                "morning": {
                    "buffer_status_at_accept": "sent",
                    "buffer_sent_at_at_accept": "2026-11-06T00:15:45.100Z",
                    "slot_jst": "2026-11-06T09:05:00+09:00",
                },
            }
        }

    def selected(self, *, sources=None, page_kinds=None, top_views=70) -> dict:
        return {
            "selectedHour": {
                "hourJst": "2026-11-06T19:00",
                "pageViews": 80,
                "pageViewsCoverage": "complete",
                "pageViewSourcesCoverage": "complete" if sources is not None else "unmeasured",
                "pageViewSources": sources,
                "pageViewKindsCoverage": "complete" if page_kinds is not None else "unmeasured",
                "pageViewKinds": page_kinds,
            },
            "topArticles": {
                "selectedHour": [
                    {"article": "patrol", "pageViews": top_views},
                    {"article": "other", "pageViews": 10},
                ]
            },
        }

    def test_peak_hour_ignores_future_hours(self):
        peak = g.peak_hour(self.live())
        self.assertEqual(peak["hourJst"], "2026-11-06T19:00")
        self.assertEqual(peak["pageViews"], 80)

    def test_x_majority_plus_matching_post_is_strong_signal(self):
        report = g.build_report(
            self.live(),
            self.selected(
                sources={"x": 60, "search": 5, "external": 5, "unattributed": 10},
                page_kinds={"home": 5, "article": 70, "archive": 3, "serial": 1, "other": 1},
            ),
            self.state(),
            {"patrol": "防犯パトロール", "other": "別記事"},
        )
        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(report["diagnosis"], "x_distribution_strong_signal")
        self.assertEqual(report["peak_hour"]["source_mix"]["shares"]["x"], 0.75)
        self.assertEqual(report["peak_hour"]["page_kind_mix"]["shares"]["article"], 0.875)
        self.assertEqual(report["peak_hour"]["page_kind_interpretation"], "article_dominant")
        self.assertEqual(report["peak_hour"]["top_article_share"], 0.875)
        self.assertEqual(len(report["x_posts_near_peak"]), 1)
        self.assertTrue(report["x_posts_near_peak"][0]["is_peak_top_article"])
        self.assertEqual(report["x_posts_near_peak"][0]["minutes_from_peak_start"], 18.2)
        self.assertEqual(report["today_so_far_vs_previous_full_day_ratio"], 1.82)

    def test_timing_match_stays_unproven_when_source_history_is_unmeasured(self):
        report = g.build_report(
            self.live(),
            self.selected(sources=None),
            self.state(),
            {"patrol": "防犯パトロール"},
        )
        self.assertEqual(report["diagnosis"], "x_timing_article_match")
        self.assertIsNone(report["peak_hour"]["source_mix"])

    def test_single_article_breakout_without_nearby_x_post(self):
        state = {"queued": {}}
        report = g.build_report(
            self.live(),
            self.selected(sources={"x": 2, "search": 4, "external": 20, "unattributed": 54}),
            state,
            {"patrol": "防犯パトロール"},
        )
        self.assertEqual(report["diagnosis"], "single_article_breakout")
        self.assertEqual(report["x_posts_near_peak"], [])

    def test_navigation_heavy_spike_is_described_without_guessing_people(self):
        report = g.build_report(
            self.live(),
            self.selected(
                sources={"x": 4, "search": 4, "external": 12, "unattributed": 60},
                page_kinds={"home": 42, "article": 16, "archive": 14, "serial": 6, "other": 2},
                top_views=10,
            ),
            {"queued": {}},
            {"patrol": "防犯パトロール"},
        )
        self.assertEqual(report["diagnosis"], "mixed_or_unattributed")
        mix = report["peak_hour"]["page_kind_mix"]
        self.assertEqual(mix["classified_page_views"], 80)
        self.assertEqual(mix["shares"]["article"], 0.2)
        self.assertEqual(
            report["peak_hour"]["page_kind_interpretation"],
            "navigation_dominant",
        )
        self.assertIn("does not identify people", report["note"])

    def test_unmeasured_page_kind_history_remains_missing(self):
        report = g.build_report(
            self.live(),
            self.selected(
                sources={"x": 10, "search": 10, "external": 10, "unattributed": 50},
                page_kinds=None,
            ),
            {"queued": {}},
            {"patrol": "防犯パトロール"},
        )
        self.assertIsNone(report["peak_hour"]["page_kind_mix"])
        self.assertIsNone(report["peak_hour"]["page_kind_interpretation"])
        self.assertEqual(report["peak_hour"]["page_kind_coverage"], "unmeasured")

    def test_selected_hour_must_match_peak(self):
        selected = self.selected(sources=None)
        selected["selectedHour"]["hourJst"] = "2026-11-06T18:00"
        with self.assertRaises(ValueError):
            g.build_report(self.live(), selected, self.state(), {})


if __name__ == "__main__":
    unittest.main()
