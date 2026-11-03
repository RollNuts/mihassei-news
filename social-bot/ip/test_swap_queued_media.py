#!/usr/bin/env python3
"""Read-only tests: remaster does not rewrite, reschedule, or delete posts."""
import unittest
from swap_queued_media import plan,mutation

class MediaSwapSafety(unittest.TestCase):
    def setup(self):
        feed={"posts":[
          {"id":"nejimachi-001-morning","series":"nejimachi","text":"第1話本文"},
          {"id":"nejimachi-002-blue-sky","series":"nejimachi","text":"第2話本文"}
        ]}
        state={"submitted":{
          "nejimachi-001-morning":{"buffer_id":"original-a"},
          "nejimachi-002-blue-sky":{"buffer_id":"original-b"}
        }}
        live={
          "original-a":{"id":"original-a","text":"第1話本文","status":"scheduled","dueAt":"2026-11-03T10:30:00.000Z"},
          "original-b":{"id":"original-b","text":"第2話本文","status":"scheduled","dueAt":"2026-11-04T10:30:00.000Z"}
        }
        return feed,state,live

    def test_only_two_original_posts(self):
        feed,state,live=self.setup()
        live["other"]={"id":"other","text":"Third-party post","status":"scheduled","dueAt":"2026-11-03T18:00:00.000Z"}
        actions,skipped=plan(feed,state,live)
        self.assertEqual(len(actions),2)
        self.assertEqual(skipped,[])
        self.assertEqual({a["buffer_id"] for a in actions},{"original-a","original-b"})

    def test_sent_or_absent_post_is_not_edited(self):
        feed,state,live=self.setup()
        live["original-a"]["status"]="sent"
        del live["original-b"]
        actions,skipped=plan(feed,state,live)
        self.assertEqual(actions,[])
        self.assertEqual(len(skipped),2)

    def test_text_mismatch_blocks_all(self):
        feed,state,live=self.setup()
        live["original-b"]["text"]="A different post"
        with self.assertRaisesRegex(RuntimeError,"mismatched"):
            plan(feed,state,live)

    def test_missing_schedule_blocks_all(self):
        feed,state,live=self.setup()
        live["original-b"]["dueAt"]=None
        with self.assertRaisesRegex(RuntimeError,"dueAt"):
            plan(feed,state,live)

    def test_edit_preserves_existing_schedule_mode(self):
        feed,state,live=self.setup()
        actions,_=plan(feed,state,live)
        m=mutation(actions[0])
        self.assertIn("editPost",m)
        self.assertIn("assets:",m)
        self.assertIn("text:",m)
        for prohibited in ("deletePost","createPost","saveToDraft","mode:","dueAt:"):
            self.assertNotIn(prohibited,m)

if __name__=="__main__":
    unittest.main(verbosity=2)
