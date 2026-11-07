"""Tests for editorial 3/2/2 metadata and duplicate guards."""
from __future__ import annotations

import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone

from scripts.editorial_mix import (
    EDITORIAL_PAYOFFS,
    EDITORIAL_TONES,
    LANE_TARGETS,
    LIFE_READER_INTERESTS,
    READER_INTERESTS,
    RECURRING_CHARACTERS,
    character_advice,
    lane_counts,
    next_preferred_lane,
    reader_interest_advice,
    reader_interest_counts,
    payoff_advice,
    payoff_counts,
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
    payoff: str | None = "action",
    followup_of: str | None = None,
    reader_interest: str | None = None,
    recurring_character: str | None = None,
) -> dict:
    item = {
        "slug": slug,
        "published_at": published.isoformat(),
        "editorial_lane": lane,
        "editorial_tone": tone,
        "editorial_signature": {
            "present": f"{slug}で今も見られる効率優先の行動がある",
            "target": f"{slug}が効率を重視しすぎる",
            "action": f"{slug}が手段を目的にする",
            "outcome": f"{slug}の本来の目的が消える",
        },
        "visual_strategy": "cpu-photo",
        "scene_prompt": (
            "A candid documentary-style photo of one ordinary person using a simple "
            "everyday object in a normal Japanese room, natural light, no readable text."
        ),
        "source": {
            "url": f"https://example.com/{slug}",
        },
    }
    if payoff is not None:
        item["editorial_payoff"] = payoff
    if followup_of:
        item["followup_of"] = followup_of
    if reader_interest is not None:
        item["reader_interest"] = reader_interest
    if recurring_character is not None:
        item["recurring_character"] = recurring_character
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

    def test_payoff_types_are_orthogonal_to_topic_and_tone(self):
        self.assertEqual(
            EDITORIAL_PAYOFFS,
            (
                "dialogue",
                "action",
                "visible_result",
                "ironic_success",
                "quiet_aftertaste",
                "wordplay",
            ),
        )

    def test_reader_interests_broaden_the_slate_without_gender_labels(self):
        self.assertEqual(
            READER_INTERESTS,
            (
                "work_money_tech",
                "beauty_wellness",
                "relationships_social",
                "food_travel_shopping",
                "fandom_entertainment",
                "family_home",
                "general_society",
            ),
        )
        self.assertEqual(len(LIFE_READER_INTERESTS), 5)
        self.assertEqual(RECURRING_CHARACTERS, ("mina", "nono", "yuu"))

    def test_reader_clarity_is_required_from_evening_rollout(self):
        published = datetime(2026, 11, 6, 21, 30, tzinfo=JST)
        item = story("clear-story", "social_life", published=published)
        item["title"] = "買い物客、エコバッグ3枚を持参してもレジ袋を購入"
        item["summary"] = "買い物客がエコバッグを3枚持っているのに、汚したくなくて使わず、有料レジ袋を買う。"
        errors, _ = validate_articles([item])
        self.assertTrue(any("reader_clarity is required" in error for error in errors))

        item["reader_clarity"] = {
            "who": "買い物客",
            "action": "エコバッグ3枚を持参したまま有料レジ袋を買う",
            "oddity": "袋を持っているのに保存を優先して、本来の用途では一枚も使わない",
        }
        errors, _ = validate_articles([item])
        self.assertFalse(any("reader_clarity" in error for error in errors))

    def test_present_reality_anchor_is_required_after_review_rollout(self):
        published = datetime(2026, 11, 6, 22, 30, tzinfo=JST)
        item = story("present-anchor", "social_life", published=published)
        item["title"] = "買い物客、ポイント確認のため店を出たあと入口へ戻る"
        item["summary"] = "買い物を早く済ませるはずが、獲得ポイントの確認に夢中になり、退店後も入口へ戻ってレシートを見直す。"
        item["reader_clarity"] = {
            "who": "買い物客",
            "action": "退店後も入口へ戻って獲得ポイントを確認する",
            "oddity": "買い物を終えるための仕組みなのに、確認作業のため店を出られない",
        }
        item["editorial_signature"].pop("present")
        errors, _ = validate_articles([item])
        self.assertTrue(any("editorial_signature.present" in error for error in errors))

        item["editorial_signature"]["present"] = "買い物後もポイントや特典を細かく確認する行動がすでにある"
        errors, _ = validate_articles([item])
        self.assertFalse(any("editorial_signature.present" in error for error in errors))

    def test_illustration_requires_a_concrete_reason_after_photo_first_rollout(self):
        published = datetime(2026, 11, 6, 22, 30, tzinfo=JST)
        item = story("illustration-reason", "original_satire", published=published)
        item["title"] = "家族4人、空席を守るため全員が別々のテーブルに着席"
        item["summary"] = "混雑を避けるため席を確保した結果、同じ家族なのに全員が離れて食事することになった。"
        item["reader_clarity"] = {
            "who": "家族4人",
            "action": "空席を確保するため全員が別々のテーブルに座る",
            "oddity": "一緒に食事するため来たのに、席を守るほど家族が離れていく",
        }
        item["visual_strategy"] = "illustration"
        item["scene_prompt"] = (
            "A simple editorial scene of four family members seated at separate tables "
            "inside one ordinary food court, no readable text."
        )
        errors, _ = validate_articles([item])
        self.assertTrue(any("illustration_reason required" in error for error in errors))

        item["illustration_reason"] = "complex_relationship"
        errors, _ = validate_articles([item])
        self.assertFalse(any("illustration_reason" in error for error in errors))

    def test_photo_wording_cannot_be_routed_to_illustration(self):
        published = datetime(2026, 11, 6, 22, 30, tzinfo=JST)
        item = story("visual-mismatch", "original_satire", published=published)
        item["title"] = "防犯パトロール2班、互いを見張って町内を3周"
        item["summary"] = "知らない巡回班を不審者だと思い、双方が相手を追ううちに同じ住宅街を何度も回った。"
        item["reader_clarity"] = {
            "who": "防犯パトロール2班",
            "action": "互いを不審者だと思って見張りながら住宅街を回る",
            "oddity": "地域を守る側同士が相手を怪しみ、防犯活動そのものが追跡を生む",
        }
        item["visual_strategy"] = "illustration"
        item["illustration_reason"] = "precise_count_or_absence"
        item["scene_prompt"] = (
            "Photorealistic documentary-style photo of two neighborhood patrol groups "
            "watching each other on a quiet residential street."
        )
        errors, _ = validate_articles([item])
        self.assertTrue(any("conflicts with a photo-directed" in error for error in errors))

    def test_report_surfaces_photo_first_advice_when_illustrations_dominate(self):
        articles = []
        for i in range(5):
            item = story(
                f"visual-{i}",
                "social_life",
                published=datetime(2026, 11, 6, 12 + i, 0, tzinfo=JST),
            )
            if i < 4:
                item["visual_strategy"] = "illustration"
            articles.append(item)
        rendered = report_day(articles, "2026-11-06")
        self.assertIn("illustration=4", rendered)
        self.assertIn("cpu-photo preferred", rendered)

    def test_reader_interest_is_required_from_next_day_rollout(self):
        published = datetime(2026, 11, 7, 9, 30, tzinfo=JST)
        item = story("broad-slate", "social_life", published=published)
        errors, _ = validate_articles([item])
        self.assertTrue(any("reader_interest required" in error for error in errors))

        item["reader_interest"] = "beauty_wellness"
        errors, _ = validate_articles([item])
        self.assertFalse(any("reader_interest" in error for error in errors))

    def test_reader_interest_advice_nudges_life_topics_when_work_dominates(self):
        articles = [
            story(
                f"work-{i}",
                "social_life",
                published=BASE_TIME + timedelta(hours=i),
                reader_interest="work_money_tech",
            )
            for i in range(4)
        ]
        counts = reader_interest_counts(articles, "2026-11-06")
        self.assertEqual(counts["work_money_tech"], 4)
        advice = reader_interest_advice(articles, "2026-11-06")
        self.assertTrue(any(interest in advice for interest in LIFE_READER_INTERESTS))

    def test_recurring_character_is_optional_but_rotated(self):
        item = story(
            "mina-episode",
            "social_life",
            reader_interest="food_travel_shopping",
            recurring_character="mina",
        )
        errors, _ = validate_articles([item])
        self.assertFalse(any("recurring_character" in error for error in errors))
        self.assertIn("standalone preferred", character_advice([item], "2026-11-06"))

        item["recurring_character"] = "unknown"
        errors, _ = validate_articles([item])
        self.assertTrue(any("recurring_character" in error for error in errors))

    def test_payoff_advice_breaks_a_two_story_run(self):
        articles = [
            story("a", "trend", payoff="dialogue"),
            story(
                "b",
                "social_life",
                payoff="dialogue",
                published=BASE_TIME + timedelta(hours=2),
            ),
        ]
        self.assertEqual(payoff_counts(articles, "2026-11-06")["dialogue"], 2)
        advice = payoff_advice(articles, "2026-11-06")
        self.assertIn("last 2 tracked stories end as dialogue", advice)
        self.assertNotIn("dialogue (last 2", advice)

    def test_missing_payoff_is_invalid_after_payoff_rollout(self):
        item = story("missing-payoff", "social_life", payoff=None)
        errors, _ = validate_articles([item])
        self.assertTrue(any("editorial_payoff required" in error for error in errors))

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

    def test_tone_advice_prioritizes_a_second_future_for_last_core_slot(self):
        articles = [
            story("a", "trend", tone="comic_exaggeration"),
            story(
                "b", "social_life", tone="observational",
                published=BASE_TIME + timedelta(hours=2),
            ),
            story(
                "c", "original_satire", tone="plausible_future",
                published=BASE_TIME + timedelta(hours=4),
            ),
            story(
                "d", "trend", tone="comic_exaggeration",
                published=BASE_TIME + timedelta(hours=6),
            ),
            story(
                "e", "social_life", tone="observational",
                published=BASE_TIME + timedelta(hours=8),
            ),
            story(
                "f", "original_satire", tone="comic_exaggeration",
                published=BASE_TIME + timedelta(hours=10),
            ),
        ]
        advice = tone_advice(articles, "2026-11-06")
        self.assertIn("plausible_future", advice)
        self.assertIn("second distinct five-year mechanism", advice)

    def test_tone_advice_rotates_away_after_three_future_stories(self):
        articles = [
            story("a", "trend", tone="plausible_future"),
            story(
                "b", "social_life", tone="comic_exaggeration",
                published=BASE_TIME + timedelta(hours=2),
            ),
            story(
                "c", "original_satire", tone="plausible_future",
                published=BASE_TIME + timedelta(hours=4),
            ),
            story(
                "d", "trend", tone="observational",
                published=BASE_TIME + timedelta(hours=6),
            ),
            story(
                "e", "social_life", tone="plausible_future",
                published=BASE_TIME + timedelta(hours=8),
            ),
        ]
        advice = tone_advice(articles, "2026-11-06")
        self.assertNotIn("plausible_future (", advice)
        self.assertIn("keep the slate from becoming all-future", advice)

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
        self.assertIn("last 2 tracked stories are comic_exaggeration", advice)
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

    def test_new_trend_requires_bridge_and_explicit_x_discovery_term(self):
        item = story(
            "new-trend",
            "trend",
            published=datetime(2026, 11, 6, 17, 30, tzinfo=JST),
            term="流行語大賞",
        )
        item["title"] = "流行語大賞、知らない人ほど受賞語の説明係に選ばれる"
        item["summary"] = "知っている人向けの表彰なのに、知らない人ほど説明を求められる逆転をニュース化する。"
        errors, _ = validate_articles([item])
        self.assertTrue(any("trend_bridge" in error for error in errors))

        item["trend_bridge"] = {
            "why_now": "流行語大賞の発表で、受賞語を知らないという反応そのものが話題になる。",
            "shared_behavior": "みんなが知っている前提の言葉ほど、知らない側に説明責任が回ってくる。",
            "satire_gap": "流行を表彰する制度なのに、受賞後ほど意味を説明する仕事が増える。",
            "x_discovery_term": "流行語大賞",
        }
        item["x_tags"] = ["流行語大賞"]
        errors, _ = validate_articles([item])
        self.assertEqual(errors, [])

    def test_trend_bridge_rejects_broad_or_unanchored_discovery_term(self):
        item = story(
            "bad-discovery",
            "trend",
            published=datetime(2026, 11, 6, 17, 30, tzinfo=JST),
            term="生成AI",
        )
        item["title"] = "生成AI会議、要約を読むための会議を新設し時間短縮"
        item["summary"] = "会議を短くするための要約導入が、要約の確認会議を増やす逆転を具体的な職場の場面として描く。"
        item["trend_bridge"] = {
            "why_now": "生成AIの業務利用が話題になり、会議や文書の要約用途も広がっている。",
            "shared_behavior": "効率化のための道具を導入すると、その運用確認が新しい仕事になりやすい。",
            "satire_gap": "時間を減らすための仕組みが、仕組みを確認する時間を増やしてしまう。",
            "x_discovery_term": "社会",
        }
        item["x_tags"] = ["生成AI"]
        errors, _ = validate_articles([item])
        self.assertTrue(any("too broad" in error for error in errors))
        self.assertTrue(any("explicitly present" in error for error in errors))

    def test_trend_discovery_term_must_be_primary_and_article_anchored(self):
        item = story(
            "anchored",
            "trend",
            published=datetime(2026, 11, 6, 17, 30, tzinfo=JST),
            term="流行語大賞",
        )
        item["title"] = "流行語を知らない人ほど受賞語の説明係に選ばれる"
        item["summary"] = "知っている前提の言葉ほど、知らない側に説明する手間が回ってくる逆転を描く。"
        item["source"]["topic"] = "2026年の流行語大賞候補発表"
        item["trend_bridge"] = {
            "why_now": "流行語大賞の候補発表があり、候補語の意味を尋ねる反応も出ている。",
            "shared_behavior": "みんなが知っている前提の言葉ほど、知らない側に説明責任が回ってくる。",
            "satire_gap": "流行を表彰するほど、その言葉の意味を説明する仕事が増えてしまう。",
            "x_discovery_term": "流行語大賞",
        }
        item["x_tags"] = ["バズ", "流行語大賞"]
        errors, _ = validate_articles([item])
        self.assertTrue(any("first explicit X discovery term" in error for error in errors))

        item["x_tags"] = ["ポイ活"]
        item["trend_bridge"]["x_discovery_term"] = "ポイ活"
        errors, _ = validate_articles([item])
        self.assertTrue(any("anchored in title" in error for error in errors))

    def test_exact_observed_trend_term_is_a_valid_discovery_anchor(self):
        item = story(
            "observed-anchor",
            "trend",
            published=datetime(2026, 11, 6, 17, 30, tzinfo=JST),
            term="流行語大賞",
        )
        item["title"] = "流行語大賞、知らない言葉ほど説明係が増える表彰式"
        item["summary"] = "候補語を知っている前提が強まるほど、意味を尋ねる人が増える逆転を描く記事。"
        item["trend_bridge"] = {
            "why_now": "流行語大賞の候補発表があり、候補語そのものへの注目が集まっている。",
            "shared_behavior": "流行していると言われると、知らないことを言い出しにくくなる。",
            "satire_gap": "流行を示す制度なのに、受賞後ほど意味を確認する人が増える。",
            "x_discovery_term": "流行語大賞",
        }
        item["x_tags"] = ["流行語大賞"]
        errors, _ = validate_articles([item])
        self.assertEqual(errors, [])

    def test_natural_trend_term_must_reach_title_or_summary(self):
        item = story(
            "metadata-only-trend",
            "trend",
            published=datetime(2026, 11, 6, 17, 30, tzinfo=JST),
            term="流行語大賞",
        )
        item["title"] = "知らない言葉ほど説明係が増える表彰式"
        item["summary"] = "候補語を知っている前提が強まるほど、意味を尋ねる人が増える逆転を描く記事。"
        item["source"]["topic"] = "流行語大賞の候補発表"
        item["trend_bridge"] = {
            "why_now": "流行語大賞の候補発表があり、候補語そのものへの注目が集まっている。",
            "shared_behavior": "流行していると言われると、知らないことを言い出しにくくなる。",
            "satire_gap": "流行を示す制度なのに、受賞後ほど意味を確認する人が増える。",
            "x_discovery_term": "流行語大賞",
        }
        item["x_tags"] = ["流行語大賞"]
        errors, _ = validate_articles([item])
        self.assertTrue(any("must appear in the title or summary" in error for error in errors))

    def test_new_articles_reject_broad_invalid_or_unpublished_x_terms(self):
        item = story(
            "x-meta",
            "social_life",
            published=datetime(2026, 11, 6, 17, 30, tzinfo=JST),
        )
        item["x_tags"] = ["社会"]
        errors, _ = validate_articles([item])
        self.assertTrue(any("generic X tag" in error for error in errors))

        item["x_tags"] = ["ポイ活", "タイパ", "推し活"]
        errors, _ = validate_articles([item])
        self.assertTrue(any("at most 2 discovery terms" in error for error in errors))

        item["x_tags"] = []
        item["x_tickers"] = ["nvda"]
        errors, _ = validate_articles([item])
        self.assertTrue(any("invalid x_tickers" in error for error in errors))

        item["x_tickers"] = ["NVDA"]
        errors, _ = validate_articles([item])
        self.assertEqual(errors, [])

    def test_natural_observed_trend_term_stays_primary_x_tag(self):
        item = story(
            "keep-trend-term",
            "trend",
            published=datetime(2026, 11, 6, 17, 30, tzinfo=JST),
            term="流行語大賞",
        )
        item["title"] = "流行語大賞の発表後、バズ狙いの解説会が町内で乱立"
        item["summary"] = "話題の言葉を知らない人向けの説明会が増え、説明する側ほど意味を調べ直す逆転を描く。"
        item["source"]["topic"] = "流行語大賞の候補発表とバズ狙いの反応"
        item["trend_bridge"] = {
            "why_now": "流行語大賞の候補発表があり、候補語そのものへの注目が集まっている。",
            "shared_behavior": "流行に乗り遅れたくないほど、知らない言葉を知っているふりしやすい。",
            "satire_gap": "流行を説明する人ほど、その言葉の意味を裏で調べ直してしまう。",
            "x_discovery_term": "バズ",
        }
        item["x_tags"] = ["バズ"]
        errors, _ = validate_articles([item])
        self.assertTrue(any("use the exact observed trend term" in error for error in errors))

    def test_stock_cashtag_can_override_a_natural_trend_phrase(self):
        item = story(
            "ticker-override",
            "trend",
            published=datetime(2026, 11, 6, 17, 30, tzinfo=JST),
            term="NVIDIA決算",
        )
        item["title"] = "NVIDIA決算後、長期投資家が1分おきにNVDAを確認"
        item["summary"] = "長期保有を宣言した投資家ほど値動きを細かく確認し、落ち着くためのルールが監視を増やす。"
        item["source"]["topic"] = "NVIDIAの決算とNVDA株価への注目"
        item["trend_bridge"] = {
            "why_now": "NVIDIA決算を受けてNVDAの値動きが注目されている。",
            "shared_behavior": "長期投資を口にするほど短期の値動きを確認したくなる人がいる。",
            "satire_gap": "長期で持つための安心材料が、短期監視の回数を増やしてしまう。",
            "x_discovery_term": "NVDA",
        }
        item["x_tickers"] = ["NVDA"]
        errors, _ = validate_articles([item])
        self.assertEqual(errors, [])

    def test_new_trend_requires_a_real_detail_and_unique_hook(self):
        item = story(
            "specificity",
            "trend",
            published=datetime(2026, 11, 6, 18, 30, tzinfo=JST),
            term="日経平均",
        )
        item["title"] = "日経平均急落、長期投資家が売却日を100年後へ変更"
        item["summary"] = "前場5.27%安を見て短期変動を忘れようとした男性が、逆に値動きを毎分記録する。"
        item["paragraphs"] = [
            "前場5.27%安となった日、男性は売却日を100年先へ変更した。",
            "通知を切ったあとも株価を毎分確認した。",
        ]
        item["source"]["topic"] = "日経平均は前場5.27%安となった。"
        item["trend_bridge"] = {
            "why_now": "日経平均が大きく下落し、短期の値動きへの注目が急に高まっている。",
            "shared_behavior": "長期で構えると言うほど、直近の数字を何度も確認したくなることがある。",
            "satire_gap": "短期変動を気にしないための長期宣言が、短期監視を増やしてしまう。",
            "x_discovery_term": "日経平均",
        }
        item["x_tags"] = ["日経平均"]
        errors, _ = validate_articles([item])
        self.assertTrue(any("real_detail" in error for error in errors))
        self.assertTrue(any("unique_hook" in error for error in errors))

        item["trend_bridge"]["real_detail"] = "前場5.27%安"
        item["trend_bridge"]["unique_hook"] = (
            "前場5.27%安という急な下落幅が、長期保有を宣言しながら短期監視へ走る動機になる。"
        )
        errors, _ = validate_articles([item])
        self.assertEqual(errors, [])

    def test_trend_real_detail_must_be_public_and_source_grounded(self):
        item = story(
            "floating-detail",
            "trend",
            published=datetime(2026, 11, 6, 18, 30, tzinfo=JST),
            term="流行語大賞",
        )
        item["title"] = "流行語大賞、知らない人ほど説明係が増える"
        item["summary"] = "候補語を知っている前提が強まるほど、意味を尋ねる人が増える逆転を描く。"
        item["paragraphs"] = ["候補語の発表後、意味を尋ねる人が増えた。"]
        item["source"]["topic"] = "流行語大賞の候補30語が発表された。"
        item["trend_bridge"] = {
            "why_now": "流行語大賞の候補発表で、候補語そのものへの注目が集まっている。",
            "shared_behavior": "流行していると言われるほど、知らないことを言い出しにくくなる。",
            "satire_gap": "流行を表彰する制度なのに、意味を説明する仕事が増えてしまう。",
            "x_discovery_term": "流行語大賞",
            "real_detail": "候補30語",
            "unique_hook": "候補30語という多さが、知らない言葉を説明する側の負担を増幅させる。",
        }
        item["x_tags"] = ["流行語大賞"]
        errors, _ = validate_articles([item])
        self.assertTrue(any("must appear in title, summary, or the first paragraph" in error for error in errors))

        item["summary"] += " 実際の候補30語を前に、説明する側も検索し直す。"
        errors, _ = validate_articles([item])
        self.assertEqual(errors, [])

    def test_secondary_trend_x_term_must_be_article_anchored(self):
        item = story(
            "secondary-tag",
            "trend",
            published=datetime(2026, 11, 6, 18, 30, tzinfo=JST),
            term="流行語大賞",
        )
        item["title"] = "流行語大賞、候補語を知らない人ほど説明係に選ばれる"
        item["summary"] = "候補30語を前に、候補語の意味を説明する側まで検索し直し、流行を示すほど説明の手間が増える逆転を描く。"
        item["paragraphs"] = ["候補30語の発表後、候補語の説明会が開かれた。"]
        item["source"]["topic"] = "流行語大賞の候補30語が発表された。"
        item["trend_bridge"] = {
            "why_now": "流行語大賞の候補発表で、候補語への注目が集まっている。",
            "shared_behavior": "流行していると言われるほど、知らないことを言い出しにくくなる。",
            "satire_gap": "流行を示すほど、意味を説明する作業が増えてしまう。",
            "x_discovery_term": "流行語大賞",
            "real_detail": "候補30語",
            "unique_hook": "候補30語という多さが、知らない言葉を説明する側の負担を増幅させる。",
        }
        item["x_tags"] = ["流行語大賞", "ポイ活"]
        errors, _ = validate_articles([item])
        self.assertTrue(any("secondary X discovery term" in error for error in errors))

        item["x_tags"] = ["流行語大賞", "候補語"]
        errors, _ = validate_articles([item])
        self.assertEqual(errors, [])

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

    def test_google_trend_observation_must_be_within_twelve_hours(self):
        recent = story("google-recent", "trend")
        recent["trend_observation"]["observed_at"] = (
            BASE_TIME - timedelta(hours=11)
        ).isoformat()
        errors, _ = validate_articles([recent])
        self.assertFalse(any("trend observation is" in error for error in errors))

        stale = story("google-stale", "trend")
        stale["trend_observation"]["observed_at"] = (
            BASE_TIME - timedelta(hours=13)
        ).isoformat()
        errors, _ = validate_articles([stale])
        self.assertTrue(any("max is 12h for google_trends" in error for error in errors))

    def test_x_trend_observation_must_be_within_six_hours(self):
        recent = story("x-recent", "trend")
        recent["trend_observation"].update({
            "source_kind": "x_official",
            "url": "https://x.com/explore/tabs/trending",
            "observed_at": (BASE_TIME - timedelta(hours=5)).isoformat(),
        })
        errors, _ = validate_articles([recent])
        self.assertFalse(any("trend observation is" in error for error in errors))

        stale = story("x-stale", "trend")
        stale["trend_observation"].update({
            "source_kind": "x_official",
            "url": "https://x.com/explore/tabs/trending",
            "observed_at": (BASE_TIME - timedelta(hours=7)).isoformat(),
        })
        errors, _ = validate_articles([stale])
        self.assertTrue(any("max is 6h for x_official" in error for error in errors))

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

    def test_pre_payoff_rollout_article_keeps_lane_and_tone_without_payoff(self):
        item = story(
            "pre-payoff",
            "social_life",
            payoff=None,
            published=datetime(2026, 11, 6, 8, 30, tzinfo=JST),
        )
        errors, _ = validate_articles([item])
        self.assertFalse(any("editorial_payoff required" in error for error in errors))

    def test_pre_rollout_article_is_not_retroactively_required(self):
        old = {
            "slug": "legacy",
            "published_at": "2026-11-05T19:30:00+09:00",
            "source": {"url": "https://example.com/legacy"},
        }
        self.assertEqual(validate_articles([old]), ([], []))


if __name__ == "__main__":
    unittest.main()
