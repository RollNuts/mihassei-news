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
import struct
import subprocess
import zlib
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dispatch as picks
import article_comics as comics
import buffer_budget as budget
import publish
READ_DELIVERY_RETRY_TIME = picks.delivery_retry_time

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


def public_record(minutes=180):
    """Synthetic independent observation; never a Buffer receipt or real post."""
    return {
        "url": "https://x.com/Veripsa/status/123",
        "posted_at_utc": (NOW - timedelta(minutes=minutes)).astimezone(timezone.utc).isoformat(),
        "observed_at_utc": NOW.astimezone(timezone.utc).isoformat(),
    }


class ArticlePickTests(unittest.TestCase):
    def setUp(self):
        # Offline fixtures must not append their synthetic sends to Actions'
        # real run summary. Dedicated summary tests supply a temporary file.
        summary_env = patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""})
        summary_env.start()
        self.addCleanup(summary_env.stop)
        # Historical fixtures must not inherit a live provider cooldown committed
        # after their simulated date. The dedicated test reads its own real file.
        live_cooldown = patch.object(picks, "delivery_retry_time", return_value=None)
        live_cooldown.start()
        self.addCleanup(live_cooldown.stop)

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

    def test_public_post_observations_validate_without_inventing_provider_receipts(self):
        picks.validate_state(ledger())  # Existing schema-v1 states need no migration.
        state = {**ledger(), "public_posts": {"observed-story": public_record()}}
        picks.validate_state(state)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            picks.save_state(state, path)
            self.assertEqual(picks.read_json(path), state)
        self.assertEqual(state["queued"], {})
        invalid = (
            {"url": "https://x.com/another-account/status/123"},
            {"url": "https://x.com.evil.invalid/Veripsa/status/123"},
            {"url": "http://x.com/Veripsa/status/123"},
            {"url": "https://x.com/Veripsa/status/123?redirect=elsewhere"},
            {"url": "https://x.com/Veripsa/status/not-a-post"},
            {"posted_at_utc": "2026-11-05T10:00:00"},
            {"observed_at_utc": "2026-11-05T12:00:00+09:00"},
            {"posted_at_utc": "invalid"}, {"observed_at_utc": None},
            {"posted_at_utc": (NOW + timedelta(minutes=1)).astimezone(timezone.utc).isoformat()},
        )
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValueError):
                picks.validate_state({**ledger(), "public_posts": {
                    "observed-story": {**public_record(), **change},
                }})
        for public in (None, [], {"../unsafe": public_record()}, {"observed-story": None}):
            with self.subTest(public=public), self.assertRaises(ValueError):
                picks.validate_state({**ledger(), "public_posts": public})

    def test_independently_observed_slugs_are_excluded_from_both_ranks_without_spending_slots(self):
        fresh, mature = article("public-fresh", 4), article("public-mature", 36)
        available = article("not-yet-shared", 4)
        state = {**ledger(), "public_posts": {
            fresh["slug"]: public_record(180), mature["slug"]: public_record(240),
        }}
        before = copy.deepcopy(state)
        self.assertEqual(picks.rank([mature], {mature["slug"]: stats(99)}, state, set(), CONFIG), [])
        self.assertEqual([a["slug"] for a in picks.rank_fresh(
            [fresh, available], state, set(), CONFIG, NOW,
        )], [available["slug"]])
        self.assertEqual(picks.promotions_today(state, NOW), 0)
        self.assertFalse(picks.slot_already_used(state, NOW))
        self.assertEqual(picks.promoted_topics_today(state, [fresh, mature], NOW), set())
        selected, _, _ = picks.decide([fresh, mature, available], {}, state, [], CONFIG, NOW)
        self.assertEqual(selected["slug"], available["slug"])
        self.assertEqual(state, before)

    def test_public_send_spacing_uses_publication_time_not_observation_time(self):
        for minutes, held in ((119, True), (120, False), (121, False)):
            with self.subTest(minutes=minutes):
                state = {**ledger(), "public_posts": {"observed-story": public_record(minutes)}}
                self.assertEqual(picks.buffer_delivery_spacing_hold([], state, NOW, 2),
                                 "recent_verified_public_x_sent" if held else None)
                selected, _, _ = picks.decide(
                    [article("other-fresh", 4)], {}, state, [], CONFIG, NOW,
                )
                self.assertEqual(selected is None, held)
                self.assertEqual(picks.promotions_today(state, NOW), 0)

    def test_recent_public_send_stops_before_provider_connection_reads_or_writes(self):
        state = {**ledger(), "public_posts": {"observed-story": public_record(119)}}
        with patch.object(picks, "read_json", side_effect=[CONFIG, state]), \
             patch.object(picks, "datetime", wraps=datetime) as clock, \
             patch.dict(os.environ, {"X_TRIGGER_EVENT": "schedule", "BUFFER_API_KEY": "test-only"}, clear=True), \
             patch.object(picks, "connected_channel") as connection, \
             patch.object(picks, "list_existing") as read, patch.object(picks, "queue_one") as send, \
             patch.object(picks, "read_page_view_metrics") as metrics, patch.object(picks, "save_state") as save:
            clock.now.return_value = NOW
            self.assertEqual(picks.main(), 0)
            connection.assert_not_called()
            read.assert_not_called()
            send.assert_not_called()
            metrics.assert_not_called()
            save.assert_not_called()

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

    def test_closed_and_used_slots_do_not_spend_provider_requests(self):
        used = ledger()
        used["queued"]["already"] = {
            "buffer_id": "receipt", "queued_at_utc": NOW.astimezone(timezone.utc).isoformat(),
            "slot_jst": picks.promotion_slot(NOW).isoformat(),
        }
        cases = [(NOW, used), (NOW.replace(hour=3), ledger()), (NOW.replace(hour=23), ledger())]
        for now, state in cases:
            with self.subTest(now=now), patch.object(picks, "read_json", side_effect=[CONFIG, state]), \
                 patch.object(picks, "datetime", wraps=datetime) as clock, \
                 patch.dict(os.environ, {"X_TRIGGER_EVENT": "schedule"}, clear=True), \
                 patch.object(picks, "choose_x_channel") as choose, \
                 patch.object(picks, "list_existing") as read, patch.object(picks, "queue_one") as send:
                clock.now.return_value = now
                self.assertEqual(picks.main(), 0)
                choose.assert_not_called()
                read.assert_not_called()
                send.assert_not_called()

    def test_recorded_provider_backoff_blocks_reads_and_sends(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "delivery.json").write_text(json.dumps({
                "provider_retry_at_utc": (NOW + timedelta(hours=1)).isoformat(),
            }))
            with patch.object(picks, "HERE", root), \
                 patch.object(picks, "delivery_retry_time", side_effect=READ_DELIVERY_RETRY_TIME), \
                 patch.object(picks, "read_json", side_effect=[CONFIG, ledger()]), \
                 patch.object(picks, "datetime", wraps=datetime) as clock, \
                 patch.dict(os.environ, {"X_TRIGGER_EVENT": "schedule", "GITHUB_STEP_SUMMARY": str(root / "summary.md"), "BUFFER_API_KEY": "secret-not-in-summary"}, clear=True), \
                 patch.object(picks, "choose_x_channel") as choose, \
                 patch.object(picks, "list_existing") as read, \
                 patch.object(picks, "read_page_view_metrics") as metrics, \
                 patch.object(picks, "queue_one") as send:
                clock.now.return_value = NOW
                # Recorded 429 backoff is a successful no-op, not a fresh failure.
                self.assertEqual(picks.main(), 0)
                choose.assert_not_called()
                read.assert_not_called()
                metrics.assert_not_called()
                send.assert_not_called()
            summary = (root / "summary.md").read_text()
            self.assertIn("プロバイダ待機", summary)
            self.assertIn("未送信・provider通信なし", summary)
            self.assertIn("2026-11-05 13:00:00 JST", summary)
            self.assertIn("2026-11-05 04:00:00 UTC", summary)
            self.assertIn("手動投稿も行っていません", summary)
            self.assertNotIn("secret-not-in-summary", summary)

    def test_summary_never_copies_unvalidated_provider_or_slug_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.md"
            path.write_text("Existing step summary\n", encoding="utf-8")
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(path)}, clear=True):
                picks.report_outcome("accepted", slug="../unsafe\n# injected",
                                     provider_status="secret raw error\n# injected")
            summary = path.read_text()
            self.assertTrue(summary.startswith("Existing step summary\n"))
            self.assertIn("X実公開は未確認", summary)
            self.assertIn("Buffer応答の状態：`未確認`", summary)
            self.assertNotIn("unsafe", summary)
            self.assertNotIn("secret raw error", summary)
            self.assertNotIn("# injected", summary)

    def test_verified_channel_cache_saves_discovery_but_keeps_account_pin(self):
        state = ledger()
        with patch.object(picks, "choose_x_channel", return_value=("org", CONFIG["channel_id"])) as choose, \
             patch.object(picks, "save_state") as save, patch.dict(os.environ, {}, clear=True):
            self.assertEqual(picks.connected_channel("secret-not-saved", CONFIG, state, NOW), ("org", CONFIG["channel_id"]))
            self.assertEqual(picks.connected_channel("secret-not-saved", CONFIG, state, NOW + timedelta(hours=2)), ("org", CONFIG["channel_id"]))
            choose.assert_called_once()
            self.assertNotIn("secret-not-saved", json.dumps(state))
            picks.connected_channel("test", CONFIG, state, NOW + timedelta(days=1))
            self.assertEqual(choose.call_count, 2)
            save.assert_called()
        with patch.dict(os.environ, {"BUFFER_CHANNEL_ID": "wrong"}, clear=True), \
             patch.object(picks, "choose_x_channel") as choose:
            with self.assertRaisesRegex(RuntimeError, "pinned"):
                picks.connected_channel("test", CONFIG, state, NOW)
            choose.assert_not_called()

    def test_changed_or_future_connection_cache_must_be_reverified(self):
        for changed in ({"channel_id": "other"}, {"verified_at_utc": (NOW + timedelta(hours=1)).isoformat()}):
            state = ledger()
            state["provider_connection"] = {"channel_id": CONFIG["channel_id"], "organization_id": "org",
                                            "verified_at_utc": NOW.isoformat(), **changed}
            with patch.dict(os.environ, {}, clear=True), patch.object(picks, "choose_x_channel",
                    return_value=("org", CONFIG["channel_id"])) as choose, patch.object(picks, "save_state"):
                picks.connected_channel("test", CONFIG, state, NOW)
                choose.assert_called_once()

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
        self.assertIn("#未発生新聞", msg)
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
        tagged["tags"] = ["AI", "働き方", "SNS"]
        tagged["x_tags"] = ["生成AI", "仕事あるある", "3つ目"]
        tagged_msg = picks.publication_text(tagged)
        self.assertIn("\n\n#未発生新聞 #生成AI #仕事あるある\n\n", tagged_msg)
        self.assertNotIn("#AI", tagged_msg)
        self.assertNotIn("#SNS", tagged_msg)
        self.assertNotIn("#3つ目", tagged_msg)
        self.assertEqual(picks.article_hashtags(tagged), ["#生成AI", "#仕事あるある"])
        tagged["x_tags"] = ["SNS", "アプリ", "ポイ活", "ポイ活"]
        self.assertEqual(picks.article_hashtags(tagged), ["#ポイ活"])

        followup = article("followup")
        followup["followup_of"] = "previous-story"
        followup_msg = picks.publication_text(followup)
        self.assertTrue(followup_msg.startswith("【続報｜未発生新聞】"))
        self.assertIn("#未発生新聞", followup_msg)
        with self.assertRaises(ValueError):
            picks.publication_text(article("../unsafe"))

    def test_accepted_photo_share_version_preserves_tracking_and_slug_no_repost(self):
        from generated_photos import generated_manifest
        from local_scene_photos import write_generated
        a = article("reviewed-photo", age_hours=3)
        # Synthetic header fixture for offline integrity gates, not an actual photo.
        data = (b"\xff\xd8\xff\xc0\x00\x11\x08"
                + (675).to_bytes(2, "big") + (1200).to_bytes(2, "big")
                + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
                + b"\x00" * 17000 + b"\xff\xd9")
        plain = "https://veripsa.com/articles/" + a["slug"] + "/"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_generated(a, data, "sd_xl_base_1.0.safetensors", root=root)
            record = generated_manifest(root)[a["slug"]]
            (root / "content").mkdir()
            review = root / "content/visual_reviews.json"
            review.write_text(json.dumps({"version": 1, "reviews": {a["slug"]: {
                "decision": "accepted", "article_sha256": record["article_sha256"],
                "image_sha256": record["sha256"], "reviewed_at": NOW.isoformat(),
                "note": "Synthetic test fixture, not a real visual approval.",
            }}}))
            with patch.object(picks, "ROOT", root):
                shared = picks.publication_text(a).splitlines()[-1]
                self.assertEqual(shared, picks.publication_text(a).splitlines()[-1])
                self.assertEqual(parse_qs(urlparse(shared).query), {
                    "utm_source": ["x"], "utm_medium": ["social"],
                    "utm_campaign": ["article_auto"], "image": [record["sha256"][:12]],
                })
                self.assertLessEqual(picks.x_weighted_length(picks.publication_text(a)), picks.X_TEXT_LIMIT)
                for remote_url in (plain, shared):
                    # A new image query cannot make a previously sent slug eligible.
                    remote = [{"id": "prior-receipt", "status": "sent", "text": remote_url,
                               "sentAt": (NOW - timedelta(days=2)).isoformat()}]
                    selected, _, _ = picks.decide([a], {}, ledger(), remote, CONFIG, NOW)
                    self.assertIsNone(selected)
                selected, _, _ = picks.decide([a], {}, ledger(), [], CONFIG, NOW)
                self.assertEqual(selected["slug"], a["slug"])
                rejected = json.loads(review.read_text())
                rejected["reviews"][a["slug"]]["decision"] = "rejected"
                review.write_text(json.dumps(rejected))
                self.assertNotIn("image", parse_qs(urlparse(picks.publication_text(a).splitlines()[-1]).query))

    def test_x_share_lead_can_replace_headline_summary_with_reader_first_copy(self):
        a = article("story-reader-lead", title="移動ポイントを稼ぐ男性、家の前を通り過ぎてもう一周")
        a["summary"] = "家の前まで戻ったが、目標ポイントに届かず、そのままもう一周した。"
        a["x_tags"] = ["ポイ活"]
        a["x_share_lead"] = (
            "家の前まで来たのに、ポイントが足りなくてもう一周した。\n"
            "窓から家族が手を振ってるのを見て、さらに遠回りした。\n"
            "これ、もう似たことやってる人いる？"
        )
        msg = picks.publication_text(a)
        self.assertTrue(msg.startswith("家の前まで来たのに"))
        self.assertNotIn("【未発生新聞】", msg)
        self.assertNotIn(a["title"], msg)
        self.assertIn("#未発生新聞 #ポイ活", msg)
        self.assertTrue(msg.endswith(
            "https://veripsa.com/articles/story-reader-lead/"
            + picks.TRACKING_QUERY
        ))
        self.assertLessEqual(picks.x_weighted_length(msg), picks.X_TEXT_LIMIT)

    def test_invalid_x_share_lead_falls_back_to_existing_headline_summary_copy(self):
        bad_leads = (
            "短い",
            "要約：家の前まで来たのにポイントが足りず、男性はもう一周することにした。",
            "家の前まで来たのに #ポイ活 のため、もう一周することにした。",
            "詳しくは https://example.com を見てください。家の前でもう一周した。",
            "家の前まで来たのにポイントが足りず、もう一周した。" * 20,
        )
        for lead in bad_leads:
            with self.subTest(lead=lead[:24]):
                a = article("story-reader-fallback", title="移動ポイントを稼ぐ男性、家の前を通り過ぎてもう一周")
                a["summary"] = "家の前まで戻ったが、目標ポイントに届かず、そのままもう一周した。"
                a["x_share_lead"] = lead
                msg = picks.publication_text(a)
                self.assertTrue(msg.startswith("【未発生新聞】"))
                self.assertIn("移動ポイントを稼ぐ男性", msg)
                self.assertIn("#未発生新聞", msg)
                self.assertTrue(msg.endswith(
                    "https://veripsa.com/articles/story-reader-fallback/"
                    + picks.TRACKING_QUERY
                ))

    def test_article_summary_adds_a_short_reader_teaser_within_budget(self):
        a = article("story-teaser", title="倍速視聴で内容が分からなくなった男性")
        a["summary"] = "時間を節約したはずが、登場人物を確認する解説動画を本編より長く見ることになった。"
        a["tags"] = ["メディア", "ドラマ"]
        a["x_tags"] = ["タイパ", "ドラマ沼"]
        msg = picks.publication_text(a)
        self.assertIn("時間を節約したはずが", msg)
        self.assertIn("#未発生新聞 #タイパ #ドラマ沼", msg)
        self.assertIn("utm_campaign=article_auto", msg)
        self.assertLessEqual(picks.x_weighted_length(msg), picks.X_TEXT_LIMIT)

    def test_summary_never_breaks_required_tags_or_tracked_url(self):
        a = article("story-summary-budget", title="見出し" * 30)
        a["summary"] = "要約" * 200
        a["tags"] = ["働き方", "メディア"]
        a["x_tags"] = ["会議あるある", "タイパ"]
        msg = picks.publication_text(a)
        self.assertIn("#未発生新聞 #会議あるある #タイパ", msg)
        self.assertTrue(msg.endswith(
            "https://veripsa.com/articles/story-summary-budget/"
            + picks.TRACKING_QUERY
        ))
        self.assertLessEqual(picks.x_weighted_length(msg), picks.X_TEXT_LIMIT)

    def test_long_slug_preserves_the_link_and_topics_within_x_budget(self):
        tagged = article("story-" + "a" * 200, title="見出し" * 40)
        tagged["tags"] = ["AI", "SNS", "アプリ"]
        tagged["x_tags"] = ["長" * 12, "別" * 12, "ポイ活"]
        msg = picks.publication_text(tagged)
        self.assertIn("…", msg)
        self.assertIn("#未発生新聞 #" + "長" * 12 + " #" + "別" * 12, msg)
        self.assertNotIn("#AI", msg)
        self.assertNotIn("#SNS", msg)
        self.assertNotIn("#ポイ活", msg)
        self.assertTrue(msg.endswith(
            "https://veripsa.com/articles/" + tagged["slug"] + "/"
            + picks.TRACKING_QUERY
        ))
        self.assertGreater(len(msg), 220)
        self.assertLessEqual(picks.x_weighted_length(msg), picks.X_TEXT_LIMIT)

    def test_every_published_article_uses_context_hashtags_without_category_fill(self):
        articles = json.loads((picks.ROOT / "content/articles.json").read_text(encoding="utf-8"))
        generic = {"#SNS", "#アプリ", "#社会", "#生活", "#暮らし", "#家族",
                   "#イベント", "#仕事", "#働き方", "#メディア", "#AI"}
        for item in articles:
            with self.subTest(slug=item["slug"]):
                msg = picks.publication_text(item)
                expected_tags = " ".join(["#未発生新聞"] + picks.article_hashtags(item))
                self.assertIn("\n\n" + expected_tags + "\n\n", msg)
                self.assertFalse(generic.intersection(msg.split()))
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
        self.assertNotIn("slot_jst", state["queued"]["story-a"])
        self.assertIsNone(state["queued"]["story-a"]["recovered_provider_time_utc"])
        self.assertEqual(picks.recover_from_buffer(state, existing, NOW, {"story-a", "story-b"}), [])
        self.assertEqual(picks.rank([a], {"story-a": stats()},
                                    state, set(), CONFIG), [])
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "state.json"
            picks.save_state(state, target)
            self.assertEqual(picks.read_json(target), state)

    def test_initial_provider_error_evidence_is_bounded_and_display_safe(self):
        details = picks.initial_provider_error_evidence({
            "error": {
                "message": "  X rejected this post.  " + "x" * 600,
                "supportUrl": "https://support.buffer.com/example",
                "rawError": "internal-provider-detail-must-not-be-stored",
            }
        })
        self.assertTrue(details["buffer_error_message_at_accept"].startswith(
            "X rejected this post."
        ))
        self.assertLessEqual(len(details["buffer_error_message_at_accept"]), 500)
        self.assertEqual(
            details["buffer_error_support_url_at_accept"],
            "https://support.buffer.com/example",
        )
        self.assertNotIn("rawError", str(details))
        self.assertEqual(
            picks.initial_provider_error_evidence({
                "error": {"message": "problem", "supportUrl": "javascript:bad"}
            }),
            {"buffer_error_message_at_accept": "problem"},
        )

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

    def test_sending_recovery_uses_created_at_and_blocks_same_slot(self):
        now = datetime.fromisoformat("2026-11-05T19:20:00+09:00")
        sending = article("sending-story", age_hours=4)
        existing = [{
            "id": "buf-sending",
            "status": "sending",
            "text": picks.publication_text(sending),
            "createdAt": "2026-11-05T10:06:00Z",
        }]
        state = ledger()
        selected, _, _ = picks.decide(
            [sending], {}, state, existing, CONFIG, now
        )
        self.assertIsNone(selected)
        entry = state["queued"]["sending-story"]
        self.assertEqual(entry["slot_jst"], "2026-11-05T19:05:00+09:00")
        self.assertEqual(
            entry["recovered_provider_time_utc"], "2026-11-05T10:06:00+00:00"
        )
        self.assertTrue(picks.slot_already_used(state, now))

    def test_unsettled_provider_article_is_recovered_and_never_auto_retried(self):
        now = datetime.fromisoformat("2026-11-05T19:20:00+09:00")
        errored = article("error-story", age_hours=4)
        existing = [{
            "id": "buf-error",
            "status": "error",
            "text": picks.publication_text(errored),
        }]
        state = ledger()
        selected, _, _ = picks.decide(
            [errored], {}, state, existing, CONFIG, now
        )
        self.assertIsNone(selected)
        self.assertIn("error-story", state["queued"])
        self.assertEqual(
            state["queued"]["error-story"]["buffer_status_when_recovered"],
            "error",
        )
        self.assertIsNone(picks.entry_promotion_slot(state["queued"]["error-story"]))
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

    def test_real_buffer_sent_time_blocks_short_gaps_without_spending_a_slot(self):
        fresh = article("fresh-after-delay", age_hours=4)
        for minutes in (115, 92):
            with self.subTest(minutes=minutes):
                prior = {
                    "id": "other-x-post",
                    "status": "sent",
                    "text": "以前のX投稿",
                    "sentAt": (NOW - timedelta(minutes=minutes)).isoformat(),
                }
                state = ledger()
                self.assertEqual(
                    picks.buffer_delivery_spacing_hold([prior], state, NOW, 2),
                    "recent_buffer_sent",
                )
                item, _, _ = picks.decide(
                    [fresh], {}, state, [prior], CONFIG, NOW
                )
                self.assertIsNone(item)
                self.assertEqual(picks.promotions_today(state, NOW), 0)
        exactly_two_hours = [{
            "id": "earlier", "status": "sent", "text": "過去の投稿",
            "sentAt": (NOW - timedelta(hours=2)).isoformat(),
        }]
        self.assertIsNone(
            picks.buffer_delivery_spacing_hold(exactly_two_hours, ledger(), NOW, 2)
        )
        selected, _, _ = picks.decide(
            [fresh], {}, ledger(), exactly_two_hours, CONFIG, NOW
        )
        self.assertEqual(selected["slug"], fresh["slug"])

    def test_near_scheduled_and_unsettled_buffer_posts_prevent_collisions(self):
        cases = (
            ({"status": "scheduled", "dueAt": (NOW + timedelta(minutes=90)).isoformat()},
             "buffer_schedule_near_or_overdue"),
            ({"status": "scheduled", "dueAt": (NOW - timedelta(hours=4)).isoformat()},
             "buffer_schedule_near_or_overdue"),
            ({"status": "scheduled"}, "unverified_buffer_schedule"),
            ({"status": "sent"}, "unverified_buffer_sent_time"),
            ({"status": "sent", "sentAt": "invalid"}, "unverified_buffer_sent_time"),
            ({"status": "sending"}, "buffer_send_in_progress"),
        )
        for remote, reason in cases:
            with self.subTest(remote=remote):
                remote = {"id": "other-x-post", "text": "過去の投稿", **remote}
                self.assertEqual(
                    picks.buffer_delivery_spacing_hold([remote], ledger(), NOW, 2),
                    reason,
                )
                selected, _, _ = picks.decide(
                    [article("unrelated-fresh", age_hours=4)],
                    {}, ledger(), [remote], CONFIG, NOW,
                )
                self.assertIsNone(selected)
        future_schedule = [{
            "id": "other-queue", "status": "scheduled",
            "dueAt": (NOW + timedelta(hours=4)).isoformat(),
        }]
        self.assertIsNone(
            picks.buffer_delivery_spacing_hold(future_schedule, ledger(), NOW, 2)
        )

    def test_saved_share_now_receipts_still_protect_when_provider_window_omits_them(self):
        sent = {
            "buffer_id": "article-prev", "queued_at_utc": (NOW - timedelta(hours=3)).isoformat(),
            "slot_jst": "2026-11-05T09:05:00+09:00",
            "submission_mode": "shareNow",
            "buffer_sent_at_at_accept": (NOW - timedelta(minutes=90)).isoformat(),
        }
        self.assertEqual(
            picks.buffer_delivery_spacing_hold([], ledger(previous=sent), NOW, 2),
            "recent_verified_article_sent",
        )
        sent["buffer_sent_at_at_accept"] = (NOW - timedelta(hours=2)).isoformat()
        self.assertIsNone(
            picks.buffer_delivery_spacing_hold([], ledger(previous=sent), NOW, 2)
        )
        sent["buffer_sent_at_at_accept"] = None
        sent["queued_at_utc"] = (NOW - timedelta(minutes=40)).isoformat()
        self.assertEqual(
            picks.buffer_delivery_spacing_hold([], ledger(previous=sent), NOW, 2),
            "recent_or_unverified_article_submission",
        )
        # An absent provider sentAt can use a matching immutable sent receipt.
        sent["queued_at_utc"] = (NOW - timedelta(hours=3)).isoformat()
        sent["buffer_sent_at_at_accept"] = (NOW - timedelta(minutes=80)).isoformat()
        self.assertEqual(
            picks.buffer_delivery_spacing_hold(
                [{"id": "article-prev", "status": "sent", "text": "記事紹介"}],
                ledger(previous=sent), NOW, 2,
            ), "recent_verified_article_sent"
        )

    def test_send_spacing_works_across_jst_midnight_and_never_counts_legacy_as_quota(self):
        now = datetime.fromisoformat("2026-11-06T00:30:00+09:00")
        old = {"id": "manual", "status": "sent",
               "sentAt": "2026-11-05T23:00:00+09:00"}
        self.assertEqual(
            picks.buffer_delivery_spacing_hold([old], ledger(), now, 2),
            "recent_buffer_sent",
        )
        self.assertEqual(picks.promotions_today(ledger(), now), 0)

    def test_active_spacing_hold_skips_pv_and_buffer_post_but_persists_recovery(self):
        fresh = article("not-sent-yet", age_hours=4)
        mature = article("mature-available", age_hours=36)
        documents = {
            HERE / "config.json": CONFIG,
            HERE / "state.json": ledger(),
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "content").mkdir()
            (root / "content/articles.json").write_text(json.dumps([fresh, mature]))
            remote = [{
                "id": "external-post", "status": "sent",
                "text": "他のX投稿",
                "sentAt": (NOW - timedelta(minutes=92)).isoformat(),
            }]
            with patch.object(picks, "ROOT", root), \
                 patch.object(picks, "read_json", side_effect=lambda p: copy.deepcopy(documents[p])), \
                 patch.object(picks, "datetime", wraps=datetime) as clock, \
                 patch.dict(os.environ, {"BUFFER_API_KEY": "test-only"}, clear=True), \
                 patch.object(picks, "read_page_view_metrics") as metrics, \
                 patch.object(picks, "choose_x_channel", return_value=("org", CONFIG["channel_id"])), \
                 patch.object(picks, "list_existing", return_value=remote), \
                 patch.object(picks, "save_state") as save, \
                 patch.object(picks, "queue_one") as queue:
                clock.now.return_value = NOW
                self.assertEqual(picks.main(), 0)
                metrics.assert_not_called()
                queue.assert_not_called()
                save.assert_called()

    def test_logical_two_hour_slots_survive_action_delay_and_block_same_slot(self):
        a = article("new-story", age_hours=36)
        counts = {"new-story": stats()}
        remote = [
            {"id": "short-1", "status": "scheduled", "text": "昔話の短文",
             "dueAt": "2026-11-05T18:00:00+09:00"},
            {"id": "short-2", "status": "scheduled", "text": "別の短文",
             "dueAt": "2026-11-05T20:00:00+09:00"},
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
             "sentAt": f"2026-11-04T{9 + i:02d}:00:00+09:00"}
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
        remote = [{"id": "article-existing", "status": "scheduled",
                   "text": "記事予約", "dueAt": "2026-11-05T18:00:00+09:00"}]
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
            promoted = article("new-story")
            promoted["x_share_lead"] = (
                "会議を短くしたかっただけなのに、最後の挨拶だけ1時間。"
                "これ、効率化した結果の方が長くなってない？"
            )
            (root / "content/articles.json").write_text(json.dumps([promoted]))
            with patch.object(picks, "ROOT", root), \
                 patch.object(picks, "read_json", side_effect=lambda path: copy.deepcopy(documents[path])), \
                 patch.object(picks, "datetime", wraps=datetime) as clock, \
                 patch.dict(os.environ, {"BUFFER_API_KEY": "test-only", "GITHUB_STEP_SUMMARY": str(root / "summary.md")}), \
                 patch.object(picks, "read_page_view_metrics", return_value=({"new-story": stats()}, window)), \
                 patch.object(picks, "choose_x_channel", return_value=("org", CONFIG["channel_id"])), \
                 patch.object(picks, "list_existing", return_value=remote) as list_existing, \
                 patch.object(picks, "save_state") as save, \
                 patch.object(picks, "queue_one", return_value={"id":"article-confirmed","status":"sent","createdAt":"2026-11-05T02:05:58Z","sentAt":"2026-11-05T02:06:00Z","externalLink":"https://x.com/veripsa/status/111","sharedNow":True,"shareMode":"shareNow"}) as queue:
                clock.now.return_value = NOW
                self.assertEqual(picks.main(), 0)
                list_existing.assert_called_once_with(
                    "test-only", "org", CONFIG["channel_id"],
                    include_due_at=True, include_unsettled=True,
                )
                queue.assert_called_once()
                self.assertEqual(queue.call_args.args[1], CONFIG["channel_id"])
                self.assertEqual(queue.call_args.args[2]["kind"], "article")
                self.assertEqual(queue.call_args.args[2]["id"], "article-new-story")
                self.assertEqual(queue.call_args.kwargs["mode"], "shareNow")
                self.assertIs(queue.call_args.kwargs["return_post"], True)
                self.assertTrue(queue.call_args.args[2]["text"].startswith(
                    "会議を短くしたかっただけなのに"
                ))
                self.assertIn("https://veripsa.com/articles/new-story/", queue.call_args.args[2]["text"])
                self.assertIn("utm_campaign=article_auto", queue.call_args.args[2]["text"])
                receipt = save.call_args.args[0]["queued"]["new-story"]
                self.assertEqual(receipt["buffer_id"], "article-confirmed")
                self.assertEqual(receipt["copy_mode"], "reader_lead")
                self.assertEqual(receipt["source"], "verified_article_page_views")
                self.assertEqual(receipt["submission_mode"], "shareNow")
                self.assertEqual(receipt["slot_jst"], "2026-11-05T11:05:00+09:00")
                self.assertEqual(receipt["buffer_status_at_accept"], "sent")
                self.assertEqual(receipt["buffer_created_at_at_accept"], "2026-11-05T02:05:58Z")
                self.assertEqual(receipt["buffer_sent_at_at_accept"], "2026-11-05T02:06:00Z")
                self.assertEqual(receipt["buffer_external_link_at_accept"], "https://x.com/veripsa/status/111")
                self.assertIs(receipt["buffer_shared_now_at_accept"], True)
                self.assertEqual(receipt["buffer_share_mode_at_accept"], "shareNow")
                self.assertEqual(receipt["metrics_at_queue"], {
                    **window, "last_7_days_page_views": 11, "today_page_views": 1})
            summary = (root / "summary.md").read_text()
            self.assertIn("Buffer受付", summary)
            self.assertIn("Buffer応答の状態：`sent`", summary)
            self.assertIn("X実公開は未確認", summary)
            self.assertIn("X実公開の独立確認ではありません", summary)
            self.assertNotIn("test-only", summary)
            self.assertNotIn("article-confirmed", summary)
        self.assertEqual(documents, original)

    def test_queue_fullness_does_not_block_share_now_article_lane(self):
        candidate = article("measured-story", age_hours=36)
        remote = [{"id":f"creative-{i}", "status":"scheduled",
                   "text":f"異なる短文の予約です{i}",
                   "dueAt":"2026-11-05T18:00:00+09:00"} for i in range(10)]
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
                 patch.dict(os.environ, {"BUFFER_API_KEY": "test-only", "GITHUB_STEP_SUMMARY": str(root / "summary.md")}), \
                 patch.object(picks, "read_page_view_metrics") as pv_read, \
                 patch.object(picks, "choose_x_channel", return_value=("org", CONFIG["channel_id"])), \
                 patch.object(picks, "list_existing", return_value=remote), \
                 patch.object(picks, "save_state") as save, \
                 patch.object(picks, "queue_one", return_value={"id":"article-fresh","status":"sending","createdAt":"2026-11-05T02:05:58Z","sharedNow":True,"shareMode":"shareNow"}) as queue:
                clock.now.return_value = NOW
                self.assertEqual(picks.main(), 0)
                pv_read.assert_not_called()
                queue.assert_called_once()
                self.assertEqual(queue.call_args.args[2]["kind"], "article")
                self.assertEqual(queue.call_args.args[2]["id"], "article-fresh-story")
                self.assertEqual(queue.call_args.kwargs["mode"], "shareNow")
                receipt = save.call_args.args[0]["queued"]["fresh-story"]
                self.assertEqual(receipt["source"], "fresh_article")
                self.assertEqual(receipt["copy_mode"], "headline_summary")
                self.assertEqual(receipt["submission_mode"], "shareNow")
                self.assertEqual(receipt["slot_jst"], "2026-11-05T11:05:00+09:00")
                self.assertEqual(receipt["buffer_status_at_accept"], "sending")
                self.assertEqual(receipt["buffer_created_at_at_accept"], "2026-11-05T02:05:58Z")
                self.assertIs(receipt["buffer_shared_now_at_accept"], True)
                self.assertEqual(receipt["buffer_share_mode_at_accept"], "shareNow")
                self.assertNotIn("metrics_at_queue", receipt)
            summary = (root / "summary.md").read_text()
            self.assertIn("Buffer応答の状態：`sending`", summary)
            self.assertIn("X実公開は未確認", summary)
            self.assertIn("対象記事：`fresh-story`", summary)

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
                 patch.object(picks, "queue_one", return_value={"id":"fresh-receipt","status":"sending","createdAt":"2026-11-05T02:05:58Z","sharedNow":True,"shareMode":"shareNow"}) as queue:
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
                 patch.object(picks, "queue_one", return_value={"id":"mature-receipt","status":"scheduled","createdAt":"2026-11-05T02:05:58Z","dueAt":"2026-11-05T02:10:00Z","sharedNow":True,"shareMode":"shareNow"}) as queue:
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


