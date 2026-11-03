"""Offline tests of Buffer-Free article picks; never queue from test."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dispatch as picks

NOW = datetime.fromisoformat("2026-11-05T12:00:00+09:00")
CONFIG = {
    "schema_version": 1, "enabled": True, "channel_id": "X-VERIPSA",
    "max_queue_size": 3, "max_new_per_run": 1, "min_readers": 8,
    "minimum_article_age_hours": 12, "maximum_article_age_days": 21,
    "minimum_interval_hours": 72,
}


def article(slug: str, age_hours: int = 36, title: str = "人が戸惑う架空ニュース"):
    return {
        "slug": slug,
        "title": title,
        "published_at": (NOW - timedelta(hours=age_hours)).isoformat(),
        "fiction_at": "2031-11-05T00:00:00+09:00",  # Never used for age.
        "source": {"published_at": "2026-11-05T00:00:00+09:00"},
    }


def stats(readers=11, likes=0, share_clicks=0, continued_readers=1):
    return {"readers": readers, "likes": likes, "share_clicks": share_clicks,
            "continued_readers": continued_readers}


def ledger(**entries):
    return {"schema_version": 1, "queued": entries}


def recorded(buf="buf-old", hours=180):
    return {"buffer_id": buf,
            "queued_at_utc": (NOW - timedelta(hours=hours)).isoformat()}


class ArticlePickTests(unittest.TestCase):
    def test_actual_config_and_initial_ledger(self):
        config = picks.read_json(HERE / "config.json")
        original = picks.read_json(HERE.parent / "experiments" / "config.json")
        picks.validate_config(config, original)
        state = picks.read_json(HERE / "state.json")
        picks.validate_state(state)
        self.assertEqual(config["max_queue_size"], 3)
        self.assertEqual(config["max_new_per_run"], 1)
        self.assertGreaterEqual(config["minimum_interval_hours"], 72)

    def test_single_clearly_fictional_post_with_canonical_url(self):
        a = article("story-a", title="家計簿の利息を銀行員に見せた夫婦")
        msg = picks.publication_text(a)
        self.assertTrue(msg.startswith("【未発生新聞・創作】"))
        self.assertIn("家計簿", msg)
        self.assertIn("https://veripsa.com/articles/story-a/", msg)
        self.assertNotIn("AI生成", msg)
        self.assertNotIn("大人気", msg)
        self.assertLessEqual(len(msg), 220)
        self.assertEqual(picks.article_slug_in_text(msg), {"story-a"})
        self.assertEqual(
            picks.article_slug_in_text("少し話題に https://veripsa.com/articles/story-a/?utm_source=x"),
            {"story-a"}
        )
        with self.assertRaises(ValueError):
            picks.publication_text(article("../unsafe"))

    def test_rank_only_observed_signals_without_fabricating_popularity(self):
        a, b, c = article("case-a"), article("case-b"), article("case-c")
        counts = {
            "case-a": stats(readers=10, continued_readers=1),
            "case-b": stats(readers=9, likes=1, continued_readers=0),
            "case-c": stats(readers=100, continued_readers=0),
        }
        rows = picks.rank([a, b, c], counts, ledger(), set(), CONFIG)
        self.assertEqual([x[0]["slug"] for x in rows],
                         ["case-c", "case-a", "case-b"])
        # Many real readers alone can qualify, but tiny passive samples cannot.
        no_engagement = article("passive-story")
        self.assertEqual(picks.rank([no_engagement], {
            "passive-story": stats(readers=9, continued_readers=0)
        }, ledger(), set(), CONFIG), [])
        # Missing evidence is not a zero metric and must not be selected.
        self.assertEqual(picks.rank([a], {}, ledger(), set(), CONFIG), [])
        self.assertEqual(picks.rank([a], {"case-a": {
            "readers": 10, "likes": None, "share_clicks": 0,
            "continued_readers": 1
        }}, ledger(), set(), CONFIG), [])

    def test_young_articles_and_2031_dates_cannot_game_promotion(self):
        a = article("yesterday", age_hours=2)
        b = article("old-story", age_hours=60 * 24)
        c = article("real-recent", age_hours=15)
        good = picks.eligible_articles([a, b, c], NOW, CONFIG)
        self.assertEqual([p["slug"] for p in good], ["real-recent"])

    def test_pinned_channel_and_limits_fail_closed(self):
        original = {"channel_id": "X-VERIPSA"}
        picks.validate_config(CONFIG, original)
        for field, value in (("channel_id", "OTHER-ACCOUNT"),
                             ("max_queue_size", 10),
                             ("max_new_per_run", 2),
                             ("minimum_interval_hours", 0),
                             ("min_readers", 0),
                             ("enabled", "true")):
            cfg = {**CONFIG, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                picks.validate_config(cfg, original)

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
        self.assertEqual(picks.recover_from_buffer(state, existing, NOW,
                                                  {"story-a", "story-b"}), [])
        self.assertEqual(picks.rank([a], {"story-a": stats()},
                                    state, set(), CONFIG), [])
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "state.json"
            picks.save_state(state, target)
            self.assertEqual(picks.read_json(target), state)

    def test_previous_post_persists_when_buffer_no_longer_lists_sent_history(self):
        old = ledger(**{"story-a": recorded(hours=500)})
        offer = picks.rank([article("story-a")], {"story-a": stats()}, old, set(), CONFIG)
        self.assertEqual(offer, [])

    def test_three_day_cooldown_and_creative_queue_safety(self):
        a = article("new-story")
        counts = {"new-story": stats()}
        remote = [
            {"id": "short-1", "status": "scheduled", "text": "昔話の短文"},
            {"id": "short-2", "status": "scheduled", "text": "別の短文"},
        ]
        state = ledger()
        item, evidence, queued = picks.decide([a], counts, state, remote, CONFIG,
                                              NOW, {"short-1", "short-2"})
        self.assertEqual(item["slug"], "new-story")
        self.assertEqual(queued, 2)
        self.assertEqual(evidence, counts["new-story"])
        item, _, _ = picks.decide([a], counts, ledger(), remote + [
            {"id": "other-3", "status": "scheduled", "text": "他の投稿"},
        ], CONFIG, NOW, {"short-1"})
        self.assertIsNone(item)
        # When all scheduled slots are unrelated, the creative lane refills first.
        item, _, _ = picks.decide([a], counts, ledger(), remote, CONFIG,
                                  NOW, set())
        self.assertIsNone(item)
        state = ledger(**{"previous-story": recorded(hours=30)})
        item, _, _ = picks.decide([a], counts, state, remote, CONFIG,
                                  NOW, {"short-1"})
        self.assertIsNone(item)
        state = ledger(**{"previous-story": recorded(hours=73)})
        item, _, _ = picks.decide([a], counts, state, remote, CONFIG,
                                  NOW, {"short-1"})
        self.assertEqual(item["slug"], "new-story")

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
