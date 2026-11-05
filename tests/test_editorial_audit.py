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
        "summary": "身近な出来事を扱う架空ニュースです。",
        "category": category,
        "published_at": published.isoformat(),
        "source": {
            "url": "https://example.org/source",
            "published_at": source.isoformat(),
            "kind": "ニュース",
            "topic": "通常のニュース",
        },
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
        del a["source"]["kind"]
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

    def test_crisis_topic_gets_an_advisory_review_warning(self):
        a = story("quake", NOW, NOW - timedelta(hours=1), title="地震後の避難所を題材にした記事")
        codes = {code for code, _, _ in audit([a], NOW)}
        self.assertIn("CRISIS_TOPIC_REVIEW", codes)

    def test_non_crisis_news_is_not_flagged_as_crisis(self):
        a = story("prices", NOW, NOW - timedelta(hours=1), title="スーパーの値札が毎日変わる")
        codes = {code for code, _, _ in audit([a], NOW)}
        self.assertNotIn("CRISIS_TOPIC_REVIEW", codes)

    def test_third_story_closes_followup_thread_in_editorial_audit(self):
        first = story("first", NOW - timedelta(hours=3), NOW - timedelta(hours=4))
        second = story("second", NOW - timedelta(hours=2), NOW - timedelta(hours=3))
        third = story("third", NOW - timedelta(hours=1), NOW - timedelta(hours=2))
        second["followup_of"] = "first"
        third["followup_of"] = "second"
        codes_by_slug = {(code, slug) for code, slug, _ in audit([third, second, first], NOW)}
        self.assertIn(("FOLLOWUP_THREAD_CLOSED", "third"), codes_by_slug)

    def test_context_dependent_headline_summary_and_opening_are_flagged(self):
        cases = (
            ("title", "この件で町内会が臨時会議"),
            ("summary", "これを受け、住民が新しい当番表を作った。"),
            ("paragraphs", ["その結果、店主は毎朝二度開店するようになった。"]),
        )
        for field, value in cases:
            with self.subTest(field=field):
                a = story("context", NOW, NOW - timedelta(hours=1))
                a["paragraphs"] = ["店主が新しい当番表を作った。"]
                a[field] = value
                codes = {code for code, _, _ in audit([a], NOW)}
                self.assertIn("CONTEXT_DEPENDENT_OPENING", codes)

    def test_standalone_headline_and_opening_are_not_flagged(self):
        a = story(
            "standalone",
            NOW,
            NOW - timedelta(hours=1),
            title="パン屋、閉店予告と誤解され毎朝『営業します』",
        )
        a["summary"] = "曖昧な告知を閉店予告と誤解されたパン屋が、毎朝営業を知らせるようになった。"
        a["paragraphs"] = ["町のパン屋が、開店前に毎朝営業中だと投稿するようになった。"]
        codes = {code for code, _, _ in audit([a], NOW)}
        self.assertNotIn("CONTEXT_DEPENDENT_OPENING", codes)


    def test_long_real_world_setup_before_fiction_gets_clarity_warning(self):
        a = story("background-heavy", NOW, NOW - timedelta(hours=1))
        a["paragraphs"] = [
            "米中間選挙後、AIの計算を担うデータセンターの新設が鈍るかもしれないとの見方から、既に施設を持つ大手IT企業の株が上がった。"
            "架空の会社は、建設を見送った空き地で完成式を開いた。"
        ]
        codes = {code for code, _, _ in audit([a], NOW)}
        self.assertIn("BACKGROUND_LOADED_OPENING", codes)

    def test_short_fact_then_fiction_is_not_background_loaded(self):
        a = story("short-setup", NOW, NOW - timedelta(hours=1))
        a["paragraphs"] = [
            "音楽特番が出演者の歌唱曲を事前発表した。"
            "その一方、架空の番組は観客に「初耳の顔」を練習させた。"
        ]
        codes = {code for code, _, _ in audit([a], NOW)}
        self.assertNotIn("BACKGROUND_LOADED_OPENING", codes)

    def test_missing_publication_date_is_not_assumed_from_future_dateline(self):
        a = story("undated", NOW, NOW)
        del a["published_at"]
        self.assertEqual(audit([a], NOW)[0][0], "MISSING_PUBLICATION")


if __name__ == "__main__":
    unittest.main()