class ArticleComicAttachmentTests(unittest.TestCase):
    """Use committed synthetic art and mocked providers, never production assets."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.story = {**article("comic-story", age_hours=4), "paragraphs": ["本文の一段落。"]}
        self.relative = "social-bot/assets/article-comic-story.png"
        self.image = self.root / self.relative
        self.image.parent.mkdir(parents=True)
        self.image.write_bytes(self.png())
        self.git("init", "-q", "--template=", ".")
        self.git("add", self.relative)
        self.git("-c", "user.name=Offline test", "-c", "user.email=test@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "Synthetic fixture")
        self.revision = self.git("rev-parse", "HEAD").strip()
        self.caption = "便利にしたら、手順が増えました。"
        self.review = {"approved_by": "owner", "reviewed_at": (NOW - timedelta(days=1)).isoformat(),
                       "article_sha256": comics.article_fingerprint(self.story),
                       "image_sha256": hashlib.sha256(self.image.read_bytes()).hexdigest(),
                       "caption": self.caption,
                       "caption_sha256": hashlib.sha256(self.caption.encode("utf-8")).hexdigest(),
                       "path": self.relative}
        self.config = {**CONFIG, "comic_attachments": {"comic-story": self.review}}

    @staticmethod
    def png(red=120):
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 3, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress((b"\0" + bytes((red, 140, 160)) * 2) * 3))
                + chunk(b"IEND", b""))

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True,
                              capture_output=True, text=True).stdout

    def submit(self, review=None, *, available=True, include_review=True, story=None, post_overrides=None):
        story = self.story if story is None else story
        post = {"kind": "article", "id": "article-" + story["slug"],
                "text": "記事はこちら https://veripsa.com/articles/" + story["slug"] + "/"}
        if include_review:
            post.update(article=story, comic_approval=self.review if review is None else review)
        post.update(post_overrides or {})
        with patch.object(publish, "HERE", self.root / "social-bot"), \
             patch.object(publish, "remote_asset_available", return_value=available,
                          side_effect=available if isinstance(available, Exception) else None) as remote, \
             patch.object(publish, "gql", return_value={"createPost": {"post": {"id": "mock-post"}}}) as send, \
             patch.object(comics, "datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            self.assertEqual(publish.queue_one("mock-only", "mock-channel", post, mode="shareNow"), "mock-post")
        return send.call_args.args[1], remote

    @contextmanager
    def main_context(self, *, config=None, state=None, retry=None, available=True,
                     articles=None, existing=None, now=NOW):
        (self.root / "content").mkdir(exist_ok=True)
        (self.root / "content/articles.json").write_text(json.dumps(
            [self.story] if articles is None else articles))
        documents = {HERE / "config.json": self.config if config is None else config,
                     HERE / "state.json": ledger() if state is None else state}
        with ExitStack() as stack:
            stack.enter_context(patch.object(picks, "ROOT", self.root))
            stack.enter_context(patch.object(publish, "HERE", self.root / "social-bot"))
            stack.enter_context(patch.object(picks, "read_json", side_effect=lambda p: copy.deepcopy(documents[p])))
            stack.enter_context(patch.object(picks, "delivery_retry_time", return_value=retry))
            stack.enter_context(patch.dict(os.environ, {
                "BUFFER_API_KEY": "mock-only", "PATH": os.environ.get("PATH", ""),
            }, clear=True))
            for module in (picks, comics):
                clock = stack.enter_context(patch.object(module, "datetime", wraps=datetime))
                clock.now.return_value = now
            mocks = {
                "channel": stack.enter_context(patch.object(picks, "connected_channel", return_value=("org", CONFIG["channel_id"]))),
                "existing": stack.enter_context(patch.object(picks, "list_existing", return_value=[] if existing is None else existing)),
                "save": stack.enter_context(patch.object(picks, "save_state")),
                "remote": stack.enter_context(patch.object(publish, "remote_asset_available", return_value=available)),
                "gql": stack.enter_context(patch.object(publish, "gql", return_value={"createPost": {"post": {"id": "mock-post"}}})),
                "queue": stack.enter_context(patch.object(picks, "queue_one", wraps=publish.queue_one)),
            }
            yield mocks

    def comic_record(self, *, slug=None, hours=180, pending=False):
        slug = slug or self.story["slug"]
        asset = comics.approved_comic_asset(self.story, self.review, self.root, NOW)
        text = publish.comic_publication_text({"slug": slug}, asset)
        return {"buffer_id": "" if pending else "saved-comic",
                "queued_at_utc": (NOW - timedelta(hours=hours)).isoformat(),
                "source": "approved_comic", "submission_mode": "shareNow",
                "submission_pending": pending, "copy_mode": "comic_caption",
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "comic_attachment": asset}

    def test_committed_owner_review_attaches_one_image_at_exact_revision(self):
        query, remote = self.submit()
        expected = publish.ASSET_BASE_URL.replace("/main/", "/" + self.revision + "/") + "/article-comic-story.png"
        remote.assert_called_once_with(expected)
        self.assertIn('assets: [{image: {url: "' + expected + '"}}]', query)
        self.assertEqual(query.count("assets:"), 1)
        self.assertIn("mode: shareNow", query)
        expected_text = self.caption + "\n\nhttps://veripsa.com/articles/comic-story/\n#未発生新聞"
        self.assertIn("text: " + publish.qstring(expected_text), query)

    def test_caption_requires_plain_short_reviewed_text_and_exact_utf8_hash(self):
        baseline, _ = self.submit(include_review=False)
        missing = {k: v for k, v in self.review.items() if not k.startswith("caption")}
        for caption in (None, "", " ", "あ" * 101, "一文。二文。三文。",
                        "詳しくは https://example.invalid/a", "詳しくは example.com",
                        "説明 #未発生新聞", "説明\n次", "説明\u2028次", "説明\u2029次",
                        "説明\x00", "<b>説明</b>"):
            review = {**self.review, "caption": caption,
                      "caption_sha256": hashlib.sha256(str(caption).encode("utf-8")).hexdigest()}
            with self.subTest(caption=caption):
                query, remote = self.submit(review)
                self.assertEqual(query, baseline)
                remote.assert_not_called()
        for review in (missing, {**self.review, "caption_sha256": "0" * 64},
                       {**self.review, "caption": self.caption + "変更。"}):
            query, remote = self.submit(review)
            self.assertEqual(query, baseline)
            remote.assert_not_called()
        for caption in ("会議は短く。挨拶は長く。", "あ" * 99 + "。"):
            review = {**self.review, "caption": caption,
                      "caption_sha256": hashlib.sha256(caption.encode("utf-8")).hexdigest()}
            query, remote = self.submit(review)
            remote.assert_called_once()
            self.assertIn("text: " + publish.qstring(caption + "\n\nhttps://veripsa.com/articles/comic-story/\n#未発生新聞"), query)

    def test_png_cap_boundary_and_structural_corruption(self):
        valid = self.png()
        def store(data):
            self.image.write_bytes(data)
            self.git("add", self.relative)
            self.git("-c", "user.name=Offline test", "-c", "user.email=test@example.invalid",
                     "-c", "commit.gpgsign=false", "commit", "-qm", "Synthetic boundary fixture")
            return {**self.review, "image_sha256": hashlib.sha256(data).hexdigest()}
        for size in (4_000_000, 4_000_001):
            padding = b"padding\0" + b"x" * (size - len(valid) - 20)
            chunk = struct.pack(">I", len(padding)) + b"tEXt" + padding + struct.pack(">I", zlib.crc32(b"tEXt" + padding))
            data = valid[:-12] + chunk + valid[-12:]
            self.assertEqual(len(data), size)
            review = store(data)
            with self.subTest(size=size):
                result = comics.approved_comic_asset(self.story, review, self.root, NOW)
                self.assertEqual(result is not None, size == 4_000_000)
        bad_crc = valid[:-1] + bytes((valid[-1] ^ 1,))
        for data in (valid[:24], bad_crc, valid[:-3], valid + b"x", valid[:33] + valid[-12:]):
            with self.subTest(data=data):
                self.assertIsNone(comics.approved_comic_asset(self.story, store(data), self.root, NOW))

    def test_missing_unapproved_stale_and_future_reviews_leave_legacy_payload(self):
        baseline, remote = self.submit(include_review=False)
        remote.assert_not_called()
        for change in ({}, {"approved_by": "agent"}, {"reviewed_at": ""},
                       {"reviewed_at": (NOW + timedelta(minutes=1)).isoformat()},
                       {"article_sha256": "0" * 64}, {"image_sha256": "0" * 64}):
            review = {} if not change else {**self.review, **change}
            with self.subTest(review=review):
                query, remote = self.submit(review)
                self.assertEqual(query, baseline)
                remote.assert_not_called()
        query, remote = self.submit(story={**self.story, "paragraphs": ["変更された本文。"]})
        self.assertEqual(query, baseline)
        remote.assert_not_called()
        self.image.write_bytes(self.png(121))
        query, remote = self.submit()
        self.assertEqual(query, baseline)
        remote.assert_not_called()

    def test_unsafe_paths_and_paused_legacy_manga_are_never_read_or_attached(self):
        for path in ("../article-comic-story.png", "/tmp/article-comic-story.png",
                     "https://example.invalid/art.png", "social-bot/assets/../article-comic-story.png",
                     "social-bot\\assets\\article-comic-story.png", "social-bot/assets/nejimachi-006-big-shadow.png"):
            with self.subTest(path=path), patch.object(Path, "read_bytes") as read:
                self.assertIsNone(comics.approved_comic_asset(self.story, {**self.review, "path": path}, self.root, NOW))
                read.assert_not_called()

    def test_symlinks_missing_and_uncommitted_or_changed_head_art_are_rejected(self):
        data = self.image.read_bytes()
        other = self.root / "other.png"
        other.write_bytes(data)
        self.image.unlink()
        self.assertIsNone(comics.approved_comic_asset(self.story, self.review, self.root, NOW))
        self.image.symlink_to(other)
        self.assertIsNone(comics.approved_comic_asset(self.story, self.review, self.root, NOW))
        self.image.unlink()
        self.image.write_bytes(data)
        assets = self.image.parent
        backup = assets.with_name("saved-assets")
        assets.rename(backup)
        assets.symlink_to(backup, target_is_directory=True)
        self.assertIsNone(comics.approved_comic_asset(self.story, self.review, self.root, NOW))
        assets.unlink()
        backup.rename(assets)
        untracked = assets / "article-comic-new.png"
        untracked.write_bytes(data)
        self.assertIsNone(comics.approved_comic_asset(self.story, {**self.review, "path": str(untracked.relative_to(self.root))}, self.root, NOW))
        self.image.write_bytes(self.png(121))
        updated = {**self.review, "image_sha256": hashlib.sha256(self.image.read_bytes()).hexdigest()}
        self.assertIsNone(comics.approved_comic_asset(self.story, updated, self.root, NOW))

    def test_unavailable_remote_art_falls_back_to_identical_text_payload(self):
        baseline, _ = self.submit(include_review=False)
        for available in (False, TimeoutError("offline simulated timeout")):
            with self.subTest(available=available):
                query, remote = self.submit(available=available)
                remote.assert_called_once()
                self.assertEqual(query, baseline)

    def test_optional_media_keeps_article_identity_slot_and_default_payload(self):
        for config in (CONFIG, {**CONFIG, "comic_attachments": {}},
                       {**CONFIG, "comic_attachments": None},
                       {**CONFIG, "comic_attachments": []}, self.config):
            with self.subTest(config=config), self.main_context(config=config) as mocks:
                self.assertEqual(picks.main(), 0)
                post = mocks["queue"].call_args.args[2]
                self.assertEqual(post["kind"], "article")
                self.assertEqual(post["id"], "article-comic-story")
                self.assertEqual(mocks["queue"].call_args.kwargs, {"mode": "shareNow", "return_post": True})
                saved = mocks["save"].call_args.args[0]
                receipt = saved["comic_posts" if config is self.config else "queued"]["comic-story"]
                self.assertEqual(receipt["slot_jst"], "2026-11-05T11:05:00+09:00")
                if config is not self.config:
                    self.assertEqual(set(post), {"kind", "id", "text"})
                    mocks["remote"].assert_not_called()
                    self.assertNotIn("comic_attachment", receipt)
                else:
                    self.assertTrue(post["comic_required"])
                    self.assertNotIn("comic-story", saved["queued"])
                    actual_text = self.caption + "\n\nhttps://veripsa.com/articles/comic-story/\n#未発生新聞"
                    self.assertEqual(receipt["copy_mode"], "comic_caption")
                    self.assertEqual(receipt["text_sha256"], hashlib.sha256(actual_text.encode("utf-8")).hexdigest())
                    self.assertIn("text: " + publish.qstring(actual_text), mocks["gql"].call_args.args[1])
                    self.assertNotIn("text", receipt["comic_attachment"])
                    for key in ("caption_sha256", "article_sha256", "image_sha256", "path"):
                        self.assertEqual(receipt["comic_attachment"][key], self.review[key])
                    self.assertEqual(receipt["comic_attachment"]["revision"], self.revision)

    def test_unavailable_or_stale_comic_uses_another_ordinary_article_without_consuming_work(self):
        stale = {**self.config, "comic_attachments": {"comic-story": {**self.review, "caption_sha256": "0" * 64}}}
        ordinary = article("ordinary-other", age_hours=4)
        for config, available in ((self.config, False), (stale, True)):
            with self.subTest(available=available), self.main_context(
                    config=config, available=available, articles=[self.story, ordinary]) as mocks:
                self.assertEqual(picks.main(), 0)
                original = mocks["queue"].call_args.args[2]["text"]
                saved = mocks["save"].call_args.args[0]
                receipt = saved["queued"]["ordinary-other"]
                self.assertNotIn("comic-story", saved.get("comic_posts", {}))
                self.assertEqual(receipt["text_sha256"], hashlib.sha256(original.encode("utf-8")).hexdigest())
                self.assertNotEqual(receipt["copy_mode"], "comic_caption")
                self.assertNotIn("comic_attachment", receipt)
                self.assertIn("text: " + publish.qstring(original), mocks["gql"].call_args.args[1])
        mismatch = {"id": "article-other-story", "text": "https://veripsa.com/articles/other-story/"}
        baseline, _ = self.submit(include_review=False, post_overrides=mismatch)
        query, remote = self.submit(post_overrides=mismatch)
        self.assertEqual(query, baseline)
        remote.assert_not_called()

    def test_required_comic_never_sends_a_text_only_fallback(self):
        post = {"kind": "article", "id": "article-comic-story", "article": self.story,
                "comic_approval": self.review, "comic_required": True,
                "text": "https://veripsa.com/articles/comic-story/"}
        for override in ({}, {"id": "article-other-story", "text": "https://veripsa.com/articles/other-story/"}):
            with self.subTest(override=override), \
                 patch.object(publish, "HERE", self.root / "social-bot"), \
                 patch.object(publish, "remote_asset_available", return_value=False), \
                 patch.object(publish, "gql") as provider:
                with self.assertRaises(publish.ComicUnavailable):
                    publish.queue_one("mock-only", "mock-channel", {**post, **override}, mode="shareNow")
                provider.assert_not_called()

    def test_old_previously_promoted_source_gets_one_comic_and_preserves_original_receipt(self):
        self.story["published_at"] = (NOW - timedelta(days=40)).isoformat()
        old = {**recorded(hours=39 * 24), "copy_mode": "reader_lead", "text_sha256": "a" * 64}
        state = {**ledger(**{"comic-story": old}), "public_posts": {"comic-story": public_record()}}
        original = copy.deepcopy(state)
        self.assertEqual(picks.eligible_articles([self.story], NOW, CONFIG), [])
        with self.main_context(state=state) as mocks:
            self.assertEqual(picks.main(), 0)
            mocks["gql"].assert_called_once()
            saved = copy.deepcopy(mocks["save"].call_args.args[0])
        self.assertEqual(saved["queued"], original["queued"])
        self.assertEqual(saved["public_posts"], original["public_posts"])
        self.assertEqual(saved["comic_posts"]["comic-story"]["buffer_id"], "mock-post")
        self.assertEqual(saved["comic_posts"]["comic-story"]["source"], "approved_comic")
        self.assertFalse(saved["comic_posts"]["comic-story"].get("submission_pending", False))
        # A replacement image cannot turn the same source into another work.
        self.image.write_bytes(self.png(121))
        self.git("add", self.relative)
        self.git("-c", "user.name=Offline test", "-c", "user.email=test@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "Replacement synthetic fixture")
        self.review["image_sha256"] = hashlib.sha256(self.image.read_bytes()).hexdigest()
        saved["comic_posts"]["comic-story"].update(
            queued_at_utc=(NOW - timedelta(hours=3)).isoformat(),
            slot_jst="2026-11-05T09:05:00+09:00")
        self.assertFalse(picks.slot_already_used(saved, NOW))
        with self.main_context(state=saved) as mocks:
            self.assertEqual(picks.main(), 0)
            mocks["gql"].assert_not_called()
            mocks["queue"].assert_not_called()

    def test_same_approved_image_is_not_reused_as_a_second_article_comic(self):
        state = {**ledger(), "comic_posts": {"another-source": self.comic_record(slug="another-source")}}
        with self.main_context(state=state) as mocks:
            self.assertEqual(picks.main(), 0)
            mocks["gql"].assert_not_called()
            mocks["queue"].assert_not_called()
        fresh_comic = {**ledger(), "comic_posts": {"comic-story": self.comic_record(hours=3)}}
        self.assertIsNone(picks.decide([self.story], {}, fresh_comic, [], CONFIG, NOW)[0])

    def test_approved_art_cannot_bypass_slug_slot_daily_spacing_or_cooldown(self):
        used = {**recorded(hours=1), "slot_jst": picks.promotion_slot(NOW).isoformat()}
        earlier = {**recorded(hours=3), "slot_jst": "2026-11-05T09:05:00+09:00"}
        recent = {**earlier, "buffer_sent_at_at_accept": (NOW - timedelta(minutes=30)).isoformat()}
        cases = [(self.config, ledger(used=used), None, True),
                 ({**self.config, "max_per_jst_day": 1}, ledger(earlier=earlier), None, True),
                 (self.config, ledger(recent=recent), None, True),
                 (self.config, ledger(), datetime.fromisoformat("2026-12-03T03:05:19+00:00"), True)]
        for config, state, retry, before_network in cases:
            with self.subTest(state=state, retry=retry), self.main_context(config=config, state=state, retry=retry) as mocks:
                self.assertEqual(picks.main(), 0)
                for name in ("queue", "remote", "gql"):
                    mocks[name].assert_not_called()
                if before_network:
                    mocks["channel"].assert_not_called()
                    mocks["existing"].assert_not_called()

    def test_comic_receipts_share_slots_day_cap_and_real_send_spacing_with_ordinary_posts(self):
        for reason in ("slot", "day", "spacing"):
            entry = self.comic_record(slug="earlier-comic", hours=3)
            config = CONFIG
            if reason == "slot":
                entry["slot_jst"] = picks.promotion_slot(NOW).isoformat()
            elif reason == "day":
                entry["slot_jst"] = "2026-11-05T09:05:00+09:00"
                config = {**CONFIG, "max_per_jst_day": 1}
            else:
                entry["buffer_sent_at_at_accept"] = (NOW - timedelta(minutes=30)).isoformat()
            state = {**ledger(), "comic_posts": {"earlier-comic": entry}}
            with self.subTest(reason=reason), self.main_context(config=config, state=state) as mocks:
                self.assertEqual(picks.main(), 0)
                mocks["channel"].assert_not_called()
                mocks["gql"].assert_not_called()

    def test_pending_comic_recovers_only_exact_unique_new_provider_evidence(self):
        pending = self.comic_record(hours=3, pending=True)
        attempted = datetime.fromisoformat(pending["queued_at_utc"])
        text = publish.comic_publication_text(self.story, pending["comic_attachment"])
        remote = {"id": "recovered-comic", "status": "sent", "text": text,
                  "createdAt": (attempted + timedelta(seconds=1)).isoformat(),
                  "sentAt": (attempted + timedelta(seconds=2)).isoformat()}
        old = recorded(hours=180)
        state = {**ledger(**{"comic-story": old}), "comic_posts": {"comic-story": pending}}
        asset = comics.approved_comic_asset(self.story, self.review, self.root, NOW)
        picks.recover_comic_posts(state, [remote], NOW, [(self.story, self.review, asset)])
        self.assertEqual(state["queued"]["comic-story"], old)
        comic = state["comic_posts"]["comic-story"]
        self.assertEqual(comic["buffer_id"], "recovered-comic")
        self.assertFalse(comic.get("submission_pending", False))
        self.assertEqual(comic["queued_at_utc"], pending["queued_at_utc"])
        picks.recover_from_buffer(state, [remote], NOW, {"comic-story"})
        self.assertEqual(state["queued"]["comic-story"], old)
        comic_only = {**ledger(), "comic_posts": {"comic-story": copy.deepcopy(comic)}}
        picks.recover_from_buffer(comic_only, [remote], NOW, {"comic-story"})
        self.assertEqual(comic_only["queued"], {})

    def test_failed_receipt_save_recovers_durable_intent_without_reposting(self):
        original = ledger(**{"comic-story": recorded(hours=180)})
        persisted = {}
        def save(state):
            comic = state.get("comic_posts", {}).get("comic-story", {})
            if comic.get("buffer_id"):
                raise OSError("simulated receipt persistence failure")
            persisted.clear()
            persisted.update(copy.deepcopy(state))
        with self.main_context(state=original) as mocks:
            mocks["save"].side_effect = save
            with self.assertRaisesRegex(OSError, "persistence failure"):
                picks.main()
            mocks["gql"].assert_called_once()
        pending = persisted["comic_posts"]["comic-story"]
        self.assertTrue(pending["submission_pending"])
        self.assertFalse(pending["buffer_id"])
        text = publish.comic_publication_text(self.story, pending["comic_attachment"])
        remote = {"id": "mock-post", "status": "sent", "text": text,
                  "createdAt": (NOW + timedelta(seconds=1)).isoformat(),
                  "sentAt": (NOW + timedelta(seconds=2)).isoformat()}
        with self.main_context(state=persisted, existing=[remote], now=NOW + timedelta(hours=3)) as mocks:
            self.assertEqual(picks.main(), 0)
            mocks["queue"].assert_not_called()
            mocks["gql"].assert_not_called()
            recovered = mocks["save"].call_args.args[0]
        self.assertEqual(recovered["queued"], original["queued"])
        comic = recovered["comic_posts"]["comic-story"]
        self.assertEqual(comic["buffer_id"], "mock-post")
        self.assertEqual(comic["slot_jst"], pending["slot_jst"])

    def test_ambiguous_or_old_normal_evidence_never_completes_or_resends_a_comic(self):
        pending = self.comic_record(hours=3, pending=True)
        attempted = datetime.fromisoformat(pending["queued_at_utc"])
        text = publish.comic_publication_text(self.story, pending["comic_attachment"])
        remote = {"id": "new-provider-id", "status": "sent", "text": text,
                  "createdAt": (attempted + timedelta(seconds=1)).isoformat()}
        old = recorded(hours=180)
        asset = comics.approved_comic_asset(self.story, self.review, self.root, NOW)
        cases = [[{**remote, "id": old["buffer_id"]}],
                 [{**remote, "id": 123}], [{**remote, "id": ""}],
                 [{**remote, "createdAt": (attempted - timedelta(seconds=1)).isoformat()}],
                 [{**remote, "createdAt": (NOW + timedelta(seconds=1)).isoformat()}],
                 [{**remote, "createdAt": None}],
                 [remote, {**remote, "id": "another-provider-id"}]]
        for existing in cases:
            state = {**ledger(**{"comic-story": copy.deepcopy(old)}),
                     "comic_posts": {"comic-story": copy.deepcopy(pending)}}
            with self.subTest(existing=existing):
                picks.recover_comic_posts(state, existing, NOW, [(self.story, self.review, asset)])
                self.assertTrue(state["comic_posts"]["comic-story"]["submission_pending"])
                self.assertFalse(state["comic_posts"]["comic-story"]["buffer_id"])
                self.assertEqual(state["queued"]["comic-story"], old)
        state = ledger(**{"comic-story": copy.deepcopy(old)})
        picks.recover_comic_posts(state, [remote], NOW, [(self.story, self.review, asset)])
        self.assertFalse(state.get("comic_posts", {}).get("comic-story", {}).get("buffer_id"))
        self.assertEqual(state["queued"]["comic-story"], old)
        with self.main_context(state=ledger(**{"comic-story": old}), existing=[remote]) as mocks:
            self.assertEqual(picks.main(), 0)
            mocks["queue"].assert_not_called()
            mocks["gql"].assert_not_called()

    def test_article_comic_still_obeys_shared_buffer_budget_hold_before_http(self):
        path = self.root / "api-budget.json"
        budget.write(path, {"schema_version": 1, "requests": [], "provider_limits": [],
                            "provider_retry_at_utc": "2026-12-03T03:05:19+00:00"})
        post = {"kind": "article", "id": "article-comic-story", "article": self.story,
                "comic_approval": self.review, "text": "https://veripsa.com/articles/comic-story/"}
        with patch.object(publish, "HERE", self.root / "social-bot"), \
             patch.object(publish, "remote_asset_available", return_value=True), \
             patch.object(publish, "urlopen") as http, \
             patch.dict(os.environ, {"BUFFER_BUDGET_PATH": str(path), "BUFFER_REQUEST_ROLE": "publish"}), \
             patch.object(budget, "datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            with self.assertRaisesRegex(RuntimeError, "hold until"):
                publish.queue_one("mock-only", "mock-channel", post, mode="shareNow")
            http.assert_not_called()
        self.assertEqual(budget.read(path)["requests"], [])


if __name__ == "__main__":
    unittest.main()
