"""Tests for editorial 3/2/2 metadata and duplicate guards."""
from __future__ import annotations

import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone

from scripts.editorial_mix import (
    EDITORIAL_TONES,
    LANE_TARGETS,
    lane_counts,
    next_preferred_lane,
    report_day,
    tone_advice,
    tone_counts,
    validate_articles,
)

JST = timezone(timedelta(hours=9))
BASE_TIME = datetime(2026, 11, 6, 9, 30, tzinfo=JST)


def story(
    slug: str,
    lane: str,
    *,
    published: datetime = BASE_TIME,
    term: str = "流行語大賞",
    tone: str = "comic_exaggeration",
    followup_of: str | None = None,
) -> dict:
    item = {
        "slug": slug,
        "published_at": published.isoformat(),
        "editorial_lane": lane,
        "editorial_tone": tone,
        "editorial_signature": {
            "target": f"{slug}が効率を重視しすぎる",
            "action": f"{slug}が手段を目的にする",
            "outcome": f"{slug}の本来の目的が消える",
        },
        "source": {
            "url": f"https://example.com/{slug}",
        },
    }
    if followup_of:
        item["followup_of"] = followup_of
    if tone == "plausible_future":
        item["plausibility_basis"] = {
            "present_signal": f"{slug}で現在すでに確認できる行動が増えている",
            "continuation_force": f"{slug}では効率化の圧力が今後も続く",
            "future_result": f"{slug}で5年後にはその行動が日常の標準になる",
        }
    if lane == "trend":
        item["trend_observation"] = {
            "source_kind": "google_trends",
            "term": term,
            "observed_at": (published - timedelta(hours=1)).isoformat(),
            "url": "https://trends.google.com/trending?geo=JP",
            "verification_urls": [f"https://example.com/{slug}"],
        }
    return item


