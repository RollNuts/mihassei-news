"""Tests for blind first-read clarity digest."""
from datetime import datetime, timedelta, timezone
import unittest

from scripts.clarity_digest import blind_rows, render_text, select_articles

JST = timezone(timedelta(hours=9))


def article(slug: str, when: datetime, *, title: str = "店主、袋を三つ持ってレジ袋購入") -> dict:
    return {
        "slug": slug,
        "published_at": when.isoformat(),
        "title": title,
        "summary": "袋を持っているのに汚したくなくて使わず、別の袋を買った。",
        "source": {"topic": "hidden source detail"},
        "editorial_signature": {"target": "hidden"},
        "paragraphs": ["hidden body"],
    }


class ClarityDigestTests(unittest.TestCase):
    def test_day_selects_only_that_jst_date(self):
        items = [
            article("a", datetime(2026, 11, 5, 23, 30, tzinfo=JST)),
            article("b", datetime(2026, 11, 6, 7, 30, tzinfo=JST)),
            article("c", datetime(2026, 11, 7, 7, 30, tzinfo=JST)),
        ]
        selected = select_articles(items, day="2026-11-06")
        self.assertEqual([item["slug"] for item in selected], ["b"])

    def test_days_uses_latest_publication_day_as_offline_anchor(self):
        items = [
            article("old", datetime(2026, 11, 1, 7, 30, tzinfo=JST)),
            article("a", datetime(2026, 11, 5, 7, 30, tzinfo=JST)),
            article("b", datetime(2026, 11, 6, 7, 30, tzinfo=JST)),
            article("c", datetime(2026, 11, 7, 7, 30, tzinfo=JST)),
        ]
        selected = select_articles(items, days=3)
        self.assertEqual([item["slug"] for item in selected], ["a", "b", "c"])

    def test_limit_keeps_latest_matches(self):
        items = [
            article(f"a-{i}", datetime(2026, 11, 6, 7 + i, 30, tzinfo=JST))
            for i in range(4)
        ]
        selected = select_articles(items, day="2026-11-06", limit=2)
        self.assertEqual([item["slug"] for item in selected], ["a-2", "a-3"])

    def test_blind_output_excludes_source_metadata_and_body(self):
        item = article("blind", datetime(2026, 11, 6, 19, 30, tzinfo=JST))
        rows = blind_rows([item])
        self.assertEqual(set(rows[0]), {"slug", "published_at", "title", "summary"})
        rendered = render_text(rows)
        self.assertIn("TITLE:", rendered)
        self.assertIn("SUMMARY:", rendered)
        self.assertNotIn("hidden source detail", rendered)
        self.assertNotIn("hidden body", rendered)
        self.assertNotIn("editorial_signature", rendered)


if __name__ == "__main__":
    unittest.main()
