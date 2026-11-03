#!/usr/bin/env python3
"""Offline regression tests for append-only canonical history policy."""
import copy
import unittest

from history_guard import CanonHistoryError, check_history


def snapshot():
    return {
        "posts":{"brand":"どうでもいい世界","posts":[
            {"id":"old","kind":"text","text":"前の投稿を残す"},
            {"id":"nejimachi-001","series":"nejimachi","episode":1,"text":"連載の確定済み第1話"}]},
        "canon":{"series_id":"nejimachi","brand":"ネジまち","subtitle":"小さな暮らし",
                 "created_jst":"2026-11-03",
                 "identity":{"principle":"住民を笑わない"},
                 "setting":{"city":"トルマ市","sky":"本当の空は未知"},
                 "characters":{"neji":{"shape":"欠けた六角"}},
                 "locations":{"station":{"introduced_in":1}},
                 "facts":{"morning":{"introduced_in":1,"value":"朝札がある"}},
                 "forbidden":{"canon":["外の真相を勝手に決めない"]},
                 "open_mysteries":[{"id":"sky","question":"空の上は？",
                                    "state":"unresolved","can_reveal_after":8}]},
        "ledger":{"series_id":"nejimachi","chapter":1,
                  "entries":[{"episode":1,"post_id":"nejimachi-001","event":"朝の札"}]},
        "state":{"submitted":{"old":{"buffer_id":"original-123"}}}
    }


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.before=snapshot()
        self.after=copy.deepcopy(self.before)

    def assert_blocked(self, words):
        with self.assertRaisesRegex(CanonHistoryError, words):
            check_history(self.before,self.after)

    def test_append_new_episode_is_allowed(self):
        self.after["posts"]["posts"].append({"id":"nejimachi-002","series":"nejimachi","episode":2})
        self.after["ledger"]["entries"].append({"episode":2,"post_id":"nejimachi-002","event":"次の話"})
        self.after["canon"]["facts"]["blue"]={"introduced_in":2,"value":"灯が青い"}
        check_history(self.before,self.after)

    def test_existing_episode_cannot_be_rewritten(self):
        self.after["posts"]["posts"][1]["text"]="別の展開"
        self.assert_blocked("posts.json")

    def test_previous_ledger_event_is_immutable(self):
        self.after["ledger"]["entries"][0]["event"]="後付けの設定"
        self.assert_blocked("ledger entries")

    def test_existing_character_cannot_change(self):
        self.after["canon"]["characters"]["neji"]["shape"]="球形"
        self.assert_blocked("canon.characters")

    def test_canon_fact_cannot_be_altered(self):
        self.after["canon"]["facts"]["morning"]["value"]="朝札は不要"
        self.assert_blocked("canon.facts")

    def test_queued_record_cannot_disappear(self):
        del self.after["state"]["submitted"]["old"]
        self.assert_blocked("state.submitted")

    def test_mystery_requires_an_episode_to_be_resolved(self):
        self.after["canon"]["open_mysteries"][0]["state"]="resolved"
        self.assert_blocked("without episode")

    def test_mystery_reveal_allowed_at_appropriate_episode(self):
        self.after["canon"]["open_mysteries"][0]["state"]="resolved"
        self.after["ledger"]["entries"].append({"episode":8,"threads_closed":["sky"]})
        check_history(self.before,self.after)

    def test_chapter_cannot_jump_forward(self):
        self.after["ledger"]["chapter"]=3
        self.assert_blocked("Chapter transition")


if __name__ == "__main__":
    unittest.main(verbosity=2)
