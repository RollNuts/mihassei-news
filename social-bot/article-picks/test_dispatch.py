"""Offline tests of Buffer-Free article picks; never queue from test."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dispatch as picks

NOW = datetime.fromisoformat("2026-11-05T12:00:00+09:00")
CONFIG = {
    "schema_version": 1, "enabled": True, "channel_id": "X-VERIPSA",
    "max_new_per_run": 1, "max_per_jst_day": 7,
    "min_page_views": 8, "fresh_minimum_age_hours": 1,
    "fresh_maximum_age_hours": 18, "minimum_article_age_hours": 12,
    "maximum_article_age_days": 21, "minimum_interval_hours": 2,
}


def article(slug: str, age_hours: int = 36, title: str = "挨拶を短くした会社、最後の挨拶だけ1時間続ける"):
    return {
        "slug": slug,
        "title": title,
        "summary": "会議を短くするため最後の挨拶を増やした会社で、会議本編よりも挨拶の時間が長くなってしまった。",
        "published_at": (NOW - timedelta(hours=age_hours)).isoformat(),
        "fiction_at": "2031-11-05T00:00:00+09:00",  # Never used for age.
        "source": {"published_at": "2026-11-05T00:00:00+09:00"},
    }


def stats(page_views=11, today_page_views=1):
    return {"last7DaysPageViews": page_views, "todayPageViews": today_page_views}


def pv_payload(slugs, started_at=None):
    started = started_at or NOW - timedelta(days=10)
    first = NOW.date() - timedelta(days=6)
    return {
        # The legacy root unit must never replace the PV tracking unit.
        "unit": "anonymous-browser",
        "pageViewTracking": {"unit": "page-view", "timezone": "Asia/Tokyo",
                             "startedAtUtc": started.isoformat()},
        "today": {"dateJst": NOW.date().isoformat()},
        "last7Days": {
            "startDateJst": first.isoformat(), "endDateJst": NOW.date().isoformat(),
            "pageViews": 10000, "measuredDays": min(7, (NOW.date() - started.date()).days + 1),
            "complete": started <= datetime.combine(first, datetime.min.time(), tzinfo=picks.JST),
        },
        "articles": {slug: stats() for slug in slugs},
    }


def ledger(**entries):
    return {"schema_version": 1, "queued": entries}


def recorded(buf="buf-old", hours=180):
    return {"buffer_id": buf,
            "queued_at_utc": (NOW - timedelta(hours=hours)).isoformat()}


class ArticlePickTests(unittest.TestCase):
    def test_actual_config_and_initial_ledger(self):
        config = picks.read_json(HERE / "config.json")
        picks.validate_config(config)
        state = picks.read_json(HERE / "state.json")
        picks.validate_state(state)
        self.assertNotIn("max_queue_size", config)
        self.assertEqual(config["max_new_per_run"], 1)
        self.assertEqual(config["max_per_jst_day"], 7)
        self.assertEqual(config["min_page_views"], 8)
        self.assertEqual(config["fresh_minimum_age_hours"], 1)
        self.assertEqual(config["fresh_maximum_age_hours"], 18)
        self.assertNotIn("min_readers", config)
        self.assertEqual(config["minimum_interval_hours"], 2)
        self.assertEqual(picks.PROMOTION_SLOT_HOURS, (9, 11, 13, 15, 17, 19, 21))
        self.assertEqual(picks.PROMOTION_SLOT_MINUTE, 5)

    def test_push_fallback_network_window_is_exactly_daytime_jst(self):
        cases = (
            ("2026-11-05T08:59:59+09:00", False),
            ("2026-11-05T09:00:00+09:00", True),
            ("2026-11-05T21:59:59+09:00", True),
            ("2026-11-05T22:00:00+09:00", False),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(
                    picks.trigger_allows_network("push", datetime.fromisoformat(value)),
                    expected,
                )
        for event in ("", "schedule", "workflow_dispatch"):
            self.assertTrue(picks.trigger_allows_network(event, NOW))
        with self.assertRaisesRegex(ValueError, "timezone"):
            picks.trigger_allows_network("push", NOW.replace(tzinfo=None))

    def test_only_article_archive_push_wakes_promotion_workflow(self):
        workflow = (picks.ROOT / ".github" / "workflows" /
                    "x-original-experiments.yml").read_text(encoding="utf-8")
        push = workflow.split("  push:\n", 1)[1].split("  pull_request:\n", 1)[0]
        self.assertIn('"content/articles.json"', push)
        self.assertNotIn('"social-bot/', push)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("X_PUSH_BEFORE: ${{ github.event.before }}", workflow)

    def test_push_requires_a_new_recent_article_in_git_history(self):
        previous = [article("existing", age_hours=3)]
        new_article = article("newly-published", age_hours=2)
        with patch.object(
            picks.subprocess, "run",
            return_value=SimpleNamespace(stdout=json.dumps(previous)),
        ) as git_show:
            self.assertFalse(picks.push_has_new_article("a" * 40, previous, NOW))
            self.assertTrue(
                picks.push_has_new_article("a" * 40, previous + [new_article], NOW)
            )
            self.assertFalse(
                picks.push_has_new_article(
                    "a" * 40, previous + [article("backfilled", age_hours=60)], NOW
                )
            )
            self.assertFalse(picks.push_has_new_article("", previous + [new_article], NOW))
            git_show.assert_called()
        with patch.object(
            picks.subprocess, "run",
            return_value=SimpleNamespace(stdout="{not valid json"),
        ):
            self.assertFalse(
                picks.push_has_new_article("a" * 40, previous + [new_article], NOW)
            )
        with patch.object(picks.subprocess, "run", side_effect=OSError("git unavailable")):
            self.assertFalse(
                picks.push_has_new_article("a" * 40, previous + [new_article], NOW)
            )

    def test_code_or_existing_article_push_never_contacts_buffer(self):
        documents = {HERE / "config.json": CONFIG, HERE / "state.json": ledger()}
        with patch.object(
            picks, "read_json",
            side_effect=lambda path: copy.deepcopy(documents[path]),
        ), patch.object(picks, "datetime", wraps=datetime) as clock, \
             patch.dict(
                 os.environ,
                 {"X_TRIGGER_EVENT": "push", "X_PUSH_BEFORE": "a" * 40},
                 clear=True,
             ), patch.object(picks, "push_has_new_article", return_value=False) as new, \
             patch.object(picks, "read_page_view_metrics") as metrics, \
             patch.object(picks, "choose_x_channel") as choose, \
             patch.object(picks, "list_existing") as existing, \
             patch.object(picks, "queue_one") as queue:
            clock.now.return_value = NOW
            self.assertEqual(picks.main(), 0)
            new.assert_called_once()
            metrics.assert_not_called()
            choose.assert_not_called()
            existing.assert_not_called()
            queue.assert_not_called()

    def test_outside_window_push_exits_before_any_buffer_or_metrics_network(self):
        documents = {
            HERE / "config.json": CONFIG,
            HERE / "state.json": ledger(),
        }
        with patch.object(
            picks, "read_json",
            side_effect=lambda path: copy.deepcopy(documents[path]),
        ), patch.object(picks, "datetime", wraps=datetime) as clock, \
             patch.dict(os.environ, {"X_TRIGGER_EVENT": "push"}, clear=True), \
             patch.object(picks, "read_page_view_metrics") as metrics, \
             patch.object(picks, "choose_x_channel") as choose, \
             patch.object(picks, "list_existing") as existing, \
             patch.object(picks, "queue_one") as queue:
            clock.now.return_value = datetime.fromisoformat("2026-11-05T08:30:00+09:00")
            self.assertEqual(picks.main(), 0)
            metrics.assert_not_called()
            choose.assert_not_called()
            existing.assert_not_called()
            queue.assert_not_called()

    def test_article_post_removes_legacy_in_story_disclaimer(self):
        raw = article("legacy-fiction-label", age_hours=4)
        raw["title"] = "架空の店が客の節約を祝う、会計だけ10万円に"
        raw["summary"] = (
            "架空の店舗で創作の制度が始まった。客は割引を増やすため品物を買い続け、"
            "値引き欄だけを見て大喜びした。"
        )
        before = copy.deepcopy(raw)
        rendered = picks.publication_text(raw)
        self.assertNotIn("架空の", rendered)
        self.assertNotIn("創作の", rendered)
        self.assertIn("会計だけ10万円に", rendered)
        self.assertIn("値引き欄", rendered)
        self.assertEqual(before, raw)

    def test_article_post_keeps_headline_and_canonical_url_without_boilerplate(self):
        a = article("story-a", title="家計簿の利息を銀行員に見せた夫婦")
        msg = picks.publication_text(a)
        self.assertTrue(msg.startswith("【未発生新聞】"))
        for label in ("創作", "架空", "フィクション"):
            self.assertNotIn(label, msg)
        self.assertIn("家計簿", msg)
        self.assertIn("https://veripsa.com/articles/story-a/?utm_source=x&utm_medium=social&utm_campaign=article_auto", msg)
        self.assertIn("utm_source=x", msg)
        self.assertIn("utm_campaign=article_auto", msg)
        self.assertNotIn("AI生成", msg)
        self.assertNotIn("大人気", msg)
        self.assertLessEqual(picks.x_weighted_length(msg), picks.X_TEXT_LIMIT)
        self.assertEqual(picks.article_slug_in_text(msg), {"story-a"})
        self.assertEqual(
            picks.article_slug_in_text("少し話題に https://veripsa.com/articles/story-a/?utm_source=x"),
            {"story-a"}
        )
        tagged = article("story-tags", title="タグを共通語彙として使う記事")
        tagged["tags"] = ["AI", "働き方", "3つ目"]
        tagged_msg = picks.publication_text(tagged)
        self.assertIn("\n\n#AI #働き方\n\n", tagged_msg)
        self.assertNotIn("#3つ目", tagged_msg)
        self.assertEqual(picks.article_hashtags(tagged), ["#AI", "#働き方"])
        tagged["tags"] = ["AI", "AI", "bad tag", "#bad", 3]
        self.assertEqual(picks.article_hashtags(tagged), ["#AI"])
        followup = article("followup")
        followup["followup_of"] = "previous-story"
        self.assertTrue(picks.publication_text(followup).startswith("【続報｜未発生新聞】"))
        with self.assertRaises(ValueError):
            picks.publication_text(article("../unsafe"))

    def test_article_summary_adds_a_short_reader_teaser_within_budget(self):
        a = article("story-teaser", title="倍速視聴で内容が分からなくなった男性")
        a["summary"] = "時間を節約したはずが、登場人物を確認する解説動画を本編より長く見ることになった。"
        a["tags"] = ["タイパ", "ドラマ"]
        msg = picks.publication_text(a)
        self.assertIn("時間を節約したはずが", msg)
        self.assertIn("#タイパ #ドラマ", msg)
        self.assertIn("utm_campaign=article_auto", msg)
        self.assertLessEqual(picks.x_weighted_length(msg), picks.X_TEXT_LIMIT)

    def test_summary_never_breaks_required_tags_or_tracked_url(self):
        a = article("story-summary-budget", title="見出し" * 30)
        a["summary"] = "要約" * 200
        a["tags"] = ["働き方", "メディア"]
        msg = picks.publication_text(a)
        self.assertIn("#働き方 #メディア", msg)
        self.assertTrue(msg.endswith(
            "https://veripsa.com/articles/story-summary-budget/"
            + picks.TRACKING_QUERY
        ))
        self.assertLessEqual(picks.x_weighted_length(msg), picks.X_TEXT_LIMIT)

    def test_long_slug_preserves_the_link_and_topics_within_x_budget(self):
        tagged = article("story-" + "a" * 200, title="見出し" * 40)
        tagged["tags"] = ["長" * 12, "別" * 12, "AI"]
        msg = picks.publication_text(tagged)
        self.assertIn("…", msg)
        self.assertIn("#" + "長" * 12 + " #" + "別" * 12, msg)
        self.assertNotIn("#AI", msg)
        self.assertTrue(msg.endswith(
            "https://veripsa.com/articles/" + tagged["slug"] + "/"
            + picks.TRACKING_QUERY
        ))
        self.assertGreater(len(msg), 220)
        self.assertLessEqual(picks.x_weighted_length(msg), picks.X_TEXT_LIMIT)

    def test_every_published_article_uses_the_shared_topic_vocabulary(self):
        articles = json.loads((picks.ROOT / "content/articles.json").read_text(encoding="utf-8"))
        for item in articles:
            with self.subTest(slug=item["slug"]):
                msg = picks.publication_text(item)
                self.assertIn("\n\n" + " ".join("#" + tag for tag in item["tags"][:2]) + "\n\n", msg)
                self.assertLessEqual(picks.x_weighted_length(msg), picks.X_TEXT_LIMIT)

    def test_rank_uses_recent_pv_then_today_then_publication_time(self):
        old, newer, busy_today, top = (article("old", 72), article("newer", 24),
                                     article("today", 48), article("top", 96))
        counts = {
            "old": {**stats(10, 1), "readers": 9000, "likes": 1000, "pageViews": 99999},
            "newer": stats(10, 1), "today": stats(10, 2), "top": stats(11, 0),
        }
        rows = picks.rank([old, newer, busy_today, top], counts, ledger(), set(), CONFIG)
        self.assertEqual([row[0]["slug"] for row in rows], ["top", "today", "newer", "old"])
        # A real PV count at the floor qualifies without an invented reaction requirement.
        self.assertEqual(len(picks.rank([old], {"old": stats(8, 0)}, ledger(), set(), CONFIG)), 1)
        self.assertEqual(picks.rank([old], {"old": stats(7, 0)}, ledger(), set(), CONFIG), [])
        self.assertEqual(picks.rank([old], counts, ledger(), {"old"}, CONFIG), [])
        # Compare actual instants, not ISO strings with different timezone offsets.
        newer["published_at"] = (NOW - timedelta(hours=23)).astimezone(timezone.utc).isoformat()
        old["published_at"] = (NOW - timedelta(hours=24)).isoformat()
        rows = picks.rank([old, newer], counts, ledger(), set(), CONFIG)
        self.assertEqual(rows[0][0]["slug"], "newer")

    def test_missing_or_invalid_pv_never_falls_back_to_browser_reactions(self):
        a = article("story")
        bad = [{}, {"readers": 999, "likes": 999}, stats(8, 9)]
        for field in picks.METRIC_FIELDS:
            for value in (None, True, -1, 8.0, 2**53):
                bad.append({**stats(), field: value})
        for counts in bad:
            with self.subTest(counts=counts):
                self.assertEqual(picks.rank([a], {"story": counts}, ledger(), set(), CONFIG), [])
                payload = pv_payload(["story"])
                payload["articles"]["story"] = counts
                with self.assertRaises(ValueError):
                    picks.read_page_view_metrics(["story"], NOW,
                        opener=lambda *_args, **_kw: io.StringIO(json.dumps(payload)))
        self.assertEqual(picks.rank([a], {}, ledger(), set(), CONFIG), [])

    def test_read_every_candidate_in_get_batches_without_snapshot_limit(self):
        slugs = [f"story-{n}" for n in range(105)]
        requests = []
        def opener(request, timeout):
            self.assertEqual(request.get_method(), "GET")
            self.assertIsNone(request.data)
            self.assertEqual(timeout, 20)
            self.assertEqual(request.get_header("Cache-control"), "no-cache")
            batch = parse_qs(urlparse(request.full_url).query)["articles"][0].split(",")
            requests.append(batch)
            payload = pv_payload(batch)
            if slugs[-1] in batch:
                payload["articles"][slugs[-1]] = stats(100, 10)
            return io.StringIO(json.dumps(payload))
        metrics, window = picks.read_page_view_metrics(slugs + slugs[:2], NOW, opener)
        self.assertEqual([len(batch) for batch in requests], [20, 20, 20, 20, 20, 5])
        self.assertEqual([slug for batch in requests for slug in batch], slugs)
        self.assertEqual(len(metrics), 105)
        self.assertEqual(window["unit"], "page-view")
        self.assertTrue(window["complete"])
        rows = picks.rank([article(slug) for slug in slugs], metrics, ledger(), set(), CONFIG)
        self.assertEqual(rows[0][0]["slug"], slugs[-1])

    def test_partial_tracking_window_retains_actual_coverage(self):
        started = NOW - timedelta(days=1)
        payload = pv_payload(["story"], started_at=started)
        metrics, window = picks.read_page_view_metrics(["story"], NOW,
            opener=lambda *_args, **_kw: io.StringIO(json.dumps(payload)))
        self.assertEqual(metrics["story"], stats())
        self.assertFalse(window["complete"])
        self.assertEqual(window["measured_days"], 2)
        self.assertEqual(window["started_at_utc"], started.astimezone(timezone.utc).isoformat())
        # Seven measured dates still aren't complete if collection started after midnight.
        payload = pv_payload(["story"], started_at=NOW - timedelta(days=6))
        window = picks.page_view_window(payload, NOW)
        self.assertEqual(window["measured_days"], 7)
        self.assertFalse(window["complete"])

    def test_invalid_or_stale_tracking_window_fails_closed(self):
        changes = (
            ("pageViewTracking", "unit", "anonymous-browser"),
            ("pageViewTracking", "timezone", "UTC"),
            ("pageViewTracking", "startedAtUtc", None),
            ("pageViewTracking", "startedAtUtc", NOW.replace(tzinfo=None).isoformat()),
            ("pageViewTracking", "startedAtUtc", (NOW + timedelta(hours=1)).isoformat()),
            ("today", "dateJst", "2026-11-04"),
            ("last7Days", "startDateJst", "2026-10-29"),
            ("last7Days", "endDateJst", "2026-11-04"),
            ("last7Days", "pageViews", None),
            ("last7Days", "measuredDays", 2),
            ("last7Days", "measuredDays", True),
            ("last7Days", "complete", False),
            ("last7Days", "complete", "true"),
        )
        for section, key, value in changes:
            with self.subTest(section=section, key=key, value=value):
                payload = pv_payload(["story"])
                payload[section][key] = value
                with self.assertRaises(ValueError):
                    picks.read_page_view_metrics(["story"], NOW,
                        opener=lambda *_args, **_kw: io.StringIO(json.dumps(payload)))

    def test_missing_batch_rows_and_changed_tracking_never_rank_partial_candidates(self):
        slugs = [f"story-{n}" for n in range(21)]
        for failure in ("missing-row", "different-start", "invalid-json"):
            with self.subTest(failure=failure):
                responses = [pv_payload(slugs[:20]), pv_payload(slugs[20:])]
                if failure == "missing-row":
                    responses[1]["articles"] = {}
                elif failure == "different-start":
                    responses[1] = pv_payload(slugs[20:], started_at=NOW - timedelta(days=11))
                def opener(*_args, **_kw):
                    response = responses.pop(0)
                    if failure == "invalid-json" and not responses:
                        return io.StringIO("{broken")
                    return io.StringIO(json.dumps(response))
                with self.assertRaises(ValueError):
                    picks.read_page_view_metrics(slugs, NOW, opener)

    def test_young_articles_and_2031_dates_cannot_game_measured_pv_promotion(self):
        a = article("young", age_hours=2)
        b = article("old-story", age_hours=60 * 24)
        c = article("real-recent", age_hours=15)
        good = picks.eligible_articles([a, b, c], NOW, CONFIG)
        self.assertEqual([p["slug"] for p in good], ["real-recent"])

    def test_fresh_lane_promotes_before_pv_and_prefers_followup_then_newest(self):
        too_new = article("too-new", age_hours=0)
        fresh_old = article("fresh-old", age_hours=10)
        fresh_new = article("fresh-new", age_hours=4)
        followup = article("fresh-followup", age_hours=8)
        followup["followup_of"] = "earlier"
        eligible = picks.eligible_fresh_articles(
            [too_new, fresh_old, fresh_new, followup], NOW, CONFIG)
        self.assertEqual(
            {item["slug"] for item in eligible},
            {"fresh-old", "fresh-new", "fresh-followup"},
        )
        ranked = picks.rank_fresh(
            [fresh_old, fresh_new, followup], ledger(), set(), CONFIG, NOW)
        self.assertEqual(ranked[0]["slug"], "fresh-followup")
        self.assertEqual(ranked[1]["slug"], "fresh-new")

    def test_fresh_lane_prefers_unused_primary_topic_within_top_candidates(self):
        used = article("used-ai", age_hours=8)
        used["tags"] = ["AI", "働き方"]
        newest_ai = article("newest-ai", age_hours=3)
        newest_ai["tags"] = ["AI", "研究"]
        diverse = article("diverse-sns", age_hours=4)
        diverse["tags"] = ["SNS", "カフェ"]
        state = ledger(**{
            "used-ai": {
                "buffer_id": "buf-used",
                "queued_at_utc": (NOW - timedelta(hours=3)).isoformat(),
            }
        })
        selected, evidence, _ = picks.decide(
            [used, newest_ai, diverse], {}, state, [], CONFIG, NOW
        )
        self.assertEqual(selected["slug"], "diverse-sns")
        self.assertIsNone(evidence)

    def test_followup_keeps_priority_even_when_topic_repeats(self):
        used = article("used-ai", age_hours=8)
        used["tags"] = ["AI", "働き方"]
        followup = article("ai-followup", age_hours=5)
        followup["tags"] = ["AI", "研究"]
        followup["followup_of"] = "earlier-ai"
        diverse = article("diverse-sns", age_hours=3)
        diverse["tags"] = ["SNS", "カフェ"]
        state = ledger(**{
            "used-ai": {
                "buffer_id": "buf-used",
                "queued_at_utc": (NOW - timedelta(hours=3)).isoformat(),
            }
        })
        selected, _, _ = picks.decide(
            [used, followup, diverse], {}, state, [], CONFIG, NOW
        )
        self.assertEqual(selected["slug"], "ai-followup")

    def test_measured_lane_diversifies_only_within_top_three(self):
        used = article("used-ai", age_hours=40)
        used["tags"] = ["AI", "働き方"]
        top_ai = article("top-ai", age_hours=36)
        top_ai["tags"] = ["AI", "研究"]
        second_ai = article("second-ai", age_hours=38)
        second_ai["tags"] = ["AI", "仕事"]
        diverse = article("diverse-market", age_hours=42)
        diverse["tags"] = ["お金", "生活"]
        low = article("low-sns", age_hours=44)
        low["tags"] = ["SNS", "文化"]
        state = ledger(**{
            "used-ai": {
                "buffer_id": "buf-used",
                "queued_at_utc": (NOW - timedelta(hours=3)).isoformat(),
            }
        })
        counts = {
            "top-ai": stats(30, 2),
            "second-ai": stats(29, 2),
            "diverse-market": stats(28, 1),
            "low-sns": stats(27, 1),
            "used-ai": stats(20, 0),
        }
        selected, evidence, _ = picks.decide(
            [used, top_ai, second_ai, diverse, low], counts, state, [], CONFIG, NOW
        )
        self.assertEqual(selected["slug"], "diverse-market")
        self.assertEqual(evidence["last7DaysPageViews"], 28)

    def test_measured_lane_does_not_promote_lower_followup_over_diverse_candidate(self):
        used = article("used-ai", age_hours=40)
        used["tags"] = ["AI", "働き方"]
        top_ai = article("top-ai", age_hours=36)
        top_ai["tags"] = ["AI", "研究"]
        lower_followup = article("lower-followup", age_hours=38)
        lower_followup["tags"] = ["AI", "続報"]
        lower_followup["followup_of"] = "older-ai"
        diverse = article("diverse-money", age_hours=42)
        diverse["tags"] = ["お金", "生活"]
        state = ledger(**{
            "used-ai": {
                "buffer_id": "buf-used",
                "queued_at_utc": (NOW - timedelta(hours=3)).isoformat(),
            }
        })
        counts = {
            "used-ai": stats(20, 0),
            "top-ai": stats(30, 2),
            "lower-followup": stats(29, 1),
            "diverse-money": stats(28, 1),
        }
        selected, _, _ = picks.decide(
            [used, top_ai, lower_followup, diverse], counts, state, [], CONFIG, NOW
        )
        self.assertEqual(selected["slug"], "diverse-money")

    def test_primary_topic_falls_back_to_category_without_tags(self):
        tagged = {"tags": ["AI", "働き方"], "category": "技術"}
        category_only = {"tags": [], "category": "社会"}
        self.assertEqual(picks.primary_topic(tagged), "tag:ai")
        self.assertEqual(picks.primary_topic(category_only), "category:社会")
        self.assertEqual(picks.primary_topic({}), "")

    def test_first_read_gate_blocks_opaque_or_unreviewed_articles_in_both_lanes(self):
        clear = article("clear", age_hours=4)
        self.assertTrue(picks.passes_x_first_read_gate(clear))
        unclear = article("opaque", age_hours=4, title="この件、ついに大きな動き　詳細は記事に")
        unclear["summary"] = "これを受け、いよいよ注目を集めた出来事に関する説明が始まったという話が広まった。"
        missing = article("missing", age_hours=4)
        missing.pop("summary")
        held = article("held", age_hours=4)
        held["x_promotion_skip"] = True
        too_brief = article("brief", age_hours=4, title="何かが起きた")
        for item in (unclear, missing, held, too_brief):
            with self.subTest(slug=item["slug"]):
                self.assertFalse(picks.passes_x_first_read_gate(item))
                self.assertEqual(picks.eligible_fresh_articles([item], NOW, CONFIG), [])
                mature = {**item, "published_at": (NOW - timedelta(hours=40)).isoformat()}
                self.assertEqual(picks.eligible_articles([mature], NOW, CONFIG), [])
        selected, _, _ = picks.decide(
            [unclear, held, clear], {}, ledger(), [], CONFIG, NOW
        )
        self.assertEqual(selected["slug"], "clear")
        self.assertIn("https://veripsa.com/articles/clear/", picks.publication_text(clear))

    def test_published_articles_pass_basic_x_first_read_structure(self):
        articles = json.loads((picks.ROOT / "content/articles.json").read_text(encoding="utf-8"))
        for item in articles:
            with self.subTest(slug=item["slug"]):
                self.assertTrue(picks.passes_x_first_read_gate(item))

    def test_crisis_articles_never_enter_automatic_article_promotion(self):
        crisis = article("quake", age_hours=4, title="地震後の避難所で起きた話")
        crisis["summary"] = "被災者の避難が続いている。"
        crisis["source"]["topic"] = "大規模地震"
        self.assertTrue(picks.is_crisis_article(crisis))
        self.assertEqual(picks.eligible_fresh_articles([crisis], NOW, CONFIG), [])
        self.assertEqual(picks.eligible_articles([crisis], NOW, CONFIG), [])

    def test_pinned_channel_and_limits_fail_closed(self):
        picks.validate_config(CONFIG)
        for field, value in (("channel_id", ""),
                             ("max_new_per_run", 2),
                             ("max_per_jst_day", 8),
                             ("minimum_interval_hours", 1),
                             ("minimum_interval_hours", 3),
                             ("fresh_minimum_age_hours", 0),
                             ("fresh_maximum_age_hours", 25),
                             ("min_page_views", 0),
                             ("enabled", "true")):
            cfg = {**CONFIG, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                picks.validate_config(cfg)

    def test_recover_scheduled_or_sent_before_queue_and_permanent_slug_dedupe(self):
        state = ledger()
        a = article("story-a")
        existing = [
            {"id": "remote-1", "status": "scheduled",
             "text": "創作記事 https://veripsa.com/articles/story-a/?utm_source=x"},
            {"id": "remote-2", "status": "sent",
             "text": "https://veripsa.com/articles/story-b/"},
        ]
        recovered = picks.recover_from_buffer(state, existing, NOW, {"story-a", "story-b"})
        self.assertEqual(set(recovered), {"story-a", "story-b"})
        self.assertEqual(state["queued"]["story-a"]["buffer_id"], "remote-1")
        self.assertEqual(state["queued"]["story-b"]["buffer_id"], "remote-2")
        self.assertEqual(
            state["queued"]["story-a"]["slot_jst"],
            "2026-11-05T11:05:00+09:00",
        )
        self.assertEqual(picks.recover_from_buffer(state, existing, NOW, {"story-a", "story-b"}), [])
        self.assertEqual(picks.rank([a], {"story-a": stats()},
                                    state, set(), CONFIG), [])
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "state.json"
            picks.save_state(state, target)
            self.assertEqual(picks.read_json(target), state)

    def test_recovery_uses_provider_time_and_never_steals_current_slot(self):
        now = datetime.fromisoformat("2026-11-05T19:20:00+09:00")
        state = ledger()
        existing = [{
            "id": "remote-old",
            "status": "sent",
            "text": "https://veripsa.com/articles/story-old/",
            "sentAt": "2026-11-05T15:20:00+09:00",
        }]
        recovered = picks.recover_from_buffer(
            state, existing, now, {"story-old"}
        )
        self.assertEqual(recovered, ["story-old"])
        entry = state["queued"]["story-old"]
        self.assertEqual(entry["slot_jst"], "2026-11-05T15:05:00+09:00")
        self.assertEqual(
            entry["recovered_provider_time_utc"], "2026-11-05T06:20:00+00:00"
        )
        self.assertFalse(picks.slot_already_used(state, now))

    def test_unsettled_provider_article_is_recovered_and_never_auto_retried(self):
        now = datetime.fromisoformat("2026-11-05T19:20:00+09:00")
        failed = article("failed-story", age_hours=4)
        existing = [{
            "id": "buf-failed",
            "status": "failed",
            "text": picks.publication_text(failed),
        }]
        state = ledger()
        selected, _, _ = picks.decide(
            [failed], {}, state, existing, CONFIG, now
        )
        self.assertIsNone(selected)
        self.assertIn("failed-story", state["queued"])
        self.assertEqual(
            state["queued"]["failed-story"]["buffer_status_when_recovered"],
            "failed",
        )
        self.assertIsNone(picks.entry_promotion_slot(state["queued"]["failed-story"]))
        self.assertFalse(picks.slot_already_used(state, now))

    def test_recovery_without_provider_time_dedupes_but_does_not_consume_slot(self):
        now = datetime.fromisoformat("2026-11-05T19:20:00+09:00")
        state = ledger()
        existing = [{
            "id": "remote-unknown-time",
            "status": "sent",
            "text": "https://veripsa.com/articles/story-unknown/",
        }]
        picks.recover_from_buffer(
            state, existing, now, {"story-unknown"}
        )
        entry = state["queued"]["story-unknown"]
        self.assertNotIn("slot_jst", entry)
        self.assertIsNone(entry["recovered_provider_time_utc"])
        self.assertIsNone(picks.entry_promotion_slot(entry))
        self.assertFalse(picks.slot_already_used(state, now))
        self.assertEqual(
            picks.rank(
                [article("story-unknown")],
                {"story-unknown": stats()},
                state, set(), CONFIG,
            ),
            [],
        )

    def test_previous_post_persists_when_buffer_no_longer_lists_sent_history(self):
        old = ledger(**{"story-a": recorded(hours=500)})
        offer = picks.rank([article("story-a")], {"story-a": stats()}, old, set(), CONFIG)
        self.assertEqual(offer, [])

    def test_logical_two_hour_slots_survive_action_delay_and_block_same_slot(self):
        a = article("new-story", age_hours=36)
        counts = {"new-story": stats()}
        remote = [
            {"id": "short-1", "status": "scheduled", "text": "昔話の短文"},
            {"id": "short-2", "status": "scheduled", "text": "別の短文"},
        ]
        late_11 = datetime.fromisoformat("2026-11-05T11:36:00+09:00")
        self.assertEqual(
            picks.promotion_slot(datetime.fromisoformat("2026-11-05T09:36:00+09:00")),
            datetime.fromisoformat("2026-11-05T09:05:00+09:00"),
        )
        self.assertEqual(
            picks.promotion_slot(late_11),
            datetime.fromisoformat("2026-11-05T11:05:00+09:00"),
        )
        self.assertIsNone(
            picks.promotion_slot(datetime.fromisoformat("2026-11-05T09:04:59+09:00"))
        )

        # A delayed 09:05 run accepted at 09:36 must not consume the 11:05 slot.
        previous_09 = ledger(**{
            "previous-story": {
                "buffer_id": "buf-prev",
                "queued_at_utc": "2026-11-05T00:36:00+00:00",
                "slot_jst": "2026-11-05T09:05:00+09:00",
            }
        })
        item, evidence, queued = picks.decide(
            [a], counts, previous_09, remote, CONFIG, late_11
        )
        self.assertEqual(item["slug"], "new-story")
        self.assertEqual(queued, 2)
        self.assertEqual(evidence, counts["new-story"])

        # A second trigger inside the same logical 11:05 slot remains blocked.
        same_11 = ledger(**{
            "previous-story": {
                "buffer_id": "buf-prev",
                "queued_at_utc": "2026-11-05T02:20:00+00:00",
                "slot_jst": "2026-11-05T11:05:00+09:00",
            }
        })
        item, _, _ = picks.decide([a], counts, same_11, remote, CONFIG, late_11)
        self.assertIsNone(item)

        # Pre-migration receipts without slot_jst infer their logical slot.
        legacy_09 = {
            "buffer_id": "buf-legacy",
            "queued_at_utc": "2026-11-05T00:36:00+00:00",
        }
        self.assertEqual(
            picks.entry_promotion_slot(legacy_09),
            datetime.fromisoformat("2026-11-05T09:05:00+09:00"),
        )
        item, _, _ = picks.decide(
            [a], counts, ledger(**{"legacy-story": legacy_09}), remote, CONFIG, late_11
        )
        self.assertEqual(item["slug"], "new-story")

        # A heavily delayed previous-day receipt after midnight belongs to the
        # previous day's last logical slot, not today's seven-post quota.
        legacy_after_midnight = {
            "buffer_id": "buf-late",
            "queued_at_utc": "2026-11-04T17:54:00+00:00",
        }
        self.assertEqual(
            picks.entry_promotion_slot(legacy_after_midnight),
            datetime.fromisoformat("2026-11-04T21:05:00+09:00"),
        )
        self.assertEqual(
            picks.promotions_today(
                ledger(**{"previous-day-late": legacy_after_midnight}), NOW
            ),
            0,
        )

        same_day = {
            f"story-{i}": {
                "buffer_id": f"buf-{i}",
                "queued_at_utc": datetime.fromisoformat(
                    f"2026-11-05T{hour:02d}:30:00+09:00"
                ).isoformat(),
                "slot_jst": f"2026-11-05T{hour:02d}:05:00+09:00",
            }
            for i, hour in enumerate(picks.PROMOTION_SLOT_HOURS)
        }
        self.assertEqual(picks.promotions_today(ledger(**same_day), NOW), 7)
        item, _, _ = picks.decide([a], counts, ledger(**same_day),
                                  remote, CONFIG, NOW)
        self.assertIsNone(item)

    def test_unrelated_x_posts_do_not_consume_article_logical_slots(self):
        fresh = article("fresh-cap", age_hours=4)
        seven_other_posts = [
            {"id": f"legacy-{i}", "status": "sent", "text": f"retired post {i}",
             "sentAt": f"2026-11-05T{9 + i:02d}:00:00+09:00"}
            for i in range(7)
        ]
        selected, _, _ = picks.decide(
            [fresh], {}, ledger(), seven_other_posts, CONFIG, NOW
        )
        self.assertEqual(selected["slug"], "fresh-cap")

        # The article lane itself is still capped at its seven logical slots.
        same_day = {
            f"article-{i}": {
                "buffer_id": f"article-buf-{i}",
                "queued_at_utc": datetime.fromisoformat(
                    f"2026-11-05T{hour:02d}:20:00+09:00"
                ).isoformat(),
                "slot_jst": f"2026-11-05T{hour:02d}:05:00+09:00",
            }
            for i, hour in enumerate(picks.PROMOTION_SLOT_HOURS)
        }
        selected, _, _ = picks.decide(
            [fresh], {}, ledger(**same_day), seven_other_posts, CONFIG, NOW
        )
        self.assertIsNone(selected)

    def test_third_article_no_longer_requires_a_creative_reservation(self):
        fresh = article("fresh", age_hours=4)
        state = ledger(
            **{
                "one": {"buffer_id": "a1", "queued_at_utc": (NOW - timedelta(hours=10)).isoformat()},
                "two": {"buffer_id": "a2", "queued_at_utc": (NOW - timedelta(hours=7)).isoformat()},
            }
        )
        remote = [{"id": "article-existing", "status": "scheduled", "text": "記事予約"}]
        item, _, _ = picks.decide([fresh], {}, state, remote, CONFIG, NOW)
        self.assertEqual(item["slug"], "fresh")

    def test_main_queues_article_without_enabling_legacy_experiments(self):
        remote = []
        documents = {
            HERE / "config.json": CONFIG,
            HERE / "state.json": ledger(),
            HERE.parent / "experiments/state.json": ledger(),
        }
        original = copy.deepcopy(documents)
        window = picks.page_view_window(pv_payload(["new-story"]), NOW)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "content").mkdir()
            (root / "content/articles.json").write_text(json.dumps([article("new-story")]))
            with patch.object(picks, "ROOT", root), \
                 patch.object(picks, "read_json", side_effect=lambda path: copy.deepcopy(documents[path])), \
                 patch.object(picks, "datetime", wraps=datetime) as clock, \
                 patch.dict(os.environ, {"BUFFER_API_KEY": "test-only"}), \
                 patch.object(picks, "read_page_view_metrics", return_value=({"new-story": stats()}, window)), \
                 patch.object(picks, "choose_x_channel", return_value=("org", CONFIG["channel_id"])), \
                 patch.object(picks, "list_existing", return_value=remote), \
                 patch.object(picks, "save_state") as save, \
                 patch.object(picks, "queue_one", return_value="article-confirmed") as queue:
                clock.now.return_value = NOW
                self.assertEqual(picks.main(), 0)
                queue.assert_called_once()
                self.assertEqual(queue.call_args.args[1], CONFIG["channel_id"])
                self.assertEqual(queue.call_args.args[2]["kind"], "article")
                self.assertEqual(queue.call_args.args[2]["id"], "article-new-story")
                self.assertEqual(queue.call_args.kwargs["mode"], "shareNow")
                self.assertIn("https://veripsa.com/articles/new-story/", queue.call_args.args[2]["text"])
                self.assertIn("utm_campaign=article_auto", queue.call_args.args[2]["text"])
                receipt = save.call_args.args[0]["queued"]["new-story"]
                self.assertEqual(receipt["buffer_id"], "article-confirmed")
                self.assertEqual(receipt["source"], "verified_article_page_views")
                self.assertEqual(receipt["submission_mode"], "shareNow")
                self.assertEqual(receipt["slot_jst"], "2026-11-05T11:05:00+09:00")
                self.assertEqual(receipt["metrics_at_queue"], {
                    **window, "last_7_days_page_views": 11, "today_page_views": 1})
        self.assertEqual(documents, original)

    def test_queue_fullness_does_not_block_share_now_article_lane(self):
        candidate = article("measured-story", age_hours=36)
        remote = [{"id":f"creative-{i}", "status":"scheduled",
                   "text":f"異なる短文の予約です{i}"} for i in range(10)]
        selected, evidence, count = picks.decide(
            [candidate], {"measured-story":stats()}, ledger(), remote,
            CONFIG, NOW)
        self.assertEqual(count,10)
        self.assertEqual(selected["slug"],"measured-story")
        self.assertEqual(evidence["last7DaysPageViews"], 11)

    def test_main_can_queue_fresh_article_without_pv_threshold(self):
        remote = []
        fresh = article("fresh-story", age_hours=4)
        documents = {
            HERE / "config.json": CONFIG,
            HERE / "state.json": ledger(),
            HERE.parent / "experiments/state.json": ledger(),
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "content").mkdir()
            (root / "content/articles.json").write_text(json.dumps([fresh]))
            with patch.object(picks, "ROOT", root), \
                 patch.object(picks, "read_json", side_effect=lambda path: copy.deepcopy(documents[path])), \
                 patch.object(picks, "datetime", wraps=datetime) as clock, \
                 patch.dict(os.environ, {"BUFFER_API_KEY": "test-only"}), \
                 patch.object(picks, "read_page_view_metrics") as pv_read, \
                 patch.object(picks, "choose_x_channel", return_value=("org", CONFIG["channel_id"])), \
                 patch.object(picks, "list_existing", return_value=remote), \
                 patch.object(picks, "save_state") as save, \
                 patch.object(picks, "queue_one", return_value="article-fresh") as queue:
                clock.now.return_value = NOW
                self.assertEqual(picks.main(), 0)
                pv_read.assert_not_called()
                queue.assert_called_once()
                self.assertEqual(queue.call_args.args[2]["kind"], "article")
                self.assertEqual(queue.call_args.args[2]["id"], "article-fresh-story")
                self.assertEqual(queue.call_args.kwargs["mode"], "shareNow")
                receipt = save.call_args.args[0]["queued"]["fresh-story"]
                self.assertEqual(receipt["source"], "fresh_article")
                self.assertEqual(receipt["submission_mode"], "shareNow")
                self.assertEqual(receipt["slot_jst"], "2026-11-05T11:05:00+09:00")
                self.assertNotIn("metrics_at_queue", receipt)

    def test_fresh_article_ignores_mature_pv_outage(self):
        """An older article must not make the fresh lane depend on the PV API."""
        fresh = article("fresh-mixed", age_hours=4)
        mature = article("mature-mixed", age_hours=36)
        documents = {HERE / "config.json": CONFIG, HERE / "state.json": ledger()}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "content").mkdir()
            (root / "content/articles.json").write_text(
                json.dumps([mature, fresh]), encoding="utf-8"
            )
            with patch.object(picks, "ROOT", root), \
                 patch.object(picks, "read_json",
                              side_effect=lambda path: copy.deepcopy(documents[path])), \
                 patch.object(picks, "datetime", wraps=datetime) as clock, \
                 patch.dict(os.environ, {"BUFFER_API_KEY": "test-only"}, clear=True), \
                 patch.object(picks, "read_page_view_metrics",
                              side_effect=RuntimeError("PV unavailable")) as pv_read, \
                 patch.object(picks, "choose_x_channel",
                              return_value=("org", CONFIG["channel_id"])), \
                 patch.object(picks, "list_existing", return_value=[]), \
                 patch.object(picks, "save_state") as save, \
                 patch.object(picks, "queue_one", return_value="fresh-receipt") as queue:
                clock.now.return_value = NOW
                self.assertEqual(picks.main(), 0)
                pv_read.assert_not_called()
                queue.assert_called_once()
                self.assertEqual(queue.call_args.kwargs["mode"], "shareNow")
                self.assertEqual(queue.call_args.args[2]["kind"], "article")
                self.assertIn("fresh-mixed", queue.call_args.args[2]["text"])
                receipt = save.call_args.args[0]["queued"]["fresh-mixed"]
                self.assertEqual(receipt["source"], "fresh_article")
                self.assertNotIn("metrics_at_queue", receipt)

    def test_mature_selection_still_requires_verified_pv_if_fresh_exhausted(self):
        """A fresh URL in permanent history may not be reused as a PV-free filler."""
        fresh = article("already-promoted", age_hours=4)
        mature = article("mature-only", age_hours=36)
        documents = {
            HERE / "config.json": CONFIG,
            HERE / "state.json": ledger(**{
                "already-promoted": recorded(buf="old-receipt", hours=7)
            }),
        }
        window = picks.page_view_window(pv_payload(["mature-only"]), NOW)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "content").mkdir()
            (root / "content/articles.json").write_text(
                json.dumps([fresh, mature]), encoding="utf-8"
            )
            with patch.object(picks, "ROOT", root), \
                 patch.object(picks, "read_json",
                              side_effect=lambda path: copy.deepcopy(documents[path])), \
                 patch.object(picks, "datetime", wraps=datetime) as clock, \
                 patch.dict(os.environ, {"BUFFER_API_KEY": "test-only"}, clear=True), \
                 patch.object(picks, "read_page_view_metrics",
                              return_value=({"mature-only": stats()}, window)) as pv_read, \
                 patch.object(picks, "choose_x_channel",
                              return_value=("org", CONFIG["channel_id"])), \
                 patch.object(picks, "list_existing", return_value=[]), \
                 patch.object(picks, "save_state") as save, \
                 patch.object(picks, "queue_one", return_value="mature-receipt") as queue:
                clock.now.return_value = NOW
                self.assertEqual(picks.main(), 0)
                pv_read.assert_called_once_with(["mature-only"], NOW)
                queue.assert_called_once()
                self.assertIn("mature-only", queue.call_args.args[2]["text"])
                receipt = save.call_args.args[0]["queued"]["mature-only"]
                self.assertEqual(receipt["source"], "verified_article_page_views")
                self.assertEqual(receipt["metrics_at_queue"]["last_7_days_page_views"], 11)
                self.assertEqual(
                    save.call_args.args[0]["queued"]["already-promoted"]["buffer_id"],
                    "old-receipt",
                )

    def test_mature_pv_failure_never_queues_without_fresh_article(self):
        """PV failure must remain fail-closed rather than create an unmeasured old post."""
        mature = article("mature-fail-closed", age_hours=36)
        documents = {HERE / "config.json": CONFIG, HERE / "state.json": ledger()}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "content").mkdir()
            (root / "content/articles.json").write_text(
                json.dumps([mature]), encoding="utf-8"
            )
            with patch.object(picks, "ROOT", root), \
                 patch.object(picks, "read_json",
                              side_effect=lambda path: copy.deepcopy(documents[path])), \
                 patch.object(picks, "datetime", wraps=datetime) as clock, \
                 patch.dict(os.environ, {"BUFFER_API_KEY": "test-only"}, clear=True), \
                 patch.object(picks, "read_page_view_metrics",
                              side_effect=RuntimeError("PV unavailable")) as pv_read, \
                 patch.object(picks, "choose_x_channel",
                              return_value=("org", CONFIG["channel_id"])), \
                 patch.object(picks, "list_existing", return_value=[]), \
                 patch.object(picks, "save_state"), \
                 patch.object(picks, "queue_one") as queue:
                clock.now.return_value = NOW
                with self.assertRaisesRegex(RuntimeError, "PV unavailable"):
                    picks.main()
                pv_read.assert_called_once()
                queue.assert_not_called()

    def test_missing_data_token_or_disabled_never_posts(self):
        with patch.object(picks, "read_json") as read, patch.object(picks, "queue_one") as queue:
            read.side_effect = [
                {**CONFIG, "enabled": False}, {"channel_id": "X-VERIPSA"}
            ]
            self.assertEqual(picks.main(), 0)
            queue.assert_not_called()
        with self.assertRaisesRegex(ValueError, "post ledger"):
            picks.validate_state({"schema_version": 1, "queued": {
                "slug": {"queued_at_utc": NOW.isoformat()}
            }})


if __name__ == "__main__":
    unittest.main()
