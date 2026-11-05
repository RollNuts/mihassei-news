"""Offline tests for descriptive editorial performance reporting."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import editorial_performance as p


def article(
    slug: str,
    lane: str | None,
    tone: str | None,
    published_at: str,
    *,
    pv: int | None = 5,
    x: int | None = 1,
    continued: int | None = 1,
    readers: int | None = 5,
) -> dict:
    item = {
        "slug": slug,
        "published_at": published_at,
        "page_views_last_7_days": pv,
        "x_arrivals": x,
        "continued_readers": continued,
        "readers": readers,
    }
    if lane is not None:
        item["editorial_lane"] = lane
    if tone is not None:
        item["editorial_tone"] = tone
    return item


class EditorialPerformanceTests(unittest.TestCase):
    def snapshot(self) -> dict:
        return {
            "measured_at_utc": "2026-11-07T00:00:00+00:00",
            "recent_articles": [
                article("t1", "trend", "plausible_future", "2026-11-06T08:00:00+09:00", pv=12, x=3),
                article("t2", "trend", "comic_exaggeration", "2026-11-06T10:00:00+09:00", pv=8, x=2),
                article("t3", "trend", "observational", "2026-11-06T12:00:00+09:00", pv=10, x=None),
                article("s1", "social_life", "observational", "2026-11-06T14:00:00+09:00", pv=6, x=1),
                article("s2", "social_life", "comic_exaggeration", "2026-11-06T16:00:00+09:00", pv=4, x=0),
                # Too new for the default 6h lower bound.
                article("new", "original_satire", "wildcard", "2026-11-07T08:00:00+09:00", pv=20, x=4),
                # Pre-rollout / legacy metadata stays excluded rather than guessed.
                article("legacy", None, None, "2026-11-06T11:00:00+09:00", pv=50, x=8),
                # Missing PV is not a zero and cannot support a PV comparison.
                article("no-pv", "original_satire", "wildcard", "2026-11-06T13:00:00+09:00", pv=None, x=2),
            ],
        }

    def test_age_bounded_report_is_descriptive_not_a_winner_ranking(self):
        report = p.build_report(
            self.snapshot(),
            source_name="analytics/daily/2026-11-06.json",
        )
        self.assertEqual(report["eligible_articles"], 5)
        self.assertEqual(report["by_lane"]["trend"]["articles"], 3)
        self.assertEqual(report["by_lane"]["trend"]["status"], "descriptive")
        self.assertEqual(
            report["by_lane"]["social_life"]["status"],
            "insufficient_sample",
        )
        self.assertEqual(
            report["by_lane"]["trend"]["page_views_last_7_days"]["median"],
            10,
        )
        self.assertAlmostEqual(
            report["by_lane"]["trend"]["page_views_per_hour"]["median"],
            0.476,
            places=3,
        )
        self.assertEqual(
            report["by_lane"]["trend"]["continuation_share"]["median"],
            0.2,
        )
        self.assertNotIn("winner", json.dumps(report))
        self.assertIn("not evidence", report["note"])

    def test_missing_x_arrivals_remain_missing_not_zero(self):
        report = p.build_report(self.snapshot(), source_name="snapshot.json")
        trend_x = report["by_lane"]["trend"]["x_arrivals"]
        self.assertEqual(trend_x["observed"], 2)
        self.assertEqual(trend_x["median"], 2.5)

    def test_missing_reader_denominator_keeps_continuation_share_missing(self):
        snapshot = self.snapshot()
        snapshot["recent_articles"][0]["readers"] = None
        report = p.build_report(snapshot, source_name="snapshot.json")
        row = next(item for item in report["articles"] if item["slug"] == "t1")
        self.assertIsNone(row["continuation_share"])

    def test_legacy_and_missing_pv_are_not_backfilled(self):
        items = p.eligible_articles(self.snapshot())
        slugs = {item["slug"] for item in items}
        self.assertNotIn("legacy", slugs)
        self.assertNotIn("no-pv", slugs)

    def test_invalid_age_window_is_rejected(self):
        with self.assertRaises(ValueError):
            p.eligible_articles(self.snapshot(), min_age_hours=12, max_age_hours=6)

    def test_latest_snapshot_uses_latest_date_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            daily = root / "analytics" / "daily"
            daily.mkdir(parents=True)
            (daily / "2026-11-05.json").write_text("{}", encoding="utf-8")
            (daily / "2026-11-07.json").write_text("{}", encoding="utf-8")
            (daily / "notes.json").write_text("{}", encoding="utf-8")
            self.assertEqual(
                p.latest_snapshot_path(root).name,
                "2026-11-07.json",
            )

    def test_load_snapshot_requires_measured_time_and_articles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text('{"recent_articles":[]}', encoding="utf-8")
            with self.assertRaises(ValueError):
                p.load_snapshot(path)


if __name__ == "__main__":
    unittest.main()
