#!/usr/bin/env python3
"""Offline safety checks: never edit a new story or an unrelated Buffer post."""
import copy
import sys
import unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from retire_legacy_queue import plan_migration, move_to_draft

def dataset():
    feed = {"posts":[
        {"id":"alien-no-entry","kind":"comic","text":"宇宙人のテスト"},
        {"id":"shooting-star-return","kind":"comic","text":"流れ星のテスト"},
        {"id":"nejimachi-001","kind":"comic","series":"nejimachi","text":"新連載"}
    ]}
    state = {"submitted":{
        "alien-no-entry":{"buffer_id":"old-1"},
        "shooting-star-return":{"buffer_id":"old-2"},
        "nejimachi-001":{"buffer_id":"new-1"}
    }}
    queue = [
        {"id":"old-1","status":"scheduled","text":"宇宙人のテスト"},
        {"id":"old-2","status":"scheduled","text":"流れ星のテスト"},
        {"id":"new-1","status":"scheduled","text":"新連載"},
        {"id":"manual-other","status":"scheduled","text":"本人が書いた投稿"},
    ]
    return feed,state,queue

class MigrationSafety(unittest.TestCase):
    def test_draft_edit_preserves_same_original_text(self):
        def fake_gql(token, query):
            self.assertIn('text: "宇宙人のテスト"', query)
            self.assertIn('saveToDraft: true', query)
            self.assertIn('id: "old-1"', query)
            return {"editPost":{"__typename":"PostActionSuccess",
                                "post":{"id":"old-1","status":"draft"}}}
        with patch("retire_legacy_queue.gql", side_effect=fake_gql):
            self.assertEqual(move_to_draft("test-token","old-1","宇宙人のテスト")["status"],"draft")

    def test_only_original_two(self):
        feed,state,queue=dataset()
        plan,notes=plan_migration(feed,state,queue)
        self.assertEqual(plan,[("alien-no-entry","old-1"),
                              ("shooting-star-return","old-2")])
        self.assertEqual(notes,[])

    def test_never_touch_sent_post(self):
        feed,state,queue=dataset()
        queue[0]["status"]="sent"
        plan,notes=plan_migration(feed,state,queue)
        self.assertEqual(plan,[("shooting-star-return","old-2")])
        self.assertEqual(notes[0]["outcome"],"already_sent")

    def test_absent_target_is_safe(self):
        feed,state,queue=dataset()
        queue=queue[1:]
        plan,notes=plan_migration(feed,state,queue)
        self.assertEqual(plan,[("shooting-star-return","old-2")])
        self.assertEqual(notes[0]["outcome"],"not_in_scheduled_or_sent")

    def test_changed_text_is_blocked(self):
        feed,state,queue=dataset()
        queue[0]["text"]="別の投稿に差し替え"
        with self.assertRaisesRegex(ValueError,"mismatch"):
            plan_migration(feed,state,queue)

    def test_missing_history_is_blocked(self):
        feed,state,queue=dataset()
        del state["submitted"]["alien-no-entry"]
        with self.assertRaisesRegex(ValueError,"missing"):
            plan_migration(feed,state,queue)

    def test_ip_story_is_never_migrated(self):
        feed,state,queue=dataset()
        feed["posts"][0]["series"]="nejimachi"
        with self.assertRaisesRegex(ValueError,"IP story"):
            plan_migration(feed,state,queue)

if __name__=="__main__":
    unittest.main(verbosity=2)
