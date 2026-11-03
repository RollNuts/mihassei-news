#!/usr/bin/env python3
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_delivery import (report, recent_unknown_ids, collect_missing_links,
                            queue_health, trusted_x_permalink)

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

    def test_preserve_permalink_only_with_matching_sent_buffer_id(self):
        old={"items":[{"post_id":"xexp-a","buffer_id":"original",
                        "buffer_status":"sent",
                        "buffer_external_link":"https://x.com/account/status/1234",
                        "buffer_asset_count":1}]}
        state={"queued":{"xexp-a":{"buffer_id":"original"}}}
        rows=report(state,[{"id":"original","status":"sent"}],old)
        self.assertEqual(rows[0]["buffer_external_link"],
                         "https://x.com/account/status/1234")
        self.assertEqual(rows[0]["buffer_asset_count"],1)
        changed={"queued":{"xexp-a":{"buffer_id":"different"}}}
        self.assertNotIn("buffer_external_link",
                         report(changed,[{"id":"different","status":"sent"}],old)[0])
        self.assertNotIn("buffer_external_link",
                         report(state,[{"id":"original","status":"scheduled"}],old)[0])
        old["items"][0]["buffer_external_link"]="https://invalid.example/status/1234"
        self.assertNotIn("buffer_external_link",
                         report(state,[{"id":"original","status":"sent"}],old)[0])
        self.assertFalse(trusted_x_permalink("https://fake-x.com/user/status/123"))

    def test_provider_detail_queries_are_read_only_and_capped_at_two(self):
        now=datetime(2026,11,4,12,0,tzinfo=timezone.utc)
        inventory={"experiments":[{"id":f"xexp-{i}","text":f"テスト文{i}",
                   "format":"surreal_text"} for i in range(5)]}
        state={"queued":{f"xexp-{i}":{"buffer_id":f"buf-{i}",
               "queued_at_utc":f"2026-11-04T0{i}:00:00+00:00"} for i in range(5)}}
        remote=[{"id":f"buf-{i}","status":"sent"} for i in range(5)]
        rows=report(state,remote)
        calls=[]
        def get_one(bufid):
            calls.append(bufid)
            i=int(bufid[-1])
            return {"id":bufid,"channelId":"Veripsa",
                    "text":f"テスト文{i}","status":"sent",
                    "externalLink":f"https://x.com/account/status/{100+i}",
                    "assets":[]}
        count=collect_missing_links(rows,state,inventory,"Veripsa",get_one,
                                    now=now,max_probes=2)
        self.assertEqual(count,2)
        self.assertEqual(len(calls),2)
        self.assertEqual(sum(bool(r.get("buffer_external_link")) for r in rows),2)
        with self.assertRaisesRegex(ValueError,"cap exceeded"):
            collect_missing_links(rows,state,inventory,"Veripsa",get_one,
                                  now=now,max_probes=3)

    def test_inventory_health_uses_only_known_records(self):
        inventory={"experiments":[{"id":"xexp-a","visual":"sock"},
                                  {"id":"xexp-b"},{"id":"xexp-c","visual":"book"}]}
        state={"queued":{"xexp-a":{"buffer_id":"sent-id"}}}
        remote=[{"id":"sent-id","status":"sent"},
                {"id":"other","status":"scheduled"}]
        health=queue_health(inventory,state,remote,report(state,remote))
        self.assertEqual(health["original_unsubmitted"],2)
        self.assertEqual(health["unsubmitted_original_images"],1)
        self.assertEqual(health["buffer_currently_scheduled_total"],1)
        self.assertEqual(health["buffer_sent_original_records"],1)
        self.assertNotIn("impressions",health)

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
