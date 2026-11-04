"""Editorial quality warnings never block a scheduled issue."""
import unittest
from datetime import datetime, timedelta, timezone

from scripts.audit_editorial import audit

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 2, 23, 0, tzinfo=JST)


def story(slug, published, source, category="社会", title="架空の地方ニュース"):
    return {
        "slug": slug,
        "title": title,
        "category": category,
        "published_at": published.isoformat(),
        "source": {"url": "https://example.org/source", "published_at": source.isoformat()},
    }


class EditorialAuditTests(unittest.TestCase):
    def test_fresh_sources_are_not_rejected(self):
        articles = [story("fresh", NOW, NOW - timedelta(hours=1))]
        self.assertEqual(audit(articles, NOW), [])

    def test_stale_sources_trigger_warning_not_an_exception(self):
        articles = [story("old", NOW, NOW - timedelta(hours=13, minutes=15))]
        warnings = audit(articles, NOW)
        self.assertEqual(warnings[0][0], "STALE_SOURCE")
        self.assertIn("13.2", warnings[0][2])

    def test_late_announcements_and_future_publication_are_flagged(self):
        a = story("later-source", NOW, NOW + timedelta(hours=1))
        b = story("future-issue", NOW + timedelta(hours=1), NOW)
        codes = {code for code, _, _ in audit([a, b], NOW)}
        self.assertEqual(codes, {"SOURCE_IN_FUTURE", "FUTURE_PUBLISHED"})

    def test_public_discussions_and_culture_do_not_expire_as_breaking_news(self):
        for kind in ("公開投稿", "生活・文化"):
            with self.subTest(kind=kind):
                a = story("ongoing-observation", NOW, NOW - timedelta(days=30))
                a["source"]["kind"] = kind
                self.assertEqual(audit([a], NOW), [])

    def test_news_and_unknown_kinds_keep_the_freshness_warning(self):
        for kind in ("ニュース", None, "", "掲示板", []):
            with self.subTest(kind=kind):
                a = story("old-news", NOW, NOW - timedelta(days=30))
                a["source"]["kind"] = kind
                self.assertEqual(
                    {code for code, _, _ in audit([a], NOW)}, {"STALE_SOURCE"},
                )

    def test_observation_kinds_still_validate_dates_and_source_urls(self):
        cases = (
            ("source", "published_at", (NOW + timedelta(hours=1)).isoformat(), "SOURCE_IN_FUTURE"),
            ("article", "published_at", (NOW + timedelta(hours=1)).isoformat(), "FUTURE_PUBLISHED"),
            ("source", "published_at", "not-a-date", "INVALID_DATE"),
            ("article", "published_at", "not-a-date", "INVALID_DATE"),
            ("source", "published_at", NOW.replace(tzinfo=None).isoformat(), "MISSING_TZ"),
            ("source", "url", "http://example.org/source", "INVALID_SOURCE_URL"),
        )
        for kind in ("公開投稿", "生活・文化"):
            for scope, key, value, expected in cases:
                with self.subTest(kind=kind, scope=scope, key=key, expected=expected):
                    a = story("observation", NOW, NOW - timedelta(days=30))
                    a["source"]["kind"] = kind
                    target = a["source"] if scope == "source" else a
                    target[key] = value
                    self.assertEqual({code for code, _, _ in audit([a], NOW)}, {expected})

    def test_missing_kind_keeps_the_freshness_warning(self):
        a = story("old-untyped-source", NOW, NOW - timedelta(days=30))
        self.assertNotIn("kind", a["source"])
        self.assertEqual({code for code, _, _ in audit([a], NOW)}, {"STALE_SOURCE"})

    def test_old_back_catalogue_is_not_rewarned(self):
        published = NOW - timedelta(days=10)
        self.assertEqual(
            audit([story("archive", published, published - timedelta(days=2))], NOW),
            [],
        )

    def test_recent_topic_and_category_concentration_is_observable(self):
        articles = [
            story(f"a-{i}", NOW - timedelta(minutes=i), NOW - timedelta(hours=2),
                  category="技術", title="AIロボットの新制度")
            for i in range(6)
        ]
        codes = {code for code, _, _ in audit(articles, NOW)}
        self.assertIn("CATEGORY_BIAS", codes)
        self.assertIn("AI_TOPIC_BIAS", codes)

    def test_missing_publication_date_is_not_assumed_from_future_dateline(self):
        a = story("undated", NOW, NOW)
        del a["published_at"]
        self.assertEqual(audit([a], NOW)[0][0], "MISSING_PUBLICATION")


if __name__ == "__main__":
    unittest.main()
