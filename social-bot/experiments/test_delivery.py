#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_delivery import report

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

if __name__=="__main__":
    unittest.main(verbosity=2)
