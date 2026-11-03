#!/usr/bin/env python3
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_delivery import report, recent_unknown_ids

class DeliveryStateTests(unittest.TestCase):
    def test_buffer_sent_not_conflated_with_impressions(self):
        state={"queued":{"xexp-a":{"buffer_id":"x1","format":"surreal_text"}}}
        rows=report(state,[{"id":"x1","status":"sent","text":"test"}])
        self.assertEqual(rows[0]["buffer_status"],"sent")
        self.assertNotIn("impressions",rows[0])

    def test_queue_is_marked_scheduled(self):
        rows=report({"queued":{"xexp-b":{"buffer_id":"x2"}}},
                    [{"id":"x2","status":"scheduled"}])
        self.assertEqual(rows[0]["buffer_status"],"scheduled")

    def test_missing_recent_post_is_unknown_not_failed(self):
        rows=report({"queued":{"xexp-a":{"buffer_id":"old"}}},[])
        self.assertEqual(rows[0]["buffer_status"],"not_visible_in_recent_buffer_list")

    def test_unrelated_post_is_ignored(self):
        rows=report({"queued":{"xexp-a":{"buffer_id":"x1"}}},
                    [{"id":"unrelated","status":"sent"}])
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["post_id"],"xexp-a")

    def test_recent_unlisted_submission_needs_review_but_is_not_failure(self):
        state={"queued":{"xexp-a":{
            "buffer_id":"recent-1",
            "queued_at_utc":"2026-11-03T08:05:00+00:00"
        }}}
        rows=report(state,[])
        now=datetime(2026,11,3,12,0,tzinfo=timezone.utc)
        self.assertEqual(recent_unknown_ids(state,rows,now),["xexp-a"])
        self.assertEqual(rows[0]["buffer_status"],"not_visible_in_recent_buffer_list")

    def test_old_or_confirmed_buffer_items_do_not_raise_recent_warning(self):
        state={"queued":{
            "xexp-old":{"buffer_id":"old","queued_at_utc":"2026-10-25T10:00:00+00:00"},
            "xexp-ok":{"buffer_id":"scheduled","queued_at_utc":"2026-11-03T08:00:00+00:00"}
        }}
        rows=report(state,[{"id":"scheduled","status":"scheduled"}])
        now=datetime(2026,11,3,12,0,tzinfo=timezone.utc)
        self.assertEqual(recent_unknown_ids(state,rows,now),[])

if __name__=="__main__":
    unittest.main(verbosity=2)