class EditorialMixTests(unittest.TestCase):
    def test_target_is_three_two_two(self):
        self.assertEqual(
            LANE_TARGETS,
            {"trend": 3, "social_life": 2, "original_satire": 2},
        )

    def test_tones_are_orthogonal_to_topic_lanes(self):
        self.assertEqual(
            EDITORIAL_TONES,
            (
                "plausible_future",
                "comic_exaggeration",
                "observational",
                "wildcard",
            ),
        )

    def test_tone_advice_prioritizes_one_plausible_future_each_day(self):
        articles = [
            story("a", "trend", tone="comic_exaggeration"),
            story(
                "b",
                "social_life",
                tone="observational",
                published=BASE_TIME + timedelta(hours=2),
            ),
        ]
        self.assertEqual(
            tone_counts(articles, "2026-11-06")["plausible_future"],
            0,
        )
        self.assertIn(
            "plausible_future",
            tone_advice(articles, "2026-11-06"),
        )

    def test_tone_advice_breaks_a_two_story_run(self):
        articles = [
            story("a", "trend", tone="plausible_future"),
            story(
                "b",
                "social_life",
                tone="comic_exaggeration",
                published=BASE_TIME + timedelta(hours=2),
            ),
            story(
                "c",
                "original_satire",
                tone="comic_exaggeration",
                published=BASE_TIME + timedelta(hours=4),
            ),
        ]
        advice = tone_advice(articles, "2026-11-06")
        self.assertIn("last 2 are comic_exaggeration", advice)
        self.assertNotIn("comic_exaggeration (last 2", advice)

    def test_missing_tone_is_invalid_after_rollout(self):
        item = story("missing-tone", "social_life")
        item.pop("editorial_tone")
        errors, _ = validate_articles([item])
        self.assertTrue(any("editorial_tone required" in error for error in errors))

    def test_plausible_future_requires_an_explicit_causal_basis(self):
        item = story("plausible", "social_life", tone="plausible_future")
        item.pop("plausibility_basis")
        errors, _ = validate_articles([item])
        self.assertTrue(any("requires plausibility_basis" in error for error in errors))

    def test_plausible_future_rejects_a_placeholder_basis(self):
        item = story("thin", "original_satire", tone="plausible_future")
        item["plausibility_basis"]["continuation_force"] = "AI"
        errors, _ = validate_articles([item])
        self.assertTrue(any("continuation_force" in error for error in errors))

    def test_other_tones_do_not_need_plausibility_basis(self):
        item = story("comic", "social_life", tone="comic_exaggeration")
        errors, _ = validate_articles([item])
        self.assertFalse(any("plausibility_basis" in error for error in errors))

    def test_tone_run_detection_crosses_midnight(self):
        previous = story(
            "previous", "social_life", tone="plausible_future",
            published=datetime(2026, 11, 5, 23, 30, tzinfo=JST),
        )
        current = story(
            "current", "trend", tone="plausible_future",
            published=datetime(2026, 11, 6, 0, 30, tzinfo=JST),
            term="深夜の話題",
        )
        advice = tone_advice([previous, current], "2026-11-06")
        self.assertIn("last 2 tracked stories are plausible_future", advice)
        self.assertIn("across midnight", advice)

    def test_next_lane_fills_largest_remaining_deficit(self):
        self.assertEqual(next_preferred_lane(Counter()), "trend")
        self.assertEqual(
            next_preferred_lane(Counter({"trend": 1})),
            "trend",
        )
        self.assertEqual(
            next_preferred_lane(Counter({"trend": 2})),
            "social_life",
        )
        self.assertIsNone(
            next_preferred_lane(
                Counter({"trend": 3, "social_life": 2, "original_satire": 2})
            )
        )

    def test_lane_counts_only_selected_jst_day(self):
        articles = [
            story("a", "trend"),
            story("b", "social_life"),
            story(
                "old",
                "original_satire",
                published=BASE_TIME - timedelta(days=1),
            ),
        ]
        counts = lane_counts(articles, "2026-11-06")
        self.assertEqual(counts["trend"], 1)
        self.assertEqual(counts["social_life"], 1)
        self.assertEqual(counts["original_satire"], 0)

    def test_rollout_day_does_not_force_backfill_after_seven_legacy_stories(self):
        articles = [
            {
                "slug": f"legacy-{i}",
                "published_at": f"2026-11-05T{7 + i * 2:02d}:30:00+09:00",
                "source": {"url": f"https://example.com/legacy-{i}"},
            }
            for i in range(7)
        ]
        report = report_day(articles, "2026-11-05")
        self.assertIn("published=7", report)
        self.assertIn("rollout tracking partial", report)

    def test_valid_trend_metadata_passes(self):
        errors, warnings = validate_articles([story("fresh", "trend")])
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_official_source_kind_requires_matching_official_host(self):
        google = story("google-host", "trend")
        google["trend_observation"]["url"] = "https://example.com/trending"
        errors, _ = validate_articles([google])
        self.assertTrue(any("trends.google.com" in error for error in errors))

        x_item = story("x-host", "trend", term="別の話題")
        x_item["trend_observation"]["source_kind"] = "x_official"
        x_item["trend_observation"]["url"] = "https://trend.example.com/x"
        errors, _ = validate_articles([x_item])
        self.assertTrue(any("x.com or twitter.com" in error for error in errors))

    def test_official_x_host_is_accepted(self):
        item = story("x-official", "trend")
        item["trend_observation"]["source_kind"] = "x_official"
        item["trend_observation"]["url"] = "https://x.com/explore/tabs/trending"
        errors, _ = validate_articles([item])
        self.assertEqual(errors, [])

    def test_public_x_aggregator_is_allowed_but_warned(self):
        item = story("x-public", "trend")
        item["trend_observation"]["source_kind"] = "x_public_aggregator"
        errors, warnings = validate_articles([item])
        self.assertEqual(errors, [])
        self.assertTrue(any("official X" in warning for warning in warnings))

    def test_trend_requires_recent_observation_and_verification(self):
        item = story("bad", "trend")
        item["trend_observation"]["observed_at"] = (
            BASE_TIME - timedelta(days=2)
        ).isoformat()
        item["trend_observation"]["verification_urls"] = []
        errors, _ = validate_articles([item])
        self.assertTrue(any("max is" in error for error in errors))
        self.assertTrue(any("verification_urls" in error for error in errors))

    def test_typography_variants_do_not_bypass_trend_duplicate_guard(self):
        first = story("first", "trend", term="ＡＩ 規制")
        second = story(
            "second",
            "trend",
            term="AI・規制！",
            published=BASE_TIME + timedelta(hours=2),
        )
        errors, _ = validate_articles([first, second])
        self.assertTrue(any("duplicate trend term" in error for error in errors))

    def test_typography_variants_do_not_bypass_signature_guard(self):
        first = story("first", "social_life")
        second = story(
            "second",
            "original_satire",
            published=BASE_TIME + timedelta(hours=2),
        )
        second["editorial_signature"] = {
            "target": "Ｆｉｒｓｔ が効率を重視しすぎる！",
            "action": "first・が手段を目的にする",
            "outcome": "ｆｉｒｓｔ・の本来の目的が消える。",
        }
        errors, _ = validate_articles([first, second])
        self.assertTrue(
            any("editorial_signature duplicates" in error for error in errors)
        )

    def test_same_trend_term_is_blocked_within_seven_days(self):
        first = story("first", "trend", term="通信障害")
        second = story(
            "second",
            "trend",
            term="通信障害",
            published=BASE_TIME + timedelta(hours=2),
        )
        errors, _ = validate_articles([first, second])
        self.assertTrue(any("duplicate trend term" in error for error in errors))

    def test_followup_chain_can_reuse_trend_term(self):
        first = story("first", "trend", term="通信障害")
        second = story(
            "second",
            "trend",
            term="通信障害",
            published=BASE_TIME + timedelta(hours=2),
            followup_of="first",
        )
        third = story(
            "third",
            "trend",
            term="通信障害",
            published=BASE_TIME + timedelta(hours=4),
            followup_of="second",
        )
        errors, _ = validate_articles([first, second, third])
        self.assertFalse(any("duplicate trend term" in error for error in errors))

    def test_unrelated_followup_cannot_bypass_duplicate_trend_guard(self):
        first = story("first", "trend", term="通信障害")
        unrelated = story(
            "unrelated",
            "social_life",
            published=BASE_TIME + timedelta(hours=1),
        )
        second = story(
            "second",
            "trend",
            term="通信障害",
            published=BASE_TIME + timedelta(hours=2),
            followup_of="unrelated",
        )
        errors, _ = validate_articles([first, unrelated, second])
        self.assertTrue(any("duplicate trend term" in error for error in errors))

    def test_exact_satire_signature_reuse_is_blocked(self):
        first = story("first", "social_life")
        second = story(
            "second",
            "original_satire",
            published=BASE_TIME + timedelta(hours=2),
        )
        second["editorial_signature"] = first["editorial_signature"].copy()
        errors, _ = validate_articles([first, second])
        self.assertTrue(
            any("editorial_signature duplicates" in error for error in errors)
        )

    def test_followup_still_cannot_reuse_exact_satire_signature(self):
        first = story("first", "trend", term="通信障害")
        second = story(
            "second",
            "trend",
            term="通信障害",
            published=BASE_TIME + timedelta(hours=2),
            followup_of="first",
        )
        second["editorial_signature"] = first["editorial_signature"].copy()
        errors, _ = validate_articles([first, second])
        self.assertTrue(
            any("editorial_signature duplicates" in error for error in errors)
        )
        self.assertFalse(any("duplicate trend term" in error for error in errors))

    def test_pre_rollout_article_is_not_retroactively_required(self):
        old = {
            "slug": "legacy",
            "published_at": "2026-11-05T19:30:00+09:00",
            "source": {"url": "https://example.com/legacy"},
        }
        self.assertEqual(validate_articles([old]), ([], []))


if __name__ == "__main__":
    unittest.main()
