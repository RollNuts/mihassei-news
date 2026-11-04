#!/usr/bin/env python3
import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from probe_delivery import (build, reconcile, x_link, normalize_buffer_impressions,
                            queue_health, fetch_status_batch, observed_original_sent_by_jst_day,
                            observed_scheduled_originals_by_jst_day, delivery_audit_needed, main)

class BufferReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.post={"id":"xexp-test","sequence":1,"format":"surreal_text",
                   "text":"架空のテスト文章。実際の投稿ではありません。"}
        self.rec={"buffer_id":"buf-1"}
        self.remote={"id":"buf-1","text":self.post["text"],
                     "channelId":"x-1","status":"sent",
                     "externalLink":"https://x.com/Veripsa/status/123?src=buffer",
                     "sentAt":"2026-11-03T12:00:00Z","assets":[]}
    def test_provider_due_at_is_accepted_only_for_verified_scheduled_post(self):
        self.remote["status"]="scheduled"
        self.remote["dueAt"]="2026-11-04T03:30:00Z"
        result=reconcile(self.post,self.rec,self.remote,"x-1")
        self.assertEqual(result["buffer_due_at_utc"],"2026-11-04T03:30:00+00:00")
        self.remote["dueAt"]="2026-11-04T12:30:00"  # naive provider value
        self.assertNotIn("buffer_due_at_utc",
                         reconcile(self.post,self.rec,self.remote,"x-1"))
        self.remote["dueAt"]="2026-11-04T03:30:00Z"
        self.remote["channelId"]="unrelated-x"
        self.assertNotIn("buffer_due_at_utc",
                         reconcile(self.post,self.rec,self.remote,"x-1"))
        self.remote["channelId"]="x-1"
        self.remote["status"]="sent"
        self.assertNotIn("buffer_due_at_utc",
                         reconcile(self.post,self.rec,self.remote,"x-1"))

    def test_future_queue_slots_only_count_fresh_verified_originals(self):
        from datetime import datetime,timezone
        now=datetime(2026,11,3,13,30,tzinfo=timezone.utc)  # Nov 3 22:30 JST
        checked=now.isoformat()
        rows=[
            {"post_id":"a","buffer_status":"scheduled",
             "last_buffer_check_utc":checked,
             "buffer_due_at_utc":"2026-11-04T00:30:00Z"}, # Nov4 09:30 JST
            {"post_id":"b","buffer_status":"scheduled",
             "last_buffer_check_utc":checked,
             "buffer_due_at_utc":"2026-11-04T06:30:00+00:00"},
            {"post_id":"c","buffer_status":"scheduled",
             "last_buffer_check_utc":"2026-11-02T13:30:00+00:00",
             "buffer_due_at_utc":"2026-11-04T09:30:00Z"},  # cached excluded
            {"post_id":"d","buffer_status":"sent",
             "last_buffer_check_utc":checked,
             "buffer_due_at_utc":"2026-11-04T10:00:00Z"},
            {"post_id":"e","buffer_status":"scheduled",
             "last_buffer_check_utc":checked,
             "buffer_due_at_utc":"invalid"},
            {"post_id":"f","buffer_status":"scheduled",
             "last_buffer_check_utc":checked,
             "buffer_due_at_utc":"2026-11-03T07:00:00Z"},  # overdue >2h
        ]
        result=observed_scheduled_originals_by_jst_day(rows,now=now,days=3)
        self.assertEqual(result["days"][1]["date_jst"],"2026-11-04")
        self.assertEqual(result["days"][1]["observed_fresh_original_scheduled"],2)
        self.assertEqual(result["days"][1]["observed_times_jst"],["09:30","15:30"])
        self.assertEqual(result["fresh_scheduled_records_checked"],4)
        self.assertEqual(result["fresh_scheduled_without_due_at"],1)
        self.assertEqual(result["overdue_scheduled_over_two_hours"],1)
        self.assertIn("partial_provider_queue",result["days"][1]["evidence"])
        with self.assertRaisesRegex(ValueError,"Invalid scheduled-slot"):
            observed_scheduled_originals_by_jst_day(rows,now=now,days=30)

    def test_status_batch_includes_due_at_without_extra_provider_queries(self):
        with patch("probe_delivery.gql",return_value={
            "p0":{"id":"buffer-a","status":"scheduled","dueAt":"2026-11-04T03:00:00Z"}
        }) as gql:
            result=fetch_status_batch("secret",["buffer-a"])
        self.assertEqual(gql.call_count,1)
        self.assertIn("dueAt",gql.call_args.args[1])
        self.assertEqual(result["buffer-a"]["dueAt"],"2026-11-04T03:00:00Z")


    def test_missing_due_at_is_flagged_separately_from_provider_sent(self):
        remote=dict(self.remote, status="scheduled", dueAt=None)
        record=dict(self.rec, buffer_due_at_at_creation_utc="2026-11-04T00:00:00+00:00")
        observed=reconcile(self.post,record,remote,"x-1")
        self.assertEqual(observed["buffer_status"],"scheduled")
        self.assertTrue(observed["buffer_due_at_missing_on_audit"])
        self.assertEqual(observed["buffer_due_at_at_creation_utc"],
                         "2026-11-04T00:00:00+00:00")
        self.assertNotIn("buffer_due_at_utc", observed)
        remote["dueAt"]="2026-11-04T01:00:00Z"
        available=reconcile(self.post,record,remote,"x-1")
        self.assertEqual(available["buffer_due_at_utc"],
                         "2026-11-04T01:00:00+00:00")
        self.assertNotIn("buffer_due_at_missing_on_audit",available)

    def test_sent_has_only_provider_permalink(self):
        row=reconcile(self.post,self.rec,self.remote,"x-1")
        self.assertEqual(row["buffer_status"],"sent")
        self.assertEqual(row["buffer_external_link"],
                         "https://x.com/Veripsa/status/123")
        self.remote["status"]="scheduled"
        row=reconcile(self.post,self.rec,self.remote,"x-1")
        self.assertNotIn("buffer_external_link",row)
    def test_cross_account_or_mutated_body_is_unverified(self):
        self.remote["channelId"]="different-x"
        self.assertEqual(reconcile(self.post,self.rec,self.remote,"x-1")
                         ["buffer_status"],"unverified")
        self.remote["channelId"]="x-1"
        self.remote["text"]="hijacked"
        self.assertEqual(reconcile(self.post,self.rec,self.remote,"x-1")
                         ["buffer_status"],"unverified")
    def test_invalid_url_rejected(self):
        for url in ("http://x.com/a/status/1",
                    "https://evil.com/a/status/1",
                    "https://x.com.evil.org/a/status/1",
                    "https://x.com/example"):
            self.assertIsNone(x_link(url))
    def test_provider_metric_requires_genuine_count_and_timestamp(self):
        sent="2026-11-03T10:55:00Z"
        snapshot=normalize_buffer_impressions({
            "metricsUpdatedAt":"2026-11-04T12:00:00Z",
            "metrics":[{"type":"impressions","value":42,"unit":"count"}]},sent)
        self.assertEqual(snapshot["impressions"],42)
        self.assertIsNone(snapshot["window_hours"])
        self.assertEqual(snapshot["provider"],"buffer_experimental_personal_api")
        self.assertIsNone(normalize_buffer_impressions({
            "metricsUpdatedAt":"2026-11-03T09:00:00Z",
            "metrics":[{"type":"impressions","value":99,"unit":"count"}]},sent))
        self.assertIsNone(normalize_buffer_impressions({
            "metricsUpdatedAt":"2026-11-04T12:00:00Z",
            "metrics":[{"type":"impressions","value":-1,"unit":"count"}]},sent))
        self.assertIsNone(normalize_buffer_impressions({
            "metricsUpdatedAt":"2026-11-04T12:00:00Z",
            "metrics":[{"type":"likes","value":99,"unit":"count"}]},sent))

    def test_initial_zero_is_not_used_as_a_real_observation(self):
        sent="2026-11-03T10:55:16Z"
        early={"metricsUpdatedAt":"2026-11-03T10:55:25Z",
               "metrics":[{"type":"impressions","value":0,"unit":"count"}]}
        self.assertIsNone(normalize_buffer_impressions(early,sent))
        early["metrics"][0]["value"]=500
        self.assertIsNone(normalize_buffer_impressions(early,sent))
        early["metricsUpdatedAt"]="2026-11-04T00:00:00Z"
        sampled=normalize_buffer_impressions(early,sent)
        self.assertEqual(sampled["impressions"],500)
        self.assertIsNone(sampled["window_hours"])

    def test_missing_metric_scope_does_not_turn_sent_into_error(self):
        inv={"schema_version":1,"experiments":[self.post]}
        import hashlib
        state={"schema_version":1,"queued":{
            self.post["id"]:{"buffer_id":"buf-1",
                "text_sha256":hashlib.sha256(self.post["text"].encode()).hexdigest()}}}
        def inaccessible(_):
            raise RuntimeError("insightsRead scope unavailable")
        from datetime import datetime, timezone
        report=build(inv,state,"x-1",lambda _:self.remote,inaccessible,
                     now=datetime(2026,11,4,1,tzinfo=timezone.utc))
        row=report["items"][0]
        self.assertEqual(row["buffer_status"],"sent")
        self.assertFalse(row["buffer_metrics_available"])
        self.assertNotIn("buffer_latest_impressions",row)

    def test_missing_remote_never_means_sent(self):
        inv={"schema_version":1,"experiments":[self.post]}
        state={"schema_version":1,"queued":{
            self.post["id"]:{"buffer_id":"buf-1",
                             "text_sha256":hashlib.sha256(self.post["text"].encode()).hexdigest()}}}
        # Pure read normalizes a missing provider response without posting.
        result=build(inv,state,"x-1",lambda id:None)
        self.assertEqual(result["items"][0]["buffer_status"],"unverified")
        self.assertEqual(len(result["recent_unverified_experiment_ids"]),1)
    def test_daily_audit_scales_without_losing_old_permalinks(self):
        posts, queued = [], {}
        for i in range(40):
            pid = f"xexp-many-{i}"
            body = f"独立した奇妙な創作ネタです。番号{i}の確認用です。"
            posts.append({"id": pid, "sequence": i+1,
                          "format": "surreal_text", "text": body})
            queued[pid] = {"buffer_id": f"b{i}",
                           "text_sha256": hashlib.sha256(body.encode()).hexdigest(),
                           "queued_at_utc": f"2026-11-{(i%20)+1:02d}T11:00:00+00:00"}
        state = {"schema_version": 1, "queued": queued}
        inventory = {"schema_version": 1, "experiments": posts}
        previous = {"items": [
            {"post_id": "xexp-many-0", "buffer_id": "b0",
             "buffer_status": "sent", "format": "surreal_text",
             "buffer_external_link": "https://x.com/user/status/123"}]}
        queried = []
        def get_post(postid):
            queried.append(postid)
            number = int(postid[1:])
            return {"id": postid, "channelId": "x-1",
                    "text": posts[number]["text"], "status": "scheduled",
                    "assets": []}
        result = build(inventory, state, "x-1", get_post, previous=previous)
        self.assertEqual(len(queried), 20)
        self.assertEqual(len(result["items"]), 40)
        self.assertEqual(result["limits"]["max_post_reads"], 20)
        old = next(item for item in result["items"]
                   if item["post_id"] == "xexp-many-0")
        self.assertEqual(old["buffer_status"], "sent")
        self.assertIn("buffer_external_link", old)

    def test_only_mature_sent_posts_use_one_metric_read(self):
        from datetime import datetime, timezone
        posts, queued = [], {}
        distinct_texts = [
            "図書館で本が突然空を飛んで、読者が座り込んで驚いてしまった。",
            "駅のホームに雲が並んで、改札機が傘を持って待っていた。",
            "弁当箱が朝から家を出て、箸だけが台所に取り残された。",
            "時計屋のショーケースの中で、秒針が静かな昼寝を始めた。",
            "海辺の灯台が夕方に目を閉じて、船に少しだけ謝った。",
            "山奥の郵便箱が手紙を読んで、宛先より先に泣き出した。",
        ]
        for i, body in enumerate(distinct_texts):
            pid = f"xexp-measure-{i}"
            posts.append({"id": pid, "sequence": i+1,
                          "format": "surreal_text", "text": body})
            queued[pid] = {"buffer_id": f"b{i}",
                           "text_sha256": hashlib.sha256(body.encode()).hexdigest()}
        state = {"schema_version": 1, "queued": queued}
        inventory = {"schema_version": 1, "experiments": posts}
        def get_post(postid):
            i = int(postid[1:])
            return {"id": postid, "channelId": "x-1",
                    "text": posts[i]["text"], "status": "sent",
                    "sentAt": "2026-11-01T00:00:00Z",
                    "externalLink": "https://x.com/u/status/123", "assets": []}
        metric_calls = []
        def get_insights(postid):
            metric_calls.append(postid)
            return {"metricsUpdatedAt": "2026-11-03T00:00:00Z",
                    "metrics": [{"type": "impressions",
                                 "value": 20, "unit": "count"}]}
        result = build(inventory, state, "x-1", get_post, get_insights,
                       now=datetime(2026,11,3,12,tzinfo=timezone.utc))
        self.assertEqual(len(metric_calls), 1)
        self.assertEqual(result["limits"]["metric_reads_this_run"], 1)
        self.assertEqual(sum("buffer_latest_impressions" in item
                             for item in result["items"]), 1)

    def test_buffer_graphql_aliases_are_one_call_per_ten_ids(self):
        posts = ["buf-one", "buf-two", "buf-three"]
        payload = {"p0": {"id": posts[0]}, "p1": {"id": posts[1]},
                   "p2": {"id": posts[2]}}
        with patch("probe_delivery.gql", return_value=payload) as caller:
            found = fetch_status_batch("token-from-secret", posts)
            self.assertEqual(caller.call_count, 1)
            query = caller.call_args.args[1]
            self.assertIn('p0: post(input: {id: "buf-one"})', query)
            self.assertIn('p2: post(input: {id: "buf-three"})', query)
            self.assertEqual(found["buf-two"]["id"], "buf-two")
        with self.assertRaisesRegex(ValueError, "alias batch"):
            fetch_status_batch("token", posts*4)
        with self.assertRaisesRegex(ValueError, "alias batch"):
            fetch_status_batch("token", ["duplicate", "duplicate"])
        with patch("probe_delivery.gql", return_value={"p0": {}}):
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                fetch_status_batch("token", posts)

    def test_twenty_post_statuses_only_cost_two_api_calls(self):
        posts, queued = [], {}
        for i in range(25):
            pid = f"xexp-bulk-{i}"
            body = f"試験用の独立した短文です。おかしな出来事番号{i}。"
            posts.append({"id": pid, "sequence": i+1,
                          "format": "surreal_text", "text": body})
            queued[pid] = {"buffer_id": f"b{i}",
                           "text_sha256": hashlib.sha256(body.encode()).hexdigest(),
                           "queued_at_utc": f"2026-11-03T{(i%24):02d}:00:00Z"}
        inv = {"schema_version": 1, "experiments": posts}
        state = {"schema_version": 1, "queued": queued}
        requests = []
        def batch_fetch(ids):
            requests.append(ids)
            return {postid: {
                "id": postid, "channelId": "x-1",
                "text": posts[int(postid[1:])]["text"], "status": "scheduled",
                "assets": []} for postid in ids}
        def must_not_call(_):
            self.fail("Batch fallback must not issue per-post queries")
        outcome = build(inv, state, "x-1", must_not_call,
                        fetch_batch=batch_fetch)
        self.assertEqual([len(ids) for ids in requests], [10, 10])
        self.assertEqual(outcome["limits"]["post_reads_this_run"], 20)
        self.assertEqual(outcome["limits"]["status_api_requests_this_run"], 2)
        self.assertEqual(len(outcome["items"]), 25)
        self.assertEqual(sum(row["buffer_status"]=="scheduled"
                             for row in outcome["items"]), 20)

    def test_failed_alias_batch_never_reposts_or_spams_fallback(self):
        inventory = {"schema_version": 1, "experiments": [self.post]}
        state = {"schema_version": 1, "queued": {
            self.post["id"]: {
                "buffer_id": "buf-1",
                "text_sha256": hashlib.sha256(self.post["text"].encode()).hexdigest()
            }
        }}
        previous = {"items": [{
            "post_id": self.post["id"], "buffer_id": "buf-1",
            "format": "surreal_text", "buffer_status": "sent",
            "buffer_external_link": "https://x.com/Veripsa/status/123"
        }]}
        def broken(_):
            raise RuntimeError("GraphQL provider temporarily unavailable")
        def must_not_call(_):
            self.fail("A failing batch must not retry all posts")
        result = build(inventory, state, "x-1", must_not_call,
                       previous=previous, fetch_batch=broken)
        row = result["items"][0]
        self.assertEqual(row["buffer_status"], "unverified")
        self.assertEqual(row["previous_buffer_external_link"],
                         "https://x.com/Veripsa/status/123")
        self.assertEqual(result["limits"]["status_api_requests_this_run"], 1)

    def test_capacity_report_is_based_on_original_inventory_only(self):
        inventory={"experiments":[
            {"id":"xexp-one","format":"surreal_text","text":"昔の本文"},
            {"id":"xexp-two","visual":"sock","format":"surreal_text","text":"新作"},
            {"id":"xexp-three","visual":"book","format":"surreal_text","text":"新作2"},
        ]}
        state={"queued":{"xexp-one":{"buffer_id":"b1"}}}
        rows=[{"post_id":"xexp-one","buffer_id":"b1",
               "buffer_status":"sent",
               "buffer_external_link":"https://x.com/user/status/12"}]
        summary=queue_health(inventory,state,rows)
        self.assertEqual(summary["original_unsubmitted"],2)
        self.assertEqual(summary["unsubmitted_original_images"],2)
        self.assertEqual(summary["provider_permalinks_last_known"],1)
        self.assertTrue(summary["unsubmitted_inventory_below_14"])
        self.assertFalse(summary["unsubmitted_images_below_2"])
        self.assertNotIn("impressions",summary)

    def test_jst_daily_receipts_cross_midnight_without_fake_reach(self):
        from datetime import datetime, timezone
        now=datetime(2026,11,4,16,0,tzinfo=timezone.utc)  # Nov 5 01:00 JST
        rows=[
            {"post_id":"a","buffer_status":"sent",
             "buffer_sent_at_utc":"2026-11-04T14:50:00Z"},
            {"post_id":"b","buffer_status":"sent",
             "buffer_sent_at_utc":"2026-11-04T15:10:00Z"},
            {"post_id":"c","buffer_status":"scheduled",
             "buffer_sent_at_utc":"2026-11-04T15:30:00Z"},
            {"post_id":"d","buffer_status":"sent"},  # no provider sent timestamp
            {"post_id":"e","buffer_status":"sent",
             "buffer_sent_at_utc":"2026-11-06T09:00:00Z"},  # future
        ]
        days=observed_original_sent_by_jst_day(rows,now=now)
        self.assertEqual(len(days),7)
        self.assertEqual(days[-2]["date_jst"],"2026-11-04")
        self.assertEqual(days[-2]["observed_original_buffer_sent"],1)
        self.assertEqual(days[-1]["date_jst"],"2026-11-05")
        self.assertEqual(days[-1]["observed_original_buffer_sent"],1)
        self.assertEqual(days[-1]["configured_daily_slots"],7)
        self.assertIn("incomplete_lower_bound",days[-1]["evidence"])

    def test_ambiguous_or_missing_buffer_dates_never_count_as_x_posts(self):
        from datetime import datetime, timezone
        now=datetime(2026,11,4,16,0,tzinfo=timezone.utc)
        rows=[
            {"buffer_status":"sent","buffer_sent_at_utc":"invalid"},
            {"buffer_status":"sent","buffer_sent_at_utc":"2026-11-04T15:15:00"},
            {"buffer_status":"unverified","buffer_sent_at_utc":"2026-11-04T15:12:00Z"},
        ]
        days=observed_original_sent_by_jst_day(rows,now=now)
        self.assertTrue(all(day["observed_original_buffer_sent"]==0 for day in days))
        self.assertTrue(all(day["evidence"].startswith("incomplete") for day in days))
        with self.assertRaisesRegex(ValueError,"Invalid daily evidence window"):
            observed_original_sent_by_jst_day(rows,now=now,days=90)

    def test_image_attachment_is_audited_not_assumed(self):
        self.post["visual"]="ufo"
        row=reconcile(self.post,self.rec,self.remote,"x-1")
        self.assertEqual(row["media_review_needed"],"buffer_image_asset_missing")
        self.remote["assets"]=[{"id":"asset1","mimeType":"image/png"}]
        self.assertEqual(reconcile(self.post,self.rec,self.remote,"x-1")
                         ["buffer_asset_count"],1)

    def test_paused_lane_only_stops_auditing_exact_confirmed_terminal_receipts(self):
        config={"experiment_publishing_enabled":False}
        state={"queued":{self.post["id"]:self.rec}}
        row={"post_id":self.post["id"],"buffer_id":"buf-1",
             "last_buffer_check_utc":"2026-11-04T00:00:00+00:00"}
        for status in ("scheduled","sending","error","unverified","needs_approval"):
            with self.subTest(status=status):
                self.assertTrue(delivery_audit_needed(config,state,{
                    "items":[dict(row,buffer_status=status)]}))
        for status in ("sent","draft"):
            previous={"items":[dict(row,buffer_status=status)]}
            self.assertFalse(delivery_audit_needed(config,state,previous))
            self.assertTrue(delivery_audit_needed(
                {"experiment_publishing_enabled":True},state,previous))
        for changed in (dict(row,buffer_id="different",buffer_status="sent"),
                        dict(row,last_buffer_check_utc=None,buffer_status="sent")):
            self.assertTrue(delivery_audit_needed(config,state,{"items":[changed]}))
        self.assertTrue(delivery_audit_needed(config,state,{}))
        self.assertFalse(delivery_audit_needed(config,{"queued":{}},{}))

    def test_paused_finished_lane_never_opens_buffer_or_rewrites_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            documents={
                "config.json":{"experiment_publishing_enabled":False,"channel_id":"x-1"},
                "posts.json":{"schema_version":1,"experiments":[self.post]},
                "state.json":{"schema_version":1,"queued":{self.post["id"]:self.rec}},
                "delivery.json":{"items":[{
                    "post_id":self.post["id"],"buffer_id":"buf-1","buffer_status":"sent",
                    "last_buffer_check_utc":"2026-11-04T00:00:00+00:00"}]},
            }
            for name, document in documents.items():
                (root/name).write_text(json.dumps(document))
            original={path.name:path.read_bytes() for path in root.iterdir()}
            with patch("probe_delivery.HERE",root), patch.dict(os.environ,{"BUFFER_API_KEY":"test-only"}), \
                 patch("probe_delivery.choose_x_channel",side_effect=AssertionError("No Buffer reads allowed")), \
                 patch("probe_delivery.gql",side_effect=AssertionError("No Buffer reads allowed")):
                self.assertEqual(main(),0)
            self.assertEqual({path.name:path.read_bytes() for path in root.iterdir()},original)
            documents["delivery.json"]["items"][0]["buffer_status"]="scheduled"
            (root/"delivery.json").write_text(json.dumps(documents["delivery.json"]))
            with patch("probe_delivery.HERE",root), patch.dict(os.environ,{"BUFFER_API_KEY":"test-only"}), \
                 patch("probe_delivery.choose_x_channel",side_effect=RuntimeError("Audit still required")):
                with self.assertRaisesRegex(RuntimeError,"Audit still required"):
                    main()

if __name__=="__main__":unittest.main(verbosity=2)
